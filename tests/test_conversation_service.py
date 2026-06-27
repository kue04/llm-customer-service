import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ConversationServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_feedback.db"
        self.db_patch = patch("services.feedback_service.DB_PATH", self.db_path)
        self.store_db_patch = patch("services.conversation_store.DB_PATH", self.db_path)
        self.db_patch.start()
        self.store_db_patch.start()

    def tearDown(self) -> None:
        self.store_db_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_context_keeps_recent_messages_and_facts(self) -> None:
        from services.conversation_service import (
            get_or_create_context,
            save_message,
            update_facts,
        )
        from services.intent_service import analyze_intents

        context = get_or_create_context(user_id="u1", session_id="s1", order_id="o1")
        for index in range(7):
            save_message("s1", "user", f"第{index}轮问题")

        intent = analyze_intents("餐品酸了，我想退款")
        update_facts("s1", "餐品酸了，我想退款", intent)
        context = get_or_create_context(user_id="u1", session_id="s1", order_id="o1")

        self.assertEqual(context["user_id"], "u1")
        self.assertEqual(context["session_id"], "s1")
        self.assertEqual(context["order_id"], "o1")
        self.assertLessEqual(len(context["recent_messages"]), 5)
        self.assertEqual(context["facts"]["last_primary_intent"], "食品安全投诉")
        self.assertEqual(context["facts"]["refund_mentioned"], "true")


if __name__ == "__main__":
    unittest.main()
