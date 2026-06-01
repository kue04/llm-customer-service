from __future__ import annotations

from dataclasses import asdict, dataclass

PRIMARY_GAP_THRESHOLD = 0.08


@dataclass(frozen=True)
class PromptContextItem:
    role: str
    evidence_strength: str
    display_title: str
    evidence_summary: str
    prompt_instruction: str
    source_question: str
    source_answer: str
    rank: int
    category: str
    intent: str
    question: str
    answer: str
    score: float
    rerank_score: float

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _shorten_text(text: str, max_length: int = 120) -> str:
    normalized_text = _normalize_text(text)
    if len(normalized_text) <= max_length:
        return normalized_text
    return f"{normalized_text[:max_length].rstrip()}..."


def _build_display_title(item: dict, role: str) -> str:
    intent = item.get("intent", "") or "unknown"
    if role == "primary":
        return f"主证据：优先按「{intent}」回答"
    return f"辅助证据：仅补充「{intent}」相关细节"


def _build_prompt_instruction(role: str, evidence_strength: str) -> str:
    if role == "primary" and evidence_strength == "close_match":
        return "优先使用这条证据回答；但分数差距较小，避免引用辅助证据中的不同业务结论。"
    if role == "primary":
        return "优先使用这条证据回答用户问题。"
    return "只能补充流程、凭证、入口等通用信息，不能覆盖主证据的业务意图。"


def build_prompt_context_items(
    retrieved_items: list[dict],
    max_items: int = 3,
) -> list[PromptContextItem]:
    deduped_items: list[dict] = []
    seen_answers: set[str] = set()

    for item in retrieved_items:
        normalized_answer = _normalize_text(item.get("answer", ""))
        if not normalized_answer or normalized_answer in seen_answers:
            continue

        seen_answers.add(normalized_answer)
        deduped_items.append(item)

        if len(deduped_items) >= max_items:
            break

    context_items: list[PromptContextItem] = []
    primary_is_close_match = False
    if len(deduped_items) >= 2:
        primary_gap = float(deduped_items[0].get("rerank_score", 0.0)) - float(
            deduped_items[1].get("rerank_score", 0.0)
        )
        primary_is_close_match = primary_gap < PRIMARY_GAP_THRESHOLD

    for index, item in enumerate(deduped_items):
        is_primary = index == 0
        role = "primary" if is_primary else "supporting"
        evidence_strength = "close_match" if is_primary and primary_is_close_match else "normal"
        display_title = _build_display_title(item, role)
        evidence_summary = _shorten_text(item.get("answer", ""))
        prompt_instruction = _build_prompt_instruction(role, evidence_strength)
        context_items.append(
            PromptContextItem(
                role=role,
                evidence_strength=evidence_strength,
                display_title=display_title,
                evidence_summary=evidence_summary,
                prompt_instruction=prompt_instruction,
                source_question=item.get("question", ""),
                source_answer=item.get("answer", ""),
                rank=int(item.get("rank", index + 1)),
                category=item.get("category", ""),
                intent=item.get("intent", ""),
                question=display_title,
                answer=evidence_summary,
                score=float(item.get("score", 0.0)),
                rerank_score=float(item.get("rerank_score", 0.0)),
            )
        )

    return context_items


def prompt_context_items_to_dicts(context_items: list[PromptContextItem]) -> list[dict]:
    return [item.to_dict() for item in context_items]
