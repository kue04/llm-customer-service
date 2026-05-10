import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from scripts.evaluate_vector_retrieval import (
    analyze_rerank_top1_change,
    parse_args,
    print_weight_comparison,
    print_query_result,
    summarize_rerank_top1_changes,
)


class AnalyzeRerankTop1ChangeTest(unittest.TestCase):
    def test_reports_improved_when_rerank_moves_expected_intent_to_top1(self) -> None:
        results = [
            {
                "score": 0.70,
                "rerank_score": 0.90,
                "source": {"intent": "refund_progress"},
            },
            {
                "score": 0.80,
                "rerank_score": 0.81,
                "source": {"intent": "timeout_cancel"},
            },
        ]

        analysis = analyze_rerank_top1_change(
            results,
            expected_intents=["refund_progress"],
        )

        self.assertTrue(analysis["top1_changed"])
        self.assertEqual(analysis["impact"], "improved")
        self.assertEqual(analysis["original_top_intent"], "timeout_cancel")
        self.assertEqual(analysis["reranked_top_intent"], "refund_progress")

    def test_reports_unchanged_when_top1_intent_stays_the_same(self) -> None:
        results = [
            {
                "score": 0.80,
                "rerank_score": 0.82,
                "source": {"intent": "refund_progress"},
            },
            {
                "score": 0.70,
                "rerank_score": 0.71,
                "source": {"intent": "timeout_cancel"},
            },
        ]

        analysis = analyze_rerank_top1_change(
            results,
            expected_intents=["refund_progress"],
        )

        self.assertFalse(analysis["top1_changed"])
        self.assertEqual(analysis["impact"], "unchanged")

    def test_summarizes_rerank_top1_impacts(self) -> None:
        summary = summarize_rerank_top1_changes(
            [
                {"rerank_impact": "improved"},
                {"rerank_impact": "worsened"},
                {"rerank_impact": "unchanged"},
                {"rerank_impact": "changed_neutral"},
            ]
        )

        self.assertEqual(summary["changed"], 3)
        self.assertEqual(summary["improved"], 1)
        self.assertEqual(summary["worsened"], 1)
        self.assertEqual(summary["unchanged"], 1)
        self.assertEqual(summary["changed_neutral"], 1)

    def test_passes_rerank_weight_to_retrieval(self) -> None:
        case = {
            "query": "refund",
            "expected_intents": ["refund_progress"],
        }

        with patch(
            "scripts.evaluate_vector_retrieval.retrieve_by_real_vector",
            return_value=[],
        ) as retrieve_mock:
            with redirect_stdout(StringIO()):
                print_query_result(case, rerank_weight=0.05)

        retrieve_mock.assert_called_once_with(
            "refund",
            limit=3,
            rerank_weight=0.05,
        )

    def test_parses_compare_rerank_weights(self) -> None:
        args = parse_args(["--compare-rerank-weights", "0.01", "0.03", "0.05"])

        self.assertEqual(args.compare_rerank_weights, [0.01, 0.03, 0.05])

    def test_prints_weight_comparison_table(self) -> None:
        def fake_evaluate_cases(rerank_weight: float, verbose: bool) -> list[dict]:
            self.assertFalse(verbose)
            if rerank_weight == 0.01:
                return [
                    {
                        "judgement": "Top1 命中",
                        "rerank_changed_count": 1,
                        "rerank_impact": "unchanged",
                    }
                ]

            return [
                {
                    "judgement": "Top3 召回但 Top1 错误",
                    "rerank_changed_count": 1,
                    "rerank_impact": "worsened",
                }
            ]

        with patch(
            "scripts.evaluate_vector_retrieval.evaluate_cases",
            side_effect=fake_evaluate_cases,
        ):
            output = StringIO()
            with redirect_stdout(output):
                print_weight_comparison([0.01, 0.03])

        text = output.getvalue()
        self.assertIn("Rerank weight comparison:", text)
        self.assertIn("0.01", text)
        self.assertIn("0.03", text)
        self.assertIn("Top1", text)
        self.assertIn("Worsened", text)


if __name__ == "__main__":
    unittest.main()
