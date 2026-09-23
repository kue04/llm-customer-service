"""幂等处理流水线（计划 3.3）。

固定状态流转（计划原文）
------------------------
``received -> stored -> parsed -> normalized -> chunked -> persisted -> indexed -> published``

每个阶段的职责（计划只给了阶段名，这里把边界定死并写进测试）
------------------------------------------------------------
=========================  =====================================================
``received``               任务已入库（上传接口建 job 后投递队列）
``stored``                 确认对象存储里能取到字节（上传时已 put，这里只做**存在性校验**）
``parsed``                 解析字节 → ``ParsedDocument``；内容 hash 判重；**创建 document_version**
``normalized``             解析产物 → 切分输入：解析生效切分配置（知识库覆盖）+ 装配 ``ChunkContext``
``chunked``                调切分器得到 ``ChunkingResult``（**不写库**）
``persisted``              落库 ``document_chunks``（重跑先删后写）
``indexed``                重建并**原子切换**向量索引（3.4）
``published``              版本与文档置 ``published``、任务置 ``succeeded``
=========================  =====================================================

为什么 ``normalized`` 阶段不自己做清洗
-------------------------------------
清洗（去空白 / 页码行 / 重复页眉页脚 / 完全重复段落）在 B5 已作为**切分器内部**的一步
交付，并带完整计数。若流水线再清一遍，同一件事会有两个实现：清洗规则一改，
两处结果就可能不同，而"少了一段内容"这种差异几乎无法从结果看出来。
因此 ``normalized`` 的定义是**归一化「输入」**（配置 + 归属上下文 + 留痕），
不是再洗一遍正文 —— 具体动作见 ``_stage_normalized``。

「幂等重试」在本实现里的准确含义
--------------------------------
计划要求「支持从失败步骤幂等重试」。实现上**不把 ``ParsedDocument`` 落盘做断点续传**，
而是让每个阶段在"重试"时处于两种模式之一：

* **准备模式**（该阶段在失败前已成功过）：**重算但不重写**。
  解析 / 切分是纯函数、确定性，重算只花 CPU；一旦让它们数据库写入，
  「重试」就会变成「第二次建版本 / 第二次插 chunk」，这才是真正会坏数据的地方。
* **执行模式**（从失败所在阶段起）：正常执行并落库。

于是「重试 N 次」与「执行 1 次」的**最终状态与数据完全相同**（有测试逐条断言：
版本数、chunk 数、chunk id 三者不变）。不可重复的副作用另有守卫：
``document_versions`` 只在 ``job.document_version_id`` 为空时创建
（重复创建会撞 ``uq(tenant_id, content_hash)``）；chunk 落库是「先删后写」；
索引重建是「整份重建 + 原子切换」；发布只是把状态置为 ``published``。

为什么不做断点续传：那需要为中间产物设计一套持久格式 + 生命周期 + 清理策略，
而它带来的收益只是"省一次解析"。用一次纯函数重算换取"少一套状态机"，是划算的。

每一个阶段结束时提交
--------------------
``job.stage`` 要能被 ``GET /ingestion-jobs/{job_id}`` 现场看到，所以阶段之间不是单一事务。
代价是"阶段之间可能失败"，这正是每个阶段都必须幂等的原因（两者是一件事的两面，不是两个缺陷）。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from config.chunking_config import ChunkConfig, ChunkConfigError, build_config
from services.ingestion import index_builder, models, parser_registry, repository
from services.ingestion.chunkers import (
    AclEntry,
    ChunkContext,
    ChunkingError,
    ChunkingResult,
    chunk_document,
)
from services.ingestion.index_builder import IndexBuildError, IndexBuildResult
from services.ingestion.index_manifest import DEFAULT_INDEX_NAME, IndexManifestError
from services.ingestion.object_store import ObjectStore, build_object_key, filename_from_source_uri
from services.ingestion.parsers.base import ParserError, ParsedDocument


logger = logging.getLogger(__name__)


#: 计划给定的固定状态流转（顺序即契约，测试按它参数化）
STAGE_ORDER: tuple[str, ...] = (
    "received",
    "stored",
    "parsed",
    "normalized",
    "chunked",
    "persisted",
    "indexed",
    "published",
)

#: 版本/文档在 ``published`` 阶段落到这个状态
STATUS_PUBLISHED = "published"
STATUS_DUPLICATE = "duplicate"
STATUS_FAILED = "failed"

#: 错误码（可直落 ``ingestion_jobs.error_code``）
ERROR_OBJECT_MISSING = "object_missing"
ERROR_SOURCE_URI_INVALID = "source_uri_invalid"
ERROR_MISSING_CONTENT_HASH = "missing_content_hash"
ERROR_INVALID_CHUNKING_CONFIG = "invalid_chunking_config"
ERROR_INVALID_DOCUMENT_ACL = "invalid_document_acl"
ERROR_PUBLISH_FAILED = "publish_failed"
ERROR_UNEXPECTED = "unexpected_error"

#: 解析警告在 ``document_versions.metadata_json`` 里的固定键（与 B4 的读取端一致）
WARNINGS_METADATA_KEY = "warnings"

#: 生效切分配置 + 统计的固定键
CHUNKING_METADATA_KEY = "chunking"


class PipelineError(RuntimeError):
    """流水线里可预期的失败（``error_code`` 直接写进任务记录）。"""

    def __init__(self, error_code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        self.error_code = error_code
        self.message = message
        self.detail: dict[str, Any] = dict(detail or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    """一次 ``process_job`` 的结果。**失败不抛异常**，而是以 ``status='failed'`` 返回。

    为什么失败不抛：worker 要连续处理一批任务，一个坏文件抛异常会把整轮消费打断；
    而失败信息本身是**业务数据**（要写进 ``ingestion_jobs`` 给用户看），
    不是程序缺陷信号。真正不可预期的异常也会被转成 ``unexpected_error`` 落到任务上，
    并保留原始 traceback 到日志里。
    """

    job_id: str
    tenant_id: str
    document_id: str = ""
    stage: str = ""
    status: str = ""
    document_version_id: str = ""
    document_version: int = 0
    chunk_count: int = 0
    index_version: int = 0
    index_switched: bool = False
    duplicate: bool = False
    warnings: tuple[dict[str, Any], ...] = ()
    resumed_from: str = ""
    skipped: bool = False
    skip_reason: str = ""
    error_code: str = ""
    error_message: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "stage": self.stage,
            "status": self.status,
            "document_version_id": self.document_version_id,
            "document_version": self.document_version,
            "chunk_count": self.chunk_count,
            "index_version": self.index_version,
            "index_switched": self.index_switched,
            "duplicate": self.duplicate,
            "warnings": [dict(item) for item in self.warnings],
            "resumed_from": self.resumed_from,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


@dataclass
class _RunState:
    """阶段之间传递的中间产物（内存态，不落盘 —— 理由见模块 docstring）。"""

    document: models.Document
    filename: str
    parsed: ParsedDocument | None = None
    config: ChunkConfig | None = None
    context: ChunkContext | None = None
    chunking: ChunkingResult | None = None
    version: models.DocumentVersion | None = None
    index_result: IndexBuildResult | None = None
    warnings: tuple[dict[str, Any], ...] = ()
    duplicate_of: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def default_embedder(text: str) -> Sequence[float]:
    """生产默认向量化实现（惰性 import，缺模型时抛 :class:`IndexBuildError`）。

    ``utils.vector_retriever`` 一被 import 就会尝试 import faiss / sentence-transformers
    （有 try/except 兜底），放在函数里是为了**只在真的要建索引时**付出这个代价 ——
    队列 worker 在启动阶段不应该因为模型文件而变慢或变脆。
    """

    from utils.vector_retriever import build_embedding

    return build_embedding(text)


def default_embedding_model() -> str:
    """生产默认 embedding 模型标识（写进 manifest，检索时必须与之一致）。"""

    from config.rag_config import get_rag_config

    return get_rag_config().embedding_model_name


def default_index_root() -> Path:
    """索引根目录：沿用既有 FAISS 向量库目录（``data/faiss_store``）。"""

    from config.rag_config import get_rag_config

    return Path(get_rag_config().faiss_store_dir)


class IngestionPipeline:
    """把「一次上传」推进到「可检索」的流水线。

    依赖全部以参数注入（``store`` / ``parser`` / ``chunker`` / ``index_runner`` / ``embedder``），
    因此测试可以：① 用内存对象存储与假 embedding；
    ② 在**任意一个阶段**注入故障来验证「失败留痕 + 从该阶段幂等重试」。
    """

    def __init__(
        self,
        *,
        session: Session,
        store: ObjectStore,
        parser: Callable[..., ParsedDocument] | None = None,
        chunker: Callable[..., ChunkingResult] | None = None,
        index_runner: Callable[..., IndexBuildResult] | None = None,
        embedder: Callable[[str], Sequence[float]] | None = None,
        embedding_model: str = "",
        index_root: str | Path | None = None,
        index_name: str = DEFAULT_INDEX_NAME,
    ) -> None:
        self.session = session
        self.store = store
        self.parser = parser or parser_registry.parse_document
        self.chunker = chunker or chunk_document
        self.index_runner = index_runner or index_builder.rebuild_index
        self.embedder = embedder
        self.embedding_model = embedding_model
        self.index_root = Path(index_root) if index_root is not None else None
        self.index_name = index_name

    # ------------------------------------------------------------ 入口

    def process_job(self, *, tenant_id: str, job_id: str) -> PipelineOutcome:
        """推进一个任务直到终态。**不抛业务异常**（见 :class:`PipelineOutcome`）。"""

        job = repository.get_ingestion_job(self.session, tenant_id, job_id)
        if job is None:
            # 跨租户 / 已被删除：对 worker 来说这只是"这条消息没活干"，
            # 但不能静默 —— 用结构化日志留痕（可能是越权探测，也可能是消息过期）。
            logger.warning(
                "[ingestion] job_not_found job=%s tenant=%s（消息可能过期或属于其他租户）",
                job_id,
                tenant_id,
            )
            return PipelineOutcome(
                job_id=job_id,
                tenant_id=tenant_id,
                skipped=True,
                skip_reason="job_not_found",
            )

        document = repository.get_document(self.session, tenant_id, job.document_id)
        if document is None:  # 理论上被外键挡住；真出现说明数据被手工改过
            return self._fail(job, "received", PipelineError(ERROR_UNEXPECTED, "任务指向的文档不存在"))

        filename = filename_from_source_uri(document.source_uri)
        if not filename:
            return self._fail(
                job,
                "stored",
                PipelineError(
                    ERROR_SOURCE_URI_INVALID,
                    f"source_uri 不是本服务生成的形式，无法反解对象 key：{document.source_uri!r}",
                    detail={"source_uri": document.source_uri},
                ),
            )

        state = _RunState(document=document, filename=filename)
        resumed_from = self._resume_stage(job)

        job.status = "running"
        job.error_code = None
        job.error_message = None
        self.session.flush()
        self.session.commit()
        logger.info(
            "[ingestion] start job=%s tenant=%s document=%s resumed_from=%s",
            job.id,
            tenant_id,
            document.id,
            resumed_from,
        )

        stages: dict[str, Callable[[models.IngestionJob, _RunState, bool], None]] = {
            "received": self._stage_received,
            "stored": self._stage_stored,
            "parsed": self._stage_parsed,
            "normalized": self._stage_normalized,
            "chunked": self._stage_chunked,
            "persisted": self._stage_persisted,
            "indexed": self._stage_indexed,
            "published": self._stage_published,
        }

        start = STAGE_ORDER.index(resumed_from)
        current_stage = resumed_from
        try:
            for position, stage in enumerate(STAGE_ORDER):
                current_stage = stage
                # 「准备模式」：这个阶段在失败前已经成功过，因此**重算但不重写**。
                # 必须重算的原因见模块 docstring（中间产物只在内存里）；
                # 不重写的原因见下面每个阶段里的 persist 分支。
                persist = position >= start
                stages[stage](job, state, persist)
                if persist and state.duplicate_of:
                    # 内容重复：没有自己的版本可推进，终态是"成功"但停在判重阶段
                    return self._duplicate_outcome(job, state, resumed_from)
                if persist:
                    self._advance(job, stage)
        except Exception as error:  # noqa: BLE001 - 逐类翻译，最后兜底为 unexpected_error
            return self._fail(job, current_stage, error, resumed_from=resumed_from)

        return PipelineOutcome(
            job_id=job.id,
            tenant_id=tenant_id,
            document_id=document.id,
            stage=job.stage,
            status=job.status,
            document_version_id=state.version.id if state.version else "",
            document_version=state.version.version if state.version else 0,
            chunk_count=len(state.chunking.chunks) if state.chunking else 0,
            index_version=state.index_result.index_version if state.index_result else 0,
            index_switched=bool(state.index_result and state.index_result.switched),
            warnings=state.warnings,
            resumed_from=resumed_from,
        )

    # ------------------------------------------------------------ 阶段

    def _stage_received(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """任务已存在即视为完成；这里只做一次结构化留痕（无持久化动作）。"""

        logger.info("[ingestion] stage=received job=%s document=%s", job.id, state.document.id)

    def _stage_stored(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """确认字节可取回。

        上传接口**已经**把字节写进对象存储（B4 的顺序：先 put 再建 job 再投递），
        所以这里不是"存储"而是"确认存储" —— 命名沿用计划的阶段名。
        确认存在的价值在于：解析阶段之前就把"文件丢了"与"文件解析不了"分开，
        两者的处理方式完全不同（前者要重传，后者要看警告）。
        """

        key = build_object_key(state.document.tenant_id, state.document.id, state.filename)
        if not self.store.exists(key):
            raise PipelineError(
                ERROR_OBJECT_MISSING,
                f"对象存储里找不到该文档的字节：{key}（可能已被清理，请重新上传）",
                detail={"object_key": key},
            )
        logger.info("[ingestion] stage=stored job=%s object=%s", job.id, key)

    def _stage_parsed(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """解析 + 内容 hash 判重 + 创建版本（[D-6]：版本由流水线创建）。

        ``persist=False``（重试时该阶段已成功过）的行为差异只有两处：
        不新建版本、不写文档状态 —— 解析与判重本身是只读计算，重算无害。
        """

        tenant_id = state.document.tenant_id
        key = build_object_key(tenant_id, state.document.id, state.filename)
        data = self.store.get(key)
        parsed = self.parser(data, state.filename, source_type=state.document.source_type)
        state.parsed = parsed
        state.warnings = tuple(item.to_dict() for item in parsed.warnings)

        content_hash = parsed.content_hash
        if not content_hash:
            raise PipelineError(
                ERROR_MISSING_CONTENT_HASH,
                "解析产物缺少 content_hash（解析器模板的契约），拒绝继续",
                detail={"parser": parsed.parser_name},
            )

        if persist:
            existing = repository.find_version_by_content_hash(self.session, tenant_id, content_hash)
            if existing is not None and existing.id != job.document_version_id:
                # 同一份内容已经存在：**不建第二份有效版本**（uq(tenant_id, content_hash) 的语义）
                state.duplicate_of = existing.id
                state.version = existing
                if existing.document_id != state.document.id:
                    # 本次上传的这个"文档"没有任何自己的内容 → 标 duplicate（计划原文口径）
                    repository.update_document_status(
                        self.session, tenant_id, state.document.id, STATUS_DUPLICATE
                    )
                logger.info(
                    "[ingestion] stage=parsed job=%s duplicate_of=%s hash=%s",
                    job.id,
                    existing.id,
                    content_hash[:12],
                )
                return

        if job.document_version_id:
            # 重试路径：版本已存在，直接复用（这是"不重复建版本"的守卫）
            version = repository.get_document_version(self.session, tenant_id, job.document_version_id)
            if version is None:
                raise PipelineError(
                    ERROR_UNEXPECTED,
                    f"任务记录的版本不存在：{job.document_version_id}",
                    detail={"document_version_id": job.document_version_id},
                )
            if persist:
                version.parser_name = parsed.parser_name
                version.parser_version = parsed.parser_version
                self.session.flush()
        else:
            if not persist:  # pragma: no cover - 只有数据被手工改过才会出现
                raise PipelineError(
                    ERROR_UNEXPECTED,
                    "任务已越过 parsed 阶段但没有版本记录，无法继续（数据被外部改动过？）",
                    detail={"job_id": job.id},
                )
            version = repository.create_document_version(
                self.session,
                tenant_id=tenant_id,
                document_id=state.document.id,
                content_hash=content_hash,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                metadata_json=self._version_metadata(parsed, state, config=None, stats=None),
                status="parsed",
            )
            repository.update_ingestion_job(
                self.session, tenant_id, job.id, document_version_id=version.id
            )

        state.version = version
        if persist:
            repository.update_document_status(self.session, tenant_id, state.document.id, "parsed")
        logger.info(
            "[ingestion] stage=parsed job=%s version=%s parser=%s@%s blocks=%d warnings=%d persist=%s",
            job.id,
            version.version,
            parsed.parser_name,
            parsed.parser_version,
            len(parsed.blocks),
            len(state.warnings),
            persist,
        )

    def _stage_normalized(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """解析产物 → 切分输入：生效配置（含知识库覆盖）+ ``ChunkContext`` + 留痕。"""

        tenant_id = state.document.tenant_id
        version = self._require_version(state)
        knowledge_base = repository.get_knowledge_base(
            self.session, tenant_id, state.document.knowledge_base_id
        )

        # 配置：环境默认 → 知识库覆盖 → 校验。越界**即失败**，不静默夹取（B5 定的行为）。
        overrides = knowledge_base.chunking_config_json if knowledge_base is not None else None
        try:
            config = build_config(overrides if isinstance(overrides, dict) else None)
        except ChunkConfigError as error:
            raise PipelineError(
                ERROR_INVALID_CHUNKING_CONFIG,
                f"知识库切分配置非法：{error}",
                detail={"errors": [item.to_dict() for item in error.errors]},
            ) from error
        state.config = config

        try:
            acl = tuple(
                AclEntry(
                    subject_type=row.subject_type,
                    subject_id=row.subject_id,
                    permission=row.permission,
                )
                for row in repository.list_document_acl(self.session, tenant_id, state.document.id)
            )
        except ValueError as error:
            raise PipelineError(
                ERROR_INVALID_DOCUMENT_ACL,
                f"文档 ACL 非法（版本不得带残缺权限进索引）：{error}",
                detail={"document_id": state.document.id},
            ) from error

        parsed = self._require_parsed(state)
        state.context = ChunkContext(
            tenant_id=tenant_id,
            document_id=state.document.id,
            document_version_id=version.id,
            document_version=int(version.version),
            acl=acl,
            source_uri=state.document.source_uri,
            filename=state.filename,
            source_type=state.document.source_type,
            document_title=state.document.title or parsed.title,
        )

        if persist:
            repository.update_document_version_metadata(
                self.session,
                tenant_id,
                version.id,
                self._version_metadata(parsed, state, config=config, stats=None),
            )
        logger.info(
            "[ingestion] stage=normalized job=%s config=%s acl=%d persist=%s",
            job.id,
            config.summary(),
            len(acl),
            persist,
        )

    def _stage_chunked(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """切分（纯函数，不写库）。空文档是**正常结果**，不是失败（B5 已定）。"""

        tenant_id = state.document.tenant_id
        version = self._require_version(state)
        parsed = self._require_parsed(state)
        config = state.config or build_config()
        context = state.context
        if context is None:  # pragma: no cover - 只有越过 normalized 阶段才会发生
            raise PipelineError(ERROR_UNEXPECTED, "缺少 ChunkContext（normalized 阶段未执行）")

        result = self.chunker(parsed, context=context, config=config)
        state.chunking = result

        if persist:
            repository.update_document_version_metadata(
                self.session,
                tenant_id,
                version.id,
                self._version_metadata(parsed, state, config=config, stats=result.stats),
            )
            repository.set_document_version_status(self.session, tenant_id, version.id, "chunked")
        logger.info(
            "[ingestion] stage=chunked job=%s %s persist=%s",
            job.id,
            result.summary(),
            persist,
        )

    def _stage_persisted(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """落库 chunk：**先删后写**，让重跑与首次执行的结果完全一致。"""

        if not persist:
            # 该阶段在本次重试的起点之前 → 上一次已经写成功过，不重复删写
            logger.info("[ingestion] stage=persisted job=%s skip=already_persisted", job.id)
            return

        tenant_id = state.document.tenant_id
        version = self._require_version(state)
        result = self._require_chunking(state)

        removed = repository.delete_chunks(self.session, tenant_id, version.id)
        inserted = repository.insert_chunks(
            self.session,
            tenant_id=tenant_id,
            document_version_id=version.id,
            chunks=result.to_insert_plan(),
        )
        logger.info(
            "[ingestion] stage=persisted job=%s version=%s removed=%d inserted=%d",
            job.id,
            version.version,
            removed,
            inserted,
        )

    def _stage_indexed(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """重建并原子切换索引；没有向量时**跳过而不失败**。"""

        if not persist:
            logger.info("[ingestion] stage=indexed job=%s skip=already_indexed", job.id)
            return

        tenant_id = state.document.tenant_id
        version = self._require_version(state)
        result = self._require_chunking(state)

        if not result.chunks:
            # 空文档：建一个 0 向量的索引会让"已有生效索引"变成假象，
            # 因此跳过索引阶段，把版本照常发布（内容为空是可解释的结果）。
            logger.info("[ingestion] stage=indexed job=%s skipped=no_chunks", job.id)
            state.extra["index_skipped"] = "no_chunks"
            return

        # 交给索引构建器之前先提交：构建器自己 commit（失败要留痕），
        # 不先提交会把本阶段之前的状态一起带进去。
        self.session.commit()

        embedding_model = self.embedding_model or default_embedding_model()
        index_result = self.index_runner(
            self.session,
            tenant_id=tenant_id,
            root=self.index_root if self.index_root is not None else default_index_root(),
            embedder=self.embedder or default_embedder,
            embedding_model=embedding_model,
            index_name=self.index_name,
            tokenizer_id=(state.config.tokenizer_id if state.config else ""),
        )
        state.index_result = index_result
        repository.set_document_version_status(self.session, tenant_id, version.id, "indexed")
        repository.update_document_status(self.session, tenant_id, state.document.id, "indexed")
        logger.info(
            "[ingestion] stage=indexed job=%s index_version=%s chunks=%d skipped=%s",
            job.id,
            index_result.index_version,
            index_result.chunk_count,
            index_result.skipped,
        )

    def _stage_published(self, job: models.IngestionJob, state: _RunState, persist: bool) -> None:
        """置 ``published``：此后才允许被检索（可见性仍由检索层 filter 判定）。"""

        if not persist:
            logger.info("[ingestion] stage=published job=%s skip=already_published", job.id)
            return

        tenant_id = state.document.tenant_id
        version = self._require_version(state)
        try:
            repository.set_document_version_status(self.session, tenant_id, version.id, STATUS_PUBLISHED)
            repository.update_document_status(
                self.session, tenant_id, state.document.id, STATUS_PUBLISHED
            )
            repository.update_ingestion_job(
                self.session, tenant_id, job.id, status="succeeded", stage="published"
            )
            self.session.flush()
        except Exception as error:  # noqa: BLE001 - 发布失败必须带稳定错误码留痕
            raise PipelineError(
                ERROR_PUBLISH_FAILED,
                f"版本发布失败：{error}",
                detail={"document_version_id": version.id},
            ) from error
        logger.info(
            "[ingestion] stage=published job=%s version=%s status=%s",
            job.id,
            version.version,
            STATUS_PUBLISHED,
        )

    # ------------------------------------------------------------ 辅助

    def _version_metadata(
        self,
        parsed: ParsedDocument,
        state: _RunState,
        *,
        config: ChunkConfig | None,
        stats: Any,
    ) -> dict[str, Any]:
        """组装 ``document_versions.metadata_json``（**整体替换**，不做 key 合并）。

        三个固定键，缺一不可：

        * ``warnings`` —— 与 B4 的读取端（``GET /ingestion-jobs/{job_id}`` 展开警告）
          **共用键名**，解析警告与切分警告同住一个数组；
        * ``parser``  —— 解析器身份与规模，便于排查"这份内容是怎么来的"；
        * ``chunking`` —— 生效配置 + tokenizer 标识 + 切分统计（B7 判断
          "这份 chunk 是哪套配置切的"的唯一依据）。
          配置载荷**直接取** ``ChunkConfig.storage_payload()``，
          不另写一份序列化（两份必然漂移）。
        """

        metadata: dict[str, Any] = {
            WARNINGS_METADATA_KEY: [dict(item) for item in state.warnings],
            "parser": {
                "name": parsed.parser_name,
                "version": parsed.parser_version,
                "source_type": parsed.source_type or state.document.source_type,
                "block_count": len(parsed.blocks),
                "page_count": parsed.page_count,
                "title": parsed.title,
            },
            "content_hash": parsed.content_hash,
        }
        if config is not None:
            payload = config.storage_payload()
            payload["stats"] = stats.to_dict() if stats is not None else None
            metadata[CHUNKING_METADATA_KEY] = payload
        return metadata

    def _resume_stage(self, job: models.IngestionJob) -> str:
        """决定从哪个阶段开始。

        * ``pending`` / ``running``（进程崩过）→ 从 ``received`` 整体重跑；
        * ``failed`` / ``retrying`` → **从失败所在的阶段**继续
          （``job.stage`` 在失败时被写成"失败的阶段"，见 :meth:`_fail`）；
        * ``succeeded`` → 整条链路重跑一遍（重复投递），靠幂等保证不出脏数据。
        """

        stage = job.stage if job.stage in STAGE_ORDER else "received"
        if job.status in ("failed", "retrying"):
            return stage
        return "received"

    def _advance(self, job: models.IngestionJob, stage: str) -> None:
        """阶段结束：写 ``job.stage`` 并提交（进度对外可见）。"""

        repository.update_ingestion_job(self.session, job.tenant_id, job.id, stage=stage)
        self.session.commit()

    def _duplicate_outcome(
        self, job: models.IngestionJob, state: _RunState, resumed_from: str
    ) -> PipelineOutcome:
        """重复文件的终态：**成功**（不是失败），但停在判重所在阶段。"""

        version = state.version
        repository.update_ingestion_job(
            self.session,
            job.tenant_id,
            job.id,
            status="succeeded",
            stage="parsed",
            document_version_id=version.id if version is not None else None,
        )
        self.session.commit()
        logger.info(
            "[ingestion] duplicate job=%s hash=%s 复用已有版本 %s（不新建版本）",
            job.id,
            (state.parsed.content_hash[:12] if state.parsed else ""),
            version.id if version is not None else "",
        )
        return PipelineOutcome(
            job_id=job.id,
            tenant_id=state.document.tenant_id,
            document_id=state.document.id,
            stage="parsed",
            status="succeeded",
            document_version_id=version.id if version is not None else "",
            document_version=version.version if version is not None else 0,
            duplicate=True,
            warnings=state.warnings,
            resumed_from=resumed_from,
        )

    def _fail(
        self,
        job: models.IngestionJob,
        stage: str,
        error: Exception,
        *,
        resumed_from: str = "",
    ) -> PipelineOutcome:
        """把异常翻译成任务上的 ``error_code`` / ``error_message`` 并返回失败结果。"""

        error_code, message = self._translate(error)
        self.session.rollback()
        try:
            repository.fail_ingestion_job(
                self.session,
                job.tenant_id,
                job.id,
                stage=stage,
                error_code=error_code,
                error_message=message[:2000],
            )
            self._mark_document_failed(job)
            self.session.commit()
        except Exception:  # pragma: no cover - 记录失败本身再失败只能记日志
            self.session.rollback()
            logger.exception("[ingestion] 记录失败状态时又出错 job=%s", job.id)

        logger.error(
            "[ingestion] failed job=%s stage=%s error_code=%s message=%s",
            job.id,
            stage,
            error_code,
            message,
        )
        if error_code == ERROR_UNEXPECTED:
            logger.exception("[ingestion] unexpected error job=%s", job.id)

        return PipelineOutcome(
            job_id=job.id,
            tenant_id=job.tenant_id,
            document_id=job.document_id,
            stage=stage,
            status=STATUS_FAILED,
            document_version_id=job.document_version_id or "",
            resumed_from=resumed_from,
            error_code=error_code,
            error_message=message,
        )

    def _mark_document_failed(self, job: models.IngestionJob) -> None:
        """文档状态只在「它确实没有任何已发布版本」时才置 ``failed``。

        如果一份文档已经有一个可用版本、新版本处理失败，把文档整体标成 ``failed``
        会让用户以为"这份文档不能用了" —— 而实际旧版本还在正常服务。
        """

        document = repository.get_document(self.session, job.tenant_id, job.document_id)
        if document is None:
            return
        versions = repository.list_document_versions(self.session, job.tenant_id, job.document_id)
        if any(item.status == STATUS_PUBLISHED for item in versions):
            return
        repository.update_document_status(self.session, job.tenant_id, job.document_id, STATUS_FAILED)

    @staticmethod
    def _translate(error: Exception) -> tuple[str, str]:
        """异常 → (error_code, message)。稳定错误码全部来自各模块自己的定义。"""

        if isinstance(error, PipelineError):
            return error.error_code, error.message
        if isinstance(error, ChunkConfigError):
            return ERROR_INVALID_CHUNKING_CONFIG, f"切分配置非法：{error}"
        if isinstance(error, ParserError):
            return error.error_code, error.message
        if isinstance(error, ChunkingError):
            return error.error_code, error.message
        if isinstance(error, (IndexBuildError, IndexManifestError)):
            return error.error_code, error.message
        return ERROR_UNEXPECTED, f"{type(error).__name__}: {error}"

    @staticmethod
    def _require_parsed(state: _RunState) -> ParsedDocument:
        if state.parsed is None:
            raise PipelineError(ERROR_UNEXPECTED, "缺少解析产物（parsed 阶段未执行）")
        return state.parsed

    @staticmethod
    def _require_version(state: _RunState) -> models.DocumentVersion:
        if state.version is None:
            raise PipelineError(ERROR_UNEXPECTED, "缺少文档版本（parsed 阶段未执行）")
        return state.version

    @staticmethod
    def _require_chunking(state: _RunState) -> ChunkingResult:
        if state.chunking is None:
            raise PipelineError(ERROR_UNEXPECTED, "缺少切分产物（chunked 阶段未执行）")
        return state.chunking


def process_job(
    session: Session,
    *,
    tenant_id: str,
    job_id: str,
    store: ObjectStore,
    **kwargs: Any,
) -> PipelineOutcome:
    """一行式的流水线入口（worker 与测试都用它）。"""

    pipeline = IngestionPipeline(session=session, store=store, **kwargs)
    return pipeline.process_job(tenant_id=tenant_id, job_id=job_id)


__all__ = [
    "CHUNKING_METADATA_KEY",
    "ERROR_INVALID_CHUNKING_CONFIG",
    "ERROR_INVALID_DOCUMENT_ACL",
    "ERROR_MISSING_CONTENT_HASH",
    "ERROR_OBJECT_MISSING",
    "ERROR_PUBLISH_FAILED",
    "ERROR_SOURCE_URI_INVALID",
    "ERROR_UNEXPECTED",
    "IngestionPipeline",
    "PipelineError",
    "PipelineOutcome",
    "STAGE_ORDER",
    "STATUS_DUPLICATE",
    "STATUS_FAILED",
    "STATUS_PUBLISHED",
    "WARNINGS_METADATA_KEY",
    "default_embedder",
    "default_embedding_model",
    "default_index_root",
    "process_job",
]
