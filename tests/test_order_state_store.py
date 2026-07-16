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

        result = query_order_status("u1", "wm1")

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["output"]["order_id"], "wm1")
        self.assertEqual(result["output"]["status_label"], "骑手已取餐")

    def test_order_tool_rejects_order_owned_by_another_user(self) -> None:
        from services.order_state_store import upsert_order_state
        from services.order_tool_service import query_order_status, query_refund_status

        upsert_order_state(
            {
                "user_id": "owner_user",
                "order_id": "wm-private",
                "status": "delivered",
                "status_label": "已送达",
                "delivery_status": "已送达",
                "summary": "订单已送达。",
                "refund_status": "none",
            }
        )

        order_result = query_order_status("other_user", "wm-private")
        refund_result = query_refund_status("other_user", "wm-private")

        self.assertEqual(order_result["status"], "failed")
        self.assertEqual(order_result["error_type"], "order_user_mismatch")
        self.assertEqual(refund_result["status"], "failed")
        self.assertEqual(refund_result["error_type"], "order_user_mismatch")

    def test_order_tool_rejects_owner_bound_mock_order_for_wrong_user(self) -> None:
        from services.order_tool_service import query_order_status, query_refund_status

        order_result = query_order_status("other_user", "__release_check_owner_order__")
        refund_result = query_refund_status("other_user", "__release_check_owner_order__")

        self.assertEqual(order_result["status"], "failed")
        self.assertEqual(order_result["error_type"], "order_user_mismatch")
        self.assertEqual(order_result["output"], {})
        self.assertEqual(refund_result["status"], "failed")
        self.assertEqual(refund_result["error_type"], "order_user_mismatch")
        self.assertEqual(refund_result["output"], {})

    def test_order_and_refund_tools_skip_when_order_id_is_missing(self) -> None:
        from services.order_tool_service import query_order_status, query_refund_status

        order_result = query_order_status("u1", None)
        refund_result = query_refund_status("u1", None)

        self.assertEqual(order_result["status"], "skipped")
        self.assertEqual(order_result["error_type"], "missing_order_id")
        self.assertFalse(order_result["retryable"])
        self.assertEqual(order_result["output"], {})
        self.assertEqual(refund_result["status"], "skipped")
        self.assertEqual(refund_result["error_type"], "missing_order_id")
        self.assertFalse(refund_result["retryable"])
        self.assertEqual(refund_result["output"], {})

    def test_tool_adapter_exception_returns_retryable_empty_failure(self) -> None:
        from services.order_tool_service import QUERY_ERROR_TYPE, query_order_status, query_refund_status

        with patch("services.order_tool_service._lookup_order", side_effect=RuntimeError("adapter down")):
            order_result = query_order_status("u1", "wm-error")
            refund_result = query_refund_status("u1", "wm-error")

        self.assertEqual(order_result["status"], "failed")
        self.assertEqual(order_result["error_type"], QUERY_ERROR_TYPE)
        self.assertTrue(order_result["retryable"])
        self.assertEqual(order_result["output"], {})
        self.assertEqual(refund_result["status"], "failed")
        self.assertEqual(refund_result["error_type"], QUERY_ERROR_TYPE)
        self.assertTrue(refund_result["retryable"])
        self.assertEqual(refund_result["output"], {})

    def test_tool_adapter_timeout_returns_retryable_empty_failure(self) -> None:
        from services.order_tool_service import TIMEOUT_ERROR_TYPE, query_order_status, query_refund_status

        order = {"user_id": "u1", "order_id": "wm-timeout", "status": "delivered"}
        with (
            patch("services.order_tool_service._lookup_order", return_value=order),
            patch("services.order_tool_service.TOOL_TIMEOUT_SECONDS", -1.0),
        ):
            order_result = query_order_status("u1", "wm-timeout")
            refund_result = query_refund_status("u1", "wm-timeout")

        self.assertEqual(order_result["status"], "failed")
        self.assertEqual(order_result["error_type"], TIMEOUT_ERROR_TYPE)
        self.assertTrue(order_result["retryable"])
        self.assertEqual(order_result["output"], {})
        self.assertEqual(refund_result["status"], "failed")
        self.assertEqual(refund_result["error_type"], TIMEOUT_ERROR_TYPE)
        self.assertTrue(refund_result["retryable"])
        self.assertEqual(refund_result["output"], {})


if __name__ == "__main__":
    unittest.main()
