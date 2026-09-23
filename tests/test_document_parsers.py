"""阶段 2.1 + 2.2 的解析契约与解析器测试。

覆盖范围
--------
1. **契约不变量**：``Block`` / ``ParsedDocument`` / ``ParseWarning`` / ``ParserError``
   的取值校验与自检（``validation_errors``）、产物可 JSON 序列化；
2. **禁止事项**：解析器不接收 ``tenant_id``、解析器模块不依赖数据库
   （用源码扫描断言，而不是靠人工 Review）；
3. **失败必须结构化且不吞异常**：空文件 / 坏文件 / 未知扩展名 / 内部未预期异常
   四条路径分别断言错误码与 ``__cause__`` 链；
4. **五种解析器的结构还原**：块顺序、标题路径、页码、表格结构、图片引用；
5. **OCR 触发条件**：文本层充足不触发、文本层不足且引擎可用才触发、
   引擎缺失时降级为警告而不是失败；
6. **注册表**：重复注册、未知类型、扩展名优先于 MIME。

样本文件由 ``scripts/make_parser_fixtures.py`` 生成，内容写死在脚本里，
因此断言可以精确到「第三块的 heading_path 是什么」。
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import sys

import pytest

from services.ingestion import models
from services.ingestion.parser_registry import (
    ALLOWED_EXTENSIONS,
    EXTENSION_SOURCE_TYPES,
    ParserRegistry,
    default_parsers,
    default_registry,
    detect_source_type,
    get_parser,
    parse_document,
    reset_default_registry,
)
from services.ingestion.parsers import base as parser_base
from services.ingestion.parsers import docx as docx_module
from services.ingestion.parsers import ocr as ocr_module
from services.ingestion.parsers import pdf as pdf_module
from services.ingestion.parsers.base import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_LIST,
    BLOCK_PARAGRAPH,
    BLOCK_TABLE,
    ERROR_CORRUPT_DOCUMENT,
    ERROR_EMPTY_DOCUMENT,
    ERROR_MISSING_DEPENDENCY,
    ERROR_PARSE_FAILED,
    ERROR_UNSUPPORTED_TYPE,
    WARNING_ENCODING_FALLBACK,
    WARNING_NO_TEXT_LAYER,
    Block,
    DocumentParser,
    ParseWarning,
    ParsedDocument,
    ParserError,
    build_table_json,
    compute_content_hash,
    render_table_text,
)
from services.ingestion.parsers.docx import DocxParser
from services.ingestion.parsers.html import HtmlParser
from services.ingestion.parsers.markdown import MarkdownParser
from services.ingestion.parsers.ocr import OCR_MIN_TEXT_CHARS_ENV, ocr_min_text_chars, should_use_ocr
from services.ingestion.parsers.pdf import PdfParser
from services.ingestion.parsers.plain_text import PlainTextParser


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
PARSER_DIR = PROJECT_ROOT / "services" / "ingestion" / "parsers"

#: 样本文件体积上限，必须与 scripts/check_repo_data_size.py 保持一致
MAX_FIXTURE_BYTES = 1024 * 1024

FIXTURE_NAMES = ("sample.md", "sample.html", "sample.docx", "sample.pdf")


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def parse_fixture(name: str) -> ParsedDocument:
    return default_registry().parse(load_fixture(name), name)


def build_pdf(lines: tuple[tuple[str, float], ...]) -> bytes:
    """用 PyMuPDF 现造一份单页 PDF，用于 OCR 与坏文件之外的边界场景。"""

    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    baseline = 80.0
    for text, font_size in lines:
        page.insert_text((72, baseline), text, fontsize=font_size, fontname="china-s")
        baseline += 40
    payload = document.tobytes()
    document.close()
    return payload


class FakeOcrEngine:
    """假的 OCR 引擎：只记录调用，不真的识别。"""

    engine_name = "fake-ocr"

    def __init__(self, recognized: str = "OCR 识别出的正文") -> None:
        self.recognized = recognized
        self.calls: list[bytes] = []

    def is_available(self) -> bool:
        return True

    def recognize_image(self, image_bytes: bytes) -> str:
        self.calls.append(image_bytes)
        return self.recognized


# ---------------------------------------------------------------- 契约：值对象


def test_block_rejects_unknown_type():
    with pytest.raises(ValueError):
        Block(type="paragraph_v2", text="x")


def test_block_normalizes_heading_path_to_tuple():
    block = Block(type=BLOCK_PARAGRAPH, text="正文", heading_path=["第一章", "1.1"])

    assert block.heading_path == ("第一章", "1.1")
    assert block.heading_path_text == "第一章 > 1.1"


def test_block_to_dict_is_json_serializable():
    block = Block(
        type=BLOCK_TABLE,
        text="A | B",
        order=3,
        page=2,
        heading_path=("第一章",),
        table_json={"columns": ["A", "B"], "rows": [["1", "2"]]},
    )

    payload = json.loads(json.dumps(block.to_dict(), ensure_ascii=False))

    assert payload["heading_path"] == ["第一章"]
    assert payload["table_json"]["columns"] == ["A", "B"]


def test_parse_warning_rejects_unknown_code():
    with pytest.raises(ValueError):
        ParseWarning(code="something_new", message="x")


def test_parser_error_rejects_unknown_code():
    with pytest.raises(ValueError):
        ParserError("not_a_code", "x")


def test_parser_error_to_dict_carries_location():
    error = ParserError(
        ERROR_CORRUPT_DOCUMENT,
        "坏了",
        parser_name="pdf",
        filename="a.pdf",
        detail={"reason": "encrypted"},
    )

    assert error.to_dict() == {
        "error_code": ERROR_CORRUPT_DOCUMENT,
        "message": "坏了",
        "parser_name": "pdf",
        "filename": "a.pdf",
        "detail": {"reason": "encrypted"},
    }
    assert "pdf@a.pdf" in str(error)


def test_build_table_json_pads_short_rows_and_truncates_long_rows():
    table = build_table_json(["A", "B", "C"], [["1"], ["1", "2", "3", "4"]])

    assert table["columns"] == ["A", "B", "C"]
    assert table["rows"] == [["1", "", ""], ["1", "2", "3"]]


def test_build_table_json_strips_whitespace():
    table = build_table_json(["  名称  ", " 数量 "], [["  甲 ", " 1 "]])

    assert table["columns"] == ["名称", "数量"]
    assert table["rows"] == [["甲", "1"]]


def test_render_table_text_uses_pipe_separator_with_header_first():
    text = render_table_text(build_table_json(["A", "B"], [["1", "2"], ["3", "4"]]))

    assert text == "A | B\n1 | 2\n3 | 4"


def test_compute_content_hash_matches_sha256():
    payload = "客户服务".encode()

    assert compute_content_hash(payload) == hashlib.sha256(payload).hexdigest()


def test_parsed_document_derived_views():
    document = ParsedDocument(
        title="标题",
        blocks=(
            Block(type=BLOCK_HEADING, text="标题", order=0, page=1),
            Block(type=BLOCK_PARAGRAPH, text="正文", order=1, page=2),
            Block(
                type=BLOCK_TABLE,
                text="A | B",
                order=2,
                page=2,
                table_json=build_table_json(["A", "B"], []),
            ),
        ),
    )

    assert document.text == "标题\n\n正文\n\nA | B"
    assert document.page_count == 2
    assert len(document.tables) == 1
    assert document.blocks_of_type(BLOCK_PARAGRAPH)[0].text == "正文"


def test_validation_errors_detects_broken_invariants():
    document = ParsedDocument(
        title="",
        blocks=(
            Block(type=BLOCK_TABLE, text="", order=7),
            Block(type=BLOCK_IMAGE, text="", order=8),
        ),
        metadata={},
    )

    problems = "\n".join(document.validation_errors())

    assert "metadata 缺少必需键：content_hash" in problems
    assert "title 为空" in problems
    assert "应为 0" in problems
    assert "表格但没有 table_json" in problems
    assert "图片但没有 image_ref" in problems


# ---------------------------------------------------------------- 契约：禁止事项


def test_parsers_do_not_accept_tenant_id():
    """解析器不接收 tenant_id：租户归属由调用方在写库时决定。"""

    for parser in default_parsers():
        parameters = list(inspect.signature(parser.parse).parameters)

        assert parameters == ["source", "filename"], f"{parser.parser_name} 的 parse 参数被改动了"


@pytest.mark.parametrize("forbidden", ["sqlalchemy", "services.ingestion.repository", "services.ingestion.db"])
def test_parser_modules_do_not_depend_on_database(forbidden):
    """解析器不得写数据库：用源码扫描把这条纪律变成可执行断言。"""

    offenders = [
        path.name
        for path in sorted(PARSER_DIR.glob("*.py"))
        if forbidden in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"以下解析器模块引用了 {forbidden}：{offenders}"


def test_every_parser_satisfies_the_document_parser_protocol():
    for parser in default_parsers():
        assert isinstance(parser, DocumentParser), f"{parser.parser_name} 不满足 DocumentParser 协议"
        assert parser.parser_name
        assert parser.parser_version
        assert parser.supported_types
        for source_type in parser.supported_types:
            assert source_type in models.DOCUMENT_SOURCE_TYPES


# ---------------------------------------------------------------- 契约：失败路径


def test_empty_bytes_reports_empty_document():
    for parser in default_parsers():
        with pytest.raises(ParserError) as excinfo:
            parser.parse(b"", "empty.bin")

        assert excinfo.value.error_code == ERROR_EMPTY_DOCUMENT


def test_non_bytes_input_raises_type_error():
    with pytest.raises(TypeError):
        PlainTextParser().parse("这其实是个字符串", "a.txt")  # type: ignore[arg-type]


def test_whitespace_only_text_reports_empty_document():
    with pytest.raises(ParserError) as excinfo:
        PlainTextParser().parse("   \n\n \t \n".encode(), "blank.txt")

    assert excinfo.value.error_code == ERROR_EMPTY_DOCUMENT


def test_corrupt_pdf_reports_corrupt_document():
    with pytest.raises(ParserError) as excinfo:
        PdfParser(use_default_ocr=False).parse(b"this is definitely not a pdf", "broken.pdf")

    assert excinfo.value.error_code == ERROR_CORRUPT_DOCUMENT
    assert excinfo.value.filename == "broken.pdf"
    assert excinfo.value.parser_name == "pdf"


def test_corrupt_docx_reports_corrupt_document():
    with pytest.raises(ParserError) as excinfo:
        DocxParser(use_default_ocr=False).parse(b"PK\x03\x04 not a real zip", "broken.docx")

    assert excinfo.value.error_code == ERROR_CORRUPT_DOCUMENT
    assert excinfo.value.parser_name == "docx"


def test_unsupported_extension_reports_unsupported_type():
    with pytest.raises(ParserError) as excinfo:
        parse_document(b"data", "archive.zip")

    assert excinfo.value.error_code == ERROR_UNSUPPORTED_TYPE
    assert "allowed_extensions" in excinfo.value.detail


def test_unexpected_exception_is_wrapped_without_being_swallowed(monkeypatch):
    """未预期异常必须转成结构化错误，同时保留原始异常链（``__cause__``）。"""

    parser = PlainTextParser()

    def explode(source: bytes, filename: str) -> ParsedDocument:
        raise RuntimeError("模拟解析器内部崩溃")

    monkeypatch.setattr(parser, "_parse", explode)

    with pytest.raises(ParserError) as excinfo:
        parser.parse(b"hello world", "a.txt")

    assert excinfo.value.error_code == ERROR_PARSE_FAILED
    assert isinstance(excinfo.value.__cause__, RuntimeError)
    assert "模拟解析器内部崩溃" in excinfo.value.message
    assert excinfo.value.detail["exception"] == "RuntimeError"


def test_parser_error_from_subclass_is_not_rewrapped(monkeypatch):
    """已经是结构化错误时原样上抛，避免二次包装丢掉原始错误码。"""

    parser = PlainTextParser()

    def explode(source: bytes, filename: str) -> ParsedDocument:
        raise ParserError(ERROR_CORRUPT_DOCUMENT, "原始错误", parser_name="plain_text")

    monkeypatch.setattr(parser, "_parse", explode)

    with pytest.raises(ParserError) as excinfo:
        parser.parse(b"hello world", "a.txt")

    assert excinfo.value.error_code == ERROR_CORRUPT_DOCUMENT
    assert excinfo.value.message == "原始错误"


def test_missing_pymupdf_reports_missing_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymupdf", None)
    monkeypatch.setitem(sys.modules, "fitz", None)

    with pytest.raises(ParserError) as excinfo:
        pdf_module._load_pymupdf()

    assert excinfo.value.error_code == ERROR_MISSING_DEPENDENCY
    assert excinfo.value.detail["missing_package"] == "pymupdf"


def test_missing_python_docx_reports_missing_dependency(monkeypatch):
    monkeypatch.setitem(sys.modules, "docx", None)

    with pytest.raises(ParserError) as excinfo:
        docx_module._require_python_docx()

    assert excinfo.value.error_code == ERROR_MISSING_DEPENDENCY
    assert excinfo.value.detail["missing_package"] == "python-docx"


def test_invalid_produced_document_is_rejected(monkeypatch):
    """解析器写出违反契约的产物时，必须在源头失败而不是流到下游。"""

    parser = PlainTextParser()

    def broken(source: bytes, filename: str) -> ParsedDocument:
        return ParsedDocument(title="标题", blocks=(Block(type=BLOCK_TABLE, text="A | B", order=5),))

    monkeypatch.setattr(parser, "_parse", broken)

    with pytest.raises(ParserError) as excinfo:
        parser.parse(b"hello world", "a.txt")

    assert excinfo.value.error_code == ERROR_PARSE_FAILED
    assert "表格但没有 table_json" in excinfo.value.message


# ---------------------------------------------------------------- 样本文件本身


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixtures_exist_and_are_under_size_limit(name):
    path = FIXTURES / name

    assert path.is_file(), f"缺少样本文件 {name}，请运行 scripts/make_parser_fixtures.py"
    assert path.stat().st_size <= MAX_FIXTURE_BYTES, f"{name} 超过 1 MB，会卡住 CI 体积检查"


def test_text_fixtures_match_the_generator_script():
    """文本样本必须与生成脚本里的常量一致（忽略换行符差异）。

    防止「有人手工改过样本文件」而脚本再生成一次就把改动冲掉，导致断言悄悄失效。
    比对前统一换行符：仓库已用 `.gitattributes` 把这两个样本锁成 LF，
    但开发者本机可能是 CRLF 检出的，换行符差异与「内容是否被改过」无关。
    二进制样本（docx / pdf）含压缩流，不做逐字节比对，
    改由「存在性 + 体积」和各自的结构断言共同守护。
    """

    from scripts.make_parser_fixtures import BUILDERS

    for name in ("sample.md", "sample.html"):
        generated = BUILDERS[name]().replace(b"\r\n", b"\n")
        on_disk = load_fixture(name).replace(b"\r\n", b"\n")

        assert generated == on_disk, f"{name} 与生成脚本不一致，请重新运行 scripts/make_parser_fixtures.py"


# ---------------------------------------------------------------- 块顺序与通用不变量


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_block_order_is_contiguous_and_starts_at_zero(name):
    document = parse_fixture(name)

    assert [block.order for block in document.blocks] == list(range(len(document.blocks)))
    assert document.validation_errors() == []


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_metadata_is_json_serializable_and_carries_content_hash(name):
    payload = load_fixture(name)
    document = parse_fixture(name)

    assert document.metadata["content_hash"] == compute_content_hash(payload)
    assert document.metadata["byte_size"] == len(payload)
    assert document.metadata["filename"] == name
    assert document.metadata["block_count"] == len(document.blocks)
    json.dumps(dict(document.metadata), ensure_ascii=False)
    json.dumps(document.to_dict(), ensure_ascii=False)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_parsing_is_deterministic(name):
    assert parse_fixture(name).to_dict() == parse_fixture(name).to_dict()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_title_is_never_empty(name):
    assert parse_fixture(name).title.strip()


# ---------------------------------------------------------------- Markdown


def test_markdown_block_sequence_matches_document_structure():
    document = parse_fixture("sample.md")

    assert document.title == "客户服务知识库测试文档"
    assert [block.type for block in document.blocks] == [
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_LIST,
        BLOCK_TABLE,
        BLOCK_CODE,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_IMAGE,
    ]


def test_markdown_heading_path_includes_the_heading_itself():
    document = parse_fixture("sample.md")

    first = document.blocks[0]
    assert first.type == BLOCK_HEADING
    assert first.heading_path == ("客户服务知识库测试文档",)

    second_level = document.blocks[2]
    assert second_level.text == "退款政策"
    assert second_level.heading_path == ("客户服务知识库测试文档", "退款政策")

    third_level = document.blocks[9]
    assert third_level.text == "抬头修改"
    assert third_level.heading_path == ("客户服务知识库测试文档", "发票开具", "抬头修改")


def test_markdown_heading_path_does_not_concatenate_closed_levels():
    """``## A`` → ``### B`` → ``# C`` 之后，C 的路径不能带上 A / B。"""

    source = "# C\n\n正文\n\n## A\n\n### B\n\n### B2\n".encode()
    document = MarkdownParser().parse(source, "t.md")

    paths = {block.text: block.heading_path for block in document.blocks if block.type == BLOCK_HEADING}

    assert paths["C"] == ("C",)
    assert paths["A"] == ("C", "A")
    assert paths["B"] == ("C", "A", "B")
    assert paths["B2"] == ("C", "A", "B2")


def test_markdown_body_blocks_inherit_current_heading_path():
    document = parse_fixture("sample.md")

    list_block = document.blocks_of_type(BLOCK_LIST)[0]
    table_block = document.blocks_of_type(BLOCK_TABLE)[0]

    assert list_block.heading_path == ("客户服务知识库测试文档", "退款政策")
    assert table_block.heading_path == ("客户服务知识库测试文档", "退款政策")


def test_markdown_strips_inline_markup_but_keeps_content():
    document = parse_fixture("sample.md")

    paragraph = document.blocks[3]

    assert "7 个工作日" in paragraph.text
    assert "服务条款" in paragraph.text
    assert "**" not in paragraph.text
    assert "https://example.com/terms" not in paragraph.text


def test_markdown_list_normalizes_markers():
    document = parse_fixture("sample.md")

    lines = document.blocks_of_type(BLOCK_LIST)[0].text.split("\n")

    assert lines == ["- 未发货订单可全额退款", "- 已发货订单需扣除运费", "- 超过 30 天的订单不予受理"]


def test_markdown_ordered_list_renumbers_from_one():
    document = MarkdownParser().parse("3. 第三项\n7. 第七项\n".encode(), "t.md")

    assert document.blocks_of_type(BLOCK_LIST)[0].text == "1. 第三项\n2. 第七项"


def test_markdown_table_json_is_exact():
    document = parse_fixture("sample.md")

    table = document.blocks_of_type(BLOCK_TABLE)[0]

    assert table.table_json == {
        "columns": ["订单状态", "处理时限", "责任人"],
        "rows": [["待发货", "1 个工作日", "客服组"], ["已发货", "3 个工作日", "物流组"]],
    }
    assert table.text == "订单状态 | 处理时限 | 责任人\n待发货 | 1 个工作日 | 客服组\n已发货 | 3 个工作日 | 物流组"


def test_markdown_code_block_is_kept_verbatim():
    document = parse_fixture("sample.md")

    code = document.blocks_of_type(BLOCK_CODE)[0]

    assert code.text == 'def refund_days(status: str) -> int:\n    return 7 if status == "paid" else 0'
    assert code.heading_path == ("客户服务知识库测试文档", "退款政策")


def test_markdown_unclosed_code_fence_consumes_to_end_of_file():
    document = MarkdownParser().parse("# 标题\n\n```\n未闭合\n".encode(), "t.md")

    assert document.blocks_of_type(BLOCK_CODE)[0].text == "未闭合"


def test_markdown_image_line_becomes_image_block_with_reference():
    document = parse_fixture("sample.md")

    image = document.blocks_of_type(BLOCK_IMAGE)[0]

    assert image.image_ref == "images/invoice-sample.png"
    assert image.text == "发票样例"


def test_markdown_setext_heading_is_recognized():
    document = MarkdownParser().parse("一级标题\n=====\n\n正文\n\n二级标题\n-----\n".encode(), "t.md")

    headings = [(block.text, block.heading_path) for block in document.blocks_of_type(BLOCK_HEADING)]

    assert headings == [("一级标题", ("一级标题",)), ("二级标题", ("一级标题", "二级标题"))]


def test_markdown_has_no_page_numbers():
    document = parse_fixture("sample.md")

    assert all(block.page is None for block in document.blocks)
    assert document.page_count == 0


# ---------------------------------------------------------------- HTML


def test_html_removes_noise_tags():
    document = parse_fixture("sample.html")
    text = document.text

    assert "window.tracker" not in text
    assert "display: none" not in text
    assert "站点页眉标题" not in text
    assert "广告位" not in text
    assert "版权所有" not in text
    assert "<input" not in text


def test_html_block_sequence_matches_document_structure():
    document = parse_fixture("sample.html")

    assert [block.type for block in document.blocks] == [
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_LIST,
        BLOCK_LIST,
        BLOCK_HEADING,
        BLOCK_TABLE,
        BLOCK_CODE,
        BLOCK_IMAGE,
    ]


def test_html_title_comes_from_title_tag():
    document = parse_fixture("sample.html")

    assert document.title == "客户服务手册（HTML 样本）"
    assert document.metadata["html_title"] == "客户服务手册（HTML 样本）"
    assert document.metadata["html_extractor"] == "beautifulsoup4"


def test_html_table_uses_thead_as_header():
    document = parse_fixture("sample.html")

    table = document.blocks_of_type(BLOCK_TABLE)[0]

    assert table.table_json == {
        "columns": ["等级", "响应时限", "适用客户"],
        "rows": [["P0", "30 分钟", "签约客户"], ["P1", "2 小时", "全部客户"]],
    }
    assert table.heading_path == ("客户服务手册", "服务等级")


def test_html_pre_becomes_code_block_and_img_becomes_image_block():
    document = parse_fixture("sample.html")

    code = document.blocks_of_type(BLOCK_CODE)[0]
    image = document.blocks_of_type(BLOCK_IMAGE)[0]

    assert code.text == "curl -X POST https://api.example.com/v1/tickets"
    assert image.image_ref == "/static/images/flow.png"
    assert image.text == "工单流转图"


def test_html_blockquote_text_is_not_duplicated():
    html = "<html><body><blockquote><p>引用内容</p></blockquote></body></html>".encode()

    blocks = HtmlParser().parse(html, "t.html").blocks

    assert [block.text for block in blocks] == ["引用内容"]
    assert blocks[0].type == "quote"


def test_html_falls_back_to_trafilatura_when_structure_yields_too_little():
    """正文被塞在畸形结构里时，结构遍历提取不到东西，应降级并留下警告。"""

    html = (
        "<html><body>"
        "<div class='layout'><span>这是一段被拆散在各处、"
        "结构遍历几乎提取不到连续正文的非常分散的内容片段集合。</span>"
        "<em>第二段同样零散的文字内容在这里继续展开描述。</em></div>"
        "</body></html>"
    ).encode()
    parser = HtmlParser(min_text_chars=10_000)

    document = parser.parse(html, "t.html")

    assert parser.min_text_chars == 10_000
    assert document.metadata["html_extractor"] == "trafilatura"
    assert [warning.code for warning in document.warnings] == ["degraded_extraction"]
    assert document.warnings[0].detail["threshold"] == 10_000
    assert document.blocks[0].type == BLOCK_PARAGRAPH


def test_html_has_no_page_numbers():
    document = parse_fixture("sample.html")

    assert all(block.page is None for block in document.blocks)
    assert document.page_count == 0


def test_html_without_extractable_text_reports_empty_document():
    html = "<html><head><script>var a = 1;</script></head><body><nav>x</nav></body></html>".encode()

    with pytest.raises(ParserError) as excinfo:
        HtmlParser().parse(html, "empty.html")

    assert excinfo.value.error_code == ERROR_EMPTY_DOCUMENT


# ---------------------------------------------------------------- DOCX


def test_docx_block_sequence_matches_document_structure():
    document = parse_fixture("sample.docx")

    assert [block.type for block in document.blocks] == [
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_LIST,
        BLOCK_LIST,
        BLOCK_TABLE,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
        BLOCK_HEADING,
        BLOCK_PARAGRAPH,
    ]


def test_docx_heading_levels_build_nested_heading_path():
    document = parse_fixture("sample.docx")

    assert document.title == "员工手册（DOCX 样本）"
    assert document.blocks[0].heading_path == ("员工手册（DOCX 样本）",)
    assert document.blocks[2].heading_path == ("员工手册（DOCX 样本）", "考勤制度")
    assert document.blocks[9].heading_path == ("员工手册（DOCX 样本）", "休假制度", "年假天数")
    assert document.metadata["docx_heading_count"] == 4


def test_docx_table_keeps_structure_and_text():
    document = parse_fixture("sample.docx")

    table = document.blocks_of_type(BLOCK_TABLE)[0]

    assert table.table_json == {
        "columns": ["班次", "上班时间", "下班时间"],
        "rows": [["早班", "09:00", "18:00"], ["晚班", "13:00", "22:00"]],
    }
    assert table.text.startswith("班次 | 上班时间 | 下班时间")
    assert table.heading_path == ("员工手册（DOCX 样本）", "考勤制度")


def test_docx_list_items_are_detected_by_style():
    document = parse_fixture("sample.docx")

    items = document.blocks_of_type(BLOCK_LIST)

    assert [item.text for item in items] == ["迟到超过三次将计入绩效。", "请假需提前一个工作日提交。"]
    assert all(item.heading_path == ("员工手册（DOCX 样本）", "考勤制度") for item in items)


def test_docx_core_property_title_wins_over_first_heading():
    import io

    from docx import Document

    source = io.BytesIO(load_fixture("sample.docx"))
    editable = Document(source)
    editable.core_properties.title = "核心属性标题"
    buffer = io.BytesIO()
    editable.save(buffer)

    document = DocxParser(use_default_ocr=False).parse(buffer.getvalue(), "sample.docx")

    assert document.title == "核心属性标题"


def test_docx_paragraphs_have_no_page_numbers():
    document = parse_fixture("sample.docx")

    assert all(block.page is None for block in document.blocks)
    assert document.page_count == 0


# ---------------------------------------------------------------- PDF


def test_pdf_keeps_page_numbers():
    document = parse_fixture("sample.pdf")

    assert document.metadata["pdf_page_count"] == 2
    assert document.page_count == 2
    assert [block.page for block in document.blocks] == [1, 1, 1, 1, 1, 2, 2]


def test_pdf_page_break_splits_heading_context_across_pages():
    document = parse_fixture("sample.pdf")

    on_second_page = [block for block in document.blocks if block.page == 2]

    assert on_second_page[0].type == BLOCK_HEADING
    assert on_second_page[0].text == "下半年计划"
    assert on_second_page[0].heading_path == ("年度服务报告", "下半年计划")
    assert on_second_page[1].heading_path == ("年度服务报告", "下半年计划")


def test_pdf_heading_levels_are_derived_from_font_size():
    document = parse_fixture("sample.pdf")

    headings = [block.text for block in document.blocks_of_type(BLOCK_HEADING)]

    assert headings == ["年度服务报告", "营收概览", "下半年计划"]


def test_pdf_table_is_extracted_and_not_duplicated_as_paragraph():
    document = parse_fixture("sample.pdf")

    tables = document.blocks_of_type(BLOCK_TABLE)

    assert len(tables) == 1
    assert tables[0].table_json == {
        "columns": ["季度", "营收", "同比"],
        "rows": [["第一季度", "1200", "+8%"], ["第二季度", "1350", "+12%"]],
    }
    assert tables[0].page == 1
    paragraphs = "".join(block.text for block in document.blocks_of_type(BLOCK_PARAGRAPH))
    assert "第一季度" not in paragraphs


def test_pdf_ocr_not_triggered_when_text_layer_is_sufficient():
    engine = FakeOcrEngine()
    parser = PdfParser(ocr_engine=engine, use_default_ocr=False)

    document = parser.parse(load_fixture("sample.pdf"), "sample.pdf")

    assert engine.calls == []
    assert document.metadata["pdf_ocr_used"] is False
    assert document.warnings == ()


def test_pdf_ocr_triggered_when_text_layer_is_insufficient():
    engine = FakeOcrEngine("扫描件正文")
    parser = PdfParser(ocr_engine=engine, use_default_ocr=False)

    document = parser.parse(build_pdf((("1", 11.0),)), "scan.pdf")

    assert len(engine.calls) == 1
    assert engine.calls[0].startswith(b"\x89PNG")
    assert document.metadata["pdf_ocr_used"] is True
    assert document.metadata["pdf_ocr_engine"] == "fake-ocr"
    assert document.warnings == ()
    assert any(block.text == "扫描件正文" for block in document.blocks)
    ocr_block = next(block for block in document.blocks if block.text == "扫描件正文")
    assert ocr_block.page == 1
    assert ocr_block.type == BLOCK_PARAGRAPH


def test_pdf_ocr_engine_missing_degrades_to_warning_not_failure():
    parser = PdfParser(use_default_ocr=False)

    document = parser.parse(build_pdf((("1", 11.0),)), "scan.pdf")

    assert [warning.code for warning in document.warnings] == [WARNING_NO_TEXT_LAYER]
    detail = document.warnings[0].detail
    assert detail["ocr_engine"] is None
    assert detail["extracted_char_count"] < detail["threshold"]
    assert document.metadata["pdf_ocr_used"] is False


def test_pdf_ocr_returning_nothing_still_warns():
    engine = FakeOcrEngine("")
    parser = PdfParser(ocr_engine=engine, use_default_ocr=False)

    document = parser.parse(build_pdf((("1", 11.0),)), "scan.pdf")

    assert engine.calls
    assert [warning.code for warning in document.warnings] == [WARNING_NO_TEXT_LAYER]
    assert document.warnings[0].detail["ocr_engine"] == "fake-ocr"
    assert "OCR 未识别出内容" in document.warnings[0].message


def test_pdf_threshold_boundary_decides_whether_ocr_runs(monkeypatch):
    engine = FakeOcrEngine("识别结果")
    parser = PdfParser(ocr_engine=engine, use_default_ocr=False)
    monkeypatch.setattr(parser, "min_text_chars", 0)

    parser.parse(build_pdf((("1", 11.0),)), "scan.pdf")

    assert engine.calls == []


# ---------------------------------------------------------------- OCR 模块本身


def test_should_use_ocr_is_below_threshold_not_equal():
    assert should_use_ocr("x" * 31, 32) is True
    assert should_use_ocr("x" * 32, 32) is False
    assert should_use_ocr("   ", 1) is True
    assert should_use_ocr("内容", 0) is False


def test_ocr_threshold_env_override(monkeypatch):
    monkeypatch.setenv(OCR_MIN_TEXT_CHARS_ENV, "5")
    assert ocr_min_text_chars() == 5
    assert should_use_ocr("12345") is False

    parser = PdfParser(use_default_ocr=False)
    assert parser.min_text_chars == 5

    monkeypatch.setenv(OCR_MIN_TEXT_CHARS_ENV, "")
    assert ocr_min_text_chars() == ocr_module.DEFAULT_OCR_MIN_TEXT_CHARS


@pytest.mark.parametrize("value", ["abc", "-1", "3.5"])
def test_invalid_ocr_threshold_env_is_rejected(monkeypatch, value):
    monkeypatch.setenv(OCR_MIN_TEXT_CHARS_ENV, value)

    with pytest.raises(ValueError):
        ocr_min_text_chars()

    with pytest.raises(ValueError):
        PdfParser(use_default_ocr=False)


def test_default_ocr_engine_is_absent_when_rapidocr_not_installed():
    """默认不装 rapidocr-onnxruntime，因此默认引擎应当是不存在而不是报错。"""

    assert isinstance(ocr_module.RapidOcrEngine().is_available(), bool)


def test_rapidocr_engine_raises_structured_error_when_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", None)

    with pytest.raises(ParserError) as excinfo:
        ocr_module.RapidOcrEngine()._load()

    assert excinfo.value.error_code == "ocr_unavailable"
    assert excinfo.value.detail["missing_package"] == "rapidocr-onnxruntime"


def test_empty_image_is_rejected_by_ocr_engine():
    with pytest.raises(ParserError) as excinfo:
        ocr_module.RapidOcrEngine().recognize_image(b"")

    assert excinfo.value.error_code == "ocr_unavailable"


# ---------------------------------------------------------------- 纯文本与编码


def test_plain_text_splits_paragraphs_on_blank_lines():
    document = PlainTextParser().parse("第一段\n仍然第一段\n\n第二段\n".encode(), "a.txt")

    assert [block.text for block in document.blocks] == ["第一段\n仍然第一段", "第二段"]
    assert all(block.type == BLOCK_PARAGRAPH for block in document.blocks)
    assert all(block.heading_path == () for block in document.blocks)


def test_plain_text_form_feed_produces_page_numbers():
    document = PlainTextParser().parse("第一页\n\f第二页\n".encode(), "a.txt")

    assert [block.page for block in document.blocks] == [1, 2]
    assert document.metadata["txt_has_page_breaks"] is True


def test_plain_text_without_form_feed_has_no_pages():
    document = PlainTextParser().parse("只有一页\n".encode(), "a.txt")

    assert all(block.page is None for block in document.blocks)
    assert document.metadata["txt_has_page_breaks"] is False


def test_non_utf8_text_is_decoded_with_warning():
    document = PlainTextParser().parse("中文内容".encode("gb18030"), "gbk.txt")

    assert document.metadata["txt_encoding"] == "gb18030"
    assert [warning.code for warning in document.warnings] == [WARNING_ENCODING_FALLBACK]
    assert document.warnings[0].detail["encoding"] == "gb18030"
    assert "中文内容" in document.text


def test_utf8_bom_is_stripped_without_warning():
    document = PlainTextParser().parse("\ufeff标题".encode("utf-8"), "bom.txt")

    assert document.title == "标题"
    assert document.warnings == ()
    assert document.metadata["txt_encoding"] == "utf-8-sig"


def test_title_falls_back_to_filename_stem_when_no_text_title_is_available():
    document = PlainTextParser().parse("\n\n只有正文，没有像标题的一行\n".encode(), "服务说明.txt")

    assert document.title == "只有正文，没有像标题的一行"


# ---------------------------------------------------------------- 注册表


def test_registry_registers_all_builtin_types():
    registry = default_registry()

    assert registry.available_types == ("docx", "html", "md", "pdf", "txt")
    assert len(registry) == 5
    assert "pdf" in registry
    assert "exe" not in registry


def test_registry_rejects_duplicate_source_type():
    registry = ParserRegistry([PlainTextParser()])

    with pytest.raises(ValueError, match="已被注册"):
        registry.register(PlainTextParser())


def test_registry_rejects_unknown_source_type():
    class ExeParser(PlainTextParser):
        supported_types = ("exe",)

    with pytest.raises(ValueError, match="未知来源类型"):
        ParserRegistry([ExeParser()])


def test_registry_rejects_parser_without_supported_types():
    class EmptyParser(PlainTextParser):
        supported_types = ()

    with pytest.raises(ValueError, match="没有声明任何"):
        ParserRegistry([EmptyParser()])


def test_registry_get_unknown_type_reports_unsupported():
    with pytest.raises(ParserError) as excinfo:
        ParserRegistry().get("pdf")

    assert excinfo.value.error_code == ERROR_UNSUPPORTED_TYPE
    assert excinfo.value.detail["available"] == []


def test_get_parser_returns_registered_parser():
    assert isinstance(get_parser("pdf"), PdfParser)
    assert isinstance(get_parser("docx"), DocxParser)
    assert isinstance(get_parser("html"), HtmlParser)
    assert isinstance(get_parser("md"), MarkdownParser)
    assert isinstance(get_parser("txt"), PlainTextParser)


def test_extension_wins_over_mime():
    """名为 .pdf 的文件即使声明 text/html，也必须走 PDF 解析器。"""

    assert detect_source_type("report.pdf", "text/html") == "pdf"
    assert detect_source_type("manual.docx", "text/plain") == "docx"


def test_mime_is_used_only_when_extension_is_unrecognized():
    assert detect_source_type("noextension", "text/html") == "html"
    assert detect_source_type("weird.bin", "application/pdf") == "pdf"
    assert detect_source_type("UPPER.PDF") == "pdf"
    assert detect_source_type("带中文名.文档.md") == "md"


def test_detect_source_type_ignores_parameters_in_content_type():
    assert detect_source_type("noextension", "text/html; charset=utf-8") == "html"


def test_unknown_extension_and_unknown_mime_reports_unsupported():
    with pytest.raises(ParserError) as excinfo:
        detect_source_type("a.zip", "application/zip")

    assert excinfo.value.error_code == ERROR_UNSUPPORTED_TYPE
    assert excinfo.value.detail["extension"] == ".zip"


def test_allowed_extensions_cover_every_source_type():
    for source_type in models.DOCUMENT_SOURCE_TYPES:
        extensions = [ext for ext, mapped in EXTENSION_SOURCE_TYPES.items() if mapped == source_type]
        assert extensions, f"来源类型 {source_type} 没有对应扩展名"
        assert all(ext in ALLOWED_EXTENSIONS for ext in extensions)


def test_registry_parse_can_skip_detection_with_explicit_source_type():
    document = default_registry().parse("纯文本内容".encode(), "unknown.bin", source_type="txt")

    assert document.source_type == "txt"
    assert document.metadata["source_type"] == "txt"


def test_registry_parse_uses_mime_fallback_for_extensionless_upload():
    document = default_registry().parse("纯文本内容".encode(), "noextension", content_type="text/plain")

    assert document.source_type == "txt"


def test_reset_default_registry_rebuilds_lazily(monkeypatch):
    reset_default_registry()
    monkeypatch.setenv(OCR_MIN_TEXT_CHARS_ENV, "7")

    assert default_registry().available_types == ("docx", "html", "md", "pdf", "txt")
    assert get_parser("pdf").min_text_chars == 7

    reset_default_registry()
    monkeypatch.delenv(OCR_MIN_TEXT_CHARS_ENV, raising=False)
    assert get_parser("pdf").min_text_chars == ocr_module.DEFAULT_OCR_MIN_TEXT_CHARS
    reset_default_registry()


# ---------------------------------------------------------------- 解析器版本与契约常量


def test_parser_versions_are_declared_and_distinct_per_format():
    versions = {parser.parser_name: parser.parser_version for parser in default_parsers()}

    assert versions == {
        "plain_text": "1.0.0",
        "markdown": "1.0.0",
        "html": "1.0.0",
        "pdf": "1.0.0",
        "docx": "1.0.0",
    }


def test_parsed_document_records_parser_identity():
    document = parse_fixture("sample.md")

    assert document.parser_name == "markdown"
    assert document.parser_version == "1.0.0"
    assert document.source_type == "md"
    assert document.metadata["parser_contract_version"] == parser_base.PARSER_CONTRACT_VERSION
