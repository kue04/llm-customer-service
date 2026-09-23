"""B7 检索隔离测试的共用基建（**不是测试文件**，与 ``auth_helpers.py`` 同类）。

为什么单独抽一个模块
--------------------
「两个租户各建一份文档 → 各跑一次流水线建索引 → 互相检索不到」这套准备动作，
在**检索层隔离测试**与**API 层测试**里都要用。复制两份的后果不是"多写几行"，
而是两边的语料/切分/embedding 会慢慢漂移，最后一边绿一边红还说不清是谁变了。

这里的替身原则沿用 B5 / B6：**不上网、不下载模型**。
embedding 用确定性的词袋哈希（同词共现 → 余弦相似度高），
因此「哪条该被检索到」是可手算的，而不是"大概能查到"。
"""

from __future__ import annotations

from types import SimpleNamespace
import zlib

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from auth_helpers import make_auth_context
from routers import retrieval
from services.ingestion import db, index_manifest, repository
from services.ingestion.object_store import LocalObjectStore, build_object_key, logical_source_uri
from services.ingestion.pipeline import IngestionPipeline


TENANT_A = "tenant-alpha"
TENANT_B = "tenant-beta"
USER_A = "user-alpha"
USER_B = "user-beta"
INDEX_NAME = index_manifest.DEFAULT_INDEX_NAME
EMBEDDING_MODEL = "fake-embedder-v1"
VECTOR_DIM = 16
#: 1 个空白分隔的词 = 1 token（与 B5/B6 的 mock tokenizer 一致）
MOCK_TOKENIZER = "mock-word-v1"


def words(count: int, *, start: int = 1, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{index}" for index in range(start, start + count))


def fake_embedding(text: str, *, dimension: int = VECTOR_DIM) -> list[float]:
    """确定性词袋哈希向量：同词共现 → 余弦相似度高。

    刻意不用随机：测试需要「相同输入 → 相同向量」，否则"检索到哪一条"变成随机结果，
    断言只能写成"至少有一条"，那种断言守不住任何东西。
    """

    vector = [0.0] * dimension
    for token in str(text).split():
        vector[zlib.crc32(token.encode("utf-8")) % dimension] += 1.0
    if not any(vector):
        vector[0] = 1.0
    norm = sum(value * value for value in vector) ** 0.5
    return [value / norm for value in vector]


def markdown_body(tag: str, *, sections: int = 2, words_per_section: int = 200, salt: str = "") -> bytes:
    """一份结构清晰的 Markdown：多个「标题 + 正文」段，能切出多个 chunk。

    ``salt`` 会让正文多一段独特文本 —— 需要"同一租户里多份**内容不同**的文档"时必须传，
    否则内容 hash 相同会被流水线的判重逻辑归成同一份版本
    （``uq_document_versions(tenant_id, content_hash)``，见 [D-6]）。
    """

    parts: list[str] = []
    for index in range(sections):
        parts.append(f"# {tag} 政策 {index}")
        parts.append(words(words_per_section, start=1 + index * 1000, prefix=f"{tag}正文"))
    if salt:
        parts.append(f"# 附录 {salt}")
        parts.append(f"salt{salt.replace('/', '-')} 补充说明")
    return "\n\n".join(parts).encode("utf-8")


def query_for(tag: str, *, tokens: int = 6) -> str:
    """能命中 ``tag`` 语料的查询串（词袋与 ``markdown_body`` 同源）。"""

    return words(tokens, start=1, prefix=f"{tag}正文")


def alembic_upgrade(url: str) -> None:
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


class RetrievalEnv:
    """两个租户 + 共用一份全局 chunk 索引的测试环境。"""

    def __init__(self, session, store: LocalObjectStore, index_root) -> None:
        self.session = session
        self.store = store
        self.index_root = index_root

    # ------------------------------------------------------------ 准备

    def ensure_tenant(self, tenant_id: str) -> None:
        if repository.get_tenant(self.session, tenant_id) is None:
            repository.create_tenant(self.session, tenant_id=tenant_id, name=tenant_id)
            self.session.commit()

    def ingest(
        self,
        tenant_id: str,
        *,
        tag: str = "alpha",
        filename: str | None = None,
        sections: int = 2,
        words_per_section: int = 200,
    ) -> SimpleNamespace:
        """建租户（按需）→ 建知识库 / 文档 → 上传字节 → 跑流水线到 ``published``。"""

        self.ensure_tenant(tenant_id)
        slug = f"kb-{tenant_id}"
        knowledge_base = repository.get_knowledge_base_by_slug(self.session, tenant_id, slug)
        if knowledge_base is None:
            knowledge_base = repository.create_knowledge_base(
                self.session,
                tenant_id=tenant_id,
                slug=slug,
                chunking_config_json={"tokenizer_id": MOCK_TOKENIZER},
            )
            self.session.commit()

        name = filename or f"{tenant_id}-{tag}.md"
        document = repository.create_document(
            self.session,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base.id,
            source_uri=logical_source_uri(name),
            source_type="md",
            title=name,
        )
        self.store.put(
            build_object_key(tenant_id, document.id, name),
            markdown_body(tag, sections=sections, words_per_section=words_per_section, salt=name),
        )
        job = repository.create_ingestion_job(
            self.session, tenant_id=tenant_id, document_id=document.id, status="pending", stage="received"
        )
        self.session.commit()

        outcome = IngestionPipeline(
            session=self.session,
            store=self.store,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            index_root=self.index_root,
        ).process_job(tenant_id=tenant_id, job_id=job.id)
        assert outcome.succeeded, outcome.to_dict()

        version = repository.latest_document_version(self.session, tenant_id, document.id)
        assert version is not None
        chunks = list(repository.list_chunks(self.session, tenant_id, version.id))
        return SimpleNamespace(
            tenant_id=tenant_id,
            document=document,
            version=version,
            chunks=chunks,
            chunk_ids=[chunk.chunk_id for chunk in chunks],
            job=job,
            outcome=outcome,
        )

    def rebuild(self, tenant_id: str) -> None:
        """整份重建索引（等价于 ``POST /ingestion/indexes/rebuild`` 的核心动作）。"""

        from services.ingestion.index_builder import rebuild_index

        rebuild_index(
            self.session,
            tenant_id=tenant_id,
            root=self.index_root,
            embedder=fake_embedding,
            embedding_model=EMBEDDING_MODEL,
            index_name=INDEX_NAME,
        )

    def set_version_status(self, tenant_id: str, version_id: str, status: str) -> None:
        repository.set_document_version_status(self.session, tenant_id, version_id, status)
        self.session.commit()

    def grant(self, tenant_id: str, document_id: str, subject_type: str, subject_id: str, permission: str = "read"):
        entry = repository.grant_document_acl(
            self.session,
            tenant_id=tenant_id,
            document_id=document_id,
            subject_type=subject_type,
            subject_id=subject_id,
            permission=permission,
        )
        self.session.commit()
        return entry

    # ------------------------------------------------------------ 断言辅助

    def manifest(self):
        manifest, _pointer, _index_file = index_manifest.load_active_manifest(self.index_root, INDEX_NAME)
        return manifest

    def auth(self, tenant_id: str, *, user_id: str = "user-1", roles=("admin",)):
        return make_auth_context(tenant_id=tenant_id, user_id=user_id, roles=roles)


def build_retrieval_app() -> FastAPI:
    """只挂检索路由的测试应用（与 ``main.py`` 的挂载前缀一致）。"""

    app = FastAPI()
    app.include_router(retrieval.router, prefix="/retrieval")
    return app


def session_factory(url: str):
    engine = db.create_db_engine(url)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)(), engine


__all__ = [
    "EMBEDDING_MODEL",
    "INDEX_NAME",
    "MOCK_TOKENIZER",
    "RetrievalEnv",
    "TENANT_A",
    "TENANT_B",
    "USER_A",
    "USER_B",
    "VECTOR_DIM",
    "alembic_upgrade",
    "build_retrieval_app",
    "fake_embedding",
    "markdown_body",
    "query_for",
    "session_factory",
    "words",
]
