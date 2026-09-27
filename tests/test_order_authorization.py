import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime_fixtures import runtime_db as _runtime_db
from sqlalchemy.exc import IntegrityError
from auth_helpers import auth_headers
from routers.order import router
from services import order_state_store as store


runtime_db = _runtime_db


@pytest.fixture
def client(runtime_db):
    app = FastAPI()
    app.include_router(router, prefix="/orders")
    with TestClient(app) as client:
        yield client


def put(client, user="owner", tenant="tenant-a", **changes):
    payload = dict(order_id="private", user_id="owner", status="created")
    payload.update(changes)
    return client.put("/orders/private/state", json=payload, headers=auth_headers(user_id=user, tenant_id=tenant))


def test_jwt_is_only_source_of_order_ownership(client):
    result = put(client, user_id="victim")
    assert result.status_code == 200
    assert result.json()["user_id"] == "owner"


@pytest.mark.parametrize("user,tenant", [("intruder", "tenant-a"), ("owner", "tenant-b")])
def test_foreign_order_read_and_update_are_not_found(client, user, tenant):
    assert put(client).status_code == 200
    headers = auth_headers(user_id=user, tenant_id=tenant)
    assert client.get("/orders/private/state", headers=headers).status_code == 404
    assert put(client, user=user, tenant=tenant, status="delivered").status_code == 404
    assert (
        client.get("/orders/private/state", headers=auth_headers(user_id="owner", tenant_id="tenant-a")).json()[
            "status"
        ]
        == "created"
    )


def test_tools_share_tenant_owner_boundary_and_no_mock_fallback(client):
    from services.order_tool_service import query_order_status, query_refund_status

    assert put(client).status_code == 200
    for tool in (query_order_status, query_refund_status):
        assert tool("owner", "private", tenant_id="tenant-a")["status"] == "success"
        for user, tenant in [("other", "tenant-a"), ("owner", "tenant-b"), ("owner", None)]:
            result = tool(user, "private", tenant_id=tenant)
            assert result["error_type"] == "order_not_found"
            assert result["output"] == {}
        assert tool("owner", "order_new", tenant_id="tenant-a")["error_type"] == "order_not_found"


def test_legacy_rows_are_not_claimed_by_first_reader_or_writer(client, tmp_path, runtime_db):
    from services.runtime_maintenance import import_legacy_sqlite

    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE order_states (order_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL, status_label TEXT NOT NULL, delivery_status TEXT NOT NULL, summary TEXT NOT NULL, refund_status TEXT NOT NULL, store_name TEXT NOT NULL, items_json TEXT NOT NULL, total REAL NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO order_states VALUES ('private','owner','created','','','','none','','[]',0,'old')")
    original = path.read_bytes()
    with pytest.raises(ValueError):
        import_legacy_sqlite(path, [], apply=True)
    assert (
        client.get("/orders/private/state", headers=auth_headers(user_id="owner", tenant_id="tenant-a")).status_code
        == 404
    )
    import_legacy_sqlite(
        path,
        [{"table": "order_states", "key": {"order_id": "private"}, "tenant_id": "tenant-a", "created_by": "owner"}],
        apply=True,
    )
    assert put(client, user="intruder").status_code == 404
    assert put(client, tenant="tenant-b").status_code == 404
    assert (
        client.get("/orders/private/state", headers=auth_headers(user_id="owner", tenant_id="tenant-a")).json()[
            "status"
        ]
        == "created"
    )
    assert path.read_bytes() == original


def test_idempotency_transition_and_atomic_audit(client, runtime_db):
    headers = auth_headers(user_id="owner", tenant_id="tenant-a") | {"Idempotency-Key": "create-1"}
    payload = dict(order_id="private", status="created")
    first = client.put("/orders/private/state", json=payload, headers=headers)
    assert first.status_code == 200
    assert client.put("/orders/private/state", json=payload, headers=headers).json() == first.json()
    assert (
        client.put("/orders/private/state", json=payload | {"status": "delivered"}, headers=headers).status_code == 409
    )
    assert put(client, status="delivered").status_code == 200
    # A retry returns its original response without reverting the current state.
    assert client.put("/orders/private/state", json=payload, headers=headers).json() == first.json()
    assert put(client, status="created").status_code == 409
    with sqlite3.connect(runtime_db) as conn:
        rows = conn.execute("SELECT before_summary, after_summary FROM runtime_audit_logs ORDER BY id").fetchall()
        assert len(rows) == 2
        assert '"status": "created"' in rows[1][0]
        assert '"status": "delivered"' in rows[1][1]
        assert '"tenant_id": "tenant-a"' in rows[1][1]


def test_failed_audit_rolls_back_state_and_idempotency(client, runtime_db):
    assert put(client).status_code == 200
    with sqlite3.connect(runtime_db) as conn:
        conn.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON runtime_audit_logs BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
        )
    with pytest.raises(IntegrityError, match="audit unavailable"):
        store.upsert_order_state(
            dict(order_id="private", status="delivered"),
            user_id="owner",
            tenant_id="tenant-a",
            idempotency_key="update",
        )
    assert store.get_order_state("private", user_id="owner", tenant_id="tenant-a")["status"] == "created"
    with sqlite3.connect(runtime_db) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_order_state_requests").fetchone()[0] == 0


def test_parallel_claims_cannot_overwrite_ownership(client, runtime_db):
    def claim(user):
        try:
            return store.upsert_order_state(
                dict(order_id="race", status="created"), user_id=user, tenant_id="tenant-a"
            )["user_id"]
        except store.OrderNotFoundError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, [f"user-{i}" for i in range(8)]))
    assert len([result for result in results if result]) == 1
    with sqlite3.connect(runtime_db) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_audit_logs").fetchone()[0] == 1


def test_parallel_idempotent_writes_have_single_audit(client, runtime_db):
    def save(_):
        return store.upsert_order_state(
            dict(order_id="race", status="created"), user_id="owner", tenant_id="tenant-a", idempotency_key="once"
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(save, range(8)))
    assert all(result == results[0] for result in results)
    with sqlite3.connect(runtime_db) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_audit_logs").fetchone()[0] == 1
