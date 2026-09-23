"""阶段 1.1 的模型与迁移测试。

覆盖范围
--------
1. ``alembic upgrade head`` 能建出全部 13 张表，且可重复执行（幂等）；
2. **迁移产物与模型定义逐表对照**（表名、列名、唯一约束名、外键名、索引名、
   CHECK 约束名），防止以后有人只改模型不改迁移，导致 SQLite 与 PostgreSQL 行为分叉；
3. 跨租户唯一性：不同租户可用相同 ``external_id`` / ``content_hash``，同租户内必须唯一；
4. 复合外键：子表不能挂到其他租户的父记录上；
5. 外键约束：chunk 必须挂在真实存在的文档版本上；父 chunk 必须同版本；
6. CHECK 约束：非法的 source_type / status / stage / acl permission 一律拒绝；
7. 默认值：ingestion_jobs 初始 stage 为 received。

测试全部跑在临时 SQLite 文件上，不触碰 data/ 下的既有 db。
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint, inspect
from sqlalchemy.exc import IntegrityError
import pytest

from services.ingestion import db, models, repository


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _alembic_config(url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture()
def db_url(tmp_path, monkeypatch) -> str:
    """建一个已 upgrade 到 head 的临时数据库，返回连接串。"""

    url = f"sqlite:///{(tmp_path / 'rag_models.db').as_posix()}"
    # env.py 里环境变量优先级最高，测试与生产共用同一套解析逻辑
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    command.upgrade(_alembic_config(url), "head")
    yield url
    db.dispose_engines()


def seed_tenant(session, name: str) -> str:
    return repository.create_tenant(session, name=name).id


def seed_document(session, tenant_id: str, *, slug: str = "kb", source_uri: str = "s3://b/f.txt"):
    knowledge_base = repository.create_knowledge_base(session, tenant_id=tenant_id, slug=slug)
    document = repository.create_document(
        session,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        source_uri=source_uri,
        source_type="txt",
    )
    return knowledge_base, document


def now_hash(value: str = "h") -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- 迁移本身


def test_alembic_upgrade_creates_all_tables(db_url):
    inspector = inspect(db.get_engine(db_url))
    tables = set(inspector.get_table_names())
    assert set(models.ALL_TABLES) <= tables
    assert "alembic_version" in tables


def test_alembic_upgrade_is_idempotent(db_url):
    """重复执行 upgrade head 应为空操作，不报错、不丢表。"""

    command.upgrade(_alembic_config(db_url), "head")
    command.upgrade(_alembic_config(db_url), "head")
    inspector = inspect(db.get_engine(db_url))
    assert set(models.ALL_TABLES) <= set(inspector.get_table_names())


def test_migrated_schema_matches_model_metadata(db_url):
    """迁移建出来的 schema 必须和 models.py 完全一致（含约束名）。"""

    inspector = inspect(db.get_engine(db_url))
    metadata = models.Base.metadata

    db_tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert db_tables == set(metadata.tables)

    for name, table in metadata.tables.items():
        assert sorted(col["name"] for col in inspector.get_columns(name)) == sorted(
            col.name for col in table.columns
        ), f"列名不一致: {name}"

        db_unique = {item["name"] for item in inspector.get_unique_constraints(name)}
        model_unique = {c.name for c in table.constraints if isinstance(c, UniqueConstraint)}
        assert db_unique == model_unique, f"唯一约束不一致: {name}"

        db_fk = {item.get("name") for item in inspector.get_foreign_keys(name)}
        model_fk = {c.name for c in table.constraints if isinstance(c, ForeignKeyConstraint)}
        assert db_fk == model_fk, f"外键不一致: {name}"

        db_index = {item["name"] for item in inspector.get_indexes(name)}
        model_index = {index.name for index in table.indexes}
        assert db_index == model_index, f"索引不一致: {name}"

        db_check = {item["name"] for item in inspector.get_check_constraints(name)}
        model_check = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
        assert db_check == model_check, f"CHECK 约束不一致: {name}"


# ---------------------------------------------------------------- 跨租户唯一性


def test_cross_tenant_uniqueness(db_url):
    """同一 external_id 可存在于两个租户；同租户内重复必须被拒。"""

    with db.session_scope(db_url) as session:
        tenant_a = seed_tenant(session, "tenant-a")
        tenant_b = seed_tenant(session, "tenant-b")
        repository.create_user(session, tenant_id=tenant_a, external_id="alice")
        repository.create_user(session, tenant_id=tenant_b, external_id="alice")

    with db.session_scope(db_url) as session:
        users = session.query(models.User).all()
        assert len(users) == 2

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            tenant_a = repository.get_tenant(session, tenant_a).id
            repository.create_user(session, tenant_id=tenant_a, external_id="alice")


def test_source_uri_unique_per_tenant_across_knowledge_bases(db_url):
    """同一租户内一个源文件只对应一份文档 —— 即使落在不同知识库也要被拒。

    这条约束与 ``uq_document_versions(tenant_id, content_hash)`` 是配套的：
    若允许同一文件在两个知识库各建一份文档，第二份文档建版本时会撞 content_hash
    唯一约束（要么报错，要么变成一个永远没有内容的 duplicate 文档）。
    重新上传同一 URI 的更新内容，正确做法是在同一份文档上追加版本。
    跨知识库共用应通过后续新增的 knowledge_base_documents 关联表实现。
    """

    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        other_tenant = seed_tenant(session, "t2")
        _, document = seed_document(session, tenant_id, source_uri="s3://bucket/same.txt")
        repository.create_knowledge_base(session, tenant_id=tenant_id, slug="kb-2")
        # 不同租户使用完全相同的 URI：允许
        seed_document(session, other_tenant, source_uri="s3://bucket/same.txt")
        document_id = document.id

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            kb2 = repository.get_knowledge_base_by_slug(session, tenant_id, "kb-2")
            repository.create_document(
                session,
                tenant_id=tenant_id,
                knowledge_base_id=kb2.id,
                source_uri="s3://bucket/same.txt",
                source_type="txt",
            )

    # 仓储层的按 URI 查询不需要知识库维度
    with db.session_scope(db_url) as session:
        found = repository.get_document_by_source_uri(session, tenant_id, "s3://bucket/same.txt")
        assert found is not None
        assert found.id == document_id


def test_reupload_same_source_uri_appends_version_not_new_document(db_url):
    """契约测试：同一 URI 重传新内容 → 同一份文档追加版本，而不是新建文档。

    责任划分（阶段 2.3 落地，见台账 [D-6]）：

    * **上传接口只负责「复用文档」**：先查 ``get_document_by_source_uri``，
      命中就用同一份文档、未命中才建文档，并新建 ``ingestion_jobs`` 排队；
    * **建版本不在上传期发生**，由流水线在 ``parsed`` 阶段创建（阶段 3.3）。
      上传期建版本会撞 ``uq_document_versions(tenant_id, content_hash)``：
      「内容相同、文件名不同」的两次上传会各自新建文档，第二个版本必然冲突
      （IntegrityError，或者一个永远没有内容的僵尸文档）。

    因此本测试用仓储层直接建版本，锁定的契约是「同一 URI 只对应一份文档、
    版本号在文档内连续递增」——这条契约对上传接口与流水线同时成立，
    与「由谁执行建版本这一步」无关。
    """

    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        _, document = seed_document(session, tenant_id, source_uri="s3://a/policy.pdf")
        first = repository.create_document_version(
            session, tenant_id=tenant_id, document_id=document.id, content_hash=now_hash("rev1")
        )
        assert first.version == 1
        document_id = document.id

    with db.session_scope(db_url) as session:
        same_document = repository.get_document_by_source_uri(session, tenant_id, "s3://a/policy.pdf")
        assert same_document is not None and same_document.id == document_id
        second = repository.create_document_version(
            session,
            tenant_id=tenant_id,
            document_id=same_document.id,
            content_hash=now_hash("rev2"),
        )
        assert second.version == 2
        assert len(repository.list_document_versions(session, tenant_id, document_id)) == 2


def test_content_hash_unique_within_tenant_but_allowed_across_tenants(db_url):
    """重复文件不得产生第二份有效版本 —— 但仅限同一租户内。"""

    with db.session_scope(db_url) as session:
        tenant_a = seed_tenant(session, "tenant-a")
        tenant_b = seed_tenant(session, "tenant-b")
        _, doc_a = seed_document(session, tenant_a, source_uri="s3://a/f.txt")
        _, doc_b = seed_document(session, tenant_b, source_uri="s3://b/f.txt")
        repository.create_document_version(
            session, tenant_id=tenant_a, document_id=doc_a.id, content_hash=now_hash("same")
        )
        # 不同租户同内容：允许
        repository.create_document_version(
            session, tenant_id=tenant_b, document_id=doc_b.id, content_hash=now_hash("same")
        )
        assert repository.find_version_by_content_hash(session, tenant_a, now_hash("same")) is not None

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            _, another_doc = seed_document(session, tenant_a, slug="kb2", source_uri="s3://a/g.txt")
            repository.create_document_version(
                session, tenant_id=tenant_a, document_id=another_doc.id, content_hash=now_hash("same")
            )


# ---------------------------------------------------------------- 外键 / 复合外键


def test_document_cannot_attach_to_other_tenant_knowledge_base(db_url):
    """A 租户的文档挂到 B 租户的知识库 —— 必须被数据库拒绝。"""

    with db.session_scope(db_url) as session:
        tenant_a = seed_tenant(session, "tenant-a")
        tenant_b = seed_tenant(session, "tenant-b")
        kb_b = repository.create_knowledge_base(session, tenant_id=tenant_b, slug="kb-b").id

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            repository.create_document(
                session,
                tenant_id=tenant_a,
                knowledge_base_id=kb_b,
                source_uri="s3://a/x.txt",
                source_type="txt",
            )


def test_document_version_cannot_use_other_tenant_document(db_url):
    with db.session_scope(db_url) as session:
        tenant_a = seed_tenant(session, "tenant-a")
        tenant_b = seed_tenant(session, "tenant-b")
        _, doc_a = seed_document(session, tenant_a, source_uri="s3://a/f.txt")

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            repository.create_document_version(
                session,
                tenant_id=tenant_b,
                document_id=doc_a.id,
                content_hash=now_hash("cross"),
            )


def test_chunk_requires_existing_document_version(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            repository.insert_chunks(
                session,
                tenant_id=tenant_id,
                document_version_id="not-a-real-version",
                chunks=[{"chunk_id": "c1", "text": "x", "token_count": 1, "content_hash": now_hash("x")}],
            )


def test_chunk_parent_must_belong_to_same_document_version(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        _, document = seed_document(session, tenant_id, source_uri="s3://a/f.txt")
        version_one = repository.create_document_version(
            session, tenant_id=tenant_id, document_id=document.id, content_hash=now_hash("v1")
        )
        version_two = repository.create_document_version(
            session, tenant_id=tenant_id, document_id=document.id, content_hash=now_hash("v2")
        )
        repository.insert_chunks(
            session,
            tenant_id=tenant_id,
            document_version_id=version_one.id,
            chunks=[{"chunk_id": "p1", "text": "parent", "token_count": 2, "content_hash": now_hash("p1")}],
        )
        version_one_id, version_two_id = version_one.id, version_two.id

    # 同版本内父子关系：允许
    with db.session_scope(db_url) as session:
        repository.insert_chunks(
            session,
            tenant_id=tenant_id,
            document_version_id=version_one_id,
            chunks=[
                {
                    "chunk_id": "c1",
                    "parent_chunk_id": "p1",
                    "text": "child",
                    "token_count": 2,
                    "content_hash": now_hash("c1"),
                }
            ],
        )

    # 指向另一个版本的 chunk：拒绝
    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            repository.insert_chunks(
                session,
                tenant_id=tenant_id,
                document_version_id=version_two_id,
                chunks=[
                    {
                        "chunk_id": "c2",
                        "parent_chunk_id": "p1",
                        "text": "child",
                        "token_count": 2,
                        "content_hash": now_hash("c2"),
                    }
                ],
            )


def test_chunk_id_unique_within_document_version(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        _, document = seed_document(session, tenant_id, source_uri="s3://a/f.txt")
        version = repository.create_document_version(
            session, tenant_id=tenant_id, document_id=document.id, content_hash=now_hash("v")
        )
        repository.insert_chunks(
            session,
            tenant_id=tenant_id,
            document_version_id=version.id,
            chunks=[{"chunk_id": "c1", "text": "a", "token_count": 1, "content_hash": now_hash("a")}],
        )
        version_id = version.id

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            repository.insert_chunks(
                session,
                tenant_id=tenant_id,
                document_version_id=version_id,
                chunks=[{"chunk_id": "c1", "text": "b", "token_count": 1, "content_hash": now_hash("b")}],
            )


# ---------------------------------------------------------------- CHECK 约束


@pytest.mark.parametrize(
    "field, value",
    [
        ("source_type", "exe"),
        ("status", "not-a-status"),
    ],
)
def test_document_check_constraints_reject_invalid_values(db_url, field, value):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        knowledge_base = repository.create_knowledge_base(session, tenant_id=tenant_id, slug="kb")
        kb_id = knowledge_base.id

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            payload = {
                "tenant_id": tenant_id,
                "knowledge_base_id": kb_id,
                "source_uri": f"s3://a/{field}.txt",
                "source_type": "txt",
            }
            payload[field] = value
            repository.create_document(session, title="x", **payload)


def test_acl_permission_check_constraint(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        _, document = seed_document(session, tenant_id, source_uri="s3://a/f.txt")
        document_id = document.id

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            repository.grant_document_acl(
                session,
                tenant_id=tenant_id,
                document_id=document_id,
                subject_type="user",
                subject_id="u1",
                permission="superuser",
            )


def test_acl_subject_unique_per_document(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        _, document = seed_document(session, tenant_id, source_uri="s3://a/f.txt")
        repository.grant_document_acl(
            session,
            tenant_id=tenant_id,
            document_id=document.id,
            subject_type="user",
            subject_id="u1",
            permission="read",
        )
        document_id = document.id

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            repository.grant_document_acl(
                session,
                tenant_id=tenant_id,
                document_id=document_id,
                subject_type="user",
                subject_id="u1",
                permission="write",
            )


def test_ingestion_job_stage_check_and_defaults(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        _, document = seed_document(session, tenant_id, source_uri="s3://a/f.txt")
        job = repository.create_ingestion_job(session, tenant_id=tenant_id, document_id=document.id)
        assert job.stage == "received"
        assert job.status == "pending"
        assert job.retry_count == 0
        document_id = document.id

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            job = repository.create_ingestion_job(session, tenant_id=tenant_id, document_id=document_id)
            job.stage = "not-a-stage"
            session.flush()


def test_index_build_unique_version_and_positive_dimension(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        first = repository.create_index_build(
            session,
            tenant_id=tenant_id,
            index_name="kb_default",
            embedding_model="bge-small-zh-v1.5",
            embedding_dimension=512,
        )
        assert first.version == 1
        second = repository.create_index_build(
            session,
            tenant_id=tenant_id,
            index_name="kb_default",
            embedding_model="bge-small-zh-v1.5",
            embedding_dimension=512,
        )
        assert second.version == 2

    with pytest.raises(IntegrityError):
        with db.session_scope(db_url) as session:
            build = repository.create_index_build(
                session,
                tenant_id=tenant_id,
                index_name="kb_default",
                embedding_model="m",
                embedding_dimension=512,
                status="active",
            )
            build.version = 1  # 与已有 (tenant, index_name, version=1) 冲突
            session.flush()


# ---------------------------------------------------------------- 审计与仓储辅助


def test_audit_event_roundtrip_and_ordering(db_url):
    with db.session_scope(db_url) as session:
        tenant_id = seed_tenant(session, "t")
        repository.record_audit_event(
            session,
            tenant_id=tenant_id,
            actor_id="u1",
            action="document:upload",
            resource_type="document",
            resource_id="d1",
            request_id="req-1",
            summary_json={"note": "redacted"},
        )
        events = repository.list_audit_events(session, tenant_id)
        assert len(events) == 1
        assert events[0].summary_json == {"note": "redacted"}


def test_repository_never_leaks_across_tenants(db_url):
    """仓储层查询必须带 tenant_id，A 租户拿不到 B 租户的文档。"""

    with db.session_scope(db_url) as session:
        tenant_a = seed_tenant(session, "tenant-a")
        tenant_b = seed_tenant(session, "tenant-b")
        _, doc_b = seed_document(session, tenant_b, source_uri="s3://b/secret.txt")

    with db.session_scope(db_url) as session:
        assert repository.get_document(session, tenant_b, doc_b.id) is not None
        assert repository.get_document(session, tenant_a, doc_b.id) is None
        assert repository.list_documents(session, tenant_a) == []
