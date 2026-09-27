"""Shared runtime persistence. Schema changes belong exclusively to Alembic.

Every operation requires trusted tenant/user context. PostgreSQL transactions
serialize writes per tenant, including absent-row creation and idempotency checks.
The small result adapter preserves the existing service mapping interfaces.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import hashlib
from datetime import datetime, timezone

from sqlalchemy import text

from services.ingestion.db import get_engine
from services.request_budget import checkpoint, remaining_timeout

_identity: ContextVar[tuple[str, str] | None] = ContextVar("runtime_identity", default=None)


def identity() -> tuple[str, str]:
    value = _identity.get()
    if value is None or not all(value):
        raise PermissionError("trusted tenant and actor context required")
    return value


@contextmanager
def runtime_scope(tenant_id: str, user_id: str):
    if not tenant_id or not user_id:
        raise PermissionError("trusted tenant and actor context required")
    current = _identity.get()
    if current and current != (tenant_id, user_id):
        raise PermissionError("runtime identity mismatch")
    token = _identity.set((tenant_id, user_id))
    try:
        yield
    finally:
        _identity.reset(token)


def scoped_route(function):
    """Enter context in the handler thread/task, after JWT dependency validation."""
    signature = inspect.signature(function)
    if inspect.iscoroutinefunction(function):

        @wraps(function)
        async def async_call(*args, **kwargs):
            auth = signature.bind(*args, **kwargs).arguments.get("auth")
            if auth is None:
                raise PermissionError("verified auth context required")
            with runtime_scope(auth.tenant_id, auth.user_id):
                return await function(*args, **kwargs)

        return async_call

    @wraps(function)
    def call(*args, **kwargs):
        auth = signature.bind(*args, **kwargs).arguments.get("auth")
        if auth is None:
            raise PermissionError("verified auth context required")
        with runtime_scope(auth.tenant_id, auth.user_id):
            return function(*args, **kwargs)

    return call


def scoped_identity(function):
    """Allow trusted service callers to pass explicit identity instead of a scope."""
    signature = inspect.signature(function)

    @wraps(function)
    def call(*args, **kwargs):
        bound = signature.bind(*args, **kwargs)
        supplied = bound.arguments
        tenant = supplied.get("tenant_id")
        user = supplied.get("user_id")
        if tenant and user:
            with runtime_scope(tenant, user):
                return function(*args, **kwargs)
        current_tenant, current_user = identity()
        if tenant and tenant != current_tenant:
            raise PermissionError("runtime tenant mismatch")
        if "tenant_id" in signature.parameters and not tenant:
            bound.arguments["tenant_id"] = current_tenant
        if "user_id" in signature.parameters and not user:
            bound.arguments["user_id"] = current_user
        if user and user != current_user:
            raise PermissionError("runtime actor mismatch")
        return function(*bound.args, **bound.kwargs)

    return call


class Result:
    def __init__(self, result):
        self.rowcount = result.rowcount
        self._rows = list(result.mappings()) if result.returns_rows else []
        self.inserted_id = self._rows[0].get("id") if self._rows else None

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class RuntimeConnection:
    def __init__(self):
        checkpoint()
        self.tenant_id, self.user_id = identity()
        self.connection = get_engine().connect()
        self._locked = False

    def lock(self):
        if not self._locked and self.connection.dialect.name == "postgresql":
            self.connection.execute(text("SELECT set_config('lock_timeout', :limit, true)"),
                                    {'limit': str(max(1, int(remaining_timeout(5)*1000)))})
            self.connection.execute(text("SELECT set_config('statement_timeout', :limit, true)"),
                                    {'limit': str(max(1, int(remaining_timeout(15)*1000)))})
            key = int.from_bytes(hashlib.sha256(self.tenant_id.encode()).digest()[:8], "big", signed=True)
            self.connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        elif not self._locked and self.connection.dialect.name == "sqlite":
            # SQLite exists only in the explicit test profile. Match transaction
            # serialization semantics without pretending it supports row locks.
            self.connection.exec_driver_sql("BEGIN IMMEDIATE")
        self._locked = True

    def lock_resource(self, kind: str, value: str):
        if self.connection.dialect.name == "postgresql":
            key = int.from_bytes(hashlib.sha256(f"{kind}:{value}".encode()).digest()[:8], "big", signed=True)
            self.connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})

    def execute(self, statement: str, parameters=()):
        checkpoint()
        # SQL in services uses SQLAlchemy named bind parameters; no dialect
        # rewriting or string interpolation of SQL identifiers/values occurs.
        self.lock()
        bound = (
            dict(parameters) if isinstance(parameters, dict) else {f"p{i}": value for i, value in enumerate(parameters)}
        )
        bound.update(_tenant=self.tenant_id, _actor=self.user_id, _now=datetime.now(timezone.utc).isoformat())
        return Result(self.connection.execute(text(statement), bound))

    def executemany(self, statement: str, rows):
        for row in rows:
            self.execute(statement, row)

    def commit(self):
        checkpoint()
        self.connection.commit()
        self._locked = False

    def rollback(self):
        self.connection.rollback()
        self._locked = False

    def close(self):
        self.connection.close()


def get_connection() -> RuntimeConnection:
    return RuntimeConnection()
