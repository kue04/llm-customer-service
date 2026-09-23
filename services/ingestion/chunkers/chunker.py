"""结构感知切分器（计划 3.2）。

它在这个项目里的定位::

    解析（B3 已完成）  ParsedDocument ──→  [本模块] Chunk[] ──→ 落库 / 索引（B6）

**本模块不是「新增一个功能」，是把断掉的链路接上。** 为什么 chunk 必须自带
租户 / ACL / 页码 / 标题路径：`document_chunks` 表**没有**这些独立列，
它只能整体进 ``metadata_json``，而那是 B7 检索过滤的**唯一**数据来源。
少带一个字段，接线就接不上。

算法流水线（每一步对应计划 3.2 的一条要求）
-------------------------------------------
1. **清洗**（要求 8）：去空白、去页码行、去重复页眉页脚、去完全重复段落。
   删除数量全部进 :class:`~.models.ChunkingStats`，可观测、可解释。
2. **分组**（要求 1）：按 ``(heading_path, page)`` 把连续块切成 section。
   标题块的 ``heading_path`` 按契约（`parsers/base.py`）**包含自己**，
   所以块「标题 + 它的正文」天然落在同一组，不需要重建层级。
3. **成单元**（要求 2 / 3 / 4）：组内把块变成「切分单元」。
   超长段落按 句末标点 → 分号 → 换行 → 按 token 硬切 逐级拆；
   表格按行拆并对每组**重复表头**；列表按**列表项边界**装箱（项内不切）；
   代码块整块不动。
4. **装箱成 child**（要求 5 / 6）：按 ``target_tokens`` 收口、``max_tokens`` 封顶、
   相邻 child 重叠 ``overlap_tokens``（按单元边界取整，不切碎语义单元）；
   每组 child 归属于同组的 parent chunk。
5. **定前缀**（要求 7）：把标题路径作为文本前缀，且**已经在正文里出现过就不再加**。
6. **产物归属**（要求 9）：租户 / 文档 / 版本 / 页码 / 标题 / 来源 / ACL / 字符数
   全部落到 :class:`~.models.Chunk` 与 ``metadata_json``。

三处刻意的取舍（都在任务级审查里标了方向）
------------------------------------------
* ``max_tokens`` 有一个**显式例外**：超过上限的**代码块**整块保留并标 ``oversize``。
  要求 3（代码不从内部截断）与要求 5（不超过 700 tokens）在「一个 800 token 的代码块」
  上直接冲突。选「不损坏内容 + 计入 ``oversize_chunks``」，而不是悄悄切成两半。
  硬切只会发生在**正文段落**上（第 2 条的第 4 级兜底），且单独计数。
* **重叠只在同一个 parent 内部发生**，不跨 parent。跨 parent 重叠会把上一节的正文
  带到下一节，检索时表现为「引用出处对不上」。
* **不产生 chunk 的块**：``image`` / ``page_break``。它们没有可检索文本，
  硬塞进 chunk 只会得到一串 ``[图片]`` 占位（图片内容检索不在本批范围）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import re

from config.chunking_config import CONFIG_VERSION, ChunkConfig
from services.ingestion.parsers.base import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_LIST,
    BLOCK_QUOTE,
    BLOCK_TABLE,
    Block,
    ParsedDocument,
)

from .cleaner import CleanCounters, clean_blocks
from .errors import ERROR_INTERNAL, ChunkingError
from .models import (
    CHUNK_TYPE_CHILD,
    CHUNK_TYPE_PARENT,
    HEADING_SEPARATOR,
    Chunk,
    ChunkContext,
    ChunkingResult,
    ChunkingStats,
    hash_text,
    join_texts,
    make_chunk_id,
)
from .tokenizer import TokenCounter, get_token_counter


#: 切分器实现版本。**切分行为变化时必须提升**，并写进 chunk metadata，
#: 这样以后能识别「同一份文件的旧切分结果」，为增量重建索引提供依据
#: （与解析器 ``parser_version`` 同一思路，见踩坑 B7）。
CHUNKER_VERSION = "1.0"

#: 单元类型
UNIT_PARAGRAPH = "paragraph"
UNIT_QUOTE = "quote"
UNIT_LIST = "list"
UNIT_TABLE = "table"
UNIT_CODE = "code"

#: 标题前缀与正文之间的分隔（单个换行：块内本来就用换行分隔，保持一致）
PREFIX_SEPARATOR = "\n"

#: 句末终止符（中文句号 / 问号 / 叹号与西文对应符号）。
#: 计划写的是「依次按句号、问号」，二者是同一类终止符（句末标点），
#: 因此实现合并为一级 —— 拆分结果与逐级拆一致（见任务级审查的方向标注）。
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])|(?<=[.!?])(?=\s)")
_SEMICOLON_SPLIT_RE = re.compile(r"(?<=[；;])")

#: 列表项起始标记（Markdown 的 ``- * +``、``1.`` ``1)``、``（1）``，以及 HTML 解析出的 ``•``）
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+•·]|\d+[.)]|\(\d+\)|[（(]\d+[）)])\s+")


# ---------------------------------------------------------------- 单元


@dataclass(frozen=True, slots=True)
class Unit:
    """切分单元：装箱的最小粒度。"""

    text: str
    token_count: int
    page: int | None
    kind: str
    heading_path: tuple[str, ...]
    #: 单块已超 ``max_tokens`` 且不可内部切分（代码块、超宽表格行）
    oversize: bool = False
    #: 正文段落走完了四级拆分仍超限，最后按 token 硬切（要求 2 未定义的第 4 级）
    hard_split: bool = False
    #: 列表项自身超限，被迫在项内部拆分（要求 4 的例外）
    list_item_split: bool = False


@dataclass(frozen=True, slots=True)
class _Section:
    """一组「同标题路径 + 同页码」的连续块。"""

    heading_path: tuple[str, ...]
    page: int | None
    blocks: tuple[Block, ...]


@dataclass(frozen=True, slots=True)
class _Draft:
    """装箱中间态：``own_units`` 是本块新内容，``tail_units`` 是从上一块带过来的重叠。"""

    own_units: tuple[Unit, ...]
    tail_units: tuple[Unit, ...] = ()
    prefix: str = ""

    @property
    def units(self) -> tuple[Unit, ...]:
        return self.tail_units + self.own_units


# ---------------------------------------------------------------- 入口


def chunk_document(
    document: ParsedDocument,
    *,
    context: ChunkContext,
    config: ChunkConfig,
) -> ChunkingResult:
    """把 B3 的 ``ParsedDocument`` 切成 chunk。

    **签名里没有裸的 ``tenant_id`` / ``acl`` 参数**（守卫测试断言），
    归属信息由不可变的 :class:`ChunkContext` 承载 —— 见台账 [D-9]。

    空文档（无可切分文本）**不报错**，返回空结果 + 统计。
    理由：解析层已经挡住了「0 字节文件」；到了这里说明文件有内容但全是图片 /
    页眉页脚，这属于「业务上没东西可检索」，是可解释的正常结果，
    不该让整个任务失败（与 B9「OCR 缺失降级为警告」同一取向）。
    """

    if not isinstance(document, ParsedDocument):
        raise ChunkingError(ERROR_INTERNAL, "chunk_document 需要 ParsedDocument")
    if not isinstance(context, ChunkContext):
        raise ChunkingError(ERROR_INTERNAL, "chunk_document 需要 ChunkContext（承载租户 / 版本 / ACL）")
    if not isinstance(config, ChunkConfig):
        raise ChunkingError(ERROR_INTERNAL, "chunk_document 需要 ChunkConfig")

    counter = get_token_counter(config.tokenizer_id)

    if not context.document_title and document.title:
        # 文档标题属于「归属/来源」信息，缺省从解析产物补，避免落库后整列是空
        context = replace(context, document_title=document.title)

    clean_result = clean_blocks(document.blocks, token_counter=counter)
    sections = _group_sections(clean_result.blocks)

    counts: dict[str, int] = {
        "section_count": len(sections),
        "parent_count": 0,
        "child_count": 0,
    }
    chunks: list[Chunk] = []

    for section in sections:
        units = _build_units(section, counter, config, counts)
        if not units:
            continue

        prefix, prefix_tokens = _section_prefix(section, config, counter)
        if prefix:
            # 要求 7 的后半句：正文开头已经就是这段标题（部分解析器会把标题
            # 同时作为 heading 块与正文首行）→ 不再加前缀，避免正文里出现两遍
            if _prefix_would_duplicate(prefix, units[0].text, section.heading_path):
                prefix, prefix_tokens = "", 0
        elif not config.preserve_heading_path and section.heading_path:
            # 关掉前缀时，标题文本必须以正文形式保留 —— 否则标题从 chunk 里彻底消失，
            # 「按标题检索」会整类失效。这是 preserve_heading_path 的语义边界。
            heading_line = HEADING_SEPARATOR.join(section.heading_path)
            heading_unit = Unit(
                text=heading_line,
                token_count=counter.count(heading_line),
                page=section.page,
                kind=UNIT_PARAGRAPH,
                heading_path=section.heading_path,
            )
            units = [heading_unit, *units]

        for parent_units in _split_parent_groups(units, prefix_tokens=prefix_tokens, config=config):
            parent_id: str | None = None
            if config.parent_chunk_enabled:
                parent_text = _render_text(prefix, parent_units)
                parent_oversize = _units_oversize(parent_units)
                chunks.append(
                    _make_chunk(
                        text=parent_text,
                        chunk_type=CHUNK_TYPE_PARENT,
                        context=context,
                        config=config,
                        counter=counter,
                        ordinal=len(chunks),
                        parent_id=None,
                        units=parent_units,
                        heading_path=section.heading_path,
                        oversize=parent_oversize,
                    )
                )
                parent_id = chunks[-1].chunk_id
                counts["parent_count"] += 1
                if parent_oversize:
                    counts["oversize_chunks"] = counts.get("oversize_chunks", 0) + 1

            drafts = _pack_children(
                parent_units,
                prefix=prefix,
                prefix_tokens=prefix_tokens,
                config=config,
                counter=counter,
                counts=counts,
            )
            for draft in drafts:
                text = _render_text(draft.prefix, draft.units)
                if not text:
                    # 理论上不会发生（draft 至少含一个非空单元）；出现即实现有 bug
                    raise ChunkingError(ERROR_INTERNAL, "生成了空 chunk 文本")
                oversize = _units_oversize(draft.units) or counter.count(text) > config.max_tokens
                chunks.append(
                    _make_chunk(
                        text=text,
                        chunk_type=CHUNK_TYPE_CHILD,
                        context=context,
                        config=config,
                        counter=counter,
                        ordinal=len(chunks),
                        parent_id=parent_id,
                        units=draft.units,
                        heading_path=section.heading_path,
                        oversize=oversize,
                    )
                )
                counts["child_count"] += 1
                if oversize:
                    counts["oversize_chunks"] = counts.get("oversize_chunks", 0) + 1

    stats = _build_stats(clean_result.counters, counts)
    return ChunkingResult(chunks=tuple(chunks), stats=stats, config=config, context=context)


# ---------------------------------------------------------------- 分组


def _group_sections(blocks: Sequence[Block]) -> list[_Section]:
    """按 ``(heading_path, page)`` 把连续块分组（计划 3.2 第 1 条的前半句）。

    只看**连续**关系：同样的标题路径在文档里出现两次（两个章节都叫「注意事项」）
    会得到两组，而不是被合并成一组 —— 合并会让 parent chunk 跨越两个不相邻的位置，
    parent 的文本变成两段无关内容的拼接。
    """

    sections: list[_Section] = []
    current_key: tuple[tuple[str, ...], int | None] | None = None
    buffer: list[Block] = []

    for block in blocks:
        key = (block.heading_path, block.page)
        if key != current_key:
            if buffer:
                sections.append(
                    _Section(heading_path=current_key[0], page=current_key[1], blocks=tuple(buffer))
                )
            buffer = []
            current_key = key
        buffer.append(block)

    if buffer and current_key is not None:
        sections.append(_Section(heading_path=current_key[0], page=current_key[1], blocks=tuple(buffer)))
    return sections


def _section_prefix(section: _Section, config: ChunkConfig, counter: TokenCounter) -> tuple[str, int]:
    """标题路径前缀（要求 7）。取消该功能时返回空串，不返回 ``None``（免得多一条空值分支）。"""

    if not config.preserve_heading_path or not section.heading_path:
        return "", 0
    text = HEADING_SEPARATOR.join(section.heading_path)
    return text, counter.count(text)


def _prefix_would_duplicate(prefix: str, first_unit_text: str, heading_path: tuple[str, ...]) -> bool:
    """正文是否已经以这段标题开头（要求 7：**不得重复原文主体**）。"""

    head = (first_unit_text or "").lstrip()
    if not head:
        return False
    if head.startswith(prefix):
        return True
    first_line = head.split("\n", 1)[0].strip()
    if heading_path and first_line == heading_path[-1].strip():
        return True
    return first_line == prefix.strip()


# ---------------------------------------------------------------- 单元构造


def _build_units(
    section: _Section,
    counter: TokenCounter,
    config: ChunkConfig,
    counts: dict[str, int],
) -> list[Unit]:
    """把一组的块转成切分单元。"""

    units: list[Unit] = []
    limit = config.max_tokens

    for block in section.blocks:
        if block.type == BLOCK_HEADING:
            # 标题文本由 heading_path（结构字段）与前缀承载，不单独成块 ——
            # 否则会产出一堆 3 token 的「只有标题」的 chunk，它们没有检索价值，
            # 却会挤占 top-k。
            continue

        if block.type == BLOCK_TABLE:
            units.extend(_table_units(block, counter, limit))
            continue

        if block.type == BLOCK_LIST:
            units.extend(_list_units(block, counter, limit, counts))
            continue

        if block.type == BLOCK_CODE:
            tokens = counter.count(block.text)
            units.append(
                Unit(
                    text=block.text,
                    token_count=tokens,
                    page=block.page,
                    kind=UNIT_CODE,
                    heading_path=block.heading_path or section.heading_path,
                    oversize=tokens > limit,
                )
            )
            continue

        kind = UNIT_QUOTE if block.type == BLOCK_QUOTE else UNIT_PARAGRAPH
        for text, hard in _split_overlong(block.text, counter, limit, counts):
            units.append(
                Unit(
                    text=text,
                    token_count=counter.count(text),
                    page=block.page,
                    kind=kind,
                    heading_path=block.heading_path or section.heading_path,
                    hard_split=hard,
                )
            )

    if not units:
        # 「只有标题、没有正文」的组（目录式文档、连续两级标题）：
        # 若不兜底，这些标题会彻底消失（标题块本身不单独成 chunk）。
        # 前缀去重逻辑会把重复的前缀摘掉，所以这里不会出现「标题写两遍」。
        heading_blocks = [block for block in section.blocks if block.type == BLOCK_HEADING]
        if heading_blocks:
            text = join_texts([block.text for block in heading_blocks])
            units.append(
                Unit(
                    text=text,
                    token_count=counter.count(text),
                    page=heading_blocks[0].page,
                    kind=UNIT_PARAGRAPH,
                    heading_path=section.heading_path,
                )
            )

    return units


def _table_units(block: Block, counter: TokenCounter, limit: int) -> list[Unit]:
    """表格按行分块并**重复表头**（计划 3.2 第 3 条前半句）。

    表头必须跟着每一块走：否则第二块之后的表格文本只剩 ``a | b`` 这样的裸单元格，
    向量检索既匹配不到表头语义，人也读不出列含义。
    """

    columns: list[str] = []
    rows: list[list[str]] = []
    if block.table_json:
        columns = [str(cell) for cell in block.table_json.get("columns", [])]
        rows = [[str(cell) for cell in row] for row in block.table_json.get("rows", [])]
    else:  # pragma: no cover - 契约保证表格块一定有 table_json，这里只是兜底
        lines = block.text.split("\n")
        columns = [lines[0]] if lines else []
        rows = [[line] for line in lines[1:]]

    header = " | ".join(columns)
    header_tokens = counter.count(header)
    page = block.page
    heading_path = block.heading_path
    units: list[Unit] = []

    buffer: list[str] = []
    buffer_tokens = header_tokens

    def flush() -> None:
        if not buffer:
            return
        text = join_texts([header, *buffer])
        units.append(
            Unit(
                text=text,
                token_count=counter.count(text),
                page=page,
                kind=UNIT_TABLE,
                heading_path=heading_path,
            )
        )

    for row in rows:
        line = " | ".join(row)
        line_tokens = counter.count(line)
        if buffer and buffer_tokens + line_tokens > limit:
            flush()
            buffer = []
            buffer_tokens = header_tokens
        if not buffer and header_tokens + line_tokens > limit:
            # 单行本身就装不下（超宽表格）：整行独立成单元并标记，不切单元格 ——
            # 把一个单元格切成两半会让「列含义 → 值」的对应关系彻底断掉
            text = join_texts([header, line])
            units.append(
                Unit(
                    text=text,
                    token_count=counter.count(text),
                    page=page,
                    kind=UNIT_TABLE,
                    heading_path=heading_path,
                    oversize=True,
                )
            )
            continue
        buffer.append(line)
        buffer_tokens += line_tokens

    flush()
    if not rows and header:
        # 只有表头的空表：保留表头（表结构本身就是信息），但不制造空的 rows 段落
        units.append(
            Unit(
                text=header,
                token_count=header_tokens,
                page=page,
                kind=UNIT_TABLE,
                heading_path=heading_path,
            )
        )
    return units


def _list_units(block: Block, counter: TokenCounter, limit: int, counts: dict[str, int]) -> list[Unit]:
    """列表项尽量保持完整（计划 3.2 第 4 条）。

    两种形态都要吃（阶段 2 的既有结论，不存在统一一说）：

    * HTML：**每个 ``<li>`` 已经是一个独立块**，进到这里通常只有一行；
    * Markdown：**连续列表合成一个块**，需要在这里按项边界拆。

    装箱策略：先把块按项边界切成 item，再把 item 贪心装进不超过 ``limit`` 的单元；
    **只有当单个 item 自己就超限时**才进入项内拆分，并计入 ``list_items_split``。
    """

    items = _split_list_items(block.text)
    units: list[Unit] = []
    heading_path = block.heading_path
    page = block.page

    buffer: list[str] = []
    buffer_tokens = 0

    def flush() -> None:
        if not buffer:
            return
        text = join_texts(buffer)
        units.append(
            Unit(
                text=text,
                token_count=counter.count(text),
                page=page,
                kind=UNIT_LIST,
                heading_path=heading_path,
            )
        )

    for item in items:
        item_tokens = counter.count(item)
        if item_tokens > limit:
            flush()
            buffer = []
            buffer_tokens = 0
            counts["list_items_split"] = counts.get("list_items_split", 0) + 1
            for piece, hard in _split_overlong(item, counter, limit, counts):
                units.append(
                    Unit(
                        text=piece,
                        token_count=counter.count(piece),
                        page=page,
                        kind=UNIT_LIST,
                        heading_path=heading_path,
                        hard_split=hard,
                        list_item_split=True,
                    )
                )
            continue
        if buffer and buffer_tokens + item_tokens > limit:
            flush()
            buffer = []
            buffer_tokens = 0
        buffer.append(item)
        buffer_tokens += item_tokens

    flush()
    return units


def _split_list_items(text: str) -> list[str]:
    """按列表项边界切分；没有识别到任何项标记时整块作为一个 item。"""

    lines = text.split("\n")
    items: list[str] = []
    buffer: list[str] = []
    for line in lines:
        if _LIST_ITEM_RE.match(line) and buffer:
            items.append("\n".join(buffer))
            buffer = [line]
            continue
        buffer.append(line)
    if buffer:
        items.append("\n".join(buffer))
    return [item for item in items if item.strip()]


def _split_overlong(
    text: str,
    counter: TokenCounter,
    limit: int,
    counts: dict[str, int],
) -> list[tuple[str, bool]]:
    """超长段落逐级拆分（计划 3.2 第 2 条）。

    级别固定为：**句末标点 → 分号 → 换行 → 按 token 硬切**。
    前三级是计划明确要求的；第 4 级是实现补的兜底 ——
    一个没有任何标点的超长串（OCR 结果的典型形态）如果不硬切，
    就会变成一块 3000 token 的 chunk，把 ``max_tokens`` 这条硬约束直接架空。
    硬切结果会标记 ``hard_split`` 并计入统计，便于事后发现「这份文档的切分质量不好」。
    """

    if counter.count(text) <= limit:
        return [(text, False)]

    pieces = _split_keep(text, _SENTENCE_SPLIT_RE)
    pieces = _escalate(pieces, counter, limit, _SEMICOLON_SPLIT_RE)
    pieces = _escalate(pieces, counter, limit, None)

    out: list[tuple[str, bool]] = []
    hard_count = 0
    for piece in pieces:
        if counter.count(piece) <= limit:
            out.append((piece, False))
            continue
        hard_count += 1
        for chunk_piece in _hard_split(piece, counter, limit):
            out.append((chunk_piece, True))
    if hard_count:
        counts["hard_split_units"] = counts.get("hard_split_units", 0) + hard_count
    return [(piece, hard) for piece, hard in out if piece.strip()]


def _split_keep(text: str, pattern: re.Pattern[str]) -> list[str]:
    """按正则切分并保留分隔符（``re.split`` 用 lookbehind，分隔符留在前一段末尾）。"""

    return [piece for piece in (item.strip() for item in pattern.split(text)) if piece]


def _escalate(
    pieces: list[str],
    counter: TokenCounter,
    limit: int,
    pattern: re.Pattern[str] | None,
) -> list[str]:
    """只对仍然超限的片段进入下一级拆分（``None`` 表示按行拆）。"""

    out: list[str] = []
    for piece in pieces:
        if counter.count(piece) <= limit:
            out.append(piece)
            continue
        if pattern is None:
            out.extend(_split_keep(piece, _NEWLINE_SPLIT_RE) or [piece])
        else:
            out.extend(_split_keep(piece, pattern) or [piece])
    return out


_NEWLINE_SPLIT_RE = re.compile(r"(?<=\n)")


def _hard_split(text: str, counter: TokenCounter, limit: int) -> list[str]:
    """按 token 硬切：用二分找出「计数不超过上限的最长前缀」，保证每片都 <= limit。

    不用「按字符数估算」是因为 tokenizer 是抽象接口（可能是启发式，也可能是真 BPE），
    只有 ``count`` 是可靠契约。
    """

    out: list[str] = []
    rest = text
    while rest and counter.count(rest) > limit:
        low, high = 1, len(rest)
        while low < high:
            mid = (low + high + 1) // 2
            if counter.count(rest[:mid]) <= limit:
                low = mid
            else:
                high = mid - 1
        out.append(rest[:low])
        rest = rest[low:]
    if rest:
        out.append(rest)
    return out


# ---------------------------------------------------------------- 装箱


def _split_parent_groups(
    units: Sequence[Unit],
    *,
    prefix_tokens: int,
    config: ChunkConfig,
) -> list[list[Unit]]:
    """把一组单元按 ``parent_max_tokens`` 切成若干 parent 组。

    为什么要切：一个 section 可能是一整章（几万 token）。让它整体当 parent，
    parent 行会变成一条几十万字符的记录 —— 它既不参与 embedding，
    也没有任何一个检索路径会用到这么长的上下文，只会把库撑大。
    本字段是计划外新增（计划 3.1 只给了 6 个默认值），理由记在台账 [T-3.1]。
    """

    capacity = max(1, config.parent_max_tokens - prefix_tokens)
    groups: list[list[Unit]] = []
    current: list[Unit] = []
    current_tokens = 0

    for unit in units:
        if current and current_tokens + unit.token_count > capacity:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(unit)
        current_tokens += unit.token_count

    if current:
        groups.append(current)
    return groups


def _take_overlap(
    previous_units: Sequence[Unit],
    *,
    target: int,
    budget: int,
) -> tuple[tuple[Unit, ...], int, bool]:
    """从上一块尾部取重叠单元。返回 ``(单元, token 数, 是否因过大而放弃)``。

    三条边界：

    * 取到 **>= target 即停**（按单元边界取整，所以实际重叠可能略大于目标）；
    * 单个超大单元不得把重叠撑到目标的两倍以上 —— 否则新块一半是重复内容；
    * 重叠本身必须装得进预算（``max_tokens - 前缀``），否则退化为不重叠。
    """

    if target <= 0 or not previous_units:
        return (), 0, False

    tail: list[Unit] = []
    accumulated = 0
    for unit in reversed(previous_units):
        if accumulated >= target:
            break
        # 单个单元就超过目标的两倍 → 不搬（否则新块可能一半是重复内容）。
        # 注意这条**不能**只在上限判断后加条件：上一块尾部恰是一个大代码块时，
        # ``accumulated`` 还是 0，那种写法会把整块 500-token 的代码搬进下一块。
        if accumulated + unit.token_count > 2 * target:
            break
        if accumulated + unit.token_count > budget:
            break
        tail.insert(0, unit)
        accumulated += unit.token_count

    if not tail:
        return (), 0, True
    return tuple(tail), accumulated, False


def _pack_children(
    units: Sequence[Unit],
    *,
    prefix: str,
    prefix_tokens: int,
    config: ChunkConfig,
    counter: TokenCounter,
    counts: dict[str, int],
) -> list[_Draft]:
    """把单元装箱成 child chunk（计划 3.2 第 5 / 6 条）。"""

    max_total = config.max_tokens
    target = config.target_tokens
    drafts: list[_Draft] = []

    current_own: list[Unit] = []
    current_tail: tuple[Unit, ...] = ()
    current_tokens = 0

    def flush() -> None:
        nonlocal current_own, current_tail, current_tokens
        if current_own:
            drafts.append(_Draft(own_units=tuple(current_own), tail_units=current_tail, prefix=prefix))
        current_own = []
        current_tail = ()
        current_tokens = 0

    for unit in units:
        # ① 与前缀都装不下 → 该单元独占一块，并放弃这一块的前缀
        if unit.token_count + prefix_tokens > max_total:
            flush()
            counts["prefix_dropped"] = counts.get("prefix_dropped", 0) + 1
            drafts.append(_Draft(own_units=(unit,), tail_units=(), prefix=""))
            continue

        # ② 装不下了 → 收口，开新块时带重叠
        if current_own and current_tokens + unit.token_count > max_total:
            flush()

        if not current_own:
            tail, tail_tokens, gave_up = _take_overlap(
                _last_units(drafts),
                target=config.overlap_tokens,
                budget=max_total - prefix_tokens,
            )
            if gave_up and config.overlap_tokens > 0 and drafts:
                counts["overlap_skipped"] = counts.get("overlap_skipped", 0) + 1
            if tail and unit.token_count + prefix_tokens + tail_tokens > max_total:
                # 重叠挤掉了正文：宁可不要重叠，也不能丢内容
                counts["overlap_skipped"] = counts.get("overlap_skipped", 0) + 1
                tail, tail_tokens = (), 0
            if tail:
                counts["overlap_applied"] = counts.get("overlap_applied", 0) + 1
                current_tail = tail
                current_tokens = prefix_tokens + tail_tokens
            else:
                current_tail = ()
                current_tokens = prefix_tokens

        current_own.append(unit)
        current_tokens += unit.token_count

        if current_tokens >= target:
            flush()

    flush()
    _merge_short_tail(drafts, prefix_tokens=prefix_tokens, config=config, counter=counter, counts=counts)
    return drafts


def _last_units(drafts: Sequence[_Draft]) -> tuple[Unit, ...]:
    """上一个 draft 的**新内容**单元（重叠取它，不取它自带的 tail，避免重叠套重叠）。"""

    return drafts[-1].own_units if drafts else ()


def _merge_short_tail(
    drafts: list[_Draft],
    *,
    prefix_tokens: int,
    config: ChunkConfig,
    counter: TokenCounter,
    counts: dict[str, int],
) -> None:
    """最后一块小于 ``min_tokens`` 时并入前一块（``min_tokens`` 的用处就在这里）。

    合并后可能略超 ``target_tokens``，只要不超 ``max_tokens`` 就允许 ——
    ``target`` 是软目标，``max`` 才是硬约束。
    合并的是「前一块的新内容 + 本块的新内容」，本块携带的重叠单元不重复计入。
    """

    if not drafts:
        return

    if len(drafts) == 1:
        if _draft_token_count(drafts[0], counter) < config.min_tokens:
            counts["below_min_chunks"] = counts.get("below_min_chunks", 0) + 1
        return

    last = drafts[-1]
    previous = drafts[-2]
    if _draft_token_count(last, counter) >= config.min_tokens:
        return

    merged_units = previous.units + last.own_units
    if prefix_tokens + _units_tokens(merged_units) <= config.max_tokens:
        drafts[-2] = _Draft(
            own_units=previous.own_units + last.own_units,
            tail_units=previous.tail_units,
            prefix=previous.prefix,
        )
        drafts.pop()
        counts["merged_short_tail"] = counts.get("merged_short_tail", 0) + 1
    else:
        counts["below_min_chunks"] = counts.get("below_min_chunks", 0) + 1


# ---------------------------------------------------------------- 工具


def _units_tokens(units: Sequence[Unit]) -> int:
    return sum(unit.token_count for unit in units)


def _units_oversize(units: Sequence[Unit]) -> bool:
    return any(unit.oversize for unit in units)


def _draft_token_count(draft: _Draft, counter: TokenCounter) -> int:
    """按**最终文本**算 token（含前缀与重叠），因为 min/target/max 约束的是入库文本。"""

    return counter.count(_render_text(draft.prefix, draft.units))


def _render_text(prefix: str, units: Sequence[Unit]) -> str:
    """拼出 chunk 文本：``标题前缀 + 换行 + 正文``。"""

    body = join_texts([unit.text for unit in units])
    if prefix and body:
        return f"{prefix}{PREFIX_SEPARATOR}{body}"
    return prefix or body


def _page_range(units: Sequence[Unit]) -> tuple[int | None, int | None]:
    """页码范围。

    无页码概念（md / html / docx）一律返回 ``(None, None)`` ——
    **不能退化成 0 或 1**，否则「第 1 页」与「没有页码」在数据里长得一样，
    B7 按页码过滤时会误伤。
    """

    pages = [unit.page for unit in units if unit.page is not None]
    if not pages:
        return None, None
    return min(pages), max(pages)


def _make_chunk(
    *,
    text: str,
    chunk_type: str,
    context: ChunkContext,
    config: ChunkConfig,
    counter: TokenCounter,
    ordinal: int,
    parent_id: str | None,
    units: Sequence[Unit],
    heading_path: tuple[str, ...],
    oversize: bool,
) -> Chunk:
    page_start, page_end = _page_range(units)
    metadata = dict(context.to_metadata())
    metadata.update(
        {
            "chunk_type": chunk_type,
            "ordinal": ordinal,
            "page_start": page_start,
            "page_end": page_end,
            "heading_path": list(heading_path),
            "heading_text": heading_path[-1] if heading_path else "",
            "char_count": len(text),
            "oversize": bool(oversize),
            "overlap_tokens": config.overlap_tokens,
            "tokenizer_id": config.tokenizer_id,
            "chunker_version": CHUNKER_VERSION,
            "chunking_config_version": CONFIG_VERSION,
        }
    )
    chunk_id = make_chunk_id(
        context,
        chunk_type=chunk_type,
        ordinal=ordinal,
        text=text,
        tokenizer_id=config.tokenizer_id,
        chunker_version=CHUNKER_VERSION,
    )
    return Chunk(
        chunk_id=chunk_id,
        text=text,
        token_count=counter.count(text),
        char_count=len(text),
        parent_chunk_id=parent_id,
        chunk_type=chunk_type,
        tenant_id=context.tenant_id,
        document_id=context.document_id,
        document_version=context.document_version,
        document_version_id=context.document_version_id,
        page_start=page_start,
        page_end=page_end,
        heading_path=heading_path,
        acl=context.acl,
        content_hash=hash_text(text),
        ordinal=ordinal,
        metadata_json=metadata,
    )


def _build_stats(clean_counters: CleanCounters, counts: dict[str, int]) -> ChunkingStats:
    return ChunkingStats(
        input_blocks=clean_counters.input_blocks,
        skipped_non_text_blocks=clean_counters.skipped_non_text_blocks,
        dropped_empty=clean_counters.dropped_empty,
        dropped_page_number=clean_counters.dropped_page_number,
        dropped_running_header=clean_counters.dropped_running_header,
        dropped_duplicate=clean_counters.dropped_duplicate,
        section_count=counts.get("section_count", 0),
        parent_count=counts.get("parent_count", 0),
        child_count=counts.get("child_count", 0),
        oversize_chunks=counts.get("oversize_chunks", 0),
        overlap_applied=counts.get("overlap_applied", 0),
        overlap_skipped=counts.get("overlap_skipped", 0),
        merged_short_tail=counts.get("merged_short_tail", 0),
        below_min_chunks=counts.get("below_min_chunks", 0),
        hard_split_units=counts.get("hard_split_units", 0),
        list_items_split=counts.get("list_items_split", 0),
        prefix_dropped=counts.get("prefix_dropped", 0),
    )


__all__ = [
    "CHUNKER_VERSION",
    "PREFIX_SEPARATOR",
    "UNIT_CODE",
    "UNIT_LIST",
    "UNIT_PARAGRAPH",
    "UNIT_QUOTE",
    "UNIT_TABLE",
    "Unit",
    "chunk_document",
]
