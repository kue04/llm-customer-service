"""索引回滚（B8）：把 ``rollback_index`` 一族从「已建未启用」变成**真能力**。

为什么这个文件存在
------------------
规范 §17.3 的判据是「**定义在、测试在、无人调用 = 未完成**」。回滚资产卡在
「零调用点、零测试」上已经好几个批次（台账记为前置项 1、F 节欠账），因此 B8 要做两件事：

* **本文件**负责「测试在」—— 证明回滚行为本身正确；
* ``routers/documents.py`` 的 ``POST /ingestion/indexes/rollback`` 负责「有人调用」；
* 两者缺一，按判据都只能记「预留」，不能记能力。

测什么、不测什么
----------------
这里只测**行为**（直接调 ``rollback_index`` 与端点函数），不起 HTTP 栈 ——
失败信息应该指向行为本身，而不是路由装配。鉴权只测「有没有这个 scope」，
JWT 解析那一段已由 ``test_auth_context`` 覆盖。

四条核心不变量（都来自 ``rollback_index`` 的设计说明）：

1. **只改指针**：不重建、不删任何文件，被切走的那一版仍然在磁盘上（可以再切回去）；
2. **先校验后切换**：目标版本不存在 / 不属于该租户 → 当场失败，**指针保持不动**
   （「回滚成功但指向坏索引」比回滚失败难排查得多）；
3. ``index_builds`` 状态跟着走：目标 → ``active``，被切走的 ``active`` → ``rolled_back``；
4. **幂等**：连续两次回滚到同一版本，结果一致。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from auth_helpers import make_auth_context
from retrieval_fixtures import (
    INDEX_NAME,
    RetrievalEnv,
    TENANT_A,
    alembic_upgrade,
)
from routers.documents import rollback_chunk_index
from schemas.document_schema import IndexRollbackRequest
from services.auth_service import RequestMeta
from services.ingestion import db, repository
from services.ingestion.index_builder import (
    ERROR_INDEX_ROLLBACK_FAILED,
    IndexBuildError,
    active_index_version,
    available_versions,
    rollback_index,
)
from services.ingestion.index_manifest import version_dir
from services.ingestion.object_store import LocalObjectStore


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """一个租户 + **至少两个索引版本**（入库流水线一次 + 显式重建一次）。

    不硬编码版本号：断言从 ``available_versions`` 现取，这样"流水线内部什么时候建索引"
    这类实现细节变化不会让测试变成假红。
    """

    url = f"sqlite:///{(tmp_path / 'rollback.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    alembic_upgrade(url)

    engine = db.create_db_engine(url)
    session = sessionmaker(bind=engine, expire_on_commit=False, future=True)()
    store = LocalObjectStore(tmp_path / "objects")
    index_root = tmp_path / "faiss_store"
    environment = RetrievalEnv(session, store, index_root)
    try:
        environment.ingest(TENANT_A, tag="alpha")
        environment.rebuild(TENANT_A)
        versions = available_versions(index_root, INDEX_NAME)
        assert len(versions) >= 2, f"夹具需要至少两个版本才能测回滚，实际 {versions}"
        yield environment, index_root
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


def _build_status(session, index_name: str = INDEX_NAME) -> dict[int, str]:
    rows = repository.list_index_builds(session, TENANT_A, index_name)
    return {int(row.version): row.status for row in rows}


# ---------------------------------------------------------------- 一、只改指针


def test_rollback_moves_pointer_only(env) -> None:
    """回滚**不删任何文件**：被切走的那一版与更旧的版本都还在磁盘上。"""

    environment, index_root = env
    versions_before = available_versions(index_root, INDEX_NAME)
    current = active_index_version(index_root, INDEX_NAME)
    target = min(versions_before)

    result = rollback_index(
        environment.session,
        tenant_id=TENANT_A,
        root=index_root,
        index_version=target,
        index_name=INDEX_NAME,
    )

    assert result.index_version == target
    assert result.switched is True
    assert active_index_version(index_root, INDEX_NAME) == target

    # 目录与版本列表一个都没少 —— 回滚不是删除
    assert set(available_versions(index_root, INDEX_NAME)) == set(versions_before)
    for version in versions_before:
        assert version_dir(index_root, INDEX_NAME, version).exists()
    # 被切走的那一版仍在（所以"再切回去"是可能的，这正是回滚可逆的前提）
    assert version_dir(index_root, INDEX_NAME, current).exists()


def test_rollback_can_switch_back(env) -> None:
    """回滚可逆：切回旧版之后，还能再切回新版（指针是唯一的，文件都是现成的）。"""

    environment, index_root = env
    versions = available_versions(index_root, INDEX_NAME)
    older, newer = min(versions), max(versions)

    rollback_index(
        environment.session, tenant_id=TENANT_A, root=index_root, index_version=older, index_name=INDEX_NAME
    )
    assert active_index_version(index_root, INDEX_NAME) == older

    rollback_index(
        environment.session, tenant_id=TENANT_A, root=index_root, index_version=newer, index_name=INDEX_NAME
    )
    assert active_index_version(index_root, INDEX_NAME) == newer


# ---------------------------------------------------------------- 二、先校验后切换


def test_rollback_rejects_missing_version_and_keeps_pointer(env) -> None:
    """目标版本不存在 → 明确失败，**指针保持不动**。

    这条是回滚最要紧的性质：宁可在回滚时失败，也不要"成功"地把生效率指向一份坏索引 ——
    后者要等下一次检索才炸，那时候已经分不清是检索坏了还是索引坏了。
    """

    environment, index_root = env
    versions = available_versions(index_root, INDEX_NAME)
    current = active_index_version(index_root, INDEX_NAME)

    with pytest.raises(IndexBuildError) as excinfo:
        rollback_index(
            environment.session,
            tenant_id=TENANT_A,
            root=index_root,
            index_version=max(versions) + 100,
            index_name=INDEX_NAME,
        )

    assert excinfo.value.error_code == ERROR_INDEX_ROLLBACK_FAILED
    assert active_index_version(index_root, INDEX_NAME) == current


# ---------------------------------------------------------------- 三、状态与幂等


def test_rollback_updates_index_build_status(env) -> None:
    """``index_builds`` 状态跟着指针走：目标 active，被切走的 active → rolled_back。"""

    environment, index_root = env
    versions = available_versions(index_root, INDEX_NAME)
    older, newer = min(versions), max(versions)

    rollback_index(
        environment.session, tenant_id=TENANT_A, root=index_root, index_version=older, index_name=INDEX_NAME
    )

    status = _build_status(environment.session)
    assert status[older] == "active"
    assert status[newer] == "rolled_back"


def test_rollback_is_idempotent(env) -> None:
    """连续两次回滚到同一版本：第二次仍然成功，且状态不变（不是"第二次就坏"）。"""

    environment, index_root = env
    versions = available_versions(index_root, INDEX_NAME)
    target = min(versions)

    for _ in range(2):
        result = rollback_index(
            environment.session,
            tenant_id=TENANT_A,
            root=index_root,
            index_version=target,
            index_name=INDEX_NAME,
        )
        assert result.index_version == target

    status = _build_status(environment.session)
    assert status[target] == "active"
    assert sum(1 for value in status.values() if value == "active") == 1


# ---------------------------------------------------------------- 四、端点接线


def _call_endpoint(environment, index_root, monkeypatch, *, roles, index_version, reason=""):
    monkeypatch.setattr("routers.documents.default_index_root", lambda: index_root)
    return rollback_chunk_index(
        IndexRollbackRequest(index_version=index_version, reason=reason),
        auth=make_auth_context(tenant_id=TENANT_A, roles=roles),
        meta=RequestMeta(ip="127.0.0.1", user_agent="pytest"),
        session=environment.session,
    )


def test_endpoint_denies_role_without_index_rollback(env, monkeypatch) -> None:
    """``agent`` 没有 ``index:rollback`` → 403（回滚是运维动作，不是普通读操作）。"""

    environment, index_root = env
    with pytest.raises(HTTPException) as excinfo:
        _call_endpoint(environment, index_root, monkeypatch, roles=("agent",), index_version=1)

    assert excinfo.value.status_code == 403


def test_endpoint_rejects_unreachable_version_with_409(env, monkeypatch) -> None:
    """目标版本不可达 → **409**（请求侧问题），不是 503 —— 调用方该换个版本重试。"""

    environment, index_root = env
    with pytest.raises(HTTPException) as excinfo:
        _call_endpoint(environment, index_root, monkeypatch, roles=("admin",), index_version=9999)

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["error_code"] == ERROR_INDEX_ROLLBACK_FAILED


def test_endpoint_returns_from_and_to_version(env, monkeypatch) -> None:
    """成功时响应要能回答「从哪版切到哪版」—— 这是运维判断"到底生效没有"的依据。"""

    environment, index_root = env
    versions = available_versions(index_root, INDEX_NAME)
    target = min(versions)

    response = _call_endpoint(
        environment,
        index_root,
        monkeypatch,
        roles=("admin",),
        index_version=target,
        reason="embedding 模型换错版本，先退回旧索引",
    )

    assert response.index_version == target
    assert response.previous_version is not None
    assert response.previous_version != target
    assert response.switched is True
    assert set(response.available_versions) == set(versions)
    assert active_index_version(index_root, INDEX_NAME) == target
