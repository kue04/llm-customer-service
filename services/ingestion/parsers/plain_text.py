"""纯文本解析器（``.txt``）。

结构约定
--------
纯文本没有标题语法，因此本解析器**不猜测标题**：所有内容都是段落块，
``heading_path`` 恒为空。这是刻意的 —— 靠「这行比较短」猜标题会产生大量假标题，
污染下游的标题路径分组；不猜的代价只是失去层级，比猜错要轻。

唯一额外识别的结构是 ASCII 换页符 ``\\f``（``0x0C``）：它是纯文本世界里
事实标准的「分页」标记，Word 导出 TXT、终端打印输出都会带。
出现换页符时按页切分并写 ``page``；整篇没有换页符时 ``page`` 保持 ``None``。
"""

from __future__ import annotations

from typing import Any

from services.ingestion.parsers._text import (
    decode_bytes,
    first_nonempty_line,
    normalize_newlines,
    split_paragraphs,
)
from services.ingestion.parsers.base import (
    BLOCK_PARAGRAPH,
    ERROR_EMPTY_DOCUMENT,
    WARNING_ENCODING_FALLBACK,
    Block,
    ParseWarning,
    ParsedDocument,
    ParserError,
    TemplateDocumentParser,
)


#: 换页符，纯文本里的分页标记
PAGE_BREAK_CHAR = "\f"


class PlainTextParser(TemplateDocumentParser):
    supported_types = ("txt",)
    parser_name = "plain_text"
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

        text = normalize_newlines(decoded.text)
        pages = text.split(PAGE_BREAK_CHAR)
        has_pages = len(pages) > 1

        blocks: list[Block] = []
        for page_index, page_text in enumerate(pages, start=1):
            page_number = page_index if has_pages else None
            for paragraph in split_paragraphs(page_text):
                blocks.append(Block(type=BLOCK_PARAGRAPH, text=paragraph, page=page_number))

        if not blocks:
            raise ParserError(
                ERROR_EMPTY_DOCUMENT,
                "文件没有可提取的正文（只有空白字符）",
                parser_name=self.parser_name,
                filename=filename,
                detail={"encoding": decoded.encoding},
            )

        metadata: dict[str, Any] = {
            "txt_encoding": decoded.encoding,
            "txt_is_fallback_encoding": decoded.is_fallback,
            "txt_has_page_breaks": has_pages,
            "txt_page_count": len(pages) if has_pages else 1,
            "txt_character_count": len(text),
        }

        return ParsedDocument(
            title=first_nonempty_line(text),
            blocks=tuple(blocks),
            metadata=metadata,
            warnings=tuple(warnings),
        )


__all__ = ["PAGE_BREAK_CHAR", "PlainTextParser"]
