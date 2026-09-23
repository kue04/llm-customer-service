"""Markdown 解析器（``.md`` / ``.markdown``）。

识别的块级语法
--------------
ATX 标题（``#`` ~ ``######``）、Setext 标题（``===`` / ``---`` 下划线）、
围栏代码块（``` ``` `` 或 ``~~~``）、GFM 管道表格、有序/无序列表、引用块、
独立成行的图片、段落。

``heading_path`` 由标题栈维护：进入 ``### 三级`` 时会先弹出所有层级 >= 3 的标题，
再把这一级压栈。于是 ``## A`` → ``### B`` → ``# C`` → ``### D``
得到的路径分别是 ``(A,)``、``(A, B)``、``(C,)``、``(C, D)``，
不会出现「A > B > C > D」这种把已闭合层级串起来的错误路径。

文本清洗
--------
块内正文会剥掉行内 Markdown 标记（``**粗体**`` → ``粗体``、``[文字](url)`` → ``文字``、
`` `code` `` → ``code``），因为 block.text 的用途是**被检索**，
留着标记符号只会给分词和向量匹配添噪音。代码块内容**原样保留**，不做任何清洗。
"""

from __future__ import annotations

import re
from typing import Any

from services.ingestion.parsers._text import (
    decode_bytes,
    first_nonempty_line,
    normalize_newlines,
)
from services.ingestion.parsers.base import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_LIST,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    BLOCK_TABLE,
    ERROR_EMPTY_DOCUMENT,
    WARNING_ENCODING_FALLBACK,
    Block,
    ParseWarning,
    ParsedDocument,
    ParserError,
    TemplateDocumentParser,
    build_table_json,
    render_table_text,
)


# ---------------------------------------------------------------- 正则

_ATX_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_SETEXT_UNDERLINE_RE = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)")
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_BLOCKQUOTE_RE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_THEMATIC_BREAK_RE = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_STANDALONE_IMAGE_RE = re.compile(r'^!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)$')
_TABLE_DELIMITER_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")

# 行内标记的清洗顺序有讲究：链接/图片先处理，否则 `[a](b)` 里的 `*` 之类的残留会干扰后续规则。
_INLINE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"__(.+?)__", re.DOTALL), r"\1"),
    (re.compile(r"~~(.+?)~~", re.DOTALL), r"\1"),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.DOTALL), r"\1"),
    (re.compile(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", re.DOTALL), r"\1"),
)


def clean_inline(text: str) -> str:
    """剥掉行内 Markdown 标记，返回可检索纯文本。"""

    result = text
    for pattern, replacement in _INLINE_RULES:
        result = pattern.sub(replacement, result)
    return result.strip()


# ---------------------------------------------------------------- 小块语法

def split_table_row(line: str) -> list[str]:
    """把一行 GFM 表格行切成单元格（支持 ``\\|`` 转义）。"""

    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _is_table_start(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    header, delimiter = lines[index], lines[index + 1]
    if "|" not in header or "|" not in delimiter:
        return False
    if _THEMATIC_BREAK_RE.match(header.strip()):
        return False
    return bool(_TABLE_DELIMITER_RE.match(delimiter))


def _heading_level(title: str, underline: str) -> int:
    return 1 if underline.startswith("=") else 2


def _push_heading(stack: list[tuple[int, str]], level: int, title: str) -> tuple[str, ...]:
    while stack and stack[-1][0] >= level:
        stack.pop()
    stack.append((level, title))
    return tuple(text for _, text in stack)


def _current_path(stack: list[tuple[int, str]]) -> tuple[str, ...]:
    return tuple(text for _, text in stack)


class MarkdownParser(TemplateDocumentParser):
    supported_types = ("md",)
    parser_name = "markdown"
    parser_version = "1.0.0"

    def _parse(self, source: bytes, filename: str) -> ParsedDocument:
        decoded = decode_bytes(source)
        warnings: list[ParseWarning] = []
        if decoded.is_fallback:
            warnings.append(
                ParseWarning(
                    WARNING_ENCODING_FALLBACK,
                    f"文件不是 UTF-8 编码，已按 {decoded.encoding} 解码，正文可能存在乱码",
                    {"encoding": decoded.encoding},
                )
            )

        lines = normalize_newlines(decoded.text).split("\n")
        blocks, stats, first_h1 = self._scan(lines)
        if not blocks:
            raise ParserError(
                ERROR_EMPTY_DOCUMENT,
                "Markdown 文件没有可提取的内容",
                parser_name=self.parser_name,
                filename=filename,
                detail={"line_count": len(lines)},
            )

        title = first_h1 or ""
        if not title:
            first_paragraph = next((b.text for b in blocks if b.type == BLOCK_PARAGRAPH), "")
            title = first_nonempty_line(first_paragraph)

        metadata: dict[str, Any] = {
            "md_line_count": len(lines),
            "md_heading_count": stats["heading"],
            "md_list_count": stats["list"],
            "md_code_block_count": stats["code"],
            "md_table_count": stats["table"],
            "md_image_count": stats["image"],
            "md_character_count": sum(len(block.text) for block in blocks),
        }
        return ParsedDocument(title=title, blocks=tuple(blocks), metadata=metadata, warnings=tuple(warnings))

    # ------------------------------------------------------------ 扫描主循环

    def _scan(self, lines: list[str]) -> tuple[list[Block], dict[str, int], str]:
        blocks: list[Block] = []
        stats = {"heading": 0, "list": 0, "code": 0, "table": 0, "image": 0, "quote": 0}
        heading_stack: list[tuple[int, str]] = []
        first_h1 = ""

        index = 0
        total = len(lines)
        while index < total:
            line = lines[index]

            if not line.strip():
                index += 1
                continue

            fence = _FENCE_RE.match(line)
            if fence:
                collected, index = self._consume_fence(lines, index, fence.group(1))
                blocks.append(Block(type=BLOCK_CODE, text=collected, heading_path=_current_path(heading_stack)))
                stats["code"] += 1
                continue

            heading = _ATX_HEADING_RE.match(line)
            if heading:
                title = clean_inline(heading.group(2))
                level = len(heading.group(1))
                if not title:
                    index += 1
                    continue
                path = _push_heading(heading_stack, level, title)
                blocks.append(Block(type=BLOCK_HEADING, text=title, heading_path=path))
                stats["heading"] += 1
                if level == 1 and not first_h1:
                    first_h1 = title
                index += 1
                continue

            if _is_table_start(lines, index):
                block, index, is_heading = self._consume_table(lines, index, _current_path(heading_stack))
                if is_heading:
                    path = _push_heading(heading_stack, 2, block.text)
                    if not first_h1:
                        first_h1 = block.text
                    block = Block(type=BLOCK_HEADING, text=block.text, heading_path=path)
                    stats["heading"] += 1
                else:
                    stats["table"] += 1
                blocks.append(block)
                continue

            underline = _SETEXT_UNDERLINE_RE.match(lines[index + 1]) if index + 1 < total else None
            if underline and line.strip() and not _LIST_ITEM_RE.match(line) and not _BLOCKQUOTE_RE.match(line):
                title = clean_inline(line)
                level = _heading_level(title, underline.group(1))
                path = _push_heading(heading_stack, level, title)
                blocks.append(Block(type=BLOCK_HEADING, text=title, heading_path=path))
                stats["heading"] += 1
                if level == 1 and not first_h1:
                    first_h1 = title
                index += 2
                continue

            if _LIST_ITEM_RE.match(line):
                text, index = self._consume_list(lines, index)
                blocks.append(Block(type=BLOCK_LIST, text=text, heading_path=_current_path(heading_stack)))
                stats["list"] += 1
                continue

            if _BLOCKQUOTE_RE.match(line):
                text, index = self._consume_blockquote(lines, index)
                blocks.append(Block(type=BLOCK_QUOTE, text=text, heading_path=_current_path(heading_stack)))
                stats["quote"] += 1
                continue

            image = _STANDALONE_IMAGE_RE.match(line.strip())
            if image:
                blocks.append(
                    Block(
                        type=BLOCK_IMAGE,
                        text=clean_inline(image.group(1)),
                        heading_path=_current_path(heading_stack),
                        image_ref=image.group(2),
                    )
                )
                stats["image"] += 1
                index += 1
                continue

            if _THEMATIC_BREAK_RE.match(line.strip()):
                # 分隔线不是内容，丢掉；它在排版上有意义，在检索上没有
                index += 1
                continue

            text, index = self._consume_paragraph(lines, index)
            if text:
                blocks.append(Block(type=BLOCK_PARAGRAPH, text=text, heading_path=_current_path(heading_stack)))

        return blocks, stats, first_h1

    # ------------------------------------------------------------ 各构造的消费

    def _consume_fence(self, lines: list[str], index: int, marker: str) -> tuple[str, int]:
        """消费一个围栏代码块，返回（代码正文，下一行下标）。未闭合时吃到文件末尾。"""

        char = marker[0]
        min_length = len(marker)
        body: list[str] = []
        cursor = index + 1
        closing = re.compile(rf"^ {{0,3}}{re.escape(char)}{{{min_length},}}[ \t]*$")
        while cursor < len(lines):
            if closing.match(lines[cursor]):
                return "\n".join(body).rstrip("\n"), cursor + 1
            body.append(lines[cursor])
            cursor += 1
        return "\n".join(body).rstrip("\n"), cursor

    def _consume_table(self, lines: list[str], index: int, heading_path: tuple[str, ...]) -> tuple[Block, int, bool]:
        """消费一张 GFM 表格。

        Setext 的 ``---`` 与表格分隔行长得像，若表头只有一个单元格，
        更可能是「标题 + 下划线」被误判，此时按二级标题处理并交由调用方压栈。
        """

        header_cells = split_table_row(lines[index])
        if len(header_cells) < 2:
            return (
                Block(type=BLOCK_HEADING, text=clean_inline(lines[index]), heading_path=heading_path),
                index + 2,
                True,
            )

        rows: list[list[str]] = []
        cursor = index + 2
        while cursor < len(lines) and lines[cursor].strip() and "|" in lines[cursor]:
            rows.append([clean_inline(cell) for cell in split_table_row(lines[cursor])])
            cursor += 1

        table_json = build_table_json([clean_inline(cell) for cell in header_cells], rows)
        return (
            Block(
                type=BLOCK_TABLE,
                text=render_table_text(table_json),
                heading_path=heading_path,
                table_json=table_json,
            ),
            cursor,
            False,
        )

    def _consume_list(self, lines: list[str], index: int) -> tuple[str, int]:
        """消费一段连续列表，归一化标记为 ``-`` / ``N.``。"""

        items: list[str] = []
        cursor = index
        ordered_index = 0
        while cursor < len(lines):
            match = _LIST_ITEM_RE.match(lines[cursor])
            if not match:
                # 允许列表项之间的空行；空行后必须仍是列表项，否则列表结束
                if not lines[cursor].strip() and cursor + 1 < len(lines) and _LIST_ITEM_RE.match(lines[cursor + 1]):
                    cursor += 1
                    continue
                break
            marker, content = match.group(2), match.group(3)
            if marker[0].isdigit():
                ordered_index += 1
                marker_text = f"{ordered_index}."
            else:
                marker_text = "-"
            items.append(f"{marker_text} {clean_inline(content)}".rstrip())
            cursor += 1
        return "\n".join(items), cursor

    def _consume_blockquote(self, lines: list[str], index: int) -> tuple[str, int]:
        collected: list[str] = []
        cursor = index
        while cursor < len(lines):
            match = _BLOCKQUOTE_RE.match(lines[cursor])
            if not match:
                break
            collected.append(clean_inline(match.group(1)))
            cursor += 1
        return "\n".join(line for line in collected).strip(), cursor

    def _consume_paragraph(self, lines: list[str], index: int) -> tuple[str, int]:
        """把连续普通行收成一段，直到空行或下一个块级构造开始。"""

        collected: list[str] = []
        cursor = index
        while cursor < len(lines):
            line = lines[cursor]
            if not line.strip():
                break
            if _FENCE_RE.match(line) or _ATX_HEADING_RE.match(line):
                break
            if cursor > index:
                if _LIST_ITEM_RE.match(line) or _BLOCKQUOTE_RE.match(line) or _THEMATIC_BREAK_RE.match(line.strip()):
                    break
                if _is_table_start(lines, cursor):
                    break
                underline = _SETEXT_UNDERLINE_RE.match(line)
                if underline and collected:
                    break
            collected.append(clean_inline(line))
            cursor += 1
        return "\n".join(collected).strip(), cursor


__all__ = ["MarkdownParser", "clean_inline", "split_table_row"]
