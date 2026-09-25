from __future__ import annotations

import unittest

from services.query_resolution import (
    QueryResolutionPlan,
    apply_subquery_evidence,
    contains_coreference,
    extract_entities,
    normalize_query,
    resolve_query,
)
from services.query_rewrite_provider import rewrite_query


class QueryResolutionTest(unittest.TestCase):
    def test_normalization_is_conservative(self) -> None:
        self.assertEqual(normalize_query("  退款咋办？  "), "退款怎么办")
        self.assertEqual(normalize_query("我想退钱，钱还没回来"), "我想退款，退款未到账")

    def test_extracts_explicit_order_id(self) -> None:
        self.assertEqual(extract_entities("订单号: ORD-20260925"), {"order_id": "ORD-20260925"})

    def test_resolves_unique_active_order_for_coreference(self) -> None:
        plan = resolve_query(
            "刚才那个订单怎么还没到账",
            {"primary_intent": "退款进度"},
            {"order_id": "ORD-001", "facts": {"turn_ids": ["turn-3"]}},
        )
        self.assertIsInstance(plan, QueryResolutionPlan)
        self.assertIn("ORD-001", plan.resolved_query)
        self.assertEqual(plan.entities["order_id"], "ORD-001")
        self.assertIn("turn-3", plan.conversation_dependencies)
        self.assertEqual(plan.retrieval_queries[0], plan.resolved_query)
        self.assertIn(plan.original_query, plan.retrieval_queries)

    def test_coreference_without_active_order_is_ambiguous(self) -> None:
        plan = resolve_query("就是刚才那个", {"primary_intent": "退款进度"}, {"facts": {}})
        self.assertEqual(plan.ambiguity_type, "ambiguous_reference")
        self.assertEqual(plan.unresolved_slots, ["order_id"])
        self.assertIn(plan.original_query, plan.retrieval_queries)

    def test_explicit_entity_wins_over_context(self) -> None:
        plan = resolve_query(
            "订单 ORD-002 怎么办",
            {"primary_intent": "订单咨询"},
            {"order_id": "ORD-001", "facts": {}},
        )
        self.assertEqual(plan.entities["order_id"], "ORD-002")
        self.assertNotIn("ORD-001", plan.resolved_query)

    def test_provider_failure_falls_back_to_original(self) -> None:
        class BrokenProvider:
            def rewrite(self, query, intent_analysis, context=None):
                raise TimeoutError("timeout")

        plan = resolve_query("退款多久到账", provider=BrokenProvider())
        self.assertEqual(plan.resolved_query, "退款多久到账")
        self.assertEqual(plan.retrieval_queries, ["退款多久到账"])
        self.assertFalse(plan.rewrite_applied)
        self.assertEqual(plan.rewrite_strategy, "fallback_original")
        self.assertTrue(plan.fallback_reason.startswith("provider_error:"))

    def test_invalid_provider_result_falls_back(self) -> None:
        class InvalidProvider:
            def rewrite(self, query, intent_analysis, context=None):
                return {"resolved_query": "invented"}

        plan = resolve_query("原始问题", provider=InvalidProvider())
        self.assertEqual(plan.resolved_query, "原始问题")
        self.assertEqual(plan.rewrite_strategy, "fallback_original")

    def test_low_confidence_rewrite_falls_back(self) -> None:
        class LowConfidenceProvider:
            def rewrite(self, query, intent_analysis, context=None):
                return QueryResolutionPlan(
                    original_query=query,
                    resolved_query="猜测出的订单 ORDER-999",
                    retrieval_queries=["猜测出的订单 ORDER-999"],
                    rewrite_applied=True,
                    confidence=0.2,
                )

        plan = resolve_query("这个订单呢", provider=LowConfidenceProvider())
        self.assertEqual(plan.resolved_query, "这个订单呢")
        self.assertEqual(plan.rewrite_strategy, "fallback_original")
        self.assertEqual(plan.fallback_reason, "low_confidence")

    def test_serializable_provider_wrapper(self) -> None:
        result = rewrite_query(
            "退款咋办",
            intent_analysis={"primary_intent": "退款进度"},
        )
        self.assertEqual(result["original_query"], "退款咋办")
        self.assertIn("退款怎么办", result["resolved_query"])
        self.assertIn("retrieval_queries", result)

    def test_multi_hop_query_is_decomposed_with_dependencies(self) -> None:
        plan = resolve_query(
            "为什么取消以及退款多久到账，并且没到账找谁",
            {"primary_intent": "退款进度"},
        )
        self.assertGreaterEqual(len(plan.sub_queries), 2)
        self.assertEqual(plan.sub_queries[0]["sub_query_id"], "q1")
        self.assertEqual(plan.sub_queries[1]["depends_on"], ["q1"])

    def test_multi_hop_query_is_decomposed_with_independent_hops(self) -> None:
        plan = resolve_query(
            "订单为什么被取消？钱什么时候退？如果还没到账我应该找谁？",
            {"primary_intent": "订单咨询"},
        )
        self.assertEqual([item["sub_query_id"] for item in plan.sub_queries], ["q1", "q2", "q3"])
        self.assertEqual(plan.sub_queries[0]["depends_on"], [])
        self.assertEqual(plan.sub_queries[1]["depends_on"], [])
        self.assertEqual(plan.sub_queries[2]["depends_on"], ["q2"])
        self.assertTrue(all(item["status"] == "planned" for item in plan.sub_queries))

    def test_subquery_evidence_is_recorded_per_hop_and_missing_is_not_inherited(self) -> None:
        plan = resolve_query("为什么取消？钱什么时候退？", {"primary_intent": "订单咨询"})
        apply_subquery_evidence(
            plan,
            {
                "q1": {"evidence_ids": ["ev-1"], "coverage": 1.0},
                "q2": {"evidence_ids": [], "coverage": 0.0},
            },
        )
        self.assertEqual(plan.sub_queries[0]["status"], "sufficient")
        self.assertEqual(plan.sub_queries[0]["evidence_ids"], ["ev-1"])
        self.assertEqual(plan.sub_queries[1]["status"], "insufficient")
        self.assertEqual(plan.evidence_gate["status"], "partial")
        self.assertEqual(plan.evidence_gate["missing_required_sub_queries"], ["q2"])
        self.assertEqual(plan.evidence_gate["answer_mode"], "partial_or_clarify")

    def test_all_subqueries_must_have_evidence_for_complete_answer(self) -> None:
        plan = resolve_query("为什么取消？钱什么时候退？", {"primary_intent": "订单咨询"})
        apply_subquery_evidence(
            plan,
            {
                "q1": {"evidence_ids": ["ev-1"], "coverage": 0.8, "status": "sufficient"},
                "q2": {"evidence_ids": ["ev-2"], "coverage": 0.9, "status": "sufficient"},
            },
        )
        self.assertEqual(plan.evidence_gate["status"], "sufficient")
        self.assertEqual(plan.evidence_gate["answer_mode"], "complete")
    def test_coreference_detector(self) -> None:
        self.assertTrue(contains_coreference("这个订单怎么处理"))
        self.assertFalse(contains_coreference("订单 ORD-001 怎么处理"))


if __name__ == "__main__":
    unittest.main()
