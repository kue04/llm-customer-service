"""阶段 3.3 的幂等流水线与 worker 测试（批次 B6）。

覆盖范围
--------
1. **正常链路**：上传产物（对象存储里的字节 + ``pending`` 任务）→ 一路推进到
   ``published``，并逐项断言"数据真的落地了"（版本、chunk、索引、manifest、状态）；
2. **每个阶段单独故障**：8 个阶段逐一注入故障，断言
   「失败阶段 + 稳定 error_code + retry_count 累加 + 文档状态」四件事；
3. **幂等**：失败后重试、成功后重复投递 —— 断言版本数、chunk 数、chunk id 三者完全不变；
4. **重复文件**：同内容不同文件名 / 同名重传 → 不产生第二份有效版本，任务算成功；
5. **配置**：知识库覆盖生效并落库、越界**失败而非静默夹取**；
6. **警告合并**：解析警告与切分统计同住 ``metadata_json``，互不覆盖；
7. **worker**：消费、ack 时机、坏任务不影响后续任务、队列不支持消费时明确报错、
   Redis 分支的 ``XGROUP``/``XREADGROUP``/``XACK`` 参数与消费者组 id 的选择理由；
8. **生产调用点守卫（AST）**：``chunk_document`` / ``insert_chunks`` / 队列消费端
   必须有真实调用方 —— 这条守卫直接对应 F1 的教训（"定义在、测试在、没人调用 = 未完成"）。

embedding 一律用确定性假实现，**测试不下载任何模型**（与 B5 的 tokenizer mock 同源）。
"""

from __future__ import annotations

import ast
from dataclasses import replace
import logging
from pathlib import Path
import zlib

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
import pytest

from services.ingestion import db, index_manifest, models, repository
from services.ingestion import pipeline as pipeline_module
from services.ingestion import worker as worker_module
from services.ingestion.chunkers import ChunkingError
from services.ingestion.chunkers import errors as chunker_errors
from services.ingestion.index_builder import IndexBuildError
from services.ingestion.object_store import LocalObjectStore, build_object_key, logical_source_uri
from services.ingestion.parsers.base import (
    BLOCK_IMAGE,
    BLOCK_PARAGRAPH,
    Block,
    ParseWarning,
    ParserError,
    ParsedDocument,
)
from services.ingestion.pipeline import (
    CHUNKING_METADATA_KEY,
    ERROR_INVALID_CHUNKING_CONFIG,
    ERROR_OBJECT_MISSING,
    ERROR_PUBLISH_FAILED,
    ERROR_UNEXPECTED,
    STAGE_ORDER,
    WARNINGS_METADATA_KEY,
    IngestionPipeline,
)
from services.ingestion.queue import (
    InMemoryIngestionQueue,
    IngestionQueue,
    QueueError,
    QueueMessage,
    RedisStreamIngestionQueue,
    build_queue,
)
from services.ingestion.worker import IngestionWorker


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TENANT = "tenant-pipeline"
INDEX_NAME = index_manifest.DEFAULT_INDEX_NAME
EMBEDDING_MODEL = "fake-embedder-v1"
VECTOR_DIM = 16

#: 让 token 数可手算（1 个空白分隔的词 = 1 token），并让切分结果稳定
MOCK_TOKENIZER = "mock-word-v1"


# ---------------------------------------------------------------- 测试基建


def words(count: int, *, start: int = 1, prefix: str = "w") -> str:
    return " ".join(f"{prefix}{index}" for index in range(start, start + count))


def markdown_body() -> str:
    """一份结构清晰的 Markdown：标题 + 多段正文，能切出多个 chunk。"""

    return "\n\n".join(
        [
            "# 退款政策",
            words(200, start=1, prefix="退款"),
            "## 处理时限",
            words(200, start=500, prefix="时限"),
            "# 配送范围",
            words(200, start=900, prefix="配送"),
        ]
    )


def fake_embedding(text: str, *, dimension: int = VECTOR_DIM) -> list[float]:
    """确定性"词袋哈希"向量：同词共现 → 余弦相似度高。

    刻意不用 random：测试需要「相同输入 → 相同向量」，否则"检索到哪一条"会变成随机结果，
    断言只能写成"至少有一条"，那种断言守不住任何东西。
    """

    vector = [0.0] * dimension
    for token in str(text).split():
        vector[zlib.crc32(token.encode("utf-8")) % dimension] += 1.0
    if not any(vector):
        vector[0] = 1.0
    norm = sum(value * value for value in vector) ** 0.5
    return [value / norm for value in vector]


class FakeParser:
    """可编程的解析器替身：控制产物、警告与故障。"""

    def __init__(
        self,
        *,
        blocks: list[Block] | None = None,
        warnings: tuple[ParseWarning, ...] = (),
        content_hash: str | None = None,
        title: str = "测试文档",
        error: Exception | None = None,
    ) -> None:
        self.blocks = blocks
        self.warnings = warnings
        self.content_hash = content_hash
        self.title = title
        self.error = error
        self.calls = 0

    def __call__(self, source: bytes, filename: str, *, source_type: str | None = None) -> ParsedDocument:
        self.calls += 1
        if self.error is not None:
            raise self.error
        blocks = self.blocks
        if blocks is None:
            blocks = [Block(type=BLOCK_PARAGRAPH, text=words(150, prefix="解析正文"))]
        renumbered = [replace(item, order=index) for index, item in enumerate(blocks)]
        return ParsedDocument(
            title=self.title,
            blocks=tuple(renumbered),
            metadata={
                "content_hash": self.content_hash or zlib.crc32(source).to_bytes(4, "big").hex() * 8,
                "filename": filename,
                "source_type": source_type or "md",
                "block_count": len(renumbered),
            },
            warnings=self.warnings,
            source_type=source_type or "md",
            parser_name="fake_parser",
            parser_version="9.9",
        )


def fail_at(monkeypatch, target: str, error: Exception, *, module=pipeline_module) -> None:
    """把某个函数替换成"抛指定异常"（用于逐阶段注入故障）。"""

    def _boom(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(module, target, _boom)


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'pipeline.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = db.create_db_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


@pytest.fixture
def store(tmp_path):
    return LocalObjectStore(tmp_path / "objects")


@pytest.fixture
def index_root(tmp_path):
    return tmp_path / "faiss_store"


class Harness:
    """把「上传后」的状态准备好，并提供跑流水线的便捷入口。"""

    def __init__(self, session, store, index_root, *, parser=None, chunker=None, index_runner=None):
        self.session = session
        self.store = store
        self.index_root = index_root
        self.parser = parser
        self.chunker = chunker
        self.index_runner = index_runner
        repository.create_tenant(session, tenant_id=TENANT, name="流水线租户")
        self.session.commit()

    # -------------------------------------------------------- 准备

    def knowledge_base(self, *, overrides=None, slug="kb-pipeline"):
        kb = repository.get_knowledge_base_by_slug(self.session, TENANT, slug)
        if kb is None:
            kb = repository.create_knowledge_base(
                self.session,
                tenant_id=TENANT,
                slug=slug,
                chunking_config_json=overrides if overrides is not None else {"tokenizer_id": MOCK_TOKENIZER},
            )
            self.session.commit()
        return kb

    def upload(self, *, filename="客服手册.md", data: bytes | None = None, document=None, kb=None):
        """模拟上传接口的持久化部分：put 字节 → 建文档（按需）→ 建 pending 任务。"""

        knowledge_base = kb or self.knowledge_base()
        payload = data if data is not None else markdown_body().encode("utf-8")
        source_uri = logical_source_uri(filename)
        if document is None:
            document = repository.get_document_by_source_uri(self.session, TENANT, source_uri)
        if document is None:
            document = repository.create_document(
                self.session,
                tenant_id=TENANT,
                knowledge_base_id=knowledge_base.id,
                source_uri=source_uri,
                source_type="md",
                title=filename,
            )
        key = build_object_key(TENANT, document.id, filename)
        self.store.put(key, payload)
        job = repository.create_ingestion_job(
            self.session, tenant_id=TENANT, document_id=document.id, status="pending", stage="received"
        )
        self.session.commit()
        return job, document

    def run(self, job, **overrides):
        options = {
            "parser": self.parser,
            "chunker": self.chunker,
            "index_runner": self.index_runner,
            "embedder": fake_embedding,
            "embedding_model": EMBEDDING_MODEL,
            "index_root": self.index_root,
        }
        options.update(overrides)
        return IngestionPipeline(session=self.session, store=self.store, **options).process_job(
            tenant_id=TENANT, job_id=job.id
        )

    def run_to_success(self, **kwargs):
        job, document = self.upload(**kwargs)
        outcome = self.run(job)
        assert outcome.succeeded, outcome.to_dict()
        return job, document, outcome

    # -------------------------------------------------------- 断言辅助

    def versions(self, document):
        return list(repository.list_document_versions(self.session, TENANT, document.id))

    def chunks_of(self, version):
        return list(repository.list_chunks(self.session, TENANT, version.id))

    def active_builds(self):
        return list(
            repository.list_index_builds(self.session, TENANT, INDEX_NAME, statuses=("active",))
        )

    def manifest(self):
        manifest, _pointer, _path = index_manifest.load_active_manifest(self.index_root, INDEX_NAME)
        return manifest


@pytest.fixture
def harness(db_session, store, index_root):
    return Harness(db_session, store, index_root)


# ================================================================ 1. 正常链路


class TestHappyPath:
    def test_upload_is_taken_all_the_way_to_published(self, harness):
        job, document, outcome = harness.run_to_success()

        assert outcome.stage == "published"
        assert outcome.status == "succeeded"
        assert outcome.chunk_count > 0
        assert outcome.index_switched is True

        # 任务记录
        fresh_job = repository.get_ingestion_job(harness.session, TENANT, job.id)
        assert (fresh_job.status, fresh_job.stage) == ("succeeded", "published")
        assert fresh_job.document_version_id == outcome.document_version_id
        assert fresh_job.error_code is None

        # 版本与文档状态
        version = repository.get_document_version(
            harness.session, TENANT, outcome.document_version_id
        )
        assert version.status == "published"
        assert version.parser_name == "markdown" or version.parser_name
        assert document.status == "published"

        # chunk 真的落库了，且数量与 manifest 一致
        rows = harness.chunks_of(version)
        assert len(rows) == outcome.chunk_count
        assert {row.chunk_id for row in rows} == set(harness.manifest().chunk_ids)

    def test_effective_config_and_stats_are_recorded(self, harness):
        _job, _document, outcome = harness.run_to_success()

        version = repository.get_document_version(harness.session, TENANT, outcome.document_version_id)
        payload = version.metadata_json[CHUNKING_METADATA_KEY]
        # 知识库覆盖生效（默认 tokenizer 是 heuristic-zh-v1，本 KB 覆盖成 mock）
        assert payload["tokenizer_id"] == MOCK_TOKENIZER
        assert payload["config_version"]
        assert payload["stats"]["child_count"] == len(
            [row for row in harness.chunks_of(version) if row.parent_chunk_id]
        )
        assert version.metadata_json["parser"]["name"]
        assert isinstance(version.metadata_json[WARNINGS_METADATA_KEY], list)

    def test_every_stage_is_logged_in_order(self, harness, caplog):
        caplog.set_level(logging.INFO, logger="services.ingestion.pipeline")
        # fixture 里的 ``alembic upgrade`` 会通过 alembic.ini 的日志配置调用
        # ``logging.config.fileConfig``，其默认值 ``disable_existing_loggers=True``
        # 会把**当时已存在的 logger** 全部置为 disabled —— 本模块的 logger 在
        # 收集测试时就已经创建，因此会被静默禁掉（现象是 caplog 收到 0 条记录）。
        # 这条不是测试写法问题，而是"跑一次迁移就能让全进程日志消失"的真实坑。
        logging.getLogger("services.ingestion.pipeline").disabled = False

        job, _document = harness.upload()
        assert harness.run(job).succeeded

        seen = [
            record.getMessage().split("stage=")[1].split()[0]
            for record in caplog.records
            if "stage=" in record.getMessage()
        ]
        assert seen == list(STAGE_ORDER)

    def test_index_build_row_is_active_and_carries_manifest_uri(self, harness):
        harness.run_to_success()

        builds = harness.active_builds()
        assert len(builds) == 1
        assert builds[0].chunk_count == harness.manifest().chunk_count
        assert builds[0].manifest_uri.endswith("index_manifest.json")
        assert builds[0].embedding_model == EMBEDDING_MODEL

    def test_chunks_keep_tenant_acl_and_ordinal_in_metadata(self, harness):
        _job, document, outcome = harness.run_to_success()
        version = repository.get_document_version(harness.session, TENANT, outcome.document_version_id)

        rows = harness.chunks_of(version)
        for row in rows:
            assert row.metadata_json["tenant_id"] == TENANT
            assert row.metadata_json["document_id"] == document.id
            assert isinstance(row.metadata_json["ordinal"], int)
            assert "acl" in row.metadata_json

        ordinals = sorted(row.metadata_json["ordinal"] for row in rows)
        assert ordinals == list(range(len(rows)))


# ================================================================ 2. 逐阶段故障


class TestStageFailures:
    def test_stored_stage_fails_when_object_is_gone(self, harness):
        job, document = harness.upload()
        key = build_object_key(TENANT, document.id, "客服手册.md")
        harness.store.delete(key)

        outcome = harness.run(job)

        assert outcome.failed and outcome.stage == "stored"
        assert outcome.error_code == ERROR_OBJECT_MISSING
        assert harness.versions(document) == []

    def test_parsed_stage_fails_when_parser_rejects_the_file(self, harness):
        harness.parser = FakeParser(error=ParserError("parse_failed", "解析失败：文件已损坏"))
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.stage == "parsed"
        assert outcome.error_code == "parse_failed"
        assert harness.versions(document) == []

    def test_normalized_stage_fails_on_invalid_knowledge_base_config(self, harness):
        harness.knowledge_base(overrides={"max_tokens": -5})
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.stage == "normalized"
        assert outcome.error_code == ERROR_INVALID_CHUNKING_CONFIG
        assert "max_tokens" in outcome.error_message
        # 版本已建（parsed 阶段），但没有生效配置留痕 —— 越界没被静默夹取成合法值
        versions = harness.versions(document)
        assert len(versions) == 1
        assert CHUNKING_METADATA_KEY not in (versions[0].metadata_json or {})

    def test_chunked_stage_fails_when_chunker_raises(self, harness):
        def broken_chunker(*_args, **_kwargs):
            raise ChunkingError(
                chunker_errors.ERROR_INTERNAL, "切分失败：内部状态不一致"
            )

        harness.chunker = broken_chunker
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.stage == "chunked"
        assert outcome.error_code == chunker_errors.ERROR_INTERNAL
        assert repository.count_chunks(harness.session, TENANT, harness.versions(document)[0].id) == 0

    def test_persisted_stage_fails_and_leaves_no_partial_chunks(self, harness, monkeypatch):
        def broken_insert(*_args, **_kwargs):
            raise IntegrityError("insert", {}, Exception("uq_document_chunks 冲突"))

        monkeypatch.setattr(pipeline_module.repository, "insert_chunks", broken_insert)
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.stage == "persisted"
        assert outcome.error_code == ERROR_UNEXPECTED
        assert repository.count_chunks(harness.session, TENANT, harness.versions(document)[0].id) == 0

    def test_indexed_stage_fails_when_embedding_backend_is_unavailable(self, harness):
        def broken_index(*_args, **_kwargs):
            raise IndexBuildError("embedding_unavailable", "向量化失败：模型未安装")

        harness.index_runner = broken_index
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.stage == "indexed"
        assert outcome.error_code == "embedding_unavailable"
        # chunk 已经落库（persisted 在前），但版本没有进入 indexed/published
        version = harness.versions(document)[0]
        assert version.status == "chunked"
        assert repository.count_chunks(harness.session, TENANT, version.id) > 0
        # 没有生效索引：失败不得写入指针
        with pytest.raises(index_manifest.IndexManifestError):
            index_manifest.read_pointer(harness.index_root, INDEX_NAME)

    def test_published_stage_failure_is_recorded_with_its_own_code(self, harness, monkeypatch):
        original = pipeline_module.repository.set_document_version_status

        def broken_publish(session, tenant_id, version_id, status):
            if status == "published":
                raise RuntimeError("数据库连接中断")
            return original(session, tenant_id, version_id, status)

        monkeypatch.setattr(pipeline_module.repository, "set_document_version_status", broken_publish)
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.stage == "published"
        assert outcome.error_code == ERROR_PUBLISH_FAILED
        # 索引已经切换（indexed 在前），版本停在上一个阶段状态
        version = harness.versions(document)[0]
        assert version.status == "indexed"

    @pytest.mark.parametrize(
        ("stage", "expected_code"),
        [
            ("stored", ERROR_OBJECT_MISSING),
            ("parsed", "parse_failed"),
            ("normalized", ERROR_INVALID_CHUNKING_CONFIG),
            ("chunked", chunker_errors.ERROR_INTERNAL),
            ("persisted", ERROR_UNEXPECTED),
            ("indexed", "embedding_unavailable"),
            ("published", ERROR_PUBLISH_FAILED),
        ],
    )
    def test_failure_marks_job_status_stage_and_retry_count(
        self, harness, monkeypatch, stage, expected_code
    ):
        """一张真值表驱动七个阶段的故障表现（避免只抽样测一两个阶段）。"""

        if stage == "stored":
            job, document = harness.upload()
            harness.store.delete(build_object_key(TENANT, document.id, "客服手册.md"))
        elif stage == "parsed":
            harness.parser = FakeParser(error=ParserError("parse_failed", "坏了"))
            job, _document = harness.upload()
        elif stage == "normalized":
            harness.knowledge_base(overrides={"max_tokens": 0})
            job, _document = harness.upload()
        elif stage == "chunked":
            def broken_chunker(*_a, **_k):
                raise ChunkingError(chunker_errors.ERROR_INTERNAL, "坏了")

            harness.chunker = broken_chunker
            job, _document = harness.upload()
        elif stage == "persisted":
            monkeypatch.setattr(
                pipeline_module.repository,
                "insert_chunks",
                lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
            )
            job, _document = harness.upload()
        elif stage == "indexed":
            harness.index_runner = lambda *_a, **_k: (_ for _ in ()).throw(
                IndexBuildError("embedding_unavailable", "模型缺失")
            )
            job, _document = harness.upload()
        else:
            original = pipeline_module.repository.set_document_version_status

            def broken_publish(session, tenant_id, version_id, status):
                if status == "published":
                    raise RuntimeError("boom")
                return original(session, tenant_id, version_id, status)

            monkeypatch.setattr(
                pipeline_module.repository, "set_document_version_status", broken_publish
            )
            job, _document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed
        assert outcome.stage == stage
        assert outcome.error_code == expected_code
        fresh = repository.get_ingestion_job(harness.session, TENANT, job.id)
        assert (fresh.status, fresh.stage, fresh.error_code) == ("failed", stage, expected_code)
        assert fresh.retry_count == 1
        assert fresh.error_message

    def test_document_is_marked_failed_only_when_no_published_version_exists(self, harness):
        harness.index_runner = lambda *_a, **_k: (_ for _ in ()).throw(
            IndexBuildError("embedding_unavailable", "模型缺失")
        )
        job, document = harness.upload()
        assert harness.run(job).failed

        assert repository.get_document(harness.session, TENANT, document.id).status == "failed"


# ================================================================ 3. 幂等


class TestIdempotency:
    def test_retry_from_failed_stage_completes_without_second_version(self, harness):
        broken = {"on": True}

        def flaky_index(*args, **kwargs):
            if broken["on"]:
                raise IndexBuildError("embedding_unavailable", "模型暂时不可用")
            from services.ingestion.index_builder import rebuild_index

            return rebuild_index(*args, **kwargs)

        harness.index_runner = flaky_index
        job, document = harness.upload()

        first = harness.run(job)
        assert first.failed and first.stage == "indexed"
        assert len(harness.versions(document)) == 1
        version_id = harness.versions(document)[0].id
        chunk_ids_before = {row.chunk_id for row in harness.chunks_of(harness.versions(document)[0])}
        assert chunk_ids_before

        broken["on"] = False
        second = harness.run(job)

        assert second.succeeded, second.to_dict()
        assert second.resumed_from == "indexed"
        versions = harness.versions(document)
        assert len(versions) == 1 and versions[0].id == version_id
        chunk_ids_after = {row.chunk_id for row in harness.chunks_of(versions[0])}
        assert chunk_ids_after == chunk_ids_before

    def test_redelivering_a_finished_job_changes_nothing(self, harness):
        job, document, _outcome = harness.run_to_success()
        version = harness.versions(document)[0]
        before = sorted(row.chunk_id for row in harness.chunks_of(version))

        again = harness.run(job)

        assert again.succeeded
        versions = harness.versions(document)
        assert len(versions) == 1
        assert sorted(row.chunk_id for row in harness.chunks_of(versions[0])) == before

    def test_chunk_ids_are_stable_across_reruns(self, harness):
        job, document = harness.upload()
        assert harness.run(job).succeeded
        version = harness.versions(document)[0]
        first = sorted(row.chunk_id for row in harness.chunks_of(version))

        for _ in range(2):
            assert harness.run(job).succeeded

        assert sorted(row.chunk_id for row in harness.chunks_of(version)) == first


# ================================================================ 4. 重复文件


class TestDuplicateContent:
    def test_same_content_under_another_name_is_marked_duplicate(self, harness):
        _job_a, _document_a, _outcome_a = harness.run_to_success(filename="原始手册.md")

        job_b, document_b = harness.upload(filename="副本手册.md")
        outcome_b = harness.run(job_b)

        assert outcome_b.duplicate is True
        assert outcome_b.status == "succeeded"
        assert outcome_b.stage == "parsed"
        assert outcome_b.chunk_count == 0
        # 新文档自己没有内容 → 标 duplicate；也没有产生第二份版本
        assert repository.get_document(harness.session, TENANT, document_b.id).status == "duplicate"
        assert harness.versions(document_b) == []
        assert len(repository.list_indexable_chunks(harness.session, TENANT)) > 0

    def test_reuploading_the_same_file_reuses_document_without_new_version(self, harness):
        job_a, document_a, _outcome = harness.run_to_success()
        assert len(harness.versions(document_a)) == 1

        job_b, document_b = harness.upload(document=document_a)
        outcome_b = harness.run(job_b)

        assert document_b.id == document_a.id
        assert outcome_b.duplicate is True
        assert len(harness.versions(document_a)) == 1
        # 文档保持已发布状态（重复上传不该把它降级）
        assert repository.get_document(harness.session, TENANT, document_a.id).status == "published"


# ================================================================ 5. 配置


class TestChunkingConfig:
    def test_knowledge_base_override_is_applied_and_persisted(self, harness):
        # target < max 是配置的跨字段约束（B5 定的），因此这里给一组合法覆盖值
        harness.knowledge_base(overrides={"tokenizer_id": MOCK_TOKENIZER, "target_tokens": 300, "max_tokens": 600})
        job, document = harness.upload()
        outcome = harness.run(job)
        assert outcome.succeeded

        version = harness.versions(document)[0]
        payload = version.metadata_json[CHUNKING_METADATA_KEY]
        assert payload["target_tokens"] == 300
        assert payload["max_tokens"] == 600
        assert payload["min_tokens"] == 120  # 未覆盖的项保持默认
        assert payload["tokenizer_id"] == MOCK_TOKENIZER

    def test_invalid_override_fails_instead_of_clamping(self, harness):
        harness.knowledge_base(overrides={"target_tokens": 5000, "max_tokens": 100})
        job, _document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.error_code == ERROR_INVALID_CHUNKING_CONFIG
        assert "target_tokens" in outcome.error_message

    def test_unknown_override_key_fails(self, harness):
        harness.knowledge_base(overrides={"max_token": 300})  # 少一个 s
        job, _document = harness.upload()

        outcome = harness.run(job)

        assert outcome.failed and outcome.error_code == ERROR_INVALID_CHUNKING_CONFIG
        assert "max_token" in outcome.error_message


# ================================================================ 6. 警告与空文档


class TestWarningsAndEmptyDocuments:
    def test_parse_warnings_survive_chunking(self, harness):
        warning = ParseWarning(code="no_text_layer", message="文本层不足", detail={"page": 1})
        harness.parser = FakeParser(
            blocks=[Block(type=BLOCK_PARAGRAPH, text=words(150, prefix="正文"))],
            warnings=(warning,),
        )
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.succeeded
        version = harness.versions(document)[0]
        codes = [item["code"] for item in version.metadata_json[WARNINGS_METADATA_KEY]]
        assert codes == ["no_text_layer"]
        # 警告与切分统计共存，互不覆盖
        assert version.metadata_json[CHUNKING_METADATA_KEY]["stats"]["child_count"] > 0
        assert [item["code"] for item in outcome.warnings] == ["no_text_layer"]

    def test_empty_document_publishes_without_an_index(self, harness):
        harness.parser = FakeParser(blocks=[Block(type=BLOCK_IMAGE, image_ref="a.png")])
        job, document = harness.upload()

        outcome = harness.run(job)

        assert outcome.succeeded, outcome.to_dict()
        assert outcome.chunk_count == 0
        assert outcome.index_switched is False
        version = harness.versions(document)[0]
        assert version.status == "published"
        with pytest.raises(index_manifest.IndexManifestError):
            index_manifest.read_pointer(harness.index_root, INDEX_NAME)
        assert harness.active_builds() == []


# ================================================================ 7. worker


class RecordingQueue(InMemoryIngestionQueue):
    """记录 ack 的内存队列（验证 [D-10] 的 ack 时机）。"""

    def __init__(self) -> None:
        super().__init__()
        self.acked: list[str] = []

    def ack(self, message_id: str) -> None:
        self.acked.append(message_id)


class _QueueWithoutConsumption(IngestionQueue):
    """只实现投递的队列：验证默认 ``read`` 是**明确报错**而不是静默空转。"""

    name = "publish-only"

    def publish(self, job_id: str) -> str:
        return "0-1"

    def depth(self) -> int:
        return 0


class _FakeRedis:
    def __init__(self, *, read_response=None, group_error: Exception | None = None) -> None:
        self.read_response = read_response
        self.group_error = group_error
        self.calls: list[tuple] = []
        self.group_created = 0

    def xgroup_create(self, stream_key, group, **kwargs):
        self.calls.append(("xgroup_create", stream_key, group, kwargs))
        if self.group_error is not None:
            raise self.group_error
        self.group_created += 1
        return b"OK"

    def xreadgroup(self, group, consumer, streams, **kwargs):
        self.calls.append(("xreadgroup", group, consumer, streams, kwargs))
        return self.read_response

    def xack(self, stream_key, group, message_id):
        self.calls.append(("xack", stream_key, group, message_id))
        return 1


class TestWorker:
    def _worker(self, harness, queue):
        return IngestionWorker(
            queue=queue,
            session_factory=_factory_for(harness.session),
            store=harness.store,
            pipeline_options={
                "embedder": fake_embedding,
                "embedding_model": EMBEDDING_MODEL,
                "index_root": harness.index_root,
            },
        )

    def test_message_is_consumed_and_job_reaches_published(self, harness):
        queue = RecordingQueue()
        job, _document = harness.upload()
        queue.publish(job.id)

        outcomes = self._worker(harness, queue).run_once()

        assert len(outcomes) == 1 and outcomes[0].succeeded
        assert queue.depth() == 0  # 消息被取走
        assert len(queue.acked) == 1  # 处理完即 ack

    def test_empty_queue_is_not_an_error(self, harness):
        assert self._worker(harness, RecordingQueue()).run_once() == []

    def test_unknown_job_is_skipped_and_acked(self, harness):
        queue = RecordingQueue()
        queue.publish("no-such-job")

        outcomes = self._worker(harness, queue).run_once()

        assert outcomes[0].skipped and outcomes[0].skip_reason == "job_not_found"
        assert len(queue.acked) == 1

    def test_one_broken_job_does_not_block_the_next_one(self, harness):
        queue = RecordingQueue()
        broken_job, broken_document = harness.upload(filename="坏文件.md")
        harness.store.delete(build_object_key(TENANT, broken_document.id, "坏文件.md"))
        good_job, _good_document = harness.upload(filename="好文件.md")
        queue.publish(broken_job.id)
        queue.publish(good_job.id)

        outcomes = self._worker(harness, queue).run_once(limit=2)

        assert [item.status for item in outcomes] == ["failed", "succeeded"]
        assert outcomes[0].error_code == ERROR_OBJECT_MISSING
        assert len(queue.acked) == 2

    def test_unexpected_exception_is_contained_and_acked(self, harness, monkeypatch):
        queue = RecordingQueue()
        job, _document = harness.upload()
        queue.publish(job.id)
        monkeypatch.setattr(
            worker_module, "process_job", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        outcomes = self._worker(harness, queue).run_once()

        assert outcomes[0].failed and outcomes[0].error_code == ERROR_UNEXPECTED
        assert len(queue.acked) == 1

    def test_run_forever_stops_after_requested_iterations(self, harness):
        stats = self._worker(harness, RecordingQueue()).run_forever(iterations=2, poll_interval=0.0)

        assert stats.consumed == 0
        assert stats.to_dict() == {
            "consumed": 0,
            "succeeded": 0,
            "failed": 0,
            "duplicates": 0,
            "skipped": 0,
        }

    def test_queue_without_consumption_support_fails_loudly(self, harness):
        with pytest.raises(QueueError, match="不支持消费"):
            self._worker(harness, _QueueWithoutConsumption()).run_once()

    def test_redis_consumer_group_is_created_from_zero(self, harness):
        """``id="0"`` 而不是 ``"$"``：否则建组之前投递的消息永远没人处理。"""

        client = _FakeRedis(read_response=[[b"rag:ingestion:jobs", [(b"1-1", {"job_id": "job-1"})]]])
        queue = RedisStreamIngestionQueue("redis://127.0.0.1:6379/0", client=client)

        messages = queue.read(count=1)

        assert messages == [QueueMessage(message_id="1-1", job_id="job-1")]
        create_call = next(item for item in client.calls if item[0] == "xgroup_create")
        assert create_call[3]["id"] == "0"
        assert create_call[3]["mkstream"] is True

    def test_redis_busygroup_is_treated_as_success(self, harness):
        client = _FakeRedis(group_error=RuntimeError("BUSYGROUP Consumer Group name already exists"))
        queue = RedisStreamIngestionQueue("redis://127.0.0.1:6379/0", client=client)

        queue.ensure_group()  # 不抛异常即通过
        assert client.group_created == 0

    def test_redis_messages_without_job_id_are_dropped(self):
        client = _FakeRedis(read_response=[[b"stream", [(b"1-1", {"other": "x"})]]])
        queue = RedisStreamIngestionQueue("redis://127.0.0.1:6379/0", client=client)

        assert queue.read(count=1) == []

    def test_redis_ack_uses_stream_group_and_message_id(self):
        client = _FakeRedis()
        queue = RedisStreamIngestionQueue("redis://127.0.0.1:6379/0", client=client)

        queue.ack("1-1")

        assert client.calls[-1] == ("xack", "rag:ingestion:jobs", "rag-workers", "1-1")

    def test_build_queue_falls_back_to_memory_without_redis_url(self):
        assert isinstance(build_queue({}), InMemoryIngestionQueue)


def _factory_for(session):
    """把已有会话的绑定引擎包成 sessionmaker，供 worker 开独立会话。"""

    return sessionmaker(bind=session.get_bind(), expire_on_commit=False, future=True)


# ================================================================ 8. 生产调用点守卫


class TestProductionCallPoints:
    """F1 的教训：**定义在、测试在、没人调用 = 未完成**。

    这里用 AST 断言三条接线（B6 门禁明确要求
    ``chunk_document()`` / ``insert_chunks()`` / 队列消费端从"已建未启用"表里消失）。

    用 AST 而不是 grep：注释与 docstring 里提到函数名不该算作调用（踩坑记录 D1）。
    检查的是**引用**（``Name`` / ``Attribute``）而不是仅 ``Call`` ——
    因为流水线是把 ``chunk_document`` / ``parse_document`` 作为**默认实现注入**的
    （``self.chunker = chunker or chunk_document``），那确实是生产调用点，
    只是不在本文件的语法树里直接以调用形式出现。
    """

    @staticmethod
    def _referenced_names(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
        return names

    @staticmethod
    def _called_names(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
        return names

    def test_pipeline_references_the_chunker_the_parser_and_insert_chunks(self):
        names = self._referenced_names(PROJECT_ROOT / "services" / "ingestion" / "pipeline.py")

        assert "chunk_document" in names
        assert "parse_document" in names
        assert "insert_chunks" in names
        # 真的被调用（而不是只写在 import 行里）
        assert {"insert_chunks"} <= self._called_names(
            PROJECT_ROOT / "services" / "ingestion" / "pipeline.py"
        )

    def test_pipeline_builds_the_index(self):
        path = PROJECT_ROOT / "services" / "ingestion" / "pipeline.py"

        assert "rebuild_index" in self._referenced_names(path)

    def test_queue_has_a_consumer_side(self):
        attributes = self._referenced_names(PROJECT_ROOT / "services" / "ingestion" / "queue.py")

        assert "xreadgroup" in attributes
        assert "xack" in attributes
        assert "xgroup_create" in attributes

    def test_worker_drives_the_queue_consumer_and_the_pipeline(self):
        names = self._referenced_names(PROJECT_ROOT / "services" / "ingestion" / "worker.py")
        called = self._called_names(PROJECT_ROOT / "services" / "ingestion" / "worker.py")

        assert {"read", "ack", "process_job"} <= names
        assert {"read", "ack", "process_job"} <= called

    def test_retriever_exposes_the_filtered_chunk_index_entry(self):
        names = self._referenced_names(PROJECT_ROOT / "utils" / "vector_retriever.py")

        assert "load_active_manifest" in names
        assert "IDSelectorBatch" in names


# ================================================================ 9. 一致性守卫


class TestContractGuards:
    def test_stage_order_matches_the_database_check_constraint(self):
        assert STAGE_ORDER == models.INGESTION_STAGES

    def test_pipeline_does_not_read_tenant_from_the_environment(self):
        """归属信息只能来自数据库里的任务/文档行，不能来自环境变量或请求头。

        与解析器 / 切分器的守卫同源（[D-5] / [D-9]）：一旦允许"从环境里取租户"，
        多租户隔离就退化成"当前离线配了哪个租户"。
        """

        tree = ast.parse(
            (PROJECT_ROOT / "services" / "ingestion" / "pipeline.py").read_text(encoding="utf-8")
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                assert name not in ("getenv", "environ"), "流水线不得从环境变量读取归属信息"
