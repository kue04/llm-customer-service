import unittest
from contextlib import redirect_stdout
from io import StringIO
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from scripts.evaluate_retrieval_metrics import (
    evaluate_case,
    first_relevant_rank,
    load_cases_from_jsonl,
    ndcg_at_k,
    parse_args,
    recall_at_k,
    reciprocal_rank,
    relevance_grades,
    save_report,
    summarize,
    summarize_by_case_type,
)


def build_candidate(intent: str) -> dict:
    return {"source": {"intent": intent}}


class MetricFunctionTest(unittest.TestCase):
    def test_relevance_grades_mark_expected_intent_as_relevant(self) -> None:
        candidates = [
            build_candidate("退款进度"),
            build_candidate("超时取消"),
            build_candidate("退款金额咨询"),
        ]

        grades = relevance_grades(candidates, ["退款进度", "退款金额咨询"])

        self.assertEqual(grades, [1, 0, 1])

    def test_first_relevant_rank_returns_zero_when_nothing_relevant(self) -> None:
        self.assertEqual(first_relevant_rank([0, 0, 0]), 0)
        self.assertEqual(first_relevant_rank([0, 1, 1]), 2)

    def test_recall_at_k_only_looks_at_first_k(self) -> None:
        grades = [0, 0, 1]

        self.assertEqual(recall_at_k(grades, 1), 0.0)
        self.assertEqual(recall_at_k(grades, 3), 1.0)
        self.assertEqual(recall_at_k(grades, 10), 1.0)

    def test_reciprocal_rank_is_zero_when_no_relevant_in_window(self) -> None:
        self.assertEqual(reciprocal_rank([0, 0, 1], 2), 0.0)
        self.assertEqual(reciprocal_rank([0, 0, 1], 3), 1 / 3)

    def test_ndcg_decays_with_rank_and_is_one_at_rank_one(self) -> None:
        self.assertEqual(ndcg_at_k([1, 0, 0], 10), 1.0)
        self.assertAlmostEqual(ndcg_at_k([0, 1, 0], 10), 1 / 1.5849625007, places=6)
        self.assertEqual(ndcg_at_k([0, 0, 0], 10), 0.0)


class EvaluateCaseTest(unittest.TestCase):
    def test_evaluate_case_records_top1_intent_and_rank(self) -> None:
        case = {
            "query": "取消订单后钱多久退回来",
            "expected_intents": ["退款进度", "退款金额咨询"],
            "error_type": "取消和退款跨域",
        }
        candidates = [
            build_candidate("超时取消"),
            build_candidate("退款进度"),
        ]

        with patch(
            "scripts.evaluate_retrieval_metrics.retrieve_by_real_vector",
            return_value=candidates,
        ):
            result = evaluate_case(case, limit=10, use_hybrid=True, rerank_weight=0.01)

        self.assertEqual(result["top1_intent"], "超时取消")
        self.assertEqual(result["first_relevant_rank"], 2)
        self.assertEqual(result["recall"]["@1"], 0.0)
        self.assertEqual(result["recall"]["@5"], 1.0)
        self.assertEqual(result["rr"], 0.5)

    def test_evaluate_case_marks_miss_when_no_relevant_candidate(self) -> None:
        case = {"query": "发票怎么开", "expected_intents": ["发票开具咨询"]}
        candidates = [build_candidate("优惠券不可用")]

        with patch(
            "scripts.evaluate_retrieval_metrics.retrieve_by_real_vector",
            return_value=candidates,
        ):
            result = evaluate_case(case, limit=10, use_hybrid=True, rerank_weight=0.01)

        self.assertEqual(result["first_relevant_rank"], 0)
        self.assertEqual(result["recall"]["@10"], 0.0)
        self.assertEqual(result["rr"], 0.0)
        self.assertEqual(result["ndcg"]["@10"], 0.0)

    def test_evaluate_case_forwards_mode_to_retriever(self) -> None:
        case = {"query": "外卖超时怎么办", "expected_intents": ["催单"]}

        with patch(
            "scripts.evaluate_retrieval_metrics.retrieve_by_real_vector",
            return_value=[],
        ) as retrieve_mock:
            evaluate_case(case, limit=5, use_hybrid=False, rerank_weight=0.03)

        self.assertFalse(retrieve_mock.call_args.kwargs["use_hybrid"])
        self.assertEqual(retrieve_mock.call_args.kwargs["limit"], 5)
        self.assertEqual(retrieve_mock.call_args.kwargs["rerank_weight"], 0.03)


class SummarizeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.results = [
            {
                "recall": {"@1": 1.0, "@5": 1.0, "@10": 1.0},
                "rr": 1.0,
                "ndcg": {"@1": 1.0, "@5": 1.0, "@10": 1.0},
            },
            {
                "recall": {"@1": 0.0, "@5": 1.0, "@10": 1.0},
                "rr": 0.25,
                "ndcg": {"@1": 0.0, "@5": 0.5, "@10": 0.5},
            },
        ]

    def test_summarize_averages_recall_mrr_ndcg(self) -> None:
        summary = summarize(self.results, limit=10)

        self.assertEqual(summary["total_queries"], 2)
        self.assertEqual(summary["recall"]["@1"], 0.5)
        self.assertEqual(summary["recall"]["@10"], 1.0)
        self.assertEqual(summary["mrr"], 0.625)
        self.assertEqual(summary["ndcg"]["@5"], 0.75)

    def test_summarize_skips_k_larger_than_limit(self) -> None:
        summary = summarize(self.results, limit=5)

        self.assertIn("@5", summary["recall"])
        self.assertNotIn("@10", summary["recall"])

    def test_summarize_returns_empty_for_no_results(self) -> None:
        self.assertEqual(summarize([], limit=10), {})


class LoadCasesFromJsonlTest(unittest.TestCase):
    def test_reads_query_and_expected_intent_from_grounding_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cases.jsonl"
            path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "id": "c1",
                                "query": "外卖超时了怎么办",
                                "expected_intent": "超时取消追问",
                                "case_type": "oral",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "id": "c2",
                                "query": "优惠券不能用",
                                "expected_intents": ["优惠券不可用", "优惠叠加咨询"],
                            },
                            ensure_ascii=False,
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            cases = load_cases_from_jsonl(path)

        self.assertEqual(len(cases), 2)
        self.assertEqual(cases[0]["expected_intents"], ["超时取消追问"])
        self.assertEqual(cases[0]["error_type"], "oral")
        self.assertEqual(cases[1]["expected_intents"], ["优惠券不可用", "优惠叠加咨询"])
        self.assertEqual(cases[1]["error_type"], "")

    def test_skips_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cases.jsonl"
            path.write_text(
                '\n{"query": "a", "expected_intent": "x"}\n\n',
                encoding="utf-8",
            )

            cases = load_cases_from_jsonl(path)

        self.assertEqual(len(cases), 1)

    def test_real_grounding_case_file_loads_ninety_cases(self) -> None:
        path = Path(__file__).resolve().parents[1] / "data" / "chat_grounding_cases.jsonl"

        cases = load_cases_from_jsonl(path)

        self.assertEqual(len(cases), 90)
        self.assertTrue(all(case["expected_intents"] for case in cases))


class SummarizeByCaseTypeTest(unittest.TestCase):
    def test_groups_metrics_by_case_type(self) -> None:
        results = [
            {"error_type": "oral", "first_relevant_rank": 1, "rr": 1.0},
            {"error_type": "oral", "first_relevant_rank": 0, "rr": 0.0},
            {"error_type": "multi_intent", "first_relevant_rank": 3, "rr": 1 / 3},
        ]

        breakdown = summarize_by_case_type(results)

        self.assertEqual(breakdown["oral"]["count"], 2)
        self.assertEqual(breakdown["oral"]["recall@1"], 0.5)
        self.assertEqual(breakdown["oral"]["mrr"], 0.5)
        self.assertEqual(breakdown["multi_intent"]["count"], 1)
        self.assertEqual(breakdown["multi_intent"]["recall@1"], 0.0)
        self.assertEqual(round(breakdown["multi_intent"]["mrr"], 4), 0.3333)

    def test_unlabelled_cases_are_grouped_together(self) -> None:
        breakdown = summarize_by_case_type([{"error_type": "", "first_relevant_rank": 1, "rr": 1.0}])

        self.assertIn("未标注", breakdown)


class ReportAndCliTest(unittest.TestCase):
    def test_save_report_writes_json_payload(self) -> None:
        results = [
            {
                "query": "外卖超时怎么办",
                "expected_intents": ["催单"],
                "error_type": "意图粒度相近",
                "returned": 10,
                "first_relevant_rank": 1,
                "top1_intent": "催单",
                "recall": {"@1": 1.0, "@5": 1.0, "@10": 1.0},
                "rr": 1.0,
                "ndcg": {"@1": 1.0, "@5": 1.0, "@10": 1.0},
                "top_intents": ["催单"],
            }
        ]
        summary = summarize(results, limit=10)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = save_report(results, summary, mode="hybrid", limit=10, output_dir=temp_dir)
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["script"], "scripts/evaluate_retrieval_metrics.py")
        self.assertEqual(payload["mode"], "hybrid")
        self.assertIn("rag_config", payload)
        self.assertEqual(payload["cases"][0]["query"], "外卖超时怎么办")

    def test_parse_args_defaults_to_hybrid_and_limit_ten(self) -> None:
        args = parse_args([])

        self.assertEqual(args.mode, "hybrid")
        self.assertEqual(args.limit, 10)
        self.assertFalse(args.save_report)

    def test_parse_args_accepts_vector_mode(self) -> None:
        args = parse_args(["--mode", "vector", "--limit", "5", "--save-report"])

        self.assertEqual(args.mode, "vector")
        self.assertEqual(args.limit, 5)
        self.assertTrue(args.save_report)

    def test_run_evaluation_prints_per_case_rank(self) -> None:
        from scripts.evaluate_retrieval_metrics import run_evaluation

        with patch(
            "scripts.evaluate_retrieval_metrics.EVAL_QUERIES",
            [{"query": "外卖超时怎么办", "expected_intents": ["催单"], "error_type": ""}],
        ):
            with patch(
                "scripts.evaluate_retrieval_metrics.retrieve_by_real_vector",
                return_value=[build_candidate("催单")],
            ):
                output = StringIO()
                with redirect_stdout(output):
                    _, summary = run_evaluation(limit=10, use_hybrid=True, rerank_weight=0.01)

        self.assertIn("外卖超时怎么办", output.getvalue())
        self.assertEqual(summary["recall"]["@1"], 1.0)


if __name__ == "__main__":
    unittest.main()
