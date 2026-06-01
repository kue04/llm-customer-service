import unittest

from services.online_generation import build_online_chat_completions_url


class OnlineGenerationConfigTest(unittest.TestCase):
    def test_build_online_chat_completions_url_appends_path(self) -> None:
        self.assertEqual(
            build_online_chat_completions_url("https://relayai.tech/v1"),
            "https://relayai.tech/v1/chat/completions",
        )

    def test_build_online_chat_completions_url_keeps_full_path(self) -> None:
        self.assertEqual(
            build_online_chat_completions_url("https://relayai.tech/v1/chat/completions"),
            "https://relayai.tech/v1/chat/completions",
        )

    def test_build_online_chat_completions_url_rejects_empty_url(self) -> None:
        with self.assertRaises(ValueError):
            build_online_chat_completions_url("")


if __name__ == "__main__":
    unittest.main()
