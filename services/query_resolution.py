from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Protocol


_ORDER_PATTERNS = (
    re.compile(r"订单(?:号|编号)?\s*[:：#]?\s*([A-Za-z0-9][A-Za-z0-9_-]{3,})", re.IGNORECASE),
    re.compile(r"\b([A-Z]{2,}[-_]?[0-9]{4,})\b", re.IGNORECASE),
)

# These replacements are deliberately conservative: they normalize wording, but do
# not add business facts or invent entities.
_NORMALIZATION_REPLACEMENTS = {
    "退钱": "退款",
    "退回来的钱": "退款",
    "钱还没回来": "退款未到账",
    "还没到账": "未到账",
    "没到账": "未到账",
    "没收到": "未收到",
    "收不到": "未收到",
    "多长时间": "多久",
    "啥时候": "什么时候",
    "咋办": "怎么办",
    "怎么弄": "怎么办",
}

_COREFERENCE_HINTS = (
    "刚才那个",
    "上次那个",
    "这个订单",
    "那笔订单",
    "该订单",
    "它",
    "这个",
    "那单",
    "上一单",
)


_MULTI_HOP_MARKERS = ("以及", "同时", "另外", "并且", "然后", "之后", "以及")
_MULTI_HOP_SPLIT = re.compile(r"(?:，|,|；|;|并且|同时|另外|然后|之后|以及)")


@dataclass(slots=True)
class SubQueryPlan:
    """One independently retrievable hop in a multi-hop request."""

    sub_query_id: str
    query: str
    depends_on: list[str] = field(default_factory=list)
    required: bool = True
    status: str = "planned"
    evidence_ids: list[str] = field(default_factory=list)
    coverage: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sub_query_id": self.sub_query_id,
            "query": self.query,
            "depends_on": list(self.depends_on),
            "required": self.required,
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "coverage": self.coverage,
            "error": self.error,
        }

    @classmethod
    def from_value(cls, value: Mapping[str, Any]) -> "SubQueryPlan":
        return cls(
            sub_query_id=str(value.get("sub_query_id") or value.get("id") or ""),
            query=str(value.get("query") or ""),
            depends_on=[str(item) for item in (value.get("depends_on") or [])],
            required=bool(value.get("required", True)),
            status=str(value.get("status") or "planned"),
            evidence_ids=[str(item) for item in (value.get("evidence_ids") or [])],
            coverage=float(value.get("coverage") or 0.0),
            error=str(value.get("error") or ""),
        )


def _split_hop_text(query: str) -> list[str]:
    parts = [part.strip(" ：:！？!? \t") for part in re.split(
        r"(?:[，,；;。！？!?]|并且|同时|另外|然后|之后|以及|还要)", query
    ) if part.strip(" ：:！？!? \t")]
    if len(parts) < 2 or len(parts) > 4:
        return []
    question_markers = ("为什么", "怎么", "多久", "什么时候", "找谁", "怎么办", "是否", "能不能", "应该")
    if not any(marker in part for part in parts for marker in question_markers):
        return []
    return parts


def _decompose_sub_queries(query: str) -> list[dict[str, Any]]:
    parts = _split_hop_text(query)
    if not parts:
        return []
    plans: list[SubQueryPlan] = []
    for index, part in enumerate(parts, start=1):
        depends_on = []
        if index > 1 and (
            any(marker in part for marker in ("如果", "然后", "之后", "还没", "未到账"))
            or (index == 2 and any(marker in query for marker in ("以及", "并且")))
        ):
            depends_on = [f"q{index - 1}"]
        plans.append(SubQueryPlan(sub_query_id=f"q{index}", query=part, depends_on=depends_on))
    return [plan.to_dict() for plan in plans]


def apply_subquery_evidence(
    plan: "QueryResolutionPlan",
    evidence: Mapping[str, Mapping[str, Any]],
) -> "QueryResolutionPlan":
    """Attach per-hop evidence and compute a safe aggregate gate."""
    updated: list[dict[str, Any]] = []
    for raw in plan.sub_queries:
        sub = SubQueryPlan.from_value(raw)
        item = evidence.get(sub.sub_query_id) or {}
        ids = [str(value) for value in (item.get("evidence_ids") or []) if value]
        coverage = max(0.0, min(1.0, float(item.get("coverage") or 0.0)))
        status = str(item.get("status") or ("sufficient" if ids and coverage > 0 else "insufficient"))
        if status == "sufficient" and not ids:
            status = "insufficient"
        sub.evidence_ids = ids
        sub.coverage = coverage
        sub.status = status
        sub.error = str(item.get("error") or "")
        updated.append(sub.to_dict())
    required = [item for item in updated if item.get("required", True)]
    missing = [item["sub_query_id"] for item in required if item.get("status") != "sufficient"]
    plan.sub_queries = updated
    plan.evidence_gate = {
        "status": "partial" if missing and len(missing) < len(required) else ("insufficient" if missing else "sufficient"),
        "missing_required_sub_queries": missing,
        "answer_mode": "partial_or_clarify" if missing else "complete",
    }
    return plan

class QueryRewriteProvider(Protocol):
    def rewrite(self, query: str, intent_analysis: dict, context: dict | None = None) -> "QueryResolutionPlan":
        ...


@dataclass(slots=True)
class QueryResolutionPlan:
    original_query: str
    resolved_query: str
    retrieval_queries: list[str] = field(default_factory=list)
    rewrite_applied: bool = False
    rewrite_strategy: str = "identity"
    confidence: float = 1.0
    intent: str = ""
    secondary_intents: list[str] = field(default_factory=list)
    entities: dict[str, str] = field(default_factory=dict)
    conversation_dependencies: list[str] = field(default_factory=list)
    unresolved_slots: list[str] = field(default_factory=list)
    ambiguity_type: str = ""
    sub_queries: list[dict[str, Any]] = field(default_factory=list)
    fallback_reason: str = ""
    evidence_gate: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.original_query = str(self.original_query or "").strip()
        self.resolved_query = str(self.resolved_query or "").strip() or self.original_query
        self.retrieval_queries = _unique_nonempty(
            self.retrieval_queries or [self.resolved_query, self.original_query]
        )
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        self.entities = {str(k): str(v) for k, v in (self.entities or {}).items() if v not in (None, "")}
        self.secondary_intents = [str(item) for item in (self.secondary_intents or []) if item]
        self.conversation_dependencies = [str(item) for item in (self.conversation_dependencies or []) if item]
        self.unresolved_slots = [str(item) for item in (self.unresolved_slots or []) if item]

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "resolved_query": self.resolved_query,
            "retrieval_queries": list(self.retrieval_queries),
            "rewrite_applied": bool(self.rewrite_applied),
            "rewrite_strategy": self.rewrite_strategy,
            "confidence": self.confidence,
            "intent": self.intent,
            "secondary_intents": list(self.secondary_intents),
            "entities": dict(self.entities),
            "conversation_dependencies": list(self.conversation_dependencies),
            "unresolved_slots": list(self.unresolved_slots),
            "ambiguity_type": self.ambiguity_type,
            "sub_queries": list(self.sub_queries),
            "fallback_reason": self.fallback_reason,
            "evidence_gate": dict(self.evidence_gate),
        }

    @classmethod
    def fallback(cls, query: str, reason: str, *, intent_analysis: dict | None = None, context: dict | None = None) -> "QueryResolutionPlan":
        intent_analysis = intent_analysis or {}
        facts = (context or {}).get("facts") or {}
        return cls(
            original_query=query,
            resolved_query=query,
            retrieval_queries=[query],
            rewrite_applied=False,
            rewrite_strategy="fallback_original",
            confidence=0.0,
            intent=str(intent_analysis.get("primary_intent") or ""),
            secondary_intents=list(intent_analysis.get("secondary_intents") or []),
            entities=_context_entities(facts),
            fallback_reason=reason,
        )


class DeterministicQueryRewriteProvider:
    """Conservative query resolver for production's default path.

    It only normalizes wording and resolves entities already present in session
    state. It never fabricates an order id or infers an unsupported business fact.
    """

    def rewrite(self, query: str, intent_analysis: dict, context: dict | None = None) -> QueryResolutionPlan:
        original = str(query or "").strip()
        if not original:
            return QueryResolutionPlan.fallback(query, "empty_query", intent_analysis=intent_analysis, context=context)

        context = context or {}
        facts = context.get("facts") or {}
        normalized = normalize_query(original)
        explicit_entities = extract_entities(original)
        context_entities = _context_entities(facts)
        entities = {**context_entities, **explicit_entities}
        dependencies: list[str] = []
        strategy_parts: list[str] = []

        active_order_id = explicit_entities.get("order_id") or _active_order_id(context, facts)
        has_coreference = contains_coreference(original)
        if has_coreference:
            if active_order_id and not explicit_entities.get("order_id"):
                normalized = f"{normalized} 订单{active_order_id}"
                entities["order_id"] = active_order_id
                dependencies.extend(_context_turn_ids(context, facts))
                strategy_parts.append("entity_resolution")
            elif not active_order_id:
                return QueryResolutionPlan(
                    original_query=original,
                    resolved_query=normalized,
                    retrieval_queries=[normalized, original],
                    rewrite_applied=normalized != original,
                    rewrite_strategy="ambiguous_reference",
                    confidence=0.35,
                    intent=str(intent_analysis.get("primary_intent") or ""),
                    secondary_intents=list(intent_analysis.get("secondary_intents") or []),
                    entities=entities,
                    unresolved_slots=["order_id"],
                    ambiguity_type="ambiguous_reference",
                )

        if normalized != original:
            strategy_parts.append("normalization")

        intent = str(intent_analysis.get("primary_intent") or "")
        secondary = list(intent_analysis.get("secondary_intents") or [])
        resolved = normalized
        if intent and _should_add_intent_hint(normalized, intent):
            # Keep intent hint short and deterministic; this is metadata-like text,
            # never a claim about the user.
            resolved = f"{normalized} 意图：{intent}"
            strategy_parts.append("intent_hint")

        sub_queries = _decompose_sub_queries(resolved)
        changed = resolved != original
        strategy = "+".join(dict.fromkeys(strategy_parts)) if strategy_parts else "identity"
        confidence = 0.94 if changed else 1.0
        if has_coreference and active_order_id:
            confidence = 0.90
        return QueryResolutionPlan(
            original_query=original,
            resolved_query=resolved,
            retrieval_queries=_unique_nonempty([resolved, original]),
            rewrite_applied=changed,
            rewrite_strategy=strategy,
            confidence=confidence,
            intent=intent,
            secondary_intents=secondary,
            entities=entities,
            conversation_dependencies=dependencies,
            sub_queries=sub_queries,
        )


def resolve_query(
    query: str,
    intent_analysis: dict | None = None,
    context: dict | None = None,
    provider: QueryRewriteProvider | None = None,
) -> QueryResolutionPlan:
    """Resolve a query while guaranteeing an original-query fallback contract."""

    intent_analysis = intent_analysis or {}
    try:
        selected = provider or DeterministicQueryRewriteProvider()
        plan = selected.rewrite(query, intent_analysis, context)
        if not isinstance(plan, QueryResolutionPlan):
            raise TypeError("query rewrite provider must return QueryResolutionPlan")
        if not plan.original_query:
            return QueryResolutionPlan.fallback(query, "empty_original_query", intent_analysis=intent_analysis, context=context)
        if not plan.resolved_query:
            return QueryResolutionPlan.fallback(query, "empty_resolved_query", intent_analysis=intent_analysis, context=context)
        if plan.confidence < 0.70 and plan.rewrite_applied and plan.ambiguity_type == "":
            return QueryResolutionPlan.fallback(query, "low_confidence", intent_analysis=intent_analysis, context=context)
        return plan
    except Exception as error:  # provider failures must never break retrieval
        return QueryResolutionPlan.fallback(
            query,
            f"provider_error:{type(error).__name__}",
            intent_analysis=intent_analysis,
            context=context,
        )


def normalize_query(query: str) -> str:
    normalized = " ".join(str(query or "").replace("\u3000", " ").split())
    for source, target in _NORMALIZATION_REPLACEMENTS.items():
        normalized = normalized.replace(source, target)
    return normalized.strip(" \t\r\n，。！？；;,:：")


def extract_entities(query: str) -> dict[str, str]:
    text = str(query or "")
    for pattern in _ORDER_PATTERNS:
        match = pattern.search(text)
        if match:
            return {"order_id": match.group(1)}
    return {}


def contains_coreference(query: str) -> bool:
    text = str(query or "")
    return any(hint in text for hint in _COREFERENCE_HINTS)


def _context_entities(facts: dict) -> dict[str, str]:
    entities = facts.get("entities") if isinstance(facts.get("entities"), dict) else {}
    merged = {str(k): str(v) for k, v in entities.items() if v not in (None, "")}
    for key in ("active_order_id", "order_id"):
        if facts.get(key) and "order_id" not in merged:
            merged["order_id"] = str(facts[key])
    return merged


def _active_order_id(context: dict, facts: dict) -> str:
    if context.get("order_id"):
        return str(context["order_id"])
    return _context_entities(facts).get("order_id", "")


def _context_turn_ids(context: dict, facts: dict) -> list[str]:
    values = facts.get("turn_ids") or context.get("conversation_dependencies") or []
    if isinstance(values, str):
        values = [values]
    return [str(value) for value in values if value]


def _should_add_intent_hint(query: str, intent: str) -> bool:
    # Avoid repeating a hint that is already explicitly present in the query.
    return intent not in query and len(query) < 240


def _unique_nonempty(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in result:
            result.append(value)
    return result


DEFAULT_QUERY_REWRITE_PROVIDER = DeterministicQueryRewriteProvider()

__all__ = [
    "DEFAULT_QUERY_REWRITE_PROVIDER",
    "DeterministicQueryRewriteProvider",
    "QueryResolutionPlan",
    "QueryRewriteProvider",
    "contains_coreference",
    "extract_entities",
    "normalize_query",
    "resolve_query",
]
