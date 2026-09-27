import sqlite3

import pytest

from runtime_fixtures import runtime_db as _runtime_db
from services import conversation_store as store
from services import customer_memory_service as memory


runtime_db = _runtime_db


@pytest.fixture
def memory_db(runtime_db):
    return runtime_db


def test_same_user_memories_cannot_cross_tenants(memory_db):
    store.upsert_user_memory("shared-user", {"last_service_summary": "tenant A private"}, tenant_id="a")
    assert store.get_user_memory("shared-user", tenant_id="b") == {}
    store.upsert_user_memory("shared-user", {"last_service_summary": "tenant B private"}, tenant_id="b")
    assert store.get_user_memory("shared-user", tenant_id="a") == {"last_service_summary": "tenant A private"}
    assert store.get_user_memory("shared-user", tenant_id="b") == {"last_service_summary": "tenant B private"}
    store.upsert_user_memory("shared-user", {"last_service_summary": "tenant B updated"}, tenant_id="b")
    assert store.get_user_memory("shared-user", tenant_id="a") == {"last_service_summary": "tenant A private"}


def test_same_tenant_memories_cannot_cross_users(memory_db):
    store.upsert_user_memory("owner", {"communication_preference": "private"}, tenant_id="a")
    assert store.get_user_memory("other", tenant_id="a") == {}
    store.upsert_user_memory("other", {"communication_preference": "other preference"}, tenant_id="a")
    assert store.get_user_memory("owner", tenant_id="a") == {"communication_preference": "private"}


def test_legacy_memory_migration_preserves_and_quarantines_rows(memory_db, tmp_path):
    from services.runtime_maintenance import import_legacy_sqlite

    legacy = tmp_path / "legacy.db"
    with sqlite3.connect(legacy) as connection:
        connection.execute(
            "CREATE TABLE user_memory (user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, key))"
        )
        connection.execute(
            "INSERT INTO user_memory VALUES ('owner', 'last_service_summary', 'legacy private', '2026-09-26')"
        )
    original = legacy.read_bytes()
    with pytest.raises(ValueError):
        import_legacy_sqlite(legacy, [], apply=True)
    assert store.get_user_memory("owner", tenant_id="a") == {}
    assert store.get_user_memory("owner", tenant_id="b") == {}
    import_legacy_sqlite(
        legacy,
        [
            {
                "table": "user_memory",
                "key": {"user_id": "owner", "key": "last_service_summary"},
                "tenant_id": "a",
                "created_by": "owner",
            }
        ],
        apply=True,
    )
    assert store.get_user_memory("owner", tenant_id="a") == {"last_service_summary": "legacy private"}
    assert store.get_user_memory("owner", tenant_id="b") == {}
    assert legacy.read_bytes() == original


def test_service_wrappers_forward_tenant_for_reads_and_updates(memory_db):
    first = memory.update_user_memory_from_turn(
        "shared-user",
        "退款何时到账",
        "tenant A reply",
        {"primary_intent": "退款进度", "risk_level": "low"},
        tenant_id="a",
    )
    assert first["common_issue_types"] == "退款/售后"
    assert first["last_reply_summary"] == "tenant A reply"
    assert memory.get_user_memory("shared-user", tenant_id="b") == {}
    second = memory.update_user_memory_from_turn(
        "shared-user",
        "查询地址",
        "tenant B reply",
        {"primary_intent": "地址咨询", "risk_level": "low"},
        tenant_id="b",
    )
    assert "common_issue_types" not in second
    assert "address_preference" in second
    first_memory = memory.get_user_memory("shared-user", tenant_id="a")
    assert "address_preference" not in first_memory
    assert first_memory["last_service_summary"] == "最近咨询：退款进度；风险等级：low"


def test_service_no_update_branch_still_uses_scoped_read(memory_db):
    store.upsert_user_memory("shared-user", {"risk_tags": "a private"}, tenant_id="a")
    result = memory.update_user_memory_from_turn(
        "shared-user",
        "你好",
        "hello",
        {},
        tenant_id="b",
    )
    assert result == {"last_reply_summary": "hello"}
