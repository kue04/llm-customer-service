"""向量索引构建、原子切换与回滚（计划 3.4）。

职责边界
--------
- 本模块**负责**：从数据库读出 chunk → 算向量 → 建 FAISS 索引 →
  校验 → 原子切换指针 → 维护 ``index_builds`` 记录 → 支持回滚。
- 本模块**不负责**：解析、切分（3.3 的流水线负责）、检索时的权限过滤
  （检索层负责，且必须 fail closed）。

为什么是「按数据库重建」而不是「原地 append」
--------------------------------------------
索引是**派生数据**，唯一真源是 ``document_chunks`` 表。原地 append 有两个死结：
① FAISS 的行号只能追加，删掉一个文档后旧向量仍在（越删越脏，且无法表达"这份 chunk 没了"）；
② 行号与 chunk_id 的对应关系会散落成"历史追加顺序"，正是 3.4 要根治的那类隐式耦合。
因此每次构建都按 ``metadata_json["ordinal"]`` **重新排序并重建**：
行号 = 本次构建里 chunk 的序号，与 chunk_id 一起写进 manifest，
检索时只认 manifest，不认任何"位置巧合"。

顺序为什么必须按 ``ordinal`` 而不是数据库读回顺序
--------------------------------------------------
``repository.list_chunks`` 的排序键是 ``(created_at ASC, chunk_id ASC)``，
同一批写入的 ``created_at`` 很可能同值，此时顺序由 **chunk_id（哈希）** 决定 ——
也就是说读回顺序**不等于**写入顺序（B5 批次发现，见台账）。据此重建索引会让
"同一份文档两次构建得到不同的行号"，manifest 与旧索引立刻对不上。
``ordinal`` 是切分器写进 ``metadata_json`` 的确定序号，是唯一可信的顺序来源。

embedding 模型不可用时**让任务失败**（与 OCR 缺失降级为警告的做法相反）
--------------------------------------------------------------------
OCR 缺失时文件仍有可检索文本，降级为警告是"少一点能力"；
而 embedding 不可用时**一个向量都算不出来**，此时若还标记 ``published``，
用户看到的是一条"处理成功但永远检索不到"的文档 —— 与 B9 同源的判断标准：
**降级的后果是"能力变弱"还是"承诺变假"**。后者必须失败。

事务边界（与仓储层的约定不同，刻意如此）
----------------------------------------
``repository`` 的函数一律不 commit，事务边界交给调用方。本模块**例外**：
它自己 commit。原因是「索引构建失败必须留痕」——若交给调用方回滚，
``index_builds`` 里的 ``failed`` 记录会随事务一起消失，
运维就只能看到"任务失败了但不知道失败在哪一版"，而这恰恰是最需要的信息。
因此约定：**调用本模块之前，调用方应把自己待提交的改动先提交**，
这样这里的 commit 不会顺手带走别人未提交的状态。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from config.chunking_config import CONFIG_VERSION

from services.ingestion import models, repository
from services.ingestion.index_manifest import (
    BUILDING_SUFFIX,
    DEFAULT_INDEX_NAME,
    FILTER_RULE,
    INDEX_FILENAME,
    VISIBILITY_POLICY,
    IndexManifest,
    IndexManifestError,
    ManifestEntry,
    index_root,
    list_versions,
    load_active_manifest,
    publish_build_directory,
    read_manifest,
    switch_pointer,
    utc_now_iso,
    verify_index_file,
    version_dir,
    write_manifest,
)
from sqlalchemy.orm import Session


#: 向量化函数：文本 → 向量。注入形式是为了让测试用**确定性假实现**，
#: 不需要下载任何模型（与 B5 的 tokenizer mock 同一取向）。
Embedder = Callable[[str], Sequence[float]]

#: 错误码
ERROR_EMBEDDING_UNAVAILABLE = "embedding_unavailable"
ERROR_EMBEDDING_DIMENSION_MISMATCH = "embedding_dimension_mismatch"
ERROR_INDEX_BUILD_FAILED = "index_build_failed"
ERROR_INDEX_ROLLBACK_FAILED = "index_rollback_failed"
ERROR_INDEX_BUILD_NOT_FOUND = "index_build_not_found"


class IndexBuildError(RuntimeError):
    """索引构建 / 回滚失败。``error_code`` 可直落 ``ingestion_jobs.error_code``。"""

    def __init__(self, error_code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
        self.error_code = error_code
        self.message = message
        self.detail: dict[str, Any] = dict(detail or {})
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    """一次构建 / 回滚的结果（供流水线与测试断言）。"""

    index_name: str
    tenant_id: str
    index_version: int = 0
    build_id: str = ""
    chunk_count: int = 0
    embedding_model: str = ""
    embedding_dimension: int = 0
    manifest_uri: str = ""
    switched: bool = False
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_name": self.index_name,
            "tenant_id": self.tenant_id,
            "index_version": self.index_version,
            "build_id": self.build_id,
            "chunk_count": self.chunk_count,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "manifest_uri": self.manifest_uri,
            "switched": self.switched,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


# ---------------------------------------------------------------- 组装条目


def _ordinal_of(chunk: models.DocumentChunk) -> int:
    metadata = chunk.metadata_json if isinstance(chunk.metadata_json, dict) else {}
    try:
        return int(metadata.get("ordinal", 0))
    except (TypeError, ValueError):
        return 0


def _metadata_of(chunk: models.DocumentChunk) -> dict[str, Any]:
    return chunk.metadata_json if isinstance(chunk.metadata_json, dict) else {}


def build_entries(
    rows: Sequence[tuple[models.DocumentChunk, models.DocumentVersion, models.Document]],
) -> list[ManifestEntry]:
    """把数据库行装配成 manifest 条目，**按 ``ordinal`` 排序并重新编 row_id**。

    排序键是 ``(document_version_id, ordinal)``：版本内按 ordinal 递增，
    版本之间按 id 分组（分组顺序稳定即可，不追求跨版本的业务顺序）。
    父 chunk 的 ordinal 一定小于同组子 chunk（切分器的输出顺序），
    因此排序后父仍先于子 —— 这点与落库约束一致，不是巧合而是同一份顺序。
    """

    ordered = sorted(
        rows,
        key=lambda item: (str(item[0].document_version_id), _ordinal_of(item[0])),
    )
    entries: list[ManifestEntry] = []
    for position, (chunk, version, document) in enumerate(ordered):
        metadata = _metadata_of(chunk)
        entries.append(
            ManifestEntry(
                row_id=position,
                chunk_id=chunk.chunk_id,
                tenant_id=chunk.tenant_id,
                document_id=version.document_id,
                document_version=int(version.version),
                document_version_id=chunk.document_version_id,
                chunk_type=str(metadata.get("chunk_type", "")) or "child",
                ordinal=_ordinal_of(chunk),
                text=chunk.text,
                heading_path=tuple(str(item) for item in (metadata.get("heading_path") or ())),
                page_start=metadata.get("page_start"),
                page_end=metadata.get("page_end"),
                acl=tuple(dict(item) for item in (metadata.get("acl") or ())),
                content_hash=chunk.content_hash,
                token_count=int(chunk.token_count or 0),
                char_count=int(metadata.get("char_count") or 0),
                source_uri=str(metadata.get("source_uri", "")) or document.source_uri,
                filename=str(metadata.get("filename", "")),
                document_title=str(metadata.get("document_title", "")) or document.title,
                source_type=str(metadata.get("source_type", "")) or document.source_type,
                oversize=bool(metadata.get("oversize", False)),
            )
        )
    return entries


def _embed_all(
    entries: Sequence[ManifestEntry],
    embedder: Embedder,
) -> np.ndarray:
    """逐条算向量并统一维度；维度不一致**立即失败**（否则 numpy 会给出奇怪的结果）。

    向量化的对象是 ``entry.text``（**被检索的原文**），不是 chunk_id ——
    后者只是个哈希，用它算向量会让检索退化成随机命中。这条看似显然，
    但只要写入与检索两侧的输入口径不一致（一边 text 一边 id），
    系统不会报任何错，只会"检索结果看起来不太对"。
    """

    vectors: list[list[float]] = []
    for entry in entries:
        try:
            vector = list(embedder(entry.text))
        except IndexBuildError:
            raise
        except Exception as error:  # 模型缺失 / 加载失败 / OOM 都归为「算不出向量」
            raise IndexBuildError(
                ERROR_EMBEDDING_UNAVAILABLE,
                f"向量化失败（embedding 后端不可用）：{error}",
                detail={"chunk_id": entry.chunk_id},
            ) from error
        if not vector:
            raise IndexBuildError(
                ERROR_EMBEDDING_UNAVAILABLE,
                "向量化返回空向量",
                detail={"chunk_id": entry.chunk_id},
            )
        vectors.append([float(value) for value in vector])

    dimension = len(vectors[0])
    for entry, vector in zip(entries, vectors):
        if len(vector) != dimension:
            raise IndexBuildError(
                ERROR_EMBEDDING_DIMENSION_MISMATCH,
                "同一批 chunk 的向量维度不一致（embedding 后端不稳定或混用了两个模型）",
                detail={"chunk_id": entry.chunk_id, "expected": dimension, "actual": len(vector)},
            )
    return np.array(vectors, dtype="float32")


def _relative_to_root(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:  # pragma: no cover - 只有把索引放到 root 之外才会发生
        return path.as_posix()


# ---------------------------------------------------------------- 构建


def rebuild_index(
    session: Session,
    *,
    tenant_id: str,
    root: str | Path,
    embedder: Embedder,
    embedding_model: str,
    index_name: str = DEFAULT_INDEX_NAME,
    version_statuses: Sequence[str] | None = None,
    tokenizer_id: str = "",
    chunking_config_version: str = CONFIG_VERSION,
    faiss_module: object | None = None,
    all_tenants: bool = True,
) -> IndexBuildResult:
    """重建索引并原子切换。任一步失败 → 当前索引保持不变。

    ``all_tenants``（B7 修复，默认 ``True``）：索引是**全局一份** ——
    指针 ``current.json``、版本目录 ``v{n}/``、索引文件都只有一份，被所有租户共用，
    隔离由检索层的 ``tenant_id`` + ``allowed_chunk_ids`` 前置过滤保证。
    因此这里必须取**全部租户**的可索引 chunk；只取触发构建的那个租户，
    会让后入库的租户把先入库租户的 chunk 从生效索引里挤出去 ——
    检索层只表现为"查不到"，没有任何报错（踩坑 **B14**）。
    传 ``False`` 可退回按租户取数（供按租户分片的部署形态使用）。

    失败时的处理分两段：
    ① 构建期（临时目录里）失败 → 清掉临时目录，``index_builds`` 记 ``failed`` +
       ``error_message``，**不写指针** —— 这条路径下当前索引连被"碰到"的机会都没有；
    ② 指针切换本身失败 → 同上，指针内容仍是旧版本（``os.replace`` 是原子的，
       不存在写坏指针的中间态）。
    """

    root_path = Path(root)
    rows = repository.list_indexable_chunks(
        session,
        tenant_id,
        version_statuses=version_statuses,
        all_tenants=all_tenants,
    )
    entries = build_entries(rows)

    if not entries:
        # 没有向量就没有"索引"可言。此时**不建索引、不写指针**：
        # 建一个 0 向量的索引会让「本租户已有生效索引」变成假象，
        # 检索层会以为能查到东西。空文档是可解释的正常结果（B5 已定），
        # 因此这里只跳过索引环节，由调用方决定任务是否算成功。
        return IndexBuildResult(
            index_name=index_name,
            tenant_id=tenant_id,
            embedding_model=embedding_model,
            skipped=True,
            skip_reason="no_chunks",
        )

    build = repository.create_index_build(
        session,
        tenant_id=tenant_id,
        index_name=index_name,
        embedding_model=embedding_model,
        embedding_dimension=1,  # 真实维度算完再回填（列上有 > 0 的 CHECK 约束）
        chunk_count=0,
        status="building",
    )
    session.commit()

    version = int(build.version)
    building_dir = version_dir(root_path, index_name, version).with_name(
        version_dir(root_path, index_name, version).name + BUILDING_SUFFIX
    )
    final_dir = version_dir(root_path, index_name, version)

    try:
        vectors = _embed_all(entries, embedder)
        manifest = IndexManifest(
            index_name=index_name,
            tenant_id=tenant_id,
            index_version=version,
            embedding_model=embedding_model,
            embedding_dimension=int(vectors.shape[1]),
            built_at=utc_now_iso(),
            chunk_ids=tuple(entry.chunk_id for entry in entries),
            entries=tuple(entries),
            index_file=INDEX_FILENAME,
            filter_rule=FILTER_RULE,
            visibility_policy=VISIBILITY_POLICY,
            tokenizer_id=tokenizer_id,
            chunking_config_version=chunking_config_version,
            extra={
                "source": "document_chunks",
                "ordered_by": "metadata_json.ordinal",
                "version_statuses": list(version_statuses) if version_statuses else None,
                "chunk_count": len(entries),
                # 索引的作用域必须留痕：全局索引里同时存在多个租户的条目，
                # 「谁被写进了这份索引」是排障时的第一个问题（踩坑 B14）。
                "scope": "all_tenants" if all_tenants else "tenant",
                "tenants": sorted({entry.tenant_id for entry in entries}),
            },
        )

        building_dir.mkdir(parents=True, exist_ok=True)
        index_path = building_dir / INDEX_FILENAME
        faiss_module = faiss_module or _require_faiss()
        index = faiss_module.IndexFlatIP(int(vectors.shape[1]))
        index.add(vectors)
        faiss_module.write_index(index, str(index_path))

        # 先写 manifest，再校验（校验要读 manifest 的条数与维度）
        write_manifest(building_dir, manifest)
        verify_index_file(index_path, manifest, faiss_module=faiss_module)

        # 校验通过后才让目录"转正"，最后写指针
        publish_build_directory(building_dir, final_dir)
        manifest_path = final_dir / "index_manifest.json"
        index_final = final_dir / INDEX_FILENAME
        pointer = switch_pointer(
            root_path,
            index_name=index_name,
            tenant_id=tenant_id,
            index_version=version,
            manifest_relpath=_relative_to_root(root_path, manifest_path),
            index_relpath=_relative_to_root(root_path, index_final),
            fingerprint=manifest.fingerprint(),
            build_id=build.id,
        )
    except Exception as error:
        # 临时目录可能留着半成品；清掉它，正式版本目录**不动**
        _cleanup_building_dir(building_dir)
        code = getattr(error, "error_code", ERROR_INDEX_BUILD_FAILED)
        repository.set_index_build_status(
            session,
            tenant_id,
            build.id,
            "failed",
            error_message=str(error)[:500],
        )
        session.commit()
        if isinstance(error, (IndexBuildError, IndexManifestError)):
            raise
        raise IndexBuildError(code, f"索引构建失败：{error}", detail={"index_version": version}) from error

    # 新版本生效：旧的 active 一律降级为 superseded（保留记录，便于回滚排查）。
    # 全局索引下必须**跨租户**降级：另一个租户的构建记录同样指向这份唯一的索引。
    for previous in repository.list_index_builds(
        session, tenant_id, index_name, statuses=("active",), all_tenants=all_tenants
    ):
        if previous.id != build.id:
            repository.set_index_build_status(session, tenant_id, previous.id, "superseded")
    repository.set_index_build_status(
        session,
        tenant_id,
        build.id,
        "active",
        manifest_uri=_relative_to_root(root_path, manifest_path),
        chunk_count=len(entries),
    )
    session.commit()

    return IndexBuildResult(
        index_name=index_name,
        tenant_id=tenant_id,
        index_version=version,
        build_id=build.id,
        chunk_count=len(entries),
        embedding_model=embedding_model,
        embedding_dimension=int(manifest.embedding_dimension),
        manifest_uri=pointer.manifest_path,
        switched=True,
    )


def rollback_index(
    session: Session,
    *,
    tenant_id: str,
    root: str | Path,
    index_version: int,
    index_name: str = DEFAULT_INDEX_NAME,
    faiss_module: object | None = None,
) -> IndexBuildResult:
    """回滚到指定版本：**只改指针**，不重建、不删文件。

    回滚是"把生效指针指回旧版本"，因此与发布共用同一段切换代码 ——
    这样它才可能和发布一样安全（失败时指针不动）。

    为什么回滚前要重新校验旧版本的文件：旧版本目录可能已在磁盘上被清理或损坏，
    此时"回滚成功"会得到一个指向坏索引的指针，下一次检索才炸 ——
    那比回滚失败难排查得多。因此宁可在回滚时就明确失败。
    """

    root_path = Path(root)
    directory = version_dir(root_path, index_name, int(index_version))
    if not directory.exists():
        raise IndexBuildError(
            ERROR_INDEX_ROLLBACK_FAILED,
            f"待回滚的索引版本目录不存在：{index_version}",
            detail={"index_version": int(index_version)},
        )
    manifest = read_manifest(directory / "index_manifest.json")
    faiss_module = faiss_module or _require_faiss()
    verify_index_file(directory / INDEX_FILENAME, manifest, faiss_module=faiss_module)

    if manifest.tenant_id != tenant_id:
        raise IndexBuildError(
            ERROR_INDEX_ROLLBACK_FAILED,
            "待回滚的索引不属于该租户",
            detail={"index_tenant": manifest.tenant_id, "tenant": tenant_id},
        )

    pointer = switch_pointer(
        root_path,
        index_name=index_name,
        tenant_id=tenant_id,
        index_version=manifest.index_version,
        manifest_relpath=_relative_to_root(root_path, directory / "index_manifest.json"),
        index_relpath=_relative_to_root(root_path, directory / INDEX_FILENAME),
        fingerprint=manifest.fingerprint(),
    )

    history = repository.list_index_builds(session, tenant_id, index_name)
    target_build = next(
        (item for item in history if int(item.version) == int(index_version)),
        None,
    )
    if target_build is None:
        raise IndexBuildError(
            ERROR_INDEX_BUILD_NOT_FOUND,
            f"index_builds 里没有版本 {index_version} 的记录",
            detail={"index_version": int(index_version)},
        )
    for item in history:
        if item.id == target_build.id:
            continue
        if item.status == "active":
            repository.set_index_build_status(session, tenant_id, item.id, "rolled_back")
    repository.set_index_build_status(
        session,
        tenant_id,
        target_build.id,
        "active",
        manifest_uri=_relative_to_root(root_path, directory / "index_manifest.json"),
        chunk_count=manifest.chunk_count,
    )
    session.commit()

    return IndexBuildResult(
        index_name=index_name,
        tenant_id=tenant_id,
        index_version=manifest.index_version,
        build_id=target_build.id,
        chunk_count=manifest.chunk_count,
        embedding_model=manifest.embedding_model,
        embedding_dimension=int(manifest.embedding_dimension),
        manifest_uri=pointer.manifest_path,
        switched=True,
    )


def active_index_version(root: str | Path, index_name: str = DEFAULT_INDEX_NAME) -> int:
    """当前生效索引的版本号（没有生效索引时抛 :class:`IndexManifestError`）。"""

    manifest, _pointer, _path = load_active_manifest(root, index_name)
    return int(manifest.index_version)


def available_versions(root: str | Path, index_name: str = DEFAULT_INDEX_NAME) -> list[int]:
    """磁盘上可用（已校验发布）的版本号，倒序。"""

    return list_versions(root, index_name)


def index_root_for(root: str | Path, index_name: str = DEFAULT_INDEX_NAME) -> Path:
    return index_root(root, index_name)


def _cleanup_building_dir(directory: Path) -> None:
    if not directory.exists():
        return
    try:
        import shutil

        shutil.rmtree(directory)
    except OSError:  # pragma: no cover - 清理失败不该掩盖真正的构建错误
        pass


def _require_faiss():
    try:
        import faiss
    except ModuleNotFoundError as error:  # pragma: no cover
        raise IndexBuildError(
            ERROR_EMBEDDING_UNAVAILABLE,
            "索引构建需要 faiss；请安装 faiss-cpu",
            detail={},
        ) from error
    return faiss


__all__ = [
    "ERROR_EMBEDDING_DIMENSION_MISMATCH",
    "ERROR_EMBEDDING_UNAVAILABLE",
    "ERROR_INDEX_BUILD_FAILED",
    "ERROR_INDEX_BUILD_NOT_FOUND",
    "ERROR_INDEX_ROLLBACK_FAILED",
    "Embedder",
    "IndexBuildError",
    "IndexBuildResult",
    "active_index_version",
    "available_versions",
    "build_entries",
    "index_root_for",
    "rebuild_index",
    "rollback_index",
]
