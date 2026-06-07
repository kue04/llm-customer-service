import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


class KnowledgeOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.knowledge_service = importlib.import_module("services.knowledge_service")
        self.previous_db_path = self.knowledge_service.DB_PATH
        self.previous_knowledge_path = self.knowledge_service.KNOWLEDGE_DATA_PATH
        self.previous_backup_dir = self.knowledge_service.BACKUP_DIR
        self.knowledge_service.DB_PATH = Path(self.temp_dir.name) / "knowledge_ops.db"
        self.knowledge_service.KNOWLEDGE_DATA_PATH = Path(self.temp_dir.name) / "seed.jsonl"
        self.knowledge_service.BACKUP_DIR = Path(self.temp_dir.name) / "backups"
        self.knowledge_service.KNOWLEDGE_DATA_PATH.write_text(
            '{"id":"seed","question":"old","answer":"old"}\n',
            encoding="utf-8",
        )
        self.addCleanup(self.restore_paths)

        knowledge_router = importlib.import_module("routers.knowledge")
        app = FastAPI()
        app.include_router(knowledge_router.router, prefix="/knowledge")
        self.client = TestClient(app)

    def restore_paths(self) -> None:
        self.knowledge_service.DB_PATH = self.previous_db_path
        self.knowledge_service.KNOWLEDGE_DATA_PATH = self.previous_knowledge_path
        self.knowledge_service.BACKUP_DIR = self.previous_backup_dir

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
        self.assertIn('"question": "退款失败怎么办"', export_body["jsonl"])

    def test_publish_approved_writes_jsonl_and_marks_published(self) -> None:
        item = self.create_item("publish me")
        self.client.post(f"/knowledge/items/{item['id']}/review", json={"status": "approved"})

        with patch.object(self.knowledge_service, "rebuild_vector_store") as rebuild:
            response = self.client.post("/knowledge/publish-approved")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["merged_count"], 1)
        self.assertTrue(Path(body["backup_path"]).exists())
        self.assertIn(
            '"question": "publish me"',
            self.knowledge_service.KNOWLEDGE_DATA_PATH.read_text(encoding="utf-8"),
        )
        self.assertEqual(self.client.get("/knowledge/items?status=published").json()["total"], 1)
        rebuild.assert_called_once()

    def test_publish_without_approved_is_noop(self) -> None:
        before = self.knowledge_service.KNOWLEDGE_DATA_PATH.read_text(encoding="utf-8")

        with patch.object(self.knowledge_service, "rebuild_vector_store") as rebuild:
            response = self.client.post("/knowledge/publish-approved")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["merged_count"], 0)
        self.assertEqual(response.json()["status"], "skipped")
        self.assertEqual(before, self.knowledge_service.KNOWLEDGE_DATA_PATH.read_text(encoding="utf-8"))
        rebuild.assert_not_called()

    def test_rollback_latest_publish_restores_backup(self) -> None:
        item = self.create_item("rollback me")
        self.client.post(f"/knowledge/items/{item['id']}/review", json={"status": "approved"})
        with patch.object(self.knowledge_service, "rebuild_vector_store"):
            self.client.post("/knowledge/publish-approved")
        self.knowledge_service.KNOWLEDGE_DATA_PATH.write_text("broken\n", encoding="utf-8")

        with patch.object(self.knowledge_service, "rebuild_vector_store") as rebuild:
            response = self.client.post("/knowledge/rollback-latest")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"], "rollback")
        self.assertEqual(
            self.knowledge_service.KNOWLEDGE_DATA_PATH.read_text(encoding="utf-8"),
            '{"id":"seed","question":"old","answer":"old"}\n',
        )
        self.assertEqual(self.client.get("/knowledge/items?status=approved").json()["total"], 1)
        rebuild.assert_called_once()

    def test_publish_history_returns_publish_and_rollback(self) -> None:
        item = self.create_item("history me")
        self.client.post(f"/knowledge/items/{item['id']}/review", json={"status": "approved"})

        with patch.object(self.knowledge_service, "rebuild_vector_store"):
            self.client.post("/knowledge/publish-approved")
            self.client.post("/knowledge/rollback-latest")

        response = self.client.get("/knowledge/publish-history")
        self.assertEqual(response.status_code, 200)
        actions = [item["action"] for item in response.json()["items"]]
        self.assertEqual(actions[:2], ["rollback", "publish"])


if __name__ == "__main__":
    unittest.main()
