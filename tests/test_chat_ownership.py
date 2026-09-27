"""JWT ownership regressions for conversation history and session reuse."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_helpers import auth_headers, make_auth_context
from routers.chat import router
from runtime_fixtures import runtime_db as _runtime_db, scoped_call
from services import conversation_store


runtime_db = _runtime_db


@pytest.fixture
def history_client(runtime_db):
    scoped_call(
        conversation_store.get_or_create_conversation, "owner", "private-session", "order-a", tenant_id="tenant-a"
    )
    scoped_call(
        conversation_store.get_or_create_conversation, "owner", "other-tenant-session", "order-b", tenant_id="tenant-b"
    )
    scoped_call(
        conversation_store.append_message,
        "private-session",
        "user",
        "private history",
        actor="owner",
        tenant="tenant-a",
    )
    scoped_call(
        conversation_store.save_turn_response,
        "private-turn",
        "private-session",
        "owner",
        "order-a",
        "private history",
        "private reply",
        {"reply": "private reply"},
        actor="owner",
        tenant="tenant-a",
    )
    app = FastAPI()
    app.include_router(router, prefix="/chat")
    return TestClient(app)


@pytest.mark.parametrize(
    "user,tenant,params",
    [
        ("attacker", "tenant-a", {"session_id": "private-session"}),
        ("attacker", "tenant-a", {"session_id": "private-session", "user_id": "owner"}),
        ("attacker", "tenant-a", {"user_id": "owner", "order_id": "order-a"}),
        ("owner", "tenant-b", {"session_id": "private-session"}),
        ("owner", "tenant-b", {"order_id": "order-a"}),
        ("owner", "tenant-a", {"session_id": "missing"}),
    ],
)
def test_history_denies_foreign_or_missing_conversation(history_client, user, tenant, params):
    response = history_client.get("/chat/history", params=params, headers=auth_headers(user_id=user, tenant_id=tenant))
    assert response.status_code == 404
    assert response.json() == {"detail": "conversation not found"}


@pytest.mark.parametrize(
    "params",
    [
        {"session_id": "private-session"},
        {"order_id": "order-a"},
        {"user_id": "attacker"},
        {},
    ],
)
def test_history_uses_jwt_identity(history_client, params):
    response = history_client.get(
        "/chat/history", params=params, headers=auth_headers(user_id="owner", tenant_id="tenant-a")
    )
    assert response.status_code == 200
    assert response.json()["session_id"] == "private-session"
    assert response.json()["user_id"] == "owner"
    assert response.json()["messages"][0]["content"] == "private history"
    assert response.json()["latest_response"]["reply"] == "private reply"


@pytest.mark.parametrize("user,tenant", [("attacker", "tenant-a"), ("owner", "tenant-b")])
@pytest.mark.parametrize("action", ["accepted", "human_handoff", "edited_and_sent", "marked_bad_case"])
def test_review_action_cannot_read_or_modify_foreign_turn(history_client, user, tenant, action):
    response = history_client.post(
        "/chat/review-action",
        headers=auth_headers(user_id=user, tenant_id=tenant),
        json={"request_id": "private-turn", "action": action, "final_reply": "attempted edit"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "conversation not found"}
    turn = scoped_call(conversation_store.get_turn_response, "private-turn", actor="owner", tenant="tenant-a")
    assert turn["response"] == {"reply": "private reply"}
    assert (
        scoped_call(
            conversation_store.find_conversation,
            "owner",
            session_id="private-session",
            tenant_id="tenant-a",
            actor="owner",
            tenant="tenant-a",
        )["status"]
        == "active"
    )


def test_review_action_owner_can_accept(history_client):
    response = history_client.post(
        "/chat/review-action",
        headers=auth_headers(user_id="owner", tenant_id="tenant-a"),
        json={"request_id": "private-turn", "action": "accepted"},
    )
    assert response.status_code == 200
    assert response.json()["final_reply"] == "private reply"


@pytest.mark.parametrize("user,tenant", [("attacker", "tenant-a"), ("owner", "tenant-b")])
def test_prompt_cannot_take_over_session(history_client, user, tenant):
    response = history_client.post(
        "/chat/prompt",
        headers=auth_headers(user_id=user, tenant_id=tenant),
        json={"message": "hello", "session_id": "private-session", "user_id": "owner"},
    )
    assert response.status_code == 404
    connection = scoped_call(conversation_store.get_connection, actor="owner", tenant="tenant-a")
    try:
        row = connection.execute(
            "SELECT user_id, tenant_id FROM runtime_conversations WHERE session_id = 'private-session'"
        ).fetchone()
        assert tuple(row.values()) == ("owner", "tenant-a")
    finally:
        connection.close()


def test_legacy_conversation_migration_does_not_assign_tenant(tmp_path, runtime_db):
    from services.runtime_maintenance import import_legacy_sqlite

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE conversations (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, order_id TEXT, status TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO conversations VALUES ('legacy', 'owner', NULL, 'active', 'private', 'now', 'now')"
        )
    original = path.read_bytes()
    with pytest.raises(ValueError):
        import_legacy_sqlite(path, [], apply=True)
    assert (
        scoped_call(
            conversation_store.find_conversation,
            "owner",
            session_id="legacy",
            tenant_id="tenant-a",
            actor="owner",
            tenant="tenant-a",
        )
        is None
    )
    assert path.read_bytes() == original
    import_legacy_sqlite(
        path,
        [{"table": "conversations", "key": {"session_id": "legacy"}, "tenant_id": "tenant-a", "created_by": "owner"}],
        apply=True,
    )
    assert (
        scoped_call(
            conversation_store.find_conversation,
            "owner",
            session_id="legacy",
            tenant_id="tenant-a",
            actor="owner",
            tenant="tenant-a",
        )["summary"]
        == "private"
    )
    assert (
        scoped_call(
            conversation_store.find_conversation,
            "owner",
            session_id="legacy",
            tenant_id="tenant-b",
            actor="owner",
            tenant="tenant-a",
        )
        is None
    )
    assert path.read_bytes() == original


def test_authenticated_session_round_trip(history_client, monkeypatch):
    from services import conversation_service
    from services.redis_context_cache import RedisContextCache

    # Disable external cache connections while keeping the actual store path.
    monkeypatch.setenv("REDIS_URL", "")
    cache = RedisContextCache()
    monkeypatch.setattr(conversation_service, "get_redis_context_cache", lambda: cache)
    context = conversation_service.get_or_create_context("owner", "fresh", "order-new", tenant_id="tenant-a")
    scoped_call(
        conversation_store.append_message,
        context["session_id"],
        "user",
        "new message",
        actor="owner",
        tenant="tenant-a",
    )
    again = conversation_service.get_or_create_context("owner", "fresh", tenant_id="tenant-a")
    assert again["recent_messages"][0]["content"] == "new message"
    response = history_client.get(
        "/chat/history", params={"session_id": "fresh"}, headers=auth_headers(user_id="owner", tenant_id="tenant-a")
    )
    assert response.status_code == 200
    assert response.json()["messages"][0]["content"] == "new message"


def test_authenticated_prompt_ignores_body_user(history_client, monkeypatch):
    from services import chat_service
    from schemas.chat_schema import ChatRequest

    class ContextReached(Exception):
        pass

    def stop_after_context(*args, **kwargs):
        raise ContextReached

    monkeypatch.setattr(chat_service, "get_user_memory", stop_after_context)
    with pytest.raises(ContextReached):
        chat_service.get_answer_from_rag(
            ChatRequest(message="hello", user_id="victim", session_id="new-auth-session"),
            make_auth_context(user_id="owner", tenant_id="tenant-a"),
        )
    assert (
        scoped_call(
            conversation_store.find_conversation,
            "owner",
            session_id="new-auth-session",
            tenant_id="tenant-a",
            actor="owner",
            tenant="tenant-a",
        )
        is not None
    )
    assert (
        scoped_call(
            conversation_store.find_conversation,
            "victim",
            session_id="new-auth-session",
            tenant_id="tenant-a",
            actor="owner",
            tenant="tenant-a",
        )
        is None
    )


def test_concurrent_session_claims_keep_one_owner(history_client):
    barrier = Barrier(2)

    def claim(user):
        barrier.wait()
        try:
            scoped_call(
                conversation_store.get_or_create_conversation,
                user,
                "raced-session",
                tenant_id="tenant-a",
                actor="owner",
                tenant="tenant-a",
            )
            return user
        except PermissionError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["owner", "attacker"]))
    winners = [user for user in results if user is not None]
    assert len(winners) == 1
    assert (
        scoped_call(
            conversation_store.find_conversation,
            winners[0],
            session_id="raced-session",
            tenant_id="tenant-a",
            actor="owner",
            tenant="tenant-a",
        )
        is not None
    )


@pytest.mark.parametrize(
    "user,tenant,allowed",
    [
        ("owner", "tenant-a", True),
        ("attacker", "tenant-a", False),
        ("owner", "tenant-b", False),
    ],
)
def test_chat_order_tools_receive_verified_identity(history_client, monkeypatch, user, tenant, allowed):
    from schemas.chat_schema import ChatRequest
    from services import chat_service, conversation_service, order_state_store
    from services.redis_context_cache import RedisContextCache

    monkeypatch.setenv("REDIS_URL", "")
    cache = RedisContextCache()
    monkeypatch.setattr(chat_service, "get_redis_context_cache", lambda: cache)
    monkeypatch.setattr(conversation_service, "get_redis_context_cache", lambda: cache)
    order_state_store.upsert_order_state(
        {
            "order_id": "private-order",
            "status": "delivering",
            "summary": "private delivery",
            "refund_status": "processing",
        },
        user_id="owner",
        tenant_id="tenant-a",
    )
    captured = []

    class ToolsReached(Exception):
        pass

    def stop_after_tools(results):
        captured.extend(results)
        raise ToolsReached

    # The actual context/store/tool chain runs; stop before retrieval/model I/O.
    monkeypatch.setattr(chat_service, "build_order_context", stop_after_tools)
    with pytest.raises(ToolsReached):
        chat_service.get_answer_from_rag(
            ChatRequest(message="查询退款进度", user_id="owner", order_id="private-order"),
            make_auth_context(user_id=user, tenant_id=tenant),
        )
    assert {result["tool_name"] for result in captured} == {"query_order_status", "query_refund_status"}
    for result in captured:
        assert result["input"]["user_id"] == user
        assert result["input"]["tenant_id"] == tenant
        if allowed:
            assert result["status"] == "success"
        else:
            assert result["status"] == "failed"
            assert result["output"] == {}
