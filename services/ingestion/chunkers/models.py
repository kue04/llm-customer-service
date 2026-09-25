"""切分器的值对象：``ChunkContext`` / ``Chunk`` / ``ChunkingStats`` / ``ChunkingResult``。

这个模块**刻意不 import** ``services.ingestion.models``（那会间接拉进 SQLAlchemy），
原因见本包 ``__init__`` 的守卫约定：切分器与解析器同类，是**纯函数**，
不允许有任何数据库依赖 —— 这条约束由 ``tests/test_chunking.py`` 的
``test_chunker_modules_do_not_depend_on_database`` 断言，而不是靠纪律。

因此 ACL 的两个枚举在这里**镜像**了一份（``ACL_SUBJECT_TYPES`` / ``ACL_PERMISSIONS``），
并由 ``test_acl_enums_mirror_models`` 断言与 ``services.ingestion.models`` 逐值一致。
「镜像 + 断言」而不是「直接 import」：前者保持纯函数边界，后者把数据库拉进切分器。
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
from typing import Any

from config.chunking_config import ChunkConfig

from .errors import ERROR_INVALID_CONTEXT, ChunkingError


# ---------------------------------------------------------------- 枚举镜像

#: 与 ``services.ingestion.models.ACL_SUBJECT_TYPES`` 一致（由测试断言）
ACL_SUBJECT_TYPES: tuple[str, ...] = ("user", "role", "tenant", "group")

#: 与 ``services.ingestion.models.ACL_PERMISSIONS`` 一致（由测试断言）
ACL_PERMISSIONS: tuple[str, ...] = ("read", "write", "review", "publish", "delete")

#: chunk 类型。parent 承载「一组的完整上下文」，child 是真正被检索/embedding 的单位。
CHUNK_TYPE_PARENT = "parent"
CHUNK_TYPE_CHILD = "child"
CHUNK_TYPES: tuple[str, ...] = (CHUNK_TYPE_PARENT, CHUNK_TYPE_CHILD)

#: 标题路径在文本前缀里的连接符
HEADING_SEPARATOR = " > "


@dataclass(frozen=True, slots=True)
class AclEntry:
    """一条资源级 ACL 授权，形状对齐 ``document_acl`` 表。

    为什么把 ACL 放进 chunk：计划 3.2 第 9 条要求「每块保存……ACL 和租户信息」，
    而 ``document_chunks`` 表**没有** acl / tenant 独立列，
    所以它必须能整体放进 ``metadata_json``（B7 检索过滤的唯一来源）。
    """

    subject_type: str
    subject_id: str
    permission: str

    def __post_init__(self) -> None:
        if self.subject_type not in ACL_SUBJECT_TYPES:
            raise ValueError(f"未知的 ACL 主体类型：{self.subject_type!r}")
        if self.permission not in ACL_PERMISSIONS:
            raise ValueError(f"未知的 ACL 权限：{self.permission!r}")
        if not str(self.subject_id).strip():
            raise ValueError("ACL 主体 id 不得为空")

    def to_dict(self) -> dict[str, str]:
        return {
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "permission": self.permission,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AclEntry:
        try:
            return cls(
                subject_type=str(data["subject_type"]),
                subject_id=str(data["subject_id"]),
                permission=str(data["permission"]),
            )
        except KeyError as exc:  # 缺字段要给得出是哪个字段
            raise ValueError(f"ACL 条目缺少字段：{exc.args[0]}") from exc


def normalize_acl(entries: Iterable[AclEntry | Mapping[str, Any]] | None) -> tuple[AclEntry, ...]:
    """把外部传入的 ACL 归一成不可变元组（接受 dict，便于从 DB 行直接构造）。"""

    if not entries:
        return ()
    normalized: list[AclEntry] = []
    for entry in entries:
        normalized.append(entry if isinstance(entry, AclEntry) else AclEntry.from_mapping(entry))
    return tuple(normalized)


# ---------------------------------------------------------------- 上下文


@dataclass(frozen=True, slots=True)
class ChunkContext:
    """切分器需要的「归属信息」，由调用方（流水线 3.3）构造。

    这是本批唯一需要拍板的设计点（见台账 [D-9]）：计划一边要求
    「切分器不得接收 ``tenant_id``」，一边要求 chunk 输出**必须包含**
    ``tenant_id`` / ``document_id`` / ``document_version`` / ``acl``。

    解法是把这些字段收进一个**不可变的上下文对象**：
    - 切分器签名里没有裸的 ``tenant_id`` 参数 —— 与解析器守卫（[D-5]）同源，
      守的是「不要把租户当成一个可随手传进来的散装参数」；
    - 但权限元数据仍然有确定的来源，否则 B7 的检索过滤没有数据可用；
    - 上下文对象整体可以 JSON 序列化进 ``metadata_json``，
      正好对上「``tenant_id`` / ``acl`` 没有独立列」这条约束。
    """

    tenant_id: str
    document_id: str
    document_version_id: str
    document_version: int
    acl: tuple[AclEntry, ...] = ()
    source_uri: str = ""
    filename: str = ""
    source_type: str = ""
    document_title: str = ""

    def __post_init__(self) -> None:
        for name in ("tenant_id", "document_id", "document_version_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ChunkingError(
                    ERROR_INVALID_CONTEXT,
                    f"ChunkContext.{name} 不得为空",
                    detail={"field": name},
                )
        if not isinstance(self.document_version, int) or isinstance(self.document_version, bool):
            raise ChunkingError(ERROR_INVALID_CONTEXT, "document_version 必须是整数", detail={"field": "document_version"})
        if self.document_version < 1:
            raise ChunkingError(
                ERROR_INVALID_CONTEXT,
                "document_version 必须 >= 1（对应 document_versions.version 的 CHECK 约束）",
                detail={"field": "document_version", "value": self.document_version},
            )
        object.__setattr__(self, "acl", normalize_acl(self.acl))

    @property
    def acl_dicts(self) -> list[dict[str, str]]:
        return [entry.to_dict() for entry in self.acl]

    def to_metadata(self) -> dict[str, Any]:
        """没有独立列、必须进 ``metadata_json`` 的那部分归属信息。"""

        return {
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "document_version_id": self.document_version_id,
            "document_version": self.document_version,
            "acl": self.acl_dicts,
            "document_title": self.document_title,
            "source_uri": self.source_uri,
            "filename": self.filename,
            "source_type": self.source_type,
        }


# ---------------------------------------------------------------- Chunk


@dataclass(frozen=True, slots=True)
class Chunk:
    """切分产物。字段**至少**覆盖计划 3.2 列出的 12 项。

    与计划字段名的两处对应关系（[D-9] 记录）：

    * ``document_version`` 在计划里是单个名字，但落库需要的是
      ``document_versions.id``（``insert_chunks`` 的参数叫 ``document_version_id``），
      而「版本号」本身也有查询价值 —— 因此两个都保留：
      ``document_version``（int，版本序号）与 ``document_version_id``（str，外键指向）。
    * ``acl`` 在计划里没有规定形状，这里定为 ``tuple[AclEntry, ...]``，
      既能 JSON 序列化，也能直接进 ``metadata_json``。

    另外三个计划没要求、但落库与可观测性需要的字段：
    ``chunk_type``（parent / child，父必须先于子写入）、
    ``char_count``（第 9 条要求「token/字符数」）、
    ``ordinal``（输出序号，便于排查顺序问题）。
    """

    chunk_id: str
    text: str
    token_count: int
    char_count: int
    parent_chunk_id: str | None
    chunk_type: str
    tenant_id: str
    document_id: str
    document_version: int
    document_version_id: str
    page_start: int | None
    page_end: int | None
    heading_path: tuple[str, ...]
    acl: tuple[AclEntry, ...]
    content_hash: str
    ordinal: int
    metadata_json: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.chunk_type not in CHUNK_TYPES:
            raise ValueError(f"未知的 chunk 类型：{self.chunk_type!r}")
        object.__setattr__(self, "heading_path", tuple(self.heading_path or ()))
        object.__setattr__(self, "acl", normalize_acl(self.acl))
        if self.chunk_type == CHUNK_TYPE_PARENT and self.parent_chunk_id is not None:
            raise ValueError("parent chunk 不得再有 parent_chunk_id")
        # child 允许 parent_chunk_id 为空：``parent_chunk_enabled=False`` 时
        # 刻意不产出 parent 行（计划 3.1 允许关掉），此时 child 直接入库。

    @property
    def heading_text(self) -> str:
        """当前标题（标题路径的最后一段）。"""

        return self.heading_path[-1] if self.heading_path else ""

    @property
    def is_oversize(self) -> bool:
        return bool(self.metadata_json.get("oversize"))

    def to_dict(self) -> dict[str, Any]:
        """完整字典形式（含全部 12+ 字段），便于任务查询 / 调试输出。"""

        return {
            "chunk_id": self.chunk_id,
            "parent_chunk_id": self.parent_chunk_id,
            "chunk_type": self.chunk_type,
            "text": self.text,
            "token_count": self.token_count,
            "char_count": self.char_count,
            "tenant_id": self.tenant_id,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "document_version_id": self.document_version_id,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "heading_path": list(self.heading_path),
            "heading_text": self.heading_text,
            "acl": [entry.to_dict() for entry in self.acl],
            "content_hash": self.content_hash,
            "ordinal": self.ordinal,
            "metadata_json": dict(self.metadata_json),
        }

    def to_insert_payload(self) -> dict[str, Any]:
        """``repository.insert_chunks`` 直接可用的载荷。

        ``insert_chunks`` 的必填字段是 ``chunk_id`` / ``text`` / ``token_count`` /
        ``content_hash``，可选 ``parent_chunk_id`` / ``metadata_json``。
        ``tenant_id`` 与 ``document_version_id`` 是 ``insert_chunks`` 的**独立参数**，
        因此不放在本载荷里（避免同一事实出现两份、以后改一处漏一处）。
        """

        return {
            "chunk_id": self.chunk_id,
            "parent_chunk_id": self.parent_chunk_id,
            "text": self.text,
            "token_count": self.token_count,
            "content_hash": self.content_hash,
            "metadata_json": dict(self.metadata_json),
        }


# ---------------------------------------------------------------- 统计与结果


@dataclass(frozen=True, slots=True)
class ChunkingStats:
    """切分的「过程统计」。

    计划 3.2 里有三类**删除 / 妥协**行为（去页眉页脚、去重复段落、代码块超限），
    如果只在内部悄悄做掉，就没法回答「这份文档为什么少了一段」。
    统计就是这类行为的可观测出口 —— 流水线（3.3）可以把它写进任务日志。
    """

    input_blocks: int = 0
    skipped_non_text_blocks: int = 0
    dropped_empty: int = 0
    dropped_page_number: int = 0
    dropped_running_header: int = 0
    dropped_duplicate: int = 0
    section_count: int = 0
    parent_count: int = 0
    child_count: int = 0
    oversize_chunks: int = 0
    overlap_applied: int = 0
    overlap_skipped: int = 0
    merged_short_tail: int = 0
    below_min_chunks: int = 0
    hard_split_units: int = 0
    list_items_split: int = 0
    prefix_dropped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__slots__}


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    """切分结果：chunk 列表 + 统计 + 生效配置。

    为什么不只返回 ``list[Chunk]``（交接文档给的参考签名）：
    - **统计**没有别的出口（删了什么只有切分器自己知道），丢掉就没法解释结果；
    - **生效配置**要写进 ``document_versions.metadata_json``（计划 3.1），
      让结果自带配置可以避免流水线再去猜一次「刚才是用哪份配置切的」。

    为了不牺牲易用性，实现了 ``__iter__`` / ``__len__`` / ``__getitem__``，
    所以 ``for chunk in result``、``len(result)``、``result[0]`` 都照常可用。
    """

    chunks: tuple[Chunk, ...] = ()
    stats: ChunkingStats = field(default_factory=ChunkingStats)
    config: ChunkConfig | None = None
    context: ChunkContext | None = None

    # ------------------------------------------------------- 序列协议
    def __iter__(self) -> Iterator[Chunk]:
        return iter(self.chunks)

    def __len__(self) -> int:
        return len(self.chunks)

    def __getitem__(self, index: int | slice) -> Chunk | tuple[Chunk, ...]:
        return self.chunks[index]

    # ------------------------------------------------------- 便捷视图
    @property
    def parents(self) -> tuple[Chunk, ...]:
        return tuple(chunk for chunk in self.chunks if chunk.chunk_type == CHUNK_TYPE_PARENT)

    @property
    def children(self) -> tuple[Chunk, ...]:
        return tuple(chunk for chunk in self.chunks if chunk.chunk_type == CHUNK_TYPE_CHILD)

    @property
    def empty(self) -> bool:
        return not self.chunks

    @property
    def config_payload(self) -> dict[str, Any]:
        """生效配置 + tokenizer 标识（计划 3.1：每个文档版本都要保存）。"""

        return self.config.storage_payload() if self.config is not None else {}

    def to_insert_plan(self) -> list[dict[str, Any]]:
        """``insert_chunks`` 的入参序列（父先于子，按输出顺序）。"""

        return [chunk.to_insert_payload() for chunk in self.chunks]

    def summary(self) -> str:
        return (
            f"blocks={self.stats.input_blocks} sections={self.stats.section_count} "
            f"parents={len(self.parents)} children={len(self.children)} "
            f"oversize={self.stats.oversize_chunks} dropped(running_header={self.stats.dropped_running_header}, "
            f"duplicate={self.stats.dropped_duplicate}, empty={self.stats.dropped_empty})"
        )


def make_chunk_id(
    context: ChunkContext,
    *,
    chunk_type: str,
    ordinal: int,
    text: str,
    tokenizer_id: str,
    chunker_version: str,
) -> str:
    """确定性 chunk id。

    为什么不用 uuid4 / 自增：**幂等重跑**要求「同样的输入得到同样的 id」，
    否则重试一次就会在 ``document_chunks`` 里多出一份内容相同、id 不同的副本，
    而 ``uq(document_version_id, chunk_id)`` 也挡不住。

    为什么要带 ``ordinal`` 而不仅是文本 hash：同一版本里两段完全相同的文本
    （标题、免责声明）会得到相同 hash，只用 hash 会撞唯一约束。
    ``ordinal + 文本 hash`` 既唯一又确定。

    为什么要带 ``tenant_id``：chunk id 会进索引（B6 的 manifest），
    跨租户共用一份索引时，id 空间必须天然隔离。
    """

    seed = "|".join(
        [
            context.tenant_id,
            context.document_id,
            context.document_version_id,
            str(context.document_version),
            chunk_type,
            str(ordinal),
            tokenizer_id,
            chunker_version,
            hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        ]
    )
    prefix = "p" if chunk_type == CHUNK_TYPE_PARENT else "c"
    return f"{prefix}_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]}"


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def join_texts(parts: Sequence[str]) -> str:
    """按段落拼接，压缩多余空行（表头 + 行之间的单换行要保留）。"""

    return "\n".join(part for part in parts if part)


__all__ = [
    "ACL_PERMISSIONS",
    "ACL_SUBJECT_TYPES",
    "CHUNK_TYPES",
    "CHUNK_TYPE_CHILD",
    "CHUNK_TYPE_PARENT",
    "HEADING_SEPARATOR",
    "AclEntry",
    "Chunk",
    "ChunkContext",
    "ChunkingResult",
    "ChunkingStats",
    "hash_text",
    "join_texts",
    "make_chunk_id",
    "normalize_acl",
]
