import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from scripts.analyze_grounding_report import (
    analyze_report,
    build_failure_attribution_table,
    build_generation_sub_attribution_table,
    compare_reports,
    classify_failure_attribution,
    classify_generation_sub_attribution,
    classify_grounding_issue,
    extract_judge_reason,
    is_bad_case,
    summarize_failure_attributions,
    summarize_generation_sub_attributions,
    summarize_issue_types,
)


class AnalyzeGroundingReportTest(unittest.TestCase):
    def test_extracts_judge_reason(self) -> None:
        report = {
            "manual_judgment": {
                "notes": "风险提示： 判断理由：回答没有直接解决问题"
            }
        }
        self.assertEqual(extract_judge_reason(report), "回答没有直接解决问题")

    def test_identifies_bad_case(self) -> None:
        self.assertFalse(
            is_bad_case(
                {
                    "manual_judgment": {
                        "direct_answer": "yes",
                        "grounded": "yes",
                        "useful": "yes",
                    }
                }
            )
        )
        self.assertTrue(
            is_bad_case(
                {
                    "manual_judgment": {
                        "direct_answer": "no",
                        "grounded": "yes",
                        "useful": "yes",
                    }
                }
            )
        )

    def test_classifies_grounding_issue_types(self) -> None:
        self.assertEqual(
            classify_grounding_issue(
                {
                    "retrieved_items": [],
                    "manual_judgment": {},
                    "risky_promises": [],
                }
            ),
            "retrieval_bad",
        )
        self.assertEqual(
            classify_grounding_issue(
                {
                    "retrieved_items": [{"intent": "refund"}],
                    "manual_judgment": {"direct_answer": "yes", "grounded": "yes"},
                    "risky_promises": ["保证"],
                }
            ),
            "safety_overclaim",
        )
        self.assertEqual(
            classify_grounding_issue(
                {
                    "retrieved_items": [{"intent": "refund"}],
                    "manual_judgment": {"direct_answer": "no", "grounded": "yes"},
                    "risky_promises": [],
                }
            ),
            "generation_not_direct",
        )
        self.assertEqual(
            classify_grounding_issue(
                {
                    "retrieved_items": [{"intent": "refund"}],
                    "manual_judgment": {"direct_answer": "yes", "grounded": "partial"},
                    "risky_promises": [],
                }
            ),
            "generation_not_grounded",
        )

    def test_summarizes_issue_types(self) -> None:
        counts = summarize_issue_types(
            [
                {
                    "retrieved_items": [],
                    "manual_judgment": {"direct_answer": "no", "grounded": "no", "useful": "no"},
                    "risky_promises": [],
                },
                {
                    "retrieved_items": [{"intent": "refund"}],
                    "manual_judgment": {"direct_answer": "no", "grounded": "yes", "useful": "yes"},
                    "risky_promises": [],
                },
            ]
        )
        self.assertEqual(counts["retrieval_bad"], 1)
        self.assertEqual(counts["generation_not_direct"], 1)

    def test_classifies_failure_attribution_layers(self) -> None:
        self.assertEqual(
            classify_failure_attribution(
                {
                    "retrieved_items": [{"intent": "refund"}],
                    "expected_intent": "cancel",
                    "manual_judgment": {"direct_answer": "no", "grounded": "no", "useful": "no"},
                }
            ),
            "retrieval_failure",
        )
        self.assertEqual(
            classify_failure_attribution(
                {
                    "retrieved_items": [{"intent": "refund"}],
                    "expected_intent": "refund",
                    "retrieved_documents": ["refund depends on payment channel"],
                    "missing_evidence_keywords": ["order page"],
                    "manual_judgment": {"direct_answer": "no", "grounded": "no", "useful": "no"},
                }
            ),
            "evidence_insufficient",
        )
        self.assertEqual(
            classify_failure_attribution(
                {
                    "retrieved_items": [{"intent": "refund"}],
                    "expected_intent": "refund",
                    "retrieved_documents": ["refund depends on payment channel and order page"],
                    "missing_evidence_keywords": ["order page"],
                    "manual_judgment": {"direct_answer": "no", "grounded": "no", "useful": "no"},
                }
            ),
            "generation_not_using_evidence",
        )

    def test_builds_failure_attribution_table(self) -> None:
        reports = [
            {
                "query": "q1",
                "retrieved_items": [],
                "manual_judgment": {"direct_answer": "no", "grounded": "no", "useful": "no"},
            },
            {
                "query": "q2",
                "retrieved_items": [{"intent": "refund"}],
                "expected_intent": "refund",
                "retrieved_documents": ["refund depends on payment channel"],
                "missing_evidence_keywords": ["order page"],
                "manual_judgment": {"direct_answer": "no", "grounded": "no", "useful": "no"},
            },
        ]

        counts = summarize_failure_attributions(reports)
        table = build_failure_attribution_table(reports)

        self.assertEqual(counts["retrieval_failure"], 1)
        self.assertEqual(counts["evidence_insufficient"], 1)
        self.assertEqual(table[0]["attribution"], "retrieval_failure")
        self.assertIn("别先改 prompt", table[1]["action"])

    def test_classifies_generation_sub_attribution(self) -> None:
        base_report = {
            "retrieved_items": [{"intent": "refund"}],
            "expected_intent": "refund",
            "retrieved_documents": ["refund depends on payment channel"],
            "missing_evidence_keywords": [],
        }

        self.assertEqual(
            classify_generation_sub_attribution(
                {
                    **base_report,
                    "manual_judgment": {
                        "direct_answer": "yes",
                        "grounded": "no",
                        "useful": "yes",
                    },
                }
            ),
            "judge_too_strict",
        )
        self.assertEqual(
            classify_generation_sub_attribution(
                {
                    **base_report,
                    "manual_judgment": {
                        "direct_answer": "no",
                        "grounded": "partial",
                        "useful": "no",
                    },
                }
            ),
            "reply_not_direct_enough",
        )
        self.assertEqual(
            classify_generation_sub_attribution(
                {
                    **base_report,
                    "manual_judgment": {
                        "direct_answer": "no",
                        "grounded": "no",
                        "useful": "no",
                        "notes": "未明确后续处理方式",
                    },
                }
            ),
            "reply_missing_required_step",
        )

    def test_summarizes_generation_sub_attributions(self) -> None:
        reports = [
            {
                "query": "q1",
                "retrieved_items": [{"intent": "refund"}],
                "expected_intent": "refund",
                "retrieved_documents": ["refund depends on payment channel"],
                "manual_judgment": {
                    "direct_answer": "yes",
                    "grounded": "no",
                    "useful": "yes",
                },
            }
        ]

        counts = summarize_generation_sub_attributions(reports)
        table = build_generation_sub_attribution_table(reports)

        self.assertEqual(counts["judge_too_strict"], 1)
        self.assertEqual(table[0]["attribution"], "judge_too_strict")

    def test_analyze_report_prints_issue_type_counts(self) -> None:
        payload = {
            "run_id": "demo-run",
            "summary": {"total": 2},
            "reports": [
                {
                    "query": "q1",
                    "retrieved_items": [],
                    "manual_judgment": {
                        "direct_answer": "no",
                        "grounded": "no",
                        "useful": "yes",
                        "notes": "风险提示： 判断理由：missing retrieval",
                    },
                    "risky_promises": [],
                    "reply": "r1",
                },
                {
                    "query": "q2",
                    "retrieved_items": [{"intent": "refund"}],
                    "manual_judgment": {
                        "direct_answer": "yes",
                        "grounded": "yes",
                        "useful": "yes",
                        "notes": "",
                    },
                    "risky_promises": [],
                    "reply": "r2",
                },
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

        self.assertEqual(result["shown_cases"], 1)
        self.assertEqual(result["issue_type_counts"]["retrieval_bad"], 1)
        self.assertEqual(result["failure_attribution_counts"]["retrieval_failure"], 1)
        self.assertIn("issue_type_counts:", output.getvalue())
        self.assertIn("failure_attribution_table:", output.getvalue())
        self.assertIn("generation_sub_attribution_table:", output.getvalue())
        self.assertIn("issue_type: retrieval_bad", output.getvalue())
        self.assertIn("trace: answer_source=", output.getvalue())
        self.assertIn("reply_rule: matched=", output.getvalue())

    def test_compare_reports_outputs_judgment_transitions(self) -> None:
        base_payload = {
            "run_id": "base-run",
            "reports": [
                {
                    "query": "q1",
                    "manual_judgment": {
                        "direct_answer": "no",
                        "grounded": "partial",
                        "useful": "no",
                    },
                    "reply": "old reply",
                },
                {
                    "query": "q2",
                    "manual_judgment": {
                        "direct_answer": "yes",
                        "grounded": "yes",
                        "useful": "yes",
                    },
                    "reply": "same reply",
                },
            ],
        }
        target_payload = {
            "run_id": "target-run",
            "reports": [
                {
                    "query": "q1",
                    "manual_judgment": {
                        "direct_answer": "yes",
                        "grounded": "yes",
                        "useful": "yes",
                    },
                    "reply": "new reply",
                },
                {
                    "query": "q2",
                    "manual_judgment": {
                        "direct_answer": "yes",
                        "grounded": "yes",
                        "useful": "yes",
                    },
                    "reply": "same reply",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            base_path = Path(temp_dir) / "base.json"
            target_path = Path(temp_dir) / "target.json"
            base_path.write_text(json.dumps(base_payload), encoding="utf-8")
            target_path.write_text(json.dumps(target_payload), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                result = compare_reports(base_path, target_path)

        self.assertEqual(result["shared_cases"], 2)
        self.assertEqual(result["shown_cases"], 1)
        self.assertEqual(result["improved_count"], 1)
        self.assertEqual(result["worsened_count"], 0)
        self.assertIn("judge_pass_rate: 0.5 -> 1.0", output.getvalue())
        self.assertIn("Changed Case 1: q1", output.getvalue())


if __name__ == "__main__":
    unittest.main()
