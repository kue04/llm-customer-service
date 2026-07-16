import importlib
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


class PromptOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.prompt_service = importlib.import_module("services.prompt_service")
        self.feedback_service = importlib.import_module("services.feedback_service")
        self.previous_prompt_db_path = self.prompt_service.DB_PATH
        self.previous_feedback_db_path = self.feedback_service.DB_PATH
        self.prompt_service.DB_PATH = Path(self.temp_dir.name) / "prompt_versions.db"
        self.feedback_service.DB_PATH = Path(self.temp_dir.name) / "ops_feedback.db"
        self.addCleanup(self.restore_paths)

        prompt_router = importlib.import_module("routers.prompt")
        app = FastAPI()
        app.include_router(prompt_router.router, prefix="/prompt")
        self.app = app
        self.client = TestClient(app)
        self.client.headers.update({"X-User-Role": "admin", "X-Operator-Id": "admin_1"})

    def restore_paths(self) -> None:
        self.prompt_service.DB_PATH = self.previous_prompt_db_path
        self.feedback_service.DB_PATH = self.previous_feedback_db_path

    def test_prompt_version_lifecycle_and_rollback(self) -> None:
        active_response = self.client.get("/prompt/active")
        self.assertEqual(active_response.status_code, 200)
        self.assertEqual(active_response.json()["version"], "prompt_v1")

        create_response = self.client.post(
            "/prompt/versions",
            json={
                "version": "prompt_v2",
                "system_prompt": "新的客服系统提示词",
                "developer_prompt": "证据不足时保守处理",
                "change_reason": "补充高风险约束",
                "evaluation_result": "pass",
            },
        )
        self.assertEqual(create_response.status_code, 200)
        prompt_id = create_response.json()["id"]
        self.assertEqual(create_response.json()["status"], "draft")

        approve_response = self.client.post(
            f"/prompt/versions/{prompt_id}/status",
            json={"status": "approved", "evaluation_result": "pass"},
        )
        self.assertEqual(approve_response.status_code, 200)
        self.assertEqual(approve_response.json()["status"], "approved")

        activate_response = self.client.post(f"/prompt/versions/{prompt_id}/activate")
        self.assertEqual(activate_response.status_code, 200)
        self.assertEqual(activate_response.json()["status"], "production")
        self.assertEqual(self.client.get("/prompt/active").json()["version"], "prompt_v2")

        rollback_response = self.client.post("/prompt/rollback-latest")
        self.assertEqual(rollback_response.status_code, 200)
        self.assertEqual(rollback_response.json()["version"], "prompt_v1")
        self.assertEqual(self.client.get("/prompt/active").json()["version"], "prompt_v1")

    def test_prompt_routes_require_identity_and_admin_for_writes(self) -> None:
        bare_client = TestClient(self.app)
        self.assertEqual(bare_client.get("/prompt/active").status_code, 401)

        qa_response = self.client.post(
            "/prompt/versions",
            headers={"X-User-Role": "qa", "X-Operator-Id": "qa_1"},
            json={"system_prompt": "qa cannot write"},
        )
        self.assertEqual(qa_response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
