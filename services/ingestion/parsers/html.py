"""HTML 解析器（``.html`` / ``.htm``）。

两道提取器
----------
1. **主路径：BeautifulSoup 结构遍历**（默认用 ``lxml``，缺失时退回标准库 ``html.parser``）。
   先删掉 ``script`` / ``style`` / ``nav`` / ``footer`` / ``header`` / ``aside`` /
   ``form`` / ``noscript`` / ``iframe`` / ``svg`` 等样板噪声，再按文档顺序把
   ``h1``~``h6`` / ``p`` / ``li`` / ``table`` / ``pre`` / ``blockquote`` / ``img``
   映射成 block。走这条路径才能拿到标题层级、表格结构与页码之外的结构信息。
2. **兜底：trafilatura**。当结构遍历提取到的正文短于阈值（例如整页正文塞在
   一个畸形 ``<div>`` 里，于是没有任何 ``p`` / ``li`` / 标题可选）时，
   改用 trafilatura 做正文抽取，把结果作为单个段落块返回，并写一条
   ``degraded_extraction`` 警告 —— 降级结果可用，但必须让任务查询接口看得见
   「这份文档走的是兜底路径」。

   兜底有两个约束，缺一不可：

   * 送进 trafilatura 的是**已清洗过噪声的 HTML**。如果送原始 HTML，
     trafilatura 会把我们已经判定为噪声的 ``nav`` / ``footer`` 内容捞回来，
     等于同一个策略在两处得出互相矛盾的结论；
   * trafilatura 的结果必须**严格多于**结构遍历的结果，否则不降级。
     否则一篇只有一句话的合法短页面（结构遍历已经完整拿到那句话）也会被
     判为「降级」，既丢掉了 ``heading_path`` 等结构信息，又平白多一条警告。

trafilatura 采用**函数内延迟导入**：本项目在没装它时（例如极简 CI 环境）
普通的 HTML 依然能正常解析，只有真的走到兜底分支才需要它。
"""

from __future__ import annotations

import re
from typing import Any

from services.ingestion.parsers._text import decode_bytes, first_nonempty_line, normalize_newlines
from services.ingestion.parsers.base import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_LIST,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    BLOCK_TABLE,
    ERROR_EMPTY_DOCUMENT,
    ERROR_MISSING_DEPENDENCY,
    WARNING_DEGRADED_EXTRACTION,
    Block,
    ParseWarning,
    ParsedDocument,
    ParserError,
    TemplateDocumentParser,
    build_table_json,
    render_table_text,
)


#: 结构遍历结果的字符数低于此值时启用 trafilatura 兜底。
#: 取 40 是因为低于这个量级的「正文」几乎必然是导航/版权之类的残渣，
#: 而不是真的只有一句话的页面。
DEFAULT_MIN_TEXT_CHARS = 40

#: 直接删除的样板标签。``header`` / ``footer`` 在这里一并删掉：
#: 站点级页眉页脚对检索只会带来噪音，而正文里真正的小节标题用的是 h1~h6。
NOISE_TAGS: tuple[str, ...] = (
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "noscript",
    "iframe",
    "svg",
    "template",
    "button",
    "select",
    "option",
)

#: 从文档顺序里挑出来的内容标签
CONTENT_TAGS: tuple[str, ...] = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "table", "pre", "blockquote", "img")

#: 这些容器会整体消费，其内部元素不再单独成块（否则 blockquote 里的 p 会重复出现）
CONSUMING_CONTAINERS: tuple[str, ...] = ("blockquote",)

#: 这些父元素的文本由父块负责，自身不再单独成块
SKIP_PARENT_TAGS: tuple[str, ...] = ("li", "blockquote", "td", "th")

_WHITESPACE_RE = re.compile(r"\s+")


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _make_soup(html: str) -> Any:
    """构造 BeautifulSoup。优先 lxml（快且容错好），不可用时退回标准库。"""

    from bs4 import BeautifulSoup

    try:
        return BeautifulSoup(html, "lxml")
    except Exception:  # pragma: no cover - lxml 缺失才会走到
        return BeautifulSoup(html, "html.parser")


class HtmlParser(TemplateDocumentParser):
    supported_types = ("html",)
    parser_name = "html"
    parser_version = "1.0.0"

    def __init__(self, min_text_chars: int = DEFAULT_MIN_TEXT_CHARS) -> None:
        self.min_text_chars = min_text_chars

    def _parse(self, source: bytes, filename: str) -> ParsedDocument:
        decoded = decode_bytes(source)
        html = normalize_newlines(decoded.text)
        soup = _make_soup(html)

        document_title = ""
        if soup.title and soup.title.string:
            document_title = _collapse(str(soup.title.string))

        removed = self._strip_noise(soup)
        root = soup.find("main") or soup.find("article") or soup.body or soup
        blocks = self._extract_blocks(root)

        warnings: list[ParseWarning] = []
        extractor = "beautifulsoup4"
        structured_char_count = sum(len(block.text) for block in blocks)
        if structured_char_count < self.min_text_chars:
            # 用清洗后的 HTML：噪声标签既然已经被判定为噪声，就不该由另一个抽取器捡回来
            fallback = self._trafilatura_fallback(str(soup))
            if fallback and len(fallback) > structured_char_count:
                warnings.append(
                    ParseWarning(
                        WARNING_DEGRADED_EXTRACTION,
                        "结构遍历提取到的正文过少，已降级为 trafilatura 正文抽取",
                        {
                            "structured_char_count": structured_char_count,
                            "fallback_char_count": len(fallback),
                            "threshold": self.min_text_chars,
                        },
                    )
                )
                blocks = [Block(type=BLOCK_PARAGRAPH, text=fallback)]
                extractor = "trafilatura"
                document_title = document_title or first_nonempty_line(fallback)

        if not blocks:
            raise ParserError(
                ERROR_EMPTY_DOCUMENT,
                "HTML 没有可提取的正文",
                parser_name=self.parser_name,
                filename=filename,
                detail={"extractor": extractor},
            )

        if not document_title:
            first_heading = next((b.text for b in blocks if b.type == BLOCK_HEADING), "")
            document_title = first_heading

        metadata: dict[str, Any] = {
            "html_title": document_title,
            "html_extractor": extractor,
            "html_removed_noise_tags": removed,
            "html_character_count": sum(len(block.text) for block in blocks),
        }
        return ParsedDocument(title=document_title, blocks=tuple(blocks), metadata=metadata, warnings=tuple(warnings))

    # ------------------------------------------------------------ 噪声清理

    def _strip_noise(self, soup: Any) -> int:
        count = 0
        for tag in NOISE_TAGS:
            for element in soup.find_all(tag):
                element.decompose()
                count += 1
        # aria-hidden / display:none 的元素对用户不可见，也不该进索引
        for element in soup.find_all(attrs={"aria-hidden": "true"}):
            element.decompose()
            count += 1
        return count

    # ------------------------------------------------------------ 结构遍历

    def _extract_blocks(self, root: Any) -> list[Block]:
        blocks: list[Block] = []
        heading_stack: list[tuple[int, str]] = []
        consumed: set[int] = set()

        for element in root.find_all(list(CONTENT_TAGS)):
            name = element.name
            if any(id(ancestor) in consumed for ancestor in element.parents):
                continue
            if name == "p" and element.parent is not None and element.parent.name in SKIP_PARENT_TAGS:
                continue

            path = tuple(text for _, text in heading_stack)

            if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                title = _collapse(element.get_text(" ", strip=True))
                if not title:
                    continue
                level = int(name[1])
                while heading_stack and heading_stack[-1][0] >= level:
                    heading_stack.pop()
                heading_stack.append((level, title))
                blocks.append(Block(type=BLOCK_HEADING, text=title, heading_path=tuple(t for _, t in heading_stack)))
                continue

            if name == "table":
                block = self._table_block(element, path)
                if block is not None:
                    blocks.append(block)
                continue

            if name == "blockquote":
                text = _collapse(element.get_text(" ", strip=True))
                consumed.add(id(element))
                if text:
                    blocks.append(Block(type=BLOCK_QUOTE, text=text, heading_path=path))
                continue

            if name == "pre":
                text = element.get_text("", strip=False).strip("\n")
                if text.strip():
                    blocks.append(Block(type=BLOCK_CODE, text=text, heading_path=path))
                continue

            if name == "img":
                reference = element.get("src") or element.get("data-src") or ""
                if not reference:
                    continue
                blocks.append(
                    Block(
                        type=BLOCK_IMAGE,
                        text=_collapse(element.get("alt") or ""),
                        heading_path=path,
                        image_ref=str(reference),
                    )
                )
                continue

            text = _collapse(element.get_text(" ", strip=True))
            if not text:
                continue
            block_type = BLOCK_LIST if name == "li" else BLOCK_PARAGRAPH
            blocks.append(Block(type=block_type, text=text, heading_path=path))

        return blocks

    def _table_block(self, table: Any, heading_path: tuple[str, ...]) -> Block | None:
        header_cells: list[str] | None = None
        body_rows: list[list[str]] = []

        thead = table.find("thead")
        if thead is not None:
            header_row = thead.find("tr")
            if header_row is not None:
                cells = header_row.find_all(["th", "td"])
                if cells:
                    header_cells = [_collapse(cell.get_text(" ", strip=True)) for cell in cells]

        for row in table.find_all("tr"):
            if thead is not None and row.find_parent("thead") is not None:
                continue
            cells = row.find_all(["th", "td"])
            if not cells:
                continue
            values = [_collapse(cell.get_text(" ", strip=True)) for cell in cells]
            if header_cells is None and all(cell.name == "th" for cell in cells):
                # 首行全用 <th> 且没有 thead，视作表头
                header_cells = values
                continue
            body_rows.append(values)

        if header_cells is None:
            if not body_rows:
                return None
            header_cells = body_rows.pop(0)

        table_json = build_table_json(header_cells, body_rows)
        return Block(
            type=BLOCK_TABLE,
            text=render_table_text(table_json),
            heading_path=heading_path,
            table_json=table_json,
        )

    # ------------------------------------------------------------ 兜底

    def _trafilatura_fallback(self, html: str) -> str:
        try:
            import trafilatura
        except ImportError as exc:
            raise ParserError(
                ERROR_MISSING_DEPENDENCY,
                "结构遍历未能提取到正文，且未安装 trafilatura 兜底抽取器",
                parser_name=self.parser_name,
                detail={"missing_package": "trafilatura"},
            ) from exc

        extracted = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True)
        return (extracted or "").strip()


__all__ = ["CONTENT_TAGS", "DEFAULT_MIN_TEXT_CHARS", "NOISE_TAGS", "HtmlParser"]
