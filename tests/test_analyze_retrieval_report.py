import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.analyze_retrieval_report import (
    analyze_report,
    classify_bad_case,
    classify_transition,
    compare_reports,
    extract_score_breakdown_lines,
    is_bad_case,
)


class AnalyzeRetrievalReportTest(unittest.TestCase):
    def test_identifies_non_top1_as_bad_case(self) -> None:
        self.assertFalse(is_bad_case({"judgement": "Top1 hit"}))
        self.assertTrue(is_bad_case({"judgement": "Top3 recall but Top1 wrong"}))
        self.assertTrue(is_bad_case({"judgement": "miss"}))

    def test_classifies_bad_case_type(self) -> None:
        self.assertEqual(classify_bad_case({"judgement": "Top1 hit"}), "top1")
        self.assertEqual(
            classify_bad_case({"judgement": "Top3 recall but Top1 wrong"}),
            "ranking_error",
        )
        self.assertEqual(classify_bad_case({"judgement": "miss"}), "miss")

    def test_classifies_transition_direction(self) -> None:
        self.assertEqual(
            classify_transition(
                {"judgement": "miss"},
                {"judgement": "Top3 recall but Top1 wrong"},
            ),
            "improved",
        )
        self.assertEqual(
            classify_transition(
                {"judgement": "Top1 hit"},
                {"judgement": "miss"},
            ),
            "worsened",
        )
        self.assertEqual(
            classify_transition(
                {"judgement": "Top1 hit"},
                {"judgement": "Top1 hit"},
            ),
            "unchanged",
        )

    def test_extracts_score_breakdown_lines(self) -> None:
        lines = extract_score_breakdown_lines(
            {
                "results": [
                    {
                        "rank": 1,
                        "score": 0.8,
                        "rerank_score": 0.82,
                        "vector_score": 0.75,
                        "model_rerank_score": 0.2,
                        "keyword_bonus": 0.05,
                        "direction_penalty": 0.01,
                        "category": "refund",
                        "intent": "refund_progress",
                        "question": "refund q",
                    }
                ]
            }
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("#1 refund/refund_progress", lines[0])
        self.assertIn("score=0.8000", lines[0])
        self.assertIn("rerank=0.8200", lines[0])

    def test_analyze_report_shows_only_bad_cases_by_default(self) -> None:
        payload = {
            "run_id": "demo-run",
            "rerank_weight": 0.01,
            "rag_config": {"embedding_model_name": "bge"},
            "summary": {"top1": 1, "top3_error": 1, "miss": 1},
            "cases": [
                {"query": "good", "judgement": "Top1 hit", "top_intents": [], "results": []},
                {"query": "bad", "judgement": "Top3 recall but Top1 wrong", "top_intents": [], "results": []},
                {"query": "missed", "judgement": "miss", "top_intents": [], "results": []},
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "report.json"
            report_path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            output = StringIO()
            with redirect_stdout(output):
                result = analyze_report(report_path)

        self.assertEqual(result["shown_cases"], 2)
        self.assertEqual(result["ranking_error_count"], 1)
        self.assertEqual(result["miss_count"], 1)
        self.assertIn("run_id: demo-run", output.getvalue())
        self.assertIn("summary: Top1=1, Top3=1, miss=1", output.getvalue())
        self.assertIn("ranking_error_count: 1", output.getvalue())
        self.assertIn("miss_count: 1", output.getvalue())
        self.assertIn("Case 1: bad", output.getvalue())
        self.assertIn("bad_case_type: ranking_error", output.getvalue())

    def test_compare_reports_outputs_transition_and_config_diff(self) -> None:
        base_payload = {
            "run_id": "base-run",
            "rerank_weight": 0.01,
            "rag_config": {
                "embedding_model_name": "bge",
                "model_rerank_weight": 0.01,
            },
            "summary": {"top1": 1, "top3_error": 0, "miss": 1},
            "cases": [
                {
                    "query": "refund",
                    "judgement": "miss",
                    "top_intents": [],
                    "results": [
                        {
                            "rank": 1,
                            "score": 0.5,
                            "rerank_score": 0.5,
                            "vector_score": 0.5,
                            "model_rerank_score": 0.0,
                            "keyword_bonus": 0.0,
                            "direction_penalty": 0.0,
                            "category": "refund",
                            "intent": "wrong_intent",
                            "question": "wrong q",
                        }
                    ],
                },
                {
                    "query": "timeout",
                    "judgement": "Top1 hit",
                    "top_intents": [],
                    "results": [],
                },
            ],
        }
        target_payload = {
            "run_id": "target-run",
            "rerank_weight": 0.03,
            "rag_config": {
                "embedding_model_name": "bge",
                "model_rerank_weight": 0.03,
            },
            "summary": {"top1": 1, "top3_error": 1, "miss": 0},
            "cases": [
                {
                    "query": "refund",
                    "judgement": "Top3 recall but Top1 wrong",
                    "top_intents": [],
                    "results": [
                        {
                            "rank": 1,
                            "score": 0.7,
                            "rerank_score": 0.72,
                            "vector_score": 0.7,
                            "model_rerank_score": 0.2,
                            "keyword_bonus": 0.0,
                            "direction_penalty": 0.0,
                            "category": "refund",
                            "intent": "refund_progress",
                            "question": "refund q",
                        }
                    ],
                },
                {
                    "query": "timeout",
                    "judgement": "miss",
                    "top_intents": [],
                    "results": [],
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "base.json"
            target_path = Path(temp_dir) / "target.json"
            base_path.write_text(json.dumps(base_payload, ensure_ascii=False), encoding="utf-8")
            target_path.write_text(json.dumps(target_payload, ensure_ascii=False), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                result = compare_reports(base_path, target_path)

        self.assertEqual(result["base_run_id"], "base-run")
        self.assertEqual(result["target_run_id"], "target-run")
        self.assertEqual(result["shared_cases"], 2)
        self.assertEqual(result["improved_count"], 1)
        self.assertEqual(result["worsened_count"], 1)
        self.assertEqual(result["unchanged_count"], 0)
        self.assertIn("model_rerank_weight", result["config_diff"])
        self.assertIn("CONFIG DIFF", output.getvalue())
        self.assertIn("transition: improved", output.getvalue())
        self.assertIn("transition: worsened", output.getvalue())


if __name__ == "__main__":
    unittest.main()
