"""索引 manifest 与**原子切换**（计划 3.4）。

为什么需要 manifest
-------------------
原实现里「FAISS 第 i 个向量」等于「JSONL 第 i 行」，两者靠**位置巧合**关联：
一旦重建索引时行序变了、或删掉一条 FAQ，位置与内容就整体错位，
而错位的表现是「检索到了别人的答案」—— 一个不报错、只看结果的静默数据错乱。

本模块把这条隐式关系换成显式的 **``row_id ↔ chunk_id`` 映射**：
manifest 里 ``chunk_ids[i]`` 就是 FAISS 第 ``i`` 行的 chunk，
``entries[i].row_id`` 必须等于 ``i``（构造期与读取期都断言）。
于是「向量序号不得再隐式等于 JSONL 行号」这条要求有了可验证的载体。

原子切换
--------
索引不是原地覆盖，而是：

1. 在**临时目录**里建向量文件 + manifest；
2. 校验「向量数 == manifest 条数 == 维度」（任一不符即失败，**不触碰当前索引**）；
3. 把临时目录改名成正式版本目录（同文件系统内 rename，原子）；
4. **最后一步**才原子替换 ``current.json`` 指针。

前 3 步失败时当前索引完好无损；第 4 步是单文件 ``os.replace``，不存在中间态。
回滚就是再写一次指针（指向旧版本目录），因此回滚与发布走的是同一段代码。

本模块**不依赖数据库、不依赖 faiss 之外的第三方包**（faiss 也是惰性引入），
因此可以被检索层（``utils/vector_retriever``）与流水线（``pipeline``）共用。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any


#: manifest 结构自身的版本（字段语义变化时提升）
MANIFEST_VERSION = "1.0"

#: 正式文件名
MANIFEST_FILENAME = "index_manifest.json"
INDEX_FILENAME = "vectors.faiss"

#: 指向「当前生效版本」的指针文件名（原子切换的唯一修改点）
POINTER_FILENAME = "current.json"

#: 索引根目录名（相对检索层的向量库目录）
CHUNK_INDEX_DIRNAME = "chunk_index"

#: 默认索引名（一个租户一份 chunk 索引）
DEFAULT_INDEX_NAME = "document_chunks"

#: 构建中目录的后缀：只有**去掉**这个后缀的目录才是可被指针引用的正式版本
BUILDING_SUFFIX = ".building"

#: manifest 里记录的过滤规则（3.4 要求「记录过滤规则」）。
#: 这里只记录**规则描述**，真正的 filter 由检索层在查询时构造并强制传入 ——
#: 因为 ACL / 生效期会变，构建期写死规则会让索引在权限变更后立即失效。
FILTER_RULE = "tenant_id 必须匹配；allowed_chunk_ids 为服务端构造的白名单；缺失 filter 一律拒绝"

#: 索引文件里包含哪些版本（构建期选择，见台账 [D-11]）
VISIBILITY_POLICY = "索引包含构建时选中的版本；可见性由检索层 filter 判定，不在构建期固化"


class IndexManifestError(RuntimeError):
    """索引 manifest / 索引文件相关的错误（`error_code` 可直落任务错误码）。"""

    def __init__(self, error_code: str, message: str, *, detail: Mapping[str, Any] | None = None) -> None:
        self.error_code = error_code
        self.message = message
        self.detail: dict[str, Any] = dict(detail or {})
        super().__init__(message)


ERROR_POINTER_MISSING = "index_pointer_missing"
ERROR_MANIFEST_MISSING = "index_manifest_missing"
ERROR_INDEX_MISSING = "index_file_missing"
ERROR_INVALID_MANIFEST = "invalid_index_manifest"
ERROR_VERIFICATION_FAILED = "index_verification_failed"
ERROR_PUBLISH_CONFLICT = "index_publish_conflict"


# ---------------------------------------------------------------- 数据模型


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """一个向量的完整描述：既是 ``row_id ↔ chunk_id`` 映射，也是检索要返回的元数据。

    为什么把「来源 / 租户 / ACL / 页码 / 标题路径」也放进来：
    计划 3.4 要求「命中必须返回 chunk、document、version、tenant、ACL、来源元数据」，
    而**检索前必须先做过滤**（预过滤要先把候选压到允许的 id 集合里）。
    如果这些字段只在数据库里，检索层就得先查库再检索 —— 那样「过滤」会退化成
    「查库得到白名单」，多一次往返且把纯检索层绑上数据库会话。
    放进 manifest 后，检索层可以只凭索引文件（+ 指针）完成「过滤 → 检索 → 返回元数据」。

    ``text`` 也在这里：它是**被向量化的原文**，也是检索命中的返回值。
    若不存，检索层就必须回数据库取正文，而检索层（``utils``）目前是
    「读文件、不持会话」的形态；把它塞进来可以保住这条边界，
    代价是 manifest 变大（向量库的 docstore 本来就是这么做的）。
    """

    row_id: int
    chunk_id: str
    tenant_id: str
    document_id: str
    document_version: int
    document_version_id: str
    chunk_type: str
    ordinal: int
    text: str = ""
    heading_path: tuple[str, ...] = ()
    page_start: int | None = None
    page_end: int | None = None
    acl: tuple[dict[str, str], ...] = ()
    content_hash: str = ""
    token_count: int = 0
    char_count: int = 0
    source_uri: str = ""
    filename: str = ""
    document_title: str = ""
    source_type: str = ""
    oversize: bool = False

    def __post_init__(self) -> None:
        if self.row_id < 0:
            raise IndexManifestError(ERROR_INVALID_MANIFEST, "row_id 不得为负", detail={"row_id": self.row_id})
        if not self.chunk_id:
            raise IndexManifestError(ERROR_INVALID_MANIFEST, "chunk_id 不得为空")
        if not self.tenant_id:
            raise IndexManifestError(
                ERROR_INVALID_MANIFEST,
                "entry.tenant_id 不得为空（检索过滤的第一判据）",
                detail={"chunk_id": self.chunk_id},
            )
        object.__setattr__(self, "heading_path", tuple(self.heading_path or ()))
        object.__setattr__(self, "acl", tuple(dict(item) for item in (self.acl or ())))

    @property
    def heading_text(self) -> str:
        return self.heading_path[-1] if self.heading_path else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "row_id": self.row_id,
            "chunk_id": self.chunk_id,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "document_version_id": self.document_version_id,
            "chunk_type": self.chunk_type,
            "ordinal": self.ordinal,
            "text": self.text,
            "heading_path": list(self.heading_path),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "acl": [dict(item) for item in self.acl],
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "source_uri": self.source_uri,
            "filename": self.filename,
            "document_title": self.document_title,
            "source_type": self.source_type,
            "oversize": self.oversize,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ManifestEntry:
        try:
            return cls(
                row_id=int(data["row_id"]),
                chunk_id=str(data["chunk_id"]),
                tenant_id=str(data["tenant_id"]),
                document_id=str(data.get("document_id", "")),
                document_version=int(data.get("document_version", 0) or 0),
                document_version_id=str(data.get("document_version_id", "")),
                chunk_type=str(data.get("chunk_type", "child")),
                ordinal=int(data.get("ordinal", 0) or 0),
                text=str(data.get("text", "")),
                heading_path=tuple(str(item) for item in (data.get("heading_path") or ())),
                page_start=data.get("page_start"),
                page_end=data.get("page_end"),
                acl=tuple(dict(item) for item in (data.get("acl") or ())),
                content_hash=str(data.get("content_hash", "")),
                token_count=int(data.get("token_count", 0) or 0),
                char_count=int(data.get("char_count", 0) or 0),
                source_uri=str(data.get("source_uri", "")),
                filename=str(data.get("filename", "")),
                document_title=str(data.get("document_title", "")),
                source_type=str(data.get("source_type", "")),
                oversize=bool(data.get("oversize", False)),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise IndexManifestError(
                ERROR_INVALID_MANIFEST,
                f"manifest 条目无法解析：{error}",
                detail={"raw": dict(data)},
            ) from error


@dataclass(frozen=True, slots=True)
class IndexManifest:
    """索引的自我描述。计划 3.4 点名的五项：索引版本 / embedding 模型与维度 /
    构建时间 / 过滤规则 / Chunk ID 顺序 —— 全部是显式字段，不是「隐含在文件里」。

    ``tenant_id`` 的含义（B7 明确）：**触发本次构建的租户**，用于审计与排障；
    索引本身是全局一份（指针 / 版本目录 / 索引文件都在全局命名空间下），
    条目可以来自多个租户，全部租户记在 ``extra["tenants"]`` 里。
    不要再把它当作「这份索引只能被某个租户检索」的依据 ——
    检索隔离由 ``search_chunk_index`` 的前置过滤负责（踩坑 **B14**）。
    """

    index_name: str
    tenant_id: str
    index_version: int
    embedding_model: str
    embedding_dimension: int
    built_at: str
    chunk_ids: tuple[str, ...] = ()
    entries: tuple[ManifestEntry, ...] = ()
    index_file: str = INDEX_FILENAME
    filter_rule: str = FILTER_RULE
    visibility_policy: str = VISIBILITY_POLICY
    tokenizer_id: str = ""
    chunking_config_version: str = ""
    manifest_version: str = MANIFEST_VERSION
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_ids", tuple(str(item) for item in (self.chunk_ids or ())))
        object.__setattr__(self, "entries", tuple(self.entries or ()))
        if self.index_version < 1:
            raise IndexManifestError(ERROR_INVALID_MANIFEST, "index_version 必须 >= 1")
        if self.embedding_dimension < 1:
            raise IndexManifestError(ERROR_INVALID_MANIFEST, "embedding_dimension 必须 >= 1")
        if not self.index_name or not self.tenant_id:
            raise IndexManifestError(ERROR_INVALID_MANIFEST, "index_name / tenant_id 不得为空")
        if not self.embedding_model:
            raise IndexManifestError(
                ERROR_INVALID_MANIFEST,
                "embedding_model 不得为空（检索时必须与构建时的模型一致，否则向量空间不可比）",
            )
        self.verify()

    # ------------------------------------------------------- 不变量

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_ids)

    def verify(self) -> None:
        """断言四下一致：``chunk_ids`` / ``entries`` / ``row_id`` / 位置。

        这是「向量序号不得再隐式等于 JSONL 行号」的守门人 ——
        任何一处错位都必须在**加载索引时**就抛错，而不是等检索到一条错内容。

        租户维度的不变量（B7 修订）：manifest 里**可以**同时出现多个租户的条目 ——
        索引指针、版本目录与索引文件都是全局一份，隔离由检索层的
        ``tenant_id`` + ``allowed_chunk_ids`` 前置过滤完成（``search_chunk_index``
        本来就是按 ``entry.tenant_id`` 逐条筛的）。因此这里只要求
        **每个条目所属的租户都在 manifest 声明的集合里**，不再要求
        「条目租户 == ``manifest.tenant_id``」：
        后者会让一份全局索引永远无法通过校验（B6 时期的自相矛盾，见踩坑 **B14**）。
        声明的来源是 ``extra["tenants"]``；B6 时期写下的老 manifest 没有这个键，
        此时退回「必须等于 ``manifest.tenant_id``」—— 与老格式完全兼容。
        """

        if len(self.chunk_ids) != len(self.entries):
            raise IndexManifestError(
                ERROR_INVALID_MANIFEST,
                "manifest 的 chunk_ids 与 entries 数量不一致",
                detail={"chunk_ids": len(self.chunk_ids), "entries": len(self.entries)},
            )
        declared_tenants = {str(item) for item in (self.extra.get("tenants") or ())} or {self.tenant_id}
        seen: set[str] = set()
        for position, entry in enumerate(self.entries):
            if entry.row_id != position:
                raise IndexManifestError(
                    ERROR_INVALID_MANIFEST,
                    "entry.row_id 必须等于它在 manifest 中的位置（否则向量与 chunk 会错位）",
                    detail={"position": position, "row_id": entry.row_id, "chunk_id": entry.chunk_id},
                )
            if self.chunk_ids[position] != entry.chunk_id:
                raise IndexManifestError(
                    ERROR_INVALID_MANIFEST,
                    "chunk_ids[position] 与 entries[position].chunk_id 不一致",
                    detail={"position": position},
                )
            if entry.chunk_id in seen:
                raise IndexManifestError(
                    ERROR_INVALID_MANIFEST,
                    "同一份索引内出现重复 chunk_id",
                    detail={"chunk_id": entry.chunk_id},
                )
            seen.add(entry.chunk_id)
            if entry.tenant_id not in declared_tenants:
                raise IndexManifestError(
                    ERROR_INVALID_MANIFEST,
                    "entry.tenant_id 不在 manifest 声明的租户集合里（跨租户共用一份索引时必须能分辨）",
                    detail={
                        "entry": entry.tenant_id,
                        "declared": sorted(declared_tenants),
                        "manifest": self.tenant_id,
                    },
                )

    # ------------------------------------------------------- 序列化

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": self.manifest_version,
            "index_name": self.index_name,
            "tenant_id": self.tenant_id,
            "index_version": self.index_version,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "built_at": self.built_at,
            "filter_rule": self.filter_rule,
            "visibility_policy": self.visibility_policy,
            "tokenizer_id": self.tokenizer_id,
            "chunking_config_version": self.chunking_config_version,
            "chunk_count": self.chunk_count,
            "chunk_ids": list(self.chunk_ids),
            "index_file": self.index_file,
            "extra": dict(self.extra),
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IndexManifest:
        try:
            return cls(
                index_name=str(data["index_name"]),
                tenant_id=str(data["tenant_id"]),
                index_version=int(data["index_version"]),
                embedding_model=str(data["embedding_model"]),
                embedding_dimension=int(data["embedding_dimension"]),
                built_at=str(data.get("built_at", "")),
                chunk_ids=tuple(str(item) for item in (data.get("chunk_ids") or ())),
                entries=tuple(ManifestEntry.from_dict(item) for item in (data.get("entries") or ())),
                index_file=str(data.get("index_file", INDEX_FILENAME)),
                filter_rule=str(data.get("filter_rule", FILTER_RULE)),
                visibility_policy=str(data.get("visibility_policy", VISIBILITY_POLICY)),
                tokenizer_id=str(data.get("tokenizer_id", "")),
                chunking_config_version=str(data.get("chunking_config_version", "")),
                manifest_version=str(data.get("manifest_version", MANIFEST_VERSION)),
                extra=dict(data.get("extra") or {}),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise IndexManifestError(
                ERROR_INVALID_MANIFEST, f"manifest 无法解析：{error}", detail={}
            ) from error

    def fingerprint(self) -> str:
        """内容指纹：判断「索引文件与 manifest 是否仍是同一份东西」。

        只取**影响向量含义**的三项（模型、维度、顺序化 chunk_id），
        不取构建时间 —— 否则「重建出完全相同的内容」会被判成不兼容。
        """

        seed = "|".join(
            [self.embedding_model, str(self.embedding_dimension), *self.chunk_ids]
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def row_ids_for_chunks(self, allowed_chunk_ids: Iterable[str]) -> list[int]:
        """把允许的 ``chunk_id`` 集合映射成 FAISS 行号（**未知 id 一律丢弃**）。

        这是预过滤的入口：FAISS 只接受「允许的行号集合」，因此映射必须在这里做，
        而且未知 id 必须**静默丢弃**而不是报错 —— 调用方的白名单来自数据库，
        与索引之间的短暂不一致（刚发布还没重建）是正常状态，
        安全侧的默认方向是「不在索引里 = 不可见」。
        """

        allowed = set(allowed_chunk_ids)
        if not allowed:
            return []
        return [entry.row_id for entry in self.entries if entry.chunk_id in allowed]


@dataclass(frozen=True, slots=True)
class IndexPointer:
    """``current.json`` 的内容：指向当前生效的版本目录。"""

    index_name: str
    tenant_id: str
    index_version: int
    manifest_path: str
    index_path: str
    fingerprint: str
    switched_at: str
    build_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_name": self.index_name,
            "tenant_id": self.tenant_id,
            "index_version": self.index_version,
            "manifest_path": self.manifest_path,
            "index_path": self.index_path,
            "fingerprint": self.fingerprint,
            "switched_at": self.switched_at,
            "build_id": self.build_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IndexPointer:
        try:
            return cls(
                index_name=str(data["index_name"]),
                tenant_id=str(data["tenant_id"]),
                index_version=int(data["index_version"]),
                manifest_path=str(data["manifest_path"]),
                index_path=str(data["index_path"]),
                fingerprint=str(data.get("fingerprint", "")),
                switched_at=str(data.get("switched_at", "")),
                build_id=str(data.get("build_id", "")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise IndexManifestError(
                ERROR_INVALID_MANIFEST, f"索引指针对无法解析：{error}", detail={}
            ) from error


# ---------------------------------------------------------------- 路径


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def index_root(root: str | Path, index_name: str = DEFAULT_INDEX_NAME) -> Path:
    return Path(root) / CHUNK_INDEX_DIRNAME / index_name


def version_dir_name(index_version: int) -> str:
    return f"v{int(index_version)}"


def version_dir(root: str | Path, index_name: str, index_version: int) -> Path:
    return index_root(root, index_name) / version_dir_name(index_version)


def pointer_path(root: str | Path, index_name: str = DEFAULT_INDEX_NAME) -> Path:
    return index_root(root, index_name) / POINTER_FILENAME


# ---------------------------------------------------------------- 写入


def write_json_atomic(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """先写临时文件再 ``os.replace``：读者永远看不到半截 JSON。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    return target


def write_manifest(directory: str | Path, manifest: IndexManifest) -> Path:
    """把 manifest 写进指定目录（**只写，不切换指针**）。"""

    manifest.verify()
    return write_json_atomic(Path(directory) / MANIFEST_FILENAME, manifest.to_dict())


def read_manifest(path: str | Path) -> IndexManifest:
    target = Path(path)
    if not target.exists():
        raise IndexManifestError(
            ERROR_MANIFEST_MISSING, f"manifest 不存在：{target}", detail={"path": str(target)}
        )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise IndexManifestError(
            ERROR_INVALID_MANIFEST, f"manifest 不是合法 JSON：{error}", detail={"path": str(target)}
        ) from error
    if not isinstance(payload, Mapping):
        raise IndexManifestError(
            ERROR_INVALID_MANIFEST, "manifest 顶层必须是对象", detail={"path": str(target)}
        )
    return IndexManifest.from_dict(payload)


# ---------------------------------------------------------------- 校验


def _require_faiss():
    try:
        import faiss
    except ModuleNotFoundError as error:  # pragma: no cover - 环境缺包时的明确报错
        raise IndexManifestError(
            ERROR_VERIFICATION_FAILED,
            "索引校验需要 faiss；请安装 faiss-cpu",
            detail={},
        ) from error
    return faiss


def verify_index_file(
    index_path: str | Path,
    manifest: IndexManifest,
    *,
    faiss_module: object | None = None,
) -> int:
    """校验「向量数 == manifest 条数 == 向量维度」，返回向量数。

    计划 3.4 原文：「构建到临时目录，验证向量数、manifest 数和向量维度一致后原子切换」。
    三项都要查，缺一项就会出现某类静默损坏：
    · 只查条数 → 维度错的文件能通过（检索时要么崩要么给出无意义分数）；
    · 只查维度 → 少写一条就整体错位（本模块要根治的正是错位）。
    """

    target = Path(index_path)
    if not target.exists():
        raise IndexManifestError(
            ERROR_INDEX_MISSING, f"索引文件不存在：{target}", detail={"path": str(target)}
        )
    faiss_module = faiss_module or _require_faiss()
    index = faiss_module.read_index(str(target))
    total = int(index.ntotal)
    dimension = int(index.d)
    if dimension != manifest.embedding_dimension:
        raise IndexManifestError(
            ERROR_VERIFICATION_FAILED,
            "索引维度与 manifest 不一致",
            detail={"index_dimension": dimension, "manifest_dimension": manifest.embedding_dimension},
        )
    if total != manifest.chunk_count:
        raise IndexManifestError(
            ERROR_VERIFICATION_FAILED,
            "索引向量数与 manifest 条数不一致",
            detail={"index_ntotal": total, "manifest_chunk_count": manifest.chunk_count},
        )
    return total


# ---------------------------------------------------------------- 发布 / 回滚


def publish_build_directory(building_dir: str | Path, final_dir: str | Path) -> Path:
    """把「构建中目录」改名成正式版本目录（同文件系统内 rename，原子）。

    目标目录已存在时只有两种情况：① 上一次同版本号的构建失败留下了残骸，
    ② 有人手工动过目录。这两种都不是「当前生效版本」（生效版本由指针决定，
    而指针只会指向构建成功的目录），因此可以安全清理后改名 ——
    否则一次构建失败就会让同版本号的后续构建永远无法发布。
    """

    source = Path(building_dir)
    target = Path(final_dir)
    if not source.exists():
        raise IndexManifestError(
            ERROR_VERIFICATION_FAILED, f"构建目录不存在：{source}", detail={"path": str(source)}
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    os.replace(source, target)
    return target


def switch_pointer(
    root: str | Path,
    *,
    index_name: str,
    tenant_id: str,
    index_version: int,
    manifest_relpath: str,
    index_relpath: str,
    fingerprint: str,
    build_id: str = "",
) -> IndexPointer:
    """**原子切换**：整个发布过程中唯一被修改的「生效状态」。

    它只有一次 ``os.replace``，因此不存在「指针写了一半」的中间态；
    切换之前的所有失败都发生在临时目录里，当前索引完好。
    """

    pointer = IndexPointer(
        index_name=index_name,
        tenant_id=tenant_id,
        index_version=int(index_version),
        manifest_path=str(manifest_relpath),
        index_path=str(index_relpath),
        fingerprint=str(fingerprint),
        switched_at=utc_now_iso(),
        build_id=str(build_id or ""),
    )
    write_json_atomic(pointer_path(root, index_name), pointer.to_dict())
    return pointer


def read_pointer(root: str | Path, index_name: str = DEFAULT_INDEX_NAME) -> IndexPointer:
    target = pointer_path(root, index_name)
    if not target.exists():
        raise IndexManifestError(
            ERROR_POINTER_MISSING,
            f"索引指针不存在（本租户还没有生效索引）：{target}",
            detail={"path": str(target)},
        )
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise IndexManifestError(
            ERROR_INVALID_MANIFEST, f"索引指针不是合法 JSON：{error}", detail={"path": str(target)}
        ) from error
    return IndexPointer.from_dict(payload)


def load_active_manifest(
    root: str | Path,
    index_name: str = DEFAULT_INDEX_NAME,
) -> tuple[IndexManifest, IndexPointer, Path]:
    """按指针加载当前生效的 manifest，并校验指纹一致。

    指纹不一致说明「指针指的文件被人改过 / 复制错了版本」，
    这时**宁可拒绝加载**（检索层 fail closed）也不要拿一份来路不明的索引去检索。
    """

    pointer = read_pointer(root, index_name)
    manifest_file = Path(root) / pointer.manifest_path
    manifest = read_manifest(manifest_file)
    if pointer.fingerprint and manifest.fingerprint() != pointer.fingerprint:
        raise IndexManifestError(
            ERROR_INVALID_MANIFEST,
            "指针记录的指纹与实际 manifest 不一致（索引被外部改动过）",
            detail={"pointer": pointer.fingerprint, "manifest": manifest.fingerprint()},
        )
    index_file = Path(root) / pointer.index_path
    return manifest, pointer, index_file


def list_versions(root: str | Path, index_name: str) -> list[int]:
    """列出已有的正式版本号（倒序）。带 ``.building`` 后缀的目录不算。"""

    directory = index_root(root, index_name)
    if not directory.exists():
        return []
    versions: list[int] = []
    for item in directory.iterdir():
        if not item.is_dir() or item.name.endswith(BUILDING_SUFFIX):
            continue
        if item.name.startswith("v") and item.name[1:].isdigit():
            versions.append(int(item.name[1:]))
    return sorted(versions, reverse=True)


def resolve_version_directory(root: str | Path, index_name: str, index_version: int) -> Path:
    directory = version_dir(root, index_name, index_version)
    if not directory.exists():
        raise IndexManifestError(
            ERROR_INDEX_MISSING,
            f"索引版本目录不存在：{directory}",
            detail={"index_version": index_version, "path": str(directory)},
        )
    return directory


__all__ = [
    "BUILDING_SUFFIX",
    "CHUNK_INDEX_DIRNAME",
    "DEFAULT_INDEX_NAME",
    "ERROR_INDEX_MISSING",
    "ERROR_INVALID_MANIFEST",
    "ERROR_MANIFEST_MISSING",
    "ERROR_POINTER_MISSING",
    "ERROR_PUBLISH_CONFLICT",
    "ERROR_VERIFICATION_FAILED",
    "FILTER_RULE",
    "INDEX_FILENAME",
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "POINTER_FILENAME",
    "VISIBILITY_POLICY",
    "IndexManifest",
    "IndexManifestError",
    "IndexPointer",
    "ManifestEntry",
    "index_root",
    "list_versions",
    "load_active_manifest",
    "pointer_path",
    "publish_build_directory",
    "read_manifest",
    "read_pointer",
    "resolve_version_directory",
    "switch_pointer",
    "utc_now_iso",
    "verify_index_file",
    "version_dir",
    "version_dir_name",
    "write_json_atomic",
    "write_manifest",
]
