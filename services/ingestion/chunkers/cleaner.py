"""块级清洗（计划 3.2 第 8 条：去除空白、重复页眉页脚和完全重复段落）。

这一层只做**「这条块该不该留下、文本该怎么规范化」**的判断，
不做切分、不碰 token 预算、不产生 chunk。把它单独拆出来的理由：

1. 清洗规则全部是**有误伤风险的启发式**（页眉页脚、页码、重复段落），
   混在切分主流程里会让「少了一段正文」这种问题无法单独定位与单独测试；
2. 每条规则的删除数量都进 :class:`CleanCounters`，
   于是「这份文档为什么少了一段」有数字可查，而不是靠猜。

三处刻意的保守取舍（方向已在任务级审查里标注）：

* **重复段落只在 ``paragraph`` 类型上做整段去重**（不覆盖 list / table / code）：
  表格与列表重复出现往往是有意义的结构（同一张表在两个章节各出现一次），
  删掉会真的丢内容；而计划原文说的是「完全重复**段落**」；
* **页眉页脚判定要求同一文本出现在 >= 3 个不同页码上**，
  且只对「短块 + 非标题」生效 —— 阈值低到 2 会把「上下两页各有一段相同的话」误杀；
* **页码行只在该页的首块或末块位置才删**：一个孤立出现的 ``2024``
  可能是正文里的年份，不是页码。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
import re

from services.ingestion.parsers.base import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_LIST,
    BLOCK_PAGE_BREAK,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    Block,
    render_table_text,
)

from .tokenizer import TokenCounter


#: 页眉页脚候选块的最大 token 数（长文本不可能是页眉）
RUNNING_HEADER_MAX_TOKENS = 16

#: 判定为「重复页眉页脚」所需的最小不同页数
RUNNING_HEADER_MIN_PAGES = 3

#: 页码行的最大字符数（超过就不像页码了）
PAGE_NUMBER_MAX_CHARS = 12

#: 不承载可检索文本的块类型（结构标记，不进入 chunk）
NON_TEXT_BLOCK_TYPES: tuple[str, ...] = (BLOCK_IMAGE, BLOCK_PAGE_BREAK)

#: 参与「完全重复段落」去重的块类型（刻意收窄，见模块 docstring）
DEDUP_BLOCK_TYPES: tuple[str, ...] = (BLOCK_PARAGRAPH,)

#: 参与「页码行」删除的块类型（同样收窄：表格 / 列表里的纯数字多半是内容）
PAGE_NUMBER_BLOCK_TYPES: tuple[str, ...] = (BLOCK_PARAGRAPH,)

#: 规范化时**保留行首缩进**的块类型。
#: 代码的缩进是语法的一部分；列表的缩进是**嵌套层级**（``- 甲`` / ``  - 甲.1``），
#: 一起 strip 掉会把结构信息抹平成「两行并列的项」。
INDENT_PRESERVING_BLOCK_TYPES: tuple[str, ...] = (BLOCK_CODE, BLOCK_LIST)

_INVISIBLE_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\x00\x0c]")

_PAGE_NUMBER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\d{1,5}"),  # 12
    re.compile(r"\d{1,5}\s*/\s*\d{1,5}"),  # 12 / 30
    re.compile(r"[-—–~·]\s*\d{1,5}\s*[-—–~·]"),  # - 12 -
    re.compile(r"第\s*\d{1,5}\s*页(?:\s*[,/]?\s*共\s*\d{1,5}\s*页)?"),  # 第 12 页 / 第12页 共30页
    re.compile(r"page\s*\d{1,5}(?:\s*of\s*\d{1,5})?", re.IGNORECASE),
)


# ---------------------------------------------------------------- 文本规范化


def normalize_text(text: str, *, keep_indent: bool = False) -> str:
    """规范化块文本。

    * 去掉零宽字符与换页符（``\\x0c`` 在文本层没有检索价值）；
    * 统一换行符；
    * 去掉每行行尾空白；
    * ``keep_indent=True``（代码块）保留行首缩进 —— 缩进是代码语义的一部分，
      顺手 strip 会让代码块内容与原文不一致；
    * 折叠连续空行、去掉首尾空行。
    """

    if not isinstance(text, str) or not text:
        return ""
    cleaned = _INVISIBLE_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in cleaned.split("\n")]
    if not keep_indent:
        lines = [line.strip() for line in lines]

    out: list[str] = []
    for line in lines:
        if not line.strip():
            if not out or out[-1] == "":
                continue
            out.append("")
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return "\n".join(out)


def is_page_number_like(text: str) -> bool:
    """是否为「纯页码」形态（只做形态判断，位置判断在调用方）。"""

    candidate = (text or "").strip()
    if not candidate or len(candidate) > PAGE_NUMBER_MAX_CHARS:
        return False
    return any(pattern.fullmatch(candidate) for pattern in _PAGE_NUMBER_PATTERNS)


# ---------------------------------------------------------------- 结果对象


@dataclass(frozen=True, slots=True)
class CleanCounters:
    """删除计数（全部进 :class:`ChunkingStats`，让删除行为可观测）。"""

    input_blocks: int = 0
    skipped_non_text_blocks: int = 0
    dropped_empty: int = 0
    dropped_page_number: int = 0
    dropped_running_header: int = 0
    dropped_duplicate: int = 0
    page_count: int = 0

    def to_dict(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in self.__slots__}


@dataclass(frozen=True, slots=True)
class CleanResult:
    blocks: tuple[Block, ...]
    counters: CleanCounters
    running_headers: tuple[str, ...] = ()


# ---------------------------------------------------------------- 主流程


def detect_running_headers(blocks: Sequence[Block], *, token_counter: TokenCounter) -> tuple[str, ...]:
    """找出「在 >= 3 个不同页码上重复出现的短块」文本。"""

    pages_by_text: dict[str, set[int]] = {}
    for block in blocks:
        if block.page is None or block.type == BLOCK_HEADING:
            continue
        if token_counter.count(block.text) > RUNNING_HEADER_MAX_TOKENS:
            continue
        pages_by_text.setdefault(block.text, set()).add(block.page)

    return tuple(
        sorted(text for text, pages in pages_by_text.items() if len(pages) >= RUNNING_HEADER_MIN_PAGES)
    )


def clean_blocks(blocks: Sequence[Block], *, token_counter: TokenCounter) -> CleanResult:
    """清洗块的顺序：规范化 → 去空 → 去页码行 → 去页眉页脚 → 去重复段落。"""

    counters = {"input_blocks": len(blocks)}

    # ---- 1) 规范化 + 去掉没有可检索文本的块
    normalized: list[Block] = []
    for block in blocks:
        if block.type in NON_TEXT_BLOCK_TYPES:
            counters["skipped_non_text_blocks"] = counters.get("skipped_non_text_blocks", 0) + 1
            continue
        text = normalize_text(block.text, keep_indent=block.type in INDENT_PRESERVING_BLOCK_TYPES)
        if not text and block.type == BLOCK_TABLE and block.table_json:
            # 表格块的可检索文本以 table_json 为准（解析器已渲染，这里只兜底）
            text = normalize_text(render_table_text(block.table_json))
        if not text:
            counters["dropped_empty"] = counters.get("dropped_empty", 0) + 1
            continue
        normalized.append(block if block.text == text else replace(block, text=text))

    pages = sorted({block.page for block in normalized if block.page is not None})
    counters["page_count"] = len(pages)

    # ---- 2) 页码行：仅当它位于该页的首块或末块（位置判据见模块 docstring）
    first_last: dict[int, tuple[int, int]] = {}
    for index, block in enumerate(normalized):
        if block.page is None:
            continue
        low, high = first_last.get(block.page, (index, index))
        first_last[block.page] = (min(low, index), max(high, index))

    after_page_numbers: list[Block] = []
    for index, block in enumerate(normalized):
        if block.page is not None and block.type in PAGE_NUMBER_BLOCK_TYPES:
            bounds = first_last.get(block.page)
            if bounds is not None and index in bounds and is_page_number_like(block.text):
                counters["dropped_page_number"] = counters.get("dropped_page_number", 0) + 1
                continue
        after_page_numbers.append(block)

    # ---- 3) 重复页眉页脚
    running_headers = set(detect_running_headers(after_page_numbers, token_counter=token_counter))
    after_headers: list[Block] = []
    for block in after_page_numbers:
        if block.text in running_headers:
            counters["dropped_running_header"] = counters.get("dropped_running_header", 0) + 1
            continue
        after_headers.append(block)

    # ---- 4) 完全重复段落（保留首次出现）
    seen: set[str] = set()
    kept: list[Block] = []
    for block in after_headers:
        if block.type in DEDUP_BLOCK_TYPES:
            if block.text in seen:
                counters["dropped_duplicate"] = counters.get("dropped_duplicate", 0) + 1
                continue
            seen.add(block.text)
        kept.append(block)

    return CleanResult(
        blocks=tuple(kept),
        counters=CleanCounters(
            input_blocks=counters.get("input_blocks", 0),
            skipped_non_text_blocks=counters.get("skipped_non_text_blocks", 0),
            dropped_empty=counters.get("dropped_empty", 0),
            dropped_page_number=counters.get("dropped_page_number", 0),
            dropped_running_header=counters.get("dropped_running_header", 0),
            dropped_duplicate=counters.get("dropped_duplicate", 0),
            page_count=counters.get("page_count", 0),
        ),
        running_headers=tuple(sorted(running_headers)),
    )


def counters_to_mapping(counters: CleanCounters) -> Mapping[str, int]:
    """只读视图，便于流水线日志直接消费。"""

    return counters.to_dict()


__all__ = [
    "DEDUP_BLOCK_TYPES",
    "INDENT_PRESERVING_BLOCK_TYPES",
    "NON_TEXT_BLOCK_TYPES",
    "PAGE_NUMBER_BLOCK_TYPES",
    "PAGE_NUMBER_MAX_CHARS",
    "RUNNING_HEADER_MAX_TOKENS",
    "RUNNING_HEADER_MIN_PAGES",
    "CleanCounters",
    "CleanResult",
    "clean_blocks",
    "counters_to_mapping",
    "detect_running_headers",
    "is_page_number_like",
    "normalize_text",
]
