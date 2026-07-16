from datetime import datetime, timezone

import pytest

from utils.knowledge_filter import is_active_knowledge_item, select_latest_active_items


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def test_legacy_item_without_status_is_active():
    assert is_active_knowledge_item({"id": "legacy_1"}, now=NOW)


@pytest.mark.parametrize("status", ["published", "approved"])
def test_published_and_approved_items_are_active(status):
    assert is_active_knowledge_item({"id": status, "status": status}, now=NOW)


@pytest.mark.parametrize(
    "status",
    ["draft", "pending_review", "rejected", "archived", "rollback"],
)
def test_inactive_statuses_are_filtered(status):
    assert not is_active_knowledge_item({"id": status, "status": status}, now=NOW)


def test_future_item_is_filtered():
    assert not is_active_knowledge_item(
        {"effective_at": "2026-07-16T12:01:00Z"},
        now=NOW,
    )


def test_expired_item_is_filtered_at_boundary():
    assert not is_active_knowledge_item(
        {"expired_at": "2026-07-16T12:00:00+00:00"},
        now=NOW,
    )


def test_selects_latest_version_per_base_id_and_uses_id_as_fallback():
    selected = select_latest_active_items(
        [
            {"id": "kb_1_v1", "base_id": "kb_1", "version": "v1"},
            {"id": "kb_1_v2", "base_id": "kb_1", "version": 2},
            {"id": "legacy_2", "version": "v1"},
        ],
        now=NOW,
    )

    assert [item["id"] for item in selected] == ["kb_1_v2", "legacy_2"]
