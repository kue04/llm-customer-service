"""Migration-backed isolated storage and trusted identities for regressions."""

from contextlib import contextmanager
import inspect
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config

from services.ingestion.db import dispose_engines
from services.runtime_db import runtime_scope


@contextmanager
def runtime_database(path):
    with patch.dict(os.environ, {"RAG_ENV": "test", "RAG_DATABASE_URL": f"sqlite:///{Path(path).as_posix()}"}):
        command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
        try:
            yield Path(path)
        finally:
            dispose_engines()


@pytest.fixture
def runtime_db(tmp_path):
    with runtime_database(tmp_path / "runtime.db") as path:
        yield path


def scoped_call(function, *args, actor="admin_1", tenant="tenant-1", **kwargs):
    """Bind trusted test identity around a direct service invocation."""
    signature = inspect.signature(function)
    supplied = signature.bind_partial(*args, **kwargs).arguments
    actor = supplied.get("user_id") or actor
    tenant = supplied.get("tenant_id") or tenant
    if "tenant_id" in signature.parameters and not supplied.get("tenant_id"):
        kwargs["tenant_id"] = tenant
    with runtime_scope(tenant, actor):
        return function(*args, **kwargs)
