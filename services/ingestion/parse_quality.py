"""Deterministic, versioned parse gate. No OCR, model calls or source text in results."""
from collections import Counter
import re
import unicodedata

from services.ingestion.parsers.base import ParsedDocument

POLICY_VERSION = "1"


def requires_review(status: str, metadata: dict | None) -> bool:
    quality = (metadata or {}).get("parse_quality")
    # Legacy versions without a quality result remain usable. Every newly
    # processed version must pass evaluate() before chunks can be persisted.
    return status == "requires_review" or (quality is not None and quality.get("status") != "passed")


def evaluate(document: ParsedDocument) -> dict:
    reasons = []
    text = document.text
    chars = [c for c in text if not c.isspace()]
    bad = sum(c == "\ufffd" or unicodedata.category(c) in {"Cc", "Cs", "Co"} for c in chars)
    # Common UTF-8-as-Latin-1 artifacts; do not classify ordinary CJK as garbled.
    mojibake = len(re.findall(r"(?:Ã.|Â[\x80-\xbf]|â[€™œž])", text))
    bad_ratio = (bad + 2 * mojibake) / max(len(chars), 1)
    if not chars:
        reasons.append("empty_text")
    elif not any(c.isalnum() for c in chars):
        reasons.append("no_meaningful_text")
    if bad_ratio >= 0.02:
        reasons.append("garbled_text")
    paragraphs = [re.sub(r"\s+", " ", p).strip() for b in document.blocks
                  if b.type not in {"heading", "page_break", "image"}
                  for p in b.text.splitlines() if p.strip()]
    counts = Counter(p for p in paragraphs if len(p) >= 20)
    repeated = sum((n - 1) * len(p) for p, n in counts.items())
    duplicate_ratio = repeated / max(sum(len(p) for p in paragraphs), 1)
    if duplicate_ratio >= 0.5:
        reasons.append("repeated_content")
    structure = []
    previous_page = 0
    previous_depth = 0
    for i, block in enumerate(document.blocks):
        if block.order != i:
            structure.append("block_order")
        if block.page is not None:
            if block.page < 1 or block.page < previous_page or block.page > previous_page + 1:
                structure.append("page_sequence")
            previous_page = block.page
        if block.type == "heading":
            depth = len(block.heading_path)
            if not block.text.strip() or depth > previous_depth + 1:
                structure.append("heading_structure")
            previous_depth = depth
        if block.type == "table":
            table = block.table_json or {}
            columns, rows = table.get("columns", []), table.get("rows", [])
            if not columns or any(len(row) != len(columns) for row in rows):
                structure.append("table_shape")
    if chars and not any(b.text.strip() for b in document.blocks if b.type not in {"heading", "image", "page_break"}):
        structure.append("headings_without_body")
    if structure:
        reasons.append("structure_anomaly")
    return {"policy_version": POLICY_VERSION, "status": "requires_review" if reasons else "passed",
            "reasons": reasons, "metrics": {"text_length": len(chars), "garbled_ratio": round(bad_ratio, 4),
            "duplicate_ratio": round(duplicate_ratio, 4), "structure_issues": sorted(set(structure))}}
