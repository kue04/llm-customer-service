import importlib
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime_fixtures import runtime_database
from auth_helpers import auth_headers


class KnowledgeOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.enterContext(runtime_database(Path(self.temp_dir.name) / "runtime.db"))
        self.knowledge_service = importlib.import_module("services.knowledge_service")
        self.feedback_service = importlib.import_module("services.feedback_service")
        self.legacy_seed_path = Path(self.temp_dir.name) / "seed.jsonl"
        self.legacy_backup_dir = Path(self.temp_dir.name) / "backups"
        self.legacy_seed_path.write_text(
            '{"id":"seed","question":"old","answer":"old"}\n',
            encoding="utf-8",
        )

        knowledge_router = importlib.import_module("routers.knowledge")
        app = FastAPI()
        app.include_router(knowledge_router.router, prefix="/knowledge")
        self.app = app
        self.client = TestClient(app)
        self.client.headers.update(auth_headers(roles=["knowledge_ops"], user_id="knowledge_ops_1"))

    def create_item(self, question: str = "优惠券不能用怎么办") -> dict:
        response = self.client.post(
            "/knowledge/items",
            json={
                "question": question,
                "answer": "请查看优惠券详情和结算页原因。",
                "category": "优惠支付",
                "intent": "优惠券不可用",
            },
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_create_item_defaults_to_draft_version_one(self) -> None:
        item = self.create_item()

        self.assertEqual(item["status"], "draft")
        self.assertEqual(item["version"], 1)
        self.assertTrue(item["base_id"])
        self.assertEqual(item["title"], "优惠券不能用怎么办")
        self.assertEqual(item["owner"], "knowledge_ops")
        self.assertEqual(item["source"], "knowledge_ops")

    def test_knowledge_routes_require_operator_identity(self) -> None:
        bare_client = TestClient(self.app)

        list_response = bare_client.get("/knowledge/items")
        create_response = bare_client.post(
            "/knowledge/items",
            json={
                "question": "q",
                "answer": "a",
                "category": "c",
                "intent": "i",
            },
        )

        self.assertEqual(list_response.status_code, 401)
        self.assertEqual(create_response.status_code, 401)

    def test_update_creates_new_draft_version(self) -> None:
        item = self.create_item()

        response = self.client.put(
            f"/knowledge/items/{item['id']}",
            json={
                "question": "红包不能用怎么办",
                "answer": "请截图后通过订单页反馈。",
                "category": "优惠支付",
                "intent": "优惠券不可用",
            },
        )

        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated["base_id"], item["base_id"])
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["status"], "draft")

    def test_archive_and_review_item(self) -> None:
        item = self.create_item()

        pending_response = self.client.post(
            f"/knowledge/items/{item['id']}/review",
            json={"status": "pending_review", "review_note": "submit"},
        )
        self.assertEqual(pending_response.status_code, 200)
        self.assertEqual(pending_response.json()["status"], "pending_review")

        review_response = self.client.post(
            f"/knowledge/items/{item['id']}/review",
            json={"status": "approved", "review_note": "ok"},
        )
        self.assertEqual(review_response.status_code, 200)
        self.assertEqual(review_response.json()["status"], "approved")

        archive_response = self.client.post(f"/knowledge/items/{item['id']}/archive")
        self.assertEqual(archive_response.status_code, 200)
        self.assertEqual(archive_response.json()["status"], "archived")

    def test_review_rejects_invalid_status(self) -> None:
        item = self.create_item()

        response = self.client.post(
            f"/knowledge/items/{item['id']}/review",
            json={"status": "published"},
        )

        self.assertEqual(response.status_code, 422)

    def test_list_filters_and_export_approved_jsonl(self) -> None:
        item = self.create_item("退款失败怎么办")
        self.client.post(f"/knowledge/items/{item['id']}/review", json={"status": "approved"})
        self.create_item("骑手联系不上")

        list_response = self.client.get("/knowledge/items?status=approved&keyword=退款&category=优惠支付")
        self.assertEqual(list_response.status_code, 200)
        body = list_response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["question"], "退款失败怎么办")

        export_response = self.client.get("/knowledge/export-approved")
        self.assertEqual(export_response.status_code, 200)
        export_body = export_response.json()
        self.assertEqual(export_body["count"], 1)
        self.assertIn('"source": "knowledge_ops"', export_body["jsonl"])
        self.assertIn('"title": "退款失败怎么办"', export_body["jsonl"])
        self.assertIn('"version": "v1"', export_body["jsonl"])
        self.assertIn('"question": "退款失败怎么办"', export_body["jsonl"])

    def test_publish_approved_persists_snapshot_and_marks_published(self) -> None:
        item = self.create_item("publish me")
        self.client.post(f"/knowledge/items/{item['id']}/review", json={"status": "approved"})

        response = self.client.post("/knowledge/publish-approved")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["merged_count"], 1)
        self.assertEqual(body["item_ids"], [item["id"]])
        history = self.client.get("/knowledge/publish-history").json()["items"]
        self.assertEqual(history[0]["publish_id"], body["publish_id"])
        self.assertEqual(history[0]["item_ids"], [item["id"]])
        self.assertEqual(self.client.get("/knowledge/items?status=published").json()["total"], 1)

    def test_publish_without_approved_is_noop(self) -> None:
        before = self.legacy_seed_path.read_text(encoding="utf-8")

        response = self.client.post("/knowledge/publish-approved")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["merged_count"], 0)
        self.assertEqual(response.json()["status"], "skipped")
        self.assertEqual(before, self.legacy_seed_path.read_text(encoding="utf-8"))

    def test_rollback_latest_publish_restores_database_status(self) -> None:
        item = self.create_item("rollback me")
        self.client.post(f"/knowledge/items/{item['id']}/review", json={"status": "approved"})
        self.client.post("/knowledge/publish-approved")

        response = self.client.post(
            "/knowledge/rollback-latest",
            headers=auth_headers(roles=["admin"], user_id="admin_1"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"], "rollback")
        self.assertEqual(
            self.legacy_seed_path.read_text(encoding="utf-8"),
            '{"id":"seed","question":"old","answer":"old"}\n',
        )
        self.assertEqual(self.client.get("/knowledge/items?status=rollback").json()["total"], 1)

    def test_publish_history_returns_publish_and_rollback(self) -> None:
        item = self.create_item("history me")
        self.client.post(f"/knowledge/items/{item['id']}/review", json={"status": "approved"})

        self.client.post("/knowledge/publish-approved")
        self.client.post(
            "/knowledge/rollback-latest",
            headers=auth_headers(roles=["admin"], user_id="admin_1"),
        )

        response = self.client.get("/knowledge/publish-history")
        self.assertEqual(response.status_code, 200)
        actions = [item["action"] for item in response.json()["items"]]
        self.assertEqual(actions[:2], ["rollback", "publish"])


if __name__ == "__main__":
    unittest.main()
