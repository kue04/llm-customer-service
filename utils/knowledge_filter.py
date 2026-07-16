from datetime import datetime, timezone
from typing import Iterable


INACTIVE_STATUSES = {
    "draft",
    "pending_review",
    "rejected",
    "archived",
    "rollback",
}


def parse_optional_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def version_number(item: dict) -> int:
    raw = str(item.get("version") or "0").lower().lstrip("v")
    return int(raw) if raw.isdigit() else 0


def is_active_knowledge_item(item: dict, now: datetime | None = None) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    status = str(item.get("status") or "").strip().lower()
    if status in INACTIVE_STATUSES:
        return False

    effective_at = parse_optional_datetime(item.get("effective_at"))
    if effective_at is not None and effective_at > current:
        return False

    expired_at = parse_optional_datetime(item.get("expired_at"))
    if expired_at is not None and expired_at <= current:
        return False

    return True


def select_latest_active_items(
    items: Iterable[dict],
    now: datetime | None = None,
) -> list[dict]:
    latest_by_key: dict[str, dict] = {}
    for item in items:
        if not is_active_knowledge_item(item, now=now):
            continue
        key = str(item.get("base_id") or item.get("id") or "").strip()
        if not key:
            continue
        existing = latest_by_key.get(key)
        if existing is None or version_number(item) > version_number(existing):
            latest_by_key[key] = item
    return list(latest_by_key.values())
