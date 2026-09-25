"""PDF 解析器（``.pdf``，基于 PyMuPDF）。

保留页码
--------
PyMuPDF 逐页取结构（``page.get_text("dict")``），每个 block 都带 1 起页码，
因此下游能回答「这句话在第几页」，也能按 ``page_start`` / ``page_end`` 做引用定位。
这是计划 2.2 明确要求的「PDF 保留页码」。

正文的最小单位是 PyMuPDF 的 **block**（约等于一个段落），而不是 line：
若按 line 成块，一个三段落的 PDF 会碎成几十个块，切分器再难拼回语义段落。
块内各行的换行**原样保留**（与纯文本解析器的取舍一致：不丢信息）。

标题识别
--------
PDF 没有「标题」这种语义，只有字号。做法是：

1. 统计全文**按块数加权**的字号分布，取出现最多的字号当作正文字号
   （以块为单位而不是以 span 为单位，避免一页里几十个页眉短 span 盖过正文）；
2. 块内最大字号 >= 正文字号 × 1.15、长度不超过 120 字符、且不以句末标点结尾 → 判为标题；
3. 按字号相对比例映射层级：>= 1.6 倍 → 1 级，>= 1.35 倍 → 2 级，>= 1.15 倍 → 3 级。

这是启发式，会误判；但它只影响 ``heading_path`` 的丰富程度，不影响正文完整性 ——
判错标题最坏的结果是一个段落被当成标题，**不会丢内容**。

表格
----
走 PyMuPDF 的 ``page.find_tables()``。表格区域内的文字会从正文块里剔除，
否则同一份内容会以「表格块」和「段落块」两种形态重复入库，检索时重复命中。
表格识别失败不中断解析，最差情况是表格以正文形态保留。

OCR
---
仅当全页可提取文本低于阈值（疑似扫描件）时才触发，且**整页渲染**后送 OCR：
扫描件的正文往往没被 PyMuPDF 登记成图片对象，按图片块逐张识别会漏掉内容。
引擎缺失时只写 ``no_text_layer`` 警告而不报错，理由见 ``ocr`` 模块 docstring。
"""

from __future__ import annotations

import re
from typing import Any

from services.ingestion.parsers._text import first_nonempty_line
from services.ingestion.parsers.base import (
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    ERROR_CORRUPT_DOCUMENT,
    ERROR_MISSING_DEPENDENCY,
    WARNING_NO_TEXT_LAYER,
    Block,
    ParseWarning,
    ParsedDocument,
    ParserError,
    TemplateDocumentParser,
    build_table_json,
    render_table_text,
)
from services.ingestion.parsers.ocr import OcrEngine, load_default_engine, ocr_min_text_chars, should_use_ocr


#: 标题判定的字号放大倍数阈值（相对正文字号）
HEADING_SIZE_RATIO = 1.15
#: 二级 / 一级标题的放大倍数
HEADING_LEVEL_2_RATIO = 1.35
HEADING_LEVEL_1_RATIO = 1.60
#: 超过这个长度就不像标题了（标题通常短）
HEADING_MAX_LENGTH = 120

#: 整页渲染送 OCR 时的缩放倍数。2 倍在「小字号也能识别」和「图片体积可控」之间取平衡。
OCR_RENDER_ZOOM = 2.0

_SENTENCE_END_RE = re.compile(r"[。；！？.!?;:]$")
_MULTI_SPACE_RE = re.compile(r"[ \t\u00a0]{2,}")


def _load_pymupdf() -> Any:
    """延迟导入 PyMuPDF，缺失时给结构化错误而不是 ImportError。

    PyMuPDF 1.24 起主包名是 ``pymupdf``，``fitz`` 是历史别名；
    两个都试一遍以兼容新旧版本。
    """

    try:
        import pymupdf

        return pymupdf
    except ImportError:
        pass
    try:
        import fitz

        return fitz
    except ImportError as exc:
        raise ParserError(
            ERROR_MISSING_DEPENDENCY,
            "未安装 pymupdf，无法解析 PDF",
            parser_name="pdf",
            detail={"missing_package": "pymupdf"},
        ) from exc


class PdfParser(TemplateDocumentParser):
    supported_types = ("pdf",)
    parser_name = "pdf"
    parser_version = "1.0.0"

    def __init__(self, ocr_engine: OcrEngine | None = None, use_default_ocr: bool = True) -> None:
        # 阈值在构造期读取：环境变量配错时立刻报错，而不是解析到一半才失败
        self.min_text_chars = ocr_min_text_chars()
        if ocr_engine is not None:
            self.ocr_engine: OcrEngine | None = ocr_engine
        elif use_default_ocr:
            self.ocr_engine = load_default_engine()
        else:
            self.ocr_engine = None

    # ------------------------------------------------------------ 主流程

    def _parse(self, source: bytes, filename: str) -> ParsedDocument:
        pymupdf = _load_pymupdf()

        try:
            document = pymupdf.open(stream=source, filetype="pdf")
        except Exception as exc:
            raise ParserError(
                ERROR_CORRUPT_DOCUMENT,
                f"PDF 无法打开：{type(exc).__name__}: {exc}",
                parser_name=self.parser_name,
                filename=filename,
                detail={"exception": type(exc).__name__},
            ) from exc

        with document:
            if document.needs_pass:
                raise ParserError(
                    ERROR_CORRUPT_DOCUMENT,
                    "PDF 有密码保护，无法解析",
                    parser_name=self.parser_name,
                    filename=filename,
                    detail={"reason": "encrypted"},
                )
            if document.page_count == 0:
                raise ParserError(
                    ERROR_CORRUPT_DOCUMENT,
                    "PDF 没有任何页面",
                    parser_name=self.parser_name,
                    filename=filename,
                    detail={"reason": "no_pages"},
                )

            pages = [self._extract_page(document, index) for index in range(document.page_count)]
            pages = self._promote_headings(pages)

            # OCR 必须在 document 关闭前完成：识别要重新渲染页面
            ocr_text_by_page = self._maybe_run_ocr(document, pages, pymupdf)

        blocks, warnings = self._build_blocks(pages, ocr_text_by_page, filename)
        if not blocks:
            raise ParserError(
                ERROR_CORRUPT_DOCUMENT,
                "PDF 没有可提取的文本或结构化内容",
                parser_name=self.parser_name,
                filename=filename,
                detail={"page_count": len(pages)},
            )

        title = next((block.text for block in blocks if block.type == BLOCK_HEADING), "")
        if not title:
            title = first_nonempty_line(next((block.text for block in blocks if block.text), ""))

        metadata: dict[str, Any] = {
            "pdf_page_count": len(pages),
            "pdf_is_encrypted": False,
            "pdf_character_count": sum(len(block.text) for block in blocks),
            "pdf_ocr_used": bool(ocr_text_by_page),
            "pdf_ocr_engine": self.ocr_engine.engine_name if self.ocr_engine is not None else None,
            "pdf_table_count": sum(1 for block in blocks if block.type == BLOCK_TABLE),
            "pdf_image_count": sum(1 for block in blocks if block.type == BLOCK_IMAGE),
        }
        return ParsedDocument(title=title, blocks=tuple(blocks), metadata=metadata, warnings=tuple(warnings))

    # ------------------------------------------------------------ 单页提取

    def _extract_page(self, document: Any, index: int) -> dict[str, Any]:
        page = document[index]
        table_boxes, table_nodes = self._extract_tables(page)
        nodes: list[dict[str, Any]] = []
        image_count = 0

        payload = page.get_text("dict")
        for raw_block in payload.get("blocks", []):
            bbox = tuple(float(value) for value in raw_block.get("bbox", (0.0, 0.0, 0.0, 0.0)))

            if raw_block.get("type") == 1:
                nodes.append(
                    {
                        "kind": BLOCK_IMAGE,
                        "bbox": bbox,
                        "text": "",
                        "image_ref": f"pdf:p{index + 1}:img{image_count}",
                    }
                )
                image_count += 1
                continue

            if any(_covers(box, bbox) for box in table_boxes):
                # 表格区域内的文字交给表格块，避免同一内容重复入库
                continue

            lines = [
                "".join(span.get("text", "") for span in line.get("spans", [])).rstrip()
                for line in raw_block.get("lines", [])
            ]
            text = "\n".join(line for line in lines if line.strip()).strip()
            if not text:
                continue
            text = _MULTI_SPACE_RE.sub(" ", text)
            max_size = max(
                (
                    float(span.get("size", 0.0))
                    for line in raw_block.get("lines", [])
                    for span in line.get("spans", [])
                ),
                default=0.0,
            )
            nodes.append({"kind": BLOCK_PARAGRAPH, "bbox": bbox, "text": text, "size": max_size})

        nodes.extend(table_nodes)
        nodes.sort(key=lambda node: (round(node["bbox"][1], 1), round(node["bbox"][0], 1)))
        return {"nodes": nodes, "sizes": [node["size"] for node in nodes if node["kind"] == BLOCK_PARAGRAPH]}

    def _extract_tables(self, page: Any) -> tuple[list[tuple[float, float, float, float]], list[dict[str, Any]]]:
        finder = getattr(page, "find_tables", None)
        if finder is None:  # pragma: no cover - 极老版本 PyMuPDF
            return [], []

        try:
            found = finder()
        except Exception:
            # 表格识别失败不应该让整份 PDF 解析失败：最差就是表格以正文段落形态保留下来
            return [], []

        boxes: list[tuple[float, float, float, float]] = []
        nodes: list[dict[str, Any]] = []
        for table in found.tables:
            rows = table.extract()
            if not rows:
                continue
            table_json = build_table_json(rows[0], rows[1:])
            bbox = tuple(float(value) for value in table.bbox)
            boxes.append(bbox)
            nodes.append(
                {
                    "kind": BLOCK_TABLE,
                    "bbox": bbox,
                    "text": render_table_text(table_json),
                    "table_json": table_json,
                }
            )
        return boxes, nodes

    # ------------------------------------------------------------ 标题提升

    def _promote_headings(self, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        all_sizes = [size for page in pages for size in page["sizes"]]
        if not all_sizes:
            return pages

        body_size = _dominant_size(all_sizes)
        if body_size <= 0:
            return pages

        for page in pages:
            for node in page["nodes"]:
                if node["kind"] != BLOCK_PARAGRAPH:
                    continue
                size = float(node.get("size") or 0.0)
                text = node["text"]
                if (
                    size >= body_size * HEADING_SIZE_RATIO
                    and len(text) <= HEADING_MAX_LENGTH
                    and not _SENTENCE_END_RE.search(text)
                ):
                    node["kind"] = BLOCK_HEADING
                    node["level"] = _heading_level(size / body_size)
        return pages

    # ------------------------------------------------------------ 组装块

    def _build_blocks(
        self,
        pages: list[dict[str, Any]],
        ocr_text_by_page: dict[int, str],
        filename: str,
    ) -> tuple[list[Block], list[ParseWarning]]:
        blocks: list[Block] = []
        warnings: list[ParseWarning] = []
        heading_stack: list[tuple[int, str]] = []
        extracted_char_count = 0

        for page_number, page in enumerate(pages, start=1):
            for node in page["nodes"]:
                path = tuple(text for _, text in heading_stack)
                if node["kind"] == BLOCK_TABLE:
                    extracted_char_count += len(node["text"])
                    blocks.append(
                        Block(
                            type=BLOCK_TABLE,
                            text=node["text"],
                            page=page_number,
                            heading_path=path,
                            table_json=node["table_json"],
                        )
                    )
                elif node["kind"] == BLOCK_IMAGE:
                    blocks.append(
                        Block(type=BLOCK_IMAGE, text="", page=page_number, heading_path=path, image_ref=node["image_ref"])
                    )
                elif node["kind"] == BLOCK_HEADING:
                    level = int(node.get("level", 3))
                    while heading_stack and heading_stack[-1][0] >= level:
                        heading_stack.pop()
                    heading_stack.append((level, node["text"]))
                    extracted_char_count += len(node["text"])
                    blocks.append(
                        Block(
                            type=BLOCK_HEADING,
                            text=node["text"],
                            page=page_number,
                            heading_path=tuple(text for _, text in heading_stack),
                        )
                    )
                else:
                    extracted_char_count += len(node["text"])
                    blocks.append(
                        Block(type=BLOCK_PARAGRAPH, text=node["text"], page=page_number, heading_path=path)
                    )

            ocr_text = ocr_text_by_page.get(page_number, "")
            if ocr_text:
                blocks.append(
                    Block(
                        type=BLOCK_PARAGRAPH,
                        text=ocr_text,
                        page=page_number,
                        heading_path=tuple(text for _, text in heading_stack),
                    )
                )

        if not ocr_text_by_page and should_use_ocr("".join(block.text for block in blocks), self.min_text_chars):
            warnings.append(
                ParseWarning(
                    WARNING_NO_TEXT_LAYER,
                    "PDF 可提取文本过少，疑似扫描件；"
                    + (
                        "OCR 未识别出内容，检索可能受影响"
                        if self.ocr_engine is not None
                        else "且未安装 OCR 引擎，本次未做 OCR"
                    ),
                    {
                        "source_file": filename,
                        "extracted_char_count": extracted_char_count,
                        "threshold": self.min_text_chars,
                        "ocr_engine": self.ocr_engine.engine_name if self.ocr_engine is not None else None,
                    },
                )
            )
        return blocks, warnings

    # ------------------------------------------------------------ OCR 触发

    def _maybe_run_ocr(self, document: Any, pages: list[dict[str, Any]], pymupdf: Any) -> dict[int, str]:
        """文本层不足且引擎可用时，整页渲染后 OCR。返回 ``{页码: 文本}``。"""

        extracted = "".join(node.get("text", "") for page in pages for node in page["nodes"])
        if not should_use_ocr(extracted, self.min_text_chars) or self.ocr_engine is None:
            return {}

        recognized: dict[int, str] = {}
        for index in range(len(pages)):
            image_bytes = self._render_page(document, index, pymupdf)
            if not image_bytes:
                continue
            try:
                text = self.ocr_engine.recognize_image(image_bytes).strip()
            except ParserError:
                # 单页 OCR 失败不应让整份文档失败：正文已经尽力取出，
                # 这里退化成「这一页没有文本」，并由 no_text_layer 警告兜住可见性
                continue
            if text:
                recognized[index + 1] = text
        return recognized

    def _render_page(self, document: Any, index: int, pymupdf: Any) -> bytes:
        try:
            page = document[index]
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(OCR_RENDER_ZOOM, OCR_RENDER_ZOOM))
            return pixmap.tobytes("png")
        except Exception:
            return ""


def _heading_level(ratio: float) -> int:
    if ratio >= HEADING_LEVEL_1_RATIO:
        return 1
    if ratio >= HEADING_LEVEL_2_RATIO:
        return 2
    return 3


def _dominant_size(sizes: list[float]) -> float:
    """取出现次数最多的字号（按 0.5pt 分桶），作为正文字号估计。"""

    buckets: dict[float, int] = {}
    for size in sizes:
        key = round(size * 2) / 2
        buckets[key] = buckets.get(key, 0) + 1
    if not buckets:
        return 0.0
    return max(buckets.items(), key=lambda item: (item[1], item[0]))[0]


def _covers(outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]) -> bool:
    """``inner`` 的中心点是否落在 ``outer`` 内。"""

    outer_x0, outer_y0, outer_x1, outer_y1 = outer
    inner_x0, inner_y0, inner_x1, inner_y1 = inner
    center_x = (inner_x0 + inner_x1) / 2
    center_y = (inner_y0 + inner_y1) / 2
    return outer_x0 <= center_x <= outer_x1 and outer_y0 <= center_y <= outer_y1


__all__ = ["HEADING_SIZE_RATIO", "OCR_RENDER_ZOOM", "PdfParser"]
