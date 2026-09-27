"""Runtime repositories exercised against migrated SQLite and real PostgreSQL.

Set RAG_TEST_POSTGRES_URL to an isolated test server to run the PostgreSQL cases.
Every PostgreSQL case uses a random schema, so replicas share real persistence
without touching an application's existing schema.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import subprocess
import sys
from uuid import uuid4

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth_helpers import auth_headers

from services import audit_service, conversation_store as conversations, feedback_service
from services import knowledge_service, order_state_store, prompt_service
from services.ingestion.db import dispose_engines, get_engine
from services.runtime_db import runtime_scope
from services.runtime_maintenance import archive_runtime_data, import_legacy_sqlite


@pytest.fixture(params=["sqlite", "postgresql"])
def runtime_database(request, monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_ENV", "test")
    admin = None
    schema = None
    if request.param == "postgresql":
        configured = os.getenv("RAG_TEST_POSTGRES_URL")
        if not configured:
            pytest.skip("RAG_TEST_POSTGRES_URL is required for live PostgreSQL verification")
        admin = create_engine(configured)
        schema = "runtime_test_" + uuid4().hex
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema}"))
        url = (
            make_url(configured)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    else:
        url = "sqlite:///" + str(tmp_path / "runtime.db")
    monkeypatch.setenv("RAG_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    yield get_engine()
    dispose_engines()
    if admin:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def test_business_schema_is_migrated_and_namespaced(runtime_database):
    inspector = inspect(runtime_database)
    assert "users" in inspector.get_table_names()
    assert "runtime_users" in inspector.get_table_names()
    for name in inspector.get_table_names():
        if name.startswith("runtime_"):
            assert {"tenant_id", "created_by", "created_at", "updated_at", "deleted_at"} <= {
                c["name"] for c in inspector.get_columns(name)
            }


def test_migrations_restart_and_downgrade(runtime_database):
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "0001_ingestion_auth")
    assert "runtime_conversations" not in inspect(runtime_database).get_table_names()
    command.upgrade(config, "head")
    assert "runtime_conversations" in inspect(runtime_database).get_table_names()


def test_conversation_memory_and_child_constraints(runtime_database):
    with runtime_scope("tenant_a", "alice"):
        conversations.get_or_create_conversation("alice", "session_a")
        conversations.append_message("session_a", "user", "hello")
        conversations.save_turn_response("req_a", "session_a", "alice", None, "q", "a", {})
        conversations.upsert_facts("session_a", {"intent": "refund"})
        conversations.upsert_user_memory("alice", {"preference": "text"})
        assert conversations.count_messages("session_a") == 1
        assert conversations.get_facts("session_a") == {"intent": "refund"}
        assert conversations.get_user_memory("alice") == {"preference": "text"}
        conversations.save_review_action({"request_id": "req_a", "action": "accepted"})
        assert conversations.get_turn_response("req_a")["response"]["conversation_status"] == "accepted"
    for tenant, user in [("tenant_a", "bob"), ("tenant_b", "alice")]:
        with runtime_scope(tenant, user):
            assert conversations.find_conversation(user, session_id="session_a") is None
            assert conversations.list_messages("session_a") == []
            assert conversations.get_user_memory(user) == {}
            with pytest.raises(conversations.ConversationAccessError):
                conversations.get_or_create_conversation(user, "session_a")
            with pytest.raises(conversations.ConversationAccessError):
                conversations.append_message("session_a", "user", "forged")
    with runtime_database.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO runtime_conversation_messages "
                    "(session_id,role,content,intent_json,risk_level,created_at,tenant_id,created_by,updated_at) "
                    "VALUES ('session_a','user','forged','{}','low','now','tenant_b','alice','now')"
                )
            )


def test_orders_are_atomic_and_idempotent(runtime_database):
    payload = {"order_id": "order_a", "status": "delivering", "user_id": "attacker"}

    def write():
        return order_state_store.upsert_order_state(
            payload, user_id="alice", tenant_id="tenant_a", idempotency_key="same-key"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: write(), range(8)))
    assert all(result == results[0] for result in results)
    assert results[0]["user_id"] == "alice"
    with runtime_scope("tenant_a", "alice"):
        assert audit_service.list_audit_logs(action_type="order_state_upsert")["count"] == 1
    for tenant, user in [("tenant_a", "bob"), ("tenant_b", "alice")]:
        assert order_state_store.get_order_state("order_a", user_id=user, tenant_id=tenant) is None
        with pytest.raises(order_state_store.OrderNotFoundError):
            order_state_store.upsert_order_state(payload, user_id=user, tenant_id=tenant)
    with pytest.raises(order_state_store.OrderConflictError):
        order_state_store.upsert_order_state({**payload, "status": "created"}, user_id="alice", tenant_id="tenant_a")


def test_prompt_feedback_knowledge_tenant_scope(runtime_database):
    with runtime_scope("tenant_a", "alice"):
        assert prompt_service.get_active_prompt_config()["version"] == "builtin_v1"
        prompt = prompt_service.create_prompt_version({"system_prompt": "one", "version": "v1"}, author="alice")
        prompt_service.update_prompt_version_status(prompt["id"], "approved")
        prompt_service.activate_prompt_version(prompt["id"])
        next_prompt = prompt_service.create_prompt_version({"system_prompt": "two", "version": "v2"}, author="alice")
        prompt_service.update_prompt_version_status(next_prompt["id"], "approved")
        prompt_service.activate_prompt_version(next_prompt["id"])
        assert prompt_service.rollback_latest_prompt_version()["version"] == "v1"
        feedback_id = feedback_service.save_feedback(
            {
                "request_id": "r",
                "query": "q",
                "reply": "a",
                "helpful": False,
                "trace": {"top1_intent": "refund", "failure_stage": "answer"},
            }
        )
        assert len(feedback_service.list_recent_feedback(helpful=False, intent="refund", failure_stage="answer")) == 1
        assert feedback_service.build_eval_case_from_feedback(feedback_id)["query"] == "q"
        item = knowledge_service.create_knowledge_item({"question": "q", "answer": "a", "category": "c", "intent": "i"})
        assert (
            knowledge_service.list_knowledge_items(keyword="q", category="c", intent="i", status="draft")["total"] == 1
        )
        knowledge_service.review_knowledge_item(item["id"], "approved")
        assert knowledge_service.export_approved_jsonl()["count"] == 1
        published = knowledge_service.publish_approved_knowledge()
        assert published["status"] == "succeeded"
        assert knowledge_service.get_knowledge_item(item["id"])["status"] == "published"
        assert knowledge_service.rollback_latest_publish()["status"] == "succeeded"
        assert knowledge_service.get_knowledge_item(item["id"])["status"] == "rollback"
    with runtime_scope("tenant_b", "alice"):
        assert prompt_service.list_prompt_versions()["count"] == 0
        assert knowledge_service.list_knowledge_items()["total"] == 0
        assert feedback_service.list_recent_feedback() == []
        with pytest.raises(KeyError):
            feedback_service.build_eval_case_from_feedback(feedback_id)
        with pytest.raises(KeyError):
            knowledge_service.get_knowledge_item(item["id"])
        with pytest.raises(KeyError):
            prompt_service.activate_prompt_version(prompt["id"])


def test_import_requires_trusted_mapping_and_preserves_source(runtime_database, tmp_path):
    source = tmp_path / "legacy.db"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE user_memory (user_id TEXT, key TEXT, value TEXT, updated_at TEXT, PRIMARY KEY(user_id,key))"
        )
        conn.execute("INSERT INTO user_memory VALUES ('alice','preference','text','2025-01-01T00:00:00+00:00')")
    before = source.read_bytes()
    assert len(import_legacy_sqlite(source, [])["unmapped"]) == 1
    with pytest.raises(ValueError):
        import_legacy_sqlite(source, [], apply=True)
    mapping = [
        {
            "table": "user_memory",
            "key": {"user_id": "alice", "key": "preference"},
            "tenant_id": "tenant_a",
            "created_by": "alice",
        }
    ]
    assert import_legacy_sqlite(source, mapping, apply=True)["applied"]
    assert source.read_bytes() == before
    with runtime_scope("tenant_a", "alice"):
        assert conversations.get_user_memory("alice") == {"preference": "text"}
    with runtime_scope("tenant_b", "alice"):
        assert conversations.get_user_memory("alice") == {}
    with pytest.raises(IntegrityError):
        import_legacy_sqlite(source, mapping, apply=True)


def test_archive_scopes_tenant_and_preserves_a_copy(runtime_database, tmp_path):
    for tenant in ["tenant_a", "tenant_b"]:
        with runtime_scope(tenant, "alice"):
            conversations.get_or_create_conversation("alice", tenant + "_session")
            conversations.append_message(tenant + "_session", "user", "history")
    target = tmp_path / "archive.jsonl"
    result = archive_runtime_data("tenant_a", "2099-01-01T00:00:00+00:00", target)
    assert not result["applied"] and not target.exists()
    result = archive_runtime_data("tenant_a", "2099-01-01T00:00:00+00:00", target, apply=True)
    assert result["applied"] and result["sha256"]
    assert all(json.loads(line)["row"]["tenant_id"] == "tenant_a" for line in target.read_text().splitlines())
    with runtime_scope("tenant_a", "alice"):
        assert conversations.find_conversation("alice") is None
        assert conversations.list_messages("tenant_a_session") == []
    with runtime_scope("tenant_b", "alice"):
        assert len(conversations.list_messages("tenant_b_session")) == 1


def test_runtime_never_creates_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_ENV", "test")
    monkeypatch.setenv("RAG_DATABASE_URL", "sqlite:///" + str(tmp_path / "unmigrated.db"))
    with runtime_scope("tenant_a", "alice"):
        with pytest.raises((OperationalError, ProgrammingError)):
            conversations.find_conversation("alice")
    assert inspect(get_engine()).get_table_names() == []
    dispose_engines()


def test_legacy_numeric_ids_advance_postgres_sequence(runtime_database, tmp_path):
    source = tmp_path / "legacy_audit.db"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE audit_logs (id INTEGER PRIMARY KEY, operator_id TEXT, operator_role TEXT, action_type TEXT, object_type TEXT, object_id TEXT, created_at TEXT)"
        )
        conn.execute(
            "INSERT INTO audit_logs VALUES (40,'alice','admin','legacy','record','one','2025-01-01T00:00:00+00:00')"
        )
    mapping = [{"table": "audit_logs", "key": {"id": 40}, "tenant_id": "tenant_a", "created_by": "alice"}]
    import_legacy_sqlite(source, mapping, apply=True)
    with runtime_scope("tenant_a", "alice"):
        new_id = audit_service.record_audit_log(
            operator_id="alice", operator_role="admin", action_type="new", object_type="record", object_id="two"
        )
        assert new_id > 40
        assert audit_service.list_audit_logs()["count"] == 2


def test_separate_processes_share_committed_business_data(runtime_database):
    writer = """
from services.runtime_db import runtime_scope
from services import conversation_store as c, feedback_service as f, order_state_store as o
with runtime_scope('replica_tenant','replica_user'):
    c.get_or_create_conversation('replica_user','replica_session')
    c.append_message('replica_session','user','durable history')
    f.save_feedback({'request_id':'replica_request','query':'q','reply':'a','helpful':True})
    f.save_chat_session('q','a',{'request_id':'metric_request','tenant_id':'forged','retrieval_path':'chunk','index_version':'v1','answer_mode':'clarify','conversation_status':'human_handoff','retrieval_count':0,'citation_quality':{'passed':False}},token_usage={'total_tokens':10})
    o.upsert_order_state({'order_id':'replica_order','status':'delivering'},user_id='replica_user',tenant_id='replica_tenant')
"""
    reader = """
from services.runtime_db import runtime_scope
from services import conversation_store as c, feedback_service as f, order_state_store as o, audit_service as a
from services.ops_metrics import get_ops_metrics
with runtime_scope('replica_tenant','replica_user'):
    assert c.list_messages('replica_session')[0]['content'] == 'durable history'
    assert len(f.list_recent_feedback()) == 1
    assert o.get_order_state('replica_order',user_id='replica_user',tenant_id='replica_tenant')['status'] == 'delivering'
    assert a.list_audit_logs(action_type='order_state_upsert')['count'] == 1
    metrics = get_ops_metrics()
    assert metrics['request_count'] == 1 and metrics['clarify_count'] == 1
    assert metrics['human_handoff_route_count'] == 1
    assert metrics['dimensions']['chunk|v1|replica_tenant|clarify']['citation_missing_count'] == 1
"""
    for script in (writer, reader):
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
        assert completed.returncode == 0, completed.stderr


def test_two_api_instances_share_state_and_enforce_jwt_boundaries(runtime_database):
    from routers.chat import router as chat_router
    from routers.order import router as order_router
    from routers.feedback import router as feedback_router

    def app_instance():
        app = FastAPI()
        app.include_router(chat_router, prefix="/chat")
        app.include_router(order_router, prefix="/orders")
        app.include_router(feedback_router, prefix="/feedback")
        return app

    owner = auth_headers(user_id="alice", tenant_id="tenant_a")
    with runtime_scope("tenant_a", "alice"):
        conversations.get_or_create_conversation("alice", "api_session")
        conversations.append_message("api_session", "user", "shared history")
    with TestClient(app_instance()) as first, TestClient(app_instance()) as second:
        payload = {"order_id": "api_order", "user_id": "forged_owner", "status": "delivering"}
        written = first.put("/orders/api_order/state", headers=owner, json=payload)
        assert written.status_code == 200
        assert written.json()["user_id"] == "alice"
        assert second.get("/orders/api_order/state", headers=owner).json() == written.json()
        history = second.get("/chat/history", headers=owner, params={"session_id": "api_session"})
        assert history.status_code == 200
        assert history.json()["messages"][0]["content"] == "shared history"
        feedback = first.post(
            "/feedback", headers=owner, json={"request_id": "api_req", "query": "q", "reply": "a", "helpful": False}
        )
        assert feedback.status_code == 200
        assert second.get("/feedback/recent", headers=owner).json()["count"] == 1
        for tenant, user in [("tenant_a", "bob"), ("tenant_b", "alice")]:
            foreign = auth_headers(user_id=user, tenant_id=tenant)
            assert second.get("/orders/api_order/state", headers=foreign).status_code == 404
            assert second.put("/orders/api_order/state", headers=foreign, json=payload).status_code == 404
            assert (
                second.get(
                    "/chat/history", headers=foreign, params={"session_id": "api_session", "user_id": "alice"}
                ).status_code
                == 404
            )
        assert (
            second.get("/feedback/recent", headers=auth_headers(user_id="alice", tenant_id="tenant_b")).json()["count"]
            == 0
        )
    # A fresh app instance after both previous instances close observes commits.
    with TestClient(app_instance()) as restarted:
        assert restarted.get("/orders/api_order/state", headers=owner).status_code == 200
