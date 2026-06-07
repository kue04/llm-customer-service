import importlib
import sys
import types
import unittest


class ChatServiceDegradeTest(unittest.TestCase):
    def _load_chat_service_with_stubs(
        self,
        retrieve_impl,
        generate_impl,
        reply_rules_impl,
    ):
        fake_transformers = types.ModuleType("transformers")

        class FakeTokenizer:
            eos_token_id = 0

            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                return cls()

            def apply_chat_template(self, messages, tokenize=False, add_generation_prompt=True):
                return "prompt"

            def __call__(self, text, return_tensors="pt"):
                return {"input_ids": [[1, 2, 3]]}

            def decode(self, tokens, skip_special_tokens=True):
                return "decoded"

        class FakeModel:
            @classmethod
            def from_pretrained(cls, *args, **kwargs):
                return cls()

            def eval(self):
                return None

            def to(self, device):
                return self

            def generate(self, **kwargs):
                return [[1, 2, 3, 4]]

        fake_transformers.AutoTokenizer = FakeTokenizer
        fake_transformers.AutoModelForCausalLM = FakeModel

        fake_peft = types.ModuleType("peft")
        fake_peft.PeftModel = types.SimpleNamespace(from_pretrained=lambda model, *args, **kwargs: model)

        fake_reply_rules = types.ModuleType("services.reply_rules")
        fake_reply_rules.apply_reply_rules = reply_rules_impl
        fake_reply_rules.apply_reply_rules_with_trace = (
            lambda query, reply, items: (
                reply_rules_impl(query, reply, items),
                {"matched": False},
            )
        )

        fake_vector_retriever = types.ModuleType("utils.vector_retriever")
        fake_vector_retriever.retrieve_rag_items = retrieve_impl
        fake_vector_retriever.detect_intent_hint = lambda query: ""

        previous_modules = {
            name: sys.modules.get(name)
            for name in [
                "transformers",
                "peft",
                "services.reply_rules",
                "utils.vector_retriever",
                "services.chat_service",
            ]
        }
        sys.modules["transformers"] = fake_transformers
        sys.modules["peft"] = fake_peft
        sys.modules["services.reply_rules"] = fake_reply_rules
        sys.modules["utils.vector_retriever"] = fake_vector_retriever
        sys.modules.pop("services.chat_service", None)

        try:
            chat_service = importlib.import_module("services.chat_service")
        finally:
            sys.modules.pop("services.chat_service", None)
            for name, previous in previous_modules.items():
                if name == "services.chat_service":
                    continue
                if previous is not None:
                    sys.modules[name] = previous
                else:
                    sys.modules.pop(name, None)

        return chat_service

    def test_retrieval_failure_degrades_to_fallback_prompt(self) -> None:
        chat_service = self._load_chat_service_with_stubs(
            retrieve_impl=lambda query: (_ for _ in ()).throw(RuntimeError("retrieval boom")),
            generate_impl=None,
            reply_rules_impl=lambda query, reply, items: reply,
        )
        chat_service.generate_reply = lambda prompt: "fallback answer"

        result = chat_service.get_answer_from_rag("refund")

        self.assertEqual(result["reply"], "fallback answer")
        self.assertEqual(result["trace"]["answer_source"], "fallback")
        self.assertTrue(result["trace"]["degraded"])
        self.assertEqual(result["trace"]["failure_stage"], "retrieval")
        self.assertIn("request_id", result["trace"])
        self.assertIn("latency_ms", result["trace"])
        self.assertEqual(result["trace"]["top1_intent"], "")
        self.assertEqual(result["prompt_context_items"], [])

    def test_generation_failure_returns_safe_fallback_reply(self) -> None:
        chat_service = self._load_chat_service_with_stubs(
            retrieve_impl=lambda query: [
                {
                    "rank": 1,
                    "answer": "doc",
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "when refund arrives",
                    "score": 0.8,
                    "rerank_score": 0.82,
                }
            ],
            generate_impl=None,
            reply_rules_impl=lambda query, reply, items: reply,
        )

        def raise_generation(prompt: str):
            raise RuntimeError("generation boom")

        chat_service.generate_reply = raise_generation

        result = chat_service.get_answer_from_rag("refund")

        self.assertEqual(result["trace"]["failure_stage"], "generation")
        self.assertEqual(result["trace"]["answer_source"], "fallback")
        self.assertTrue(result["trace"]["degraded"])
        self.assertLess(result["confidence_score"], 0.5)
        self.assertEqual(result["retrieved_documents"], ["doc"])
        self.assertIn("1. 最相关参考资料", result["final_prompt"])
        self.assertEqual(result["prompt_context_items"][0]["intent"], "refund_progress")

    def test_reply_rules_failure_keeps_original_reply(self) -> None:
        chat_service = self._load_chat_service_with_stubs(
            retrieve_impl=lambda query: [
                {
                    "rank": 1,
                    "answer": "doc",
                    "category": "refund",
                    "intent": "refund_progress",
                    "question": "when refund arrives",
                    "score": 0.8,
                    "rerank_score": 0.82,
                }
            ],
            generate_impl=None,
            reply_rules_impl=lambda query, reply, items: (_ for _ in ()).throw(RuntimeError("rules boom")),
        )
        chat_service.generate_reply = lambda prompt: "model answer"

        result = chat_service.get_answer_from_rag("refund")

        self.assertEqual(result["reply"], "doc。")
        self.assertEqual(result["trace"]["failure_stage"], "reply_rules")
        self.assertTrue(result["trace"]["degraded"])
        self.assertFalse(result["trace"]["reply_rules_applied"])
        self.assertEqual(result["prompt_context_items"][0]["source_question"], "when refund arrives")


if __name__ == "__main__":
    unittest.main()
