import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class OrderStateStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_feedback.db"
        self.feedback_db_patch = patch("services.feedback_service.DB_PATH", self.db_path)
        self.order_db_patch = patch("services.order_state_store.DB_PATH", self.db_path)
        self.feedback_db_patch.start()
        self.order_db_patch.start()

    def tearDown(self) -> None:
        self.order_db_patch.stop()
        self.feedback_db_patch.stop()
        self.temp_dir.cleanup()

    def test_order_tool_reads_persisted_order_state(self) -> None:
        from services.order_state_store import upsert_order_state
        from services.order_tool_service import query_order_status

        upsert_order_state(
            {
                "user_id": "u1",
                "order_id": "wm1",
                "status": "delivering",
                "status_label": "骑手已取餐",
                "delivery_status": "骑手已取餐，正在配送中",
                "summary": "骑手已取餐，不应直接承诺全额退款。",
                "refund_status": "pending_review",
                "store_name": "青禾轻食",
                "items": [{"name": "鸡胸能量碗", "quantity": 1}],
                "total": 35,
            }
        )

        result = query_order_status("wm1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"]["order_id"], "wm1")
        self.assertEqual(result["output"]["status_label"], "骑手已取餐")


if __name__ == "__main__":
    unittest.main()
