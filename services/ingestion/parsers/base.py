"""文档解析契约（计划 2.1）。

这一层只定义「解析器长什么样」和「解析产物长什么样」，不含任何具体格式的实现，
也不碰数据库。下游（切分器、流水线、API）只依赖本模块的类型，不依赖具体解析器。

契约要点
--------
* **解析器是无状态、纯函数式的**：``parse(source, filename)`` 只吃字节、只吐
  :class:`ParsedDocument`，不接收 ``tenant_id``、不写数据库、不读环境里的租户上下文。
  租户归属由调用方（API 层的 ``AuthContext``）在写库时决定，解析器无权也无需知道。
* **失败必须是结构化的，且不能吞异常**：所有可预期的失败抛 :class:`ParserError`
  （带稳定 ``error_code``），非预期异常会被 :class:`TemplateDocumentParser` 包装成
  ``parse_failed`` 并用 ``raise ... from exc`` 保留原始 traceback。
  解析器**永不**返回 ``None`` 或用空文档掩盖失败。
* **产物可以 JSON 直存**：``metadata`` 与 ``warnings[*].detail`` 会被
  ``json.dumps`` 校验，保证能原样写进 ``document_versions.metadata_json``。
* **``content_hash`` 是租户内重复文件抑制的唯一依据**：每个解析器都必须对原始字节
  算 SHA-256，由模板统一写入 ``metadata["content_hash"]``。解析器本身不判定
  duplicate —— 判定逻辑属于流水线（阶段 3.3）。

约定：block 的 ``heading_path``
------------------------------
* 对标题块：``heading_path`` **包含自己**，例如 ``("第二章", "2.1 背景")``；
* 对正文块：``heading_path`` 是当前生效的标题栈，例如 ``("第二章", "2.1 背景")``。

这样切分器（阶段 3.2）可以只看 block 的 ``heading_path`` 就完成「按标题层级分组」，
不需要回头重建层级关系。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
import hashlib
import json
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------- 版本与枚举

#: 契约自身的版本。字段语义发生不兼容变化时提升，用于排查历史数据。
PARSER_CONTRACT_VERSION = "1.0"

#: block 类型。切分器按类型走不同策略，因此这是封闭集合，不接受随手新增。
BLOCK_HEADING = "heading"
BLOCK_PARAGRAPH = "paragraph"
BLOCK_LIST = "list"
BLOCK_TABLE = "table"
BLOCK_CODE = "code"
BLOCK_IMAGE = "image"
BLOCK_QUOTE = "quote"
BLOCK_PAGE_BREAK = "page_break"

BLOCK_TYPES: tuple[str, ...] = (
    BLOCK_HEADING,
    BLOCK_PARAGRAPH,
    BLOCK_LIST,
    BLOCK_TABLE,
    BLOCK_CODE,
    BLOCK_IMAGE,
    BLOCK_QUOTE,
    BLOCK_PAGE_BREAK,
)

#: ``ParsedDocument.metadata`` 里由模板保证必然存在的键。
#: 其余键由各解析器自行补充，命名建议加格式前缀（如 ``pdf_is_encrypted``）。
REQUIRED_METADATA_KEYS: tuple[str, ...] = (
    "content_hash",
    "byte_size",
    "filename",
    "source_type",
    "block_count",
)

# ---------------------------------------------------------------- 错误码

ERROR_UNSUPPORTED_TYPE = "unsupported_type"
ERROR_EMPTY_DOCUMENT = "empty_document"
ERROR_CORRUPT_DOCUMENT = "corrupt_document"
ERROR_MISSING_DEPENDENCY = "missing_dependency"
ERROR_OCR_UNAVAILABLE = "ocr_unavailable"
ERROR_PARSE_FAILED = "parse_failed"

ERROR_CODES: tuple[str, ...] = (
    ERROR_UNSUPPORTED_TYPE,
    ERROR_EMPTY_DOCUMENT,
    ERROR_CORRUPT_DOCUMENT,
    ERROR_MISSING_DEPENDENCY,
    ERROR_OCR_UNAVAILABLE,
    ERROR_PARSE_FAILED,
)

# ---------------------------------------------------------------- 警告码

WARNING_ENCODING_FALLBACK = "encoding_fallback"
WARNING_NO_TEXT_LAYER = "no_text_layer"
WARNING_EMPTY_SECTION = "empty_section"
WARNING_DEGRADED_EXTRACTION = "degraded_extraction"
WARNING_IMAGE_SKIPPED = "image_skipped"

WARNING_CODES: tuple[str, ...] = (
    WARNING_ENCODING_FALLBACK,
    WARNING_NO_TEXT_LAYER,
    WARNING_EMPTY_SECTION,
    WARNING_DEGRADED_EXTRACTION,
    WARNING_IMAGE_SKIPPED,
)


# ---------------------------------------------------------------- 工具函数


def compute_content_hash(source: bytes) -> str:
    """对原始文件字节算 SHA-256。

    统一放在这里，避免各解析器各写一份（哪怕写法略有差异都会导致同一文件
    在不同解析路径下得到不同 hash，从而让重复文件抑制失效）。
    """

    return hashlib.sha256(source).hexdigest()


def build_table_json(
    columns: Sequence[str],
    rows: Iterable[Sequence[str]],
) -> dict[str, Any]:
    """构造**稳定**的表格 JSON。

    形状固定为 ``{"columns": [...], "rows": [[...], ...]}``，且每行都会补齐到
    ``len(columns)``（短行补空串、长行截断），使下游可以无条件按下标取值，
    不必处理参差不齐的行。表格结构只此一种表示，避免 PDF / DOCX / HTML
    三个解析器各自发明一种 dict 形状。
    """

    normalized_columns = [str(cell or "").strip() for cell in columns]
    width = len(normalized_columns)
    normalized_rows: list[list[str]] = []
    for row in rows:
        cells = [str(cell or "").strip() for cell in row]
        if len(cells) < width:
            cells = cells + [""] * (width - len(cells))
        elif len(cells) > width:
            cells = cells[:width]
        normalized_rows.append(cells)
    return {"columns": normalized_columns, "rows": normalized_rows}


def render_table_text(table_json: Mapping[str, Any]) -> str:
    """把表格 JSON 渲染成可检索文本。

    表格既要「稳定 JSON」（给程序用），也要「可检索文本」（给 embedding 用）：
    只存 JSON 的话，向量检索永远匹配不到表里的单元格内容。
    渲染规则固定为每行用 `` | `` 连接，首行为表头。
    """

    columns = [str(cell) for cell in table_json.get("columns", [])]
    rows = table_json.get("rows", [])
    lines = [" | ".join(columns)] if columns else []
    for row in rows:
        lines.append(" | ".join(str(cell) for cell in row))
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- 值对象


@dataclass(slots=True)
class Block:
    """解析出的一个内容块。

    字段与计划 2.1 一一对应：

    ==================  ====================================================
    ``type``            :data:`BLOCK_TYPES` 之一
    ``text``            可检索正文（表格块这里是 :func:`render_table_text` 的结果）
    ``order``           文档内全局顺序，**从 0 开始连续**，由模板统一重编号
    ``page``            1 起页码；格式本身无页码概念时为 ``None``
    ``heading_path``    标题栈，约定见模块 docstring
    ``table_json``      仅表格块有值，形状见 :func:`build_table_json`
    ``image_ref``       仅图片块有值，是**引用**而非二进制内容
    ==================  ====================================================

    可变而不是 frozen：解析器按流式顺序拼块，需要边解析边回填 ``order`` / ``image_ref``；
    真正对外承诺不可变的终态是 :class:`ParsedDocument`。
    """

    type: str
    text: str = ""
    order: int = 0
    page: int | None = None
    heading_path: tuple[str, ...] = ()
    table_json: dict[str, Any] | None = None
    image_ref: str | None = None

    def __post_init__(self) -> None:
        if self.type not in BLOCK_TYPES:
            raise ValueError(f"未知的 block 类型：{self.type!r}")
        # 宽松地把 list 归一成 tuple，避免解析器从 JSON / DOM 拿到的 list 直接漏进来
        self.heading_path = tuple(self.heading_path or ())

    @property
    def heading_path_text(self) -> str:
        """``"第二章 > 2.1 背景"`` 形式的扁平标题路径，便于做检索上下文前缀。"""

        return " > ".join(self.heading_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "order": self.order,
            "page": self.page,
            "heading_path": list(self.heading_path),
            "table_json": self.table_json,
            "image_ref": self.image_ref,
        }


@dataclass(frozen=True, slots=True)
class ParseWarning:
    """一条可查询的解析警告（对应计划「解析警告必须可由任务查询接口读取」）。

    不做成裸字符串的原因：任务查询接口需要按 ``code`` 分类展示，
    并且 ``detail`` 里要能带上「回退到哪种编码」「第几页没有文本层」这类定位信息。
    """

    code: str
    message: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.code not in WARNING_CODES:
            raise ValueError(f"未知的警告码：{self.code!r}")
        object.__setattr__(self, "detail", dict(self.detail))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """解析产物，对外唯一承诺的终态对象。

    除计划列出的 ``title`` / ``blocks`` / ``metadata`` / ``warnings`` 外，多带三个
    自描述字段（``source_type`` / ``parser_name`` / ``parser_version``）：

    ``document_versions`` 需要落 ``parser_name`` 与 ``parser_version``，而流水线在
    「解析成功 → 写库」之间可能重试、可能换 worker。让产物自带这三个字段，
    就不必在流水线里额外保存「这次是哪个解析器对象干的」，
    也让一条历史版本记录可以脱离运行环境被解释。
    """

    title: str
    blocks: tuple[Block, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[ParseWarning, ...] = ()
    source_type: str = ""
    parser_name: str = ""
    parser_version: str = ""

    # ------------------------------------------------------------ 便捷视图

    @property
    def content_hash(self) -> str:
        """原始文件字节的 SHA-256，由模板保证存在。"""

        return str(self.metadata.get("content_hash", ""))

    @property
    def text(self) -> str:
        """所有块按顺序拼接的全文，块之间用空行分隔。"""

        return "\n\n".join(block.text for block in self.blocks if block.text).strip()

    @property
    def page_count(self) -> int:
        """最大页码；无页码概念（如 Markdown）时为 0。"""

        pages = [block.page for block in self.blocks if block.page is not None]
        return max(pages) if pages else 0

    def blocks_of_type(self, block_type: str) -> tuple[Block, ...]:
        return tuple(block for block in self.blocks if block.type == block_type)

    @property
    def tables(self) -> tuple[Block, ...]:
        return self.blocks_of_type(BLOCK_TABLE)

    # ------------------------------------------------------------ 自校验

    def validation_errors(self) -> list[str]:
        """检查本契约承诺的不变量，返回问题清单（空列表即合法）。

        由 :class:`TemplateDocumentParser` 在返回前调用，因此解析器写出
        「表格块没有 table_json」这类半成品会在源头暴露，而不是等切分器读到才炸。
        """

        problems: list[str] = []

        for key in REQUIRED_METADATA_KEYS:
            if key not in self.metadata:
                problems.append(f"metadata 缺少必需键：{key}")

        if list(self.metadata) != sorted(self.metadata):
            # 不强制排序，只是提示：稳定顺序能让 metadata_json 的 diff 保持可读
            pass

        for index, block in enumerate(self.blocks):
            if block.order != index:
                problems.append(f"第 {index} 个 block 的 order={block.order}，应为 {index}（必须连续且从 0 开始）")
            if block.type == BLOCK_TABLE:
                if not block.table_json:
                    problems.append(f"第 {index} 个 block 是表格但没有 table_json")
                elif not block.text:
                    problems.append(f"第 {index} 个 block 是表格但没有可检索文本")
            if block.type == BLOCK_IMAGE and not block.image_ref:
                problems.append(f"第 {index} 个 block 是图片但没有 image_ref")
            if block.type in (BLOCK_HEADING, BLOCK_PARAGRAPH, BLOCK_LIST, BLOCK_CODE, BLOCK_QUOTE):
                if not block.text:
                    problems.append(f"第 {index} 个 block（{block.type}）没有文本")

        if not self.title.strip():
            problems.append("title 为空")

        try:
            json.dumps(dict(self.metadata), ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            problems.append(f"metadata 无法 JSON 序列化：{exc}")

        for index, warning in enumerate(self.warnings):
            try:
                json.dumps(dict(warning.detail), ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                problems.append(f"第 {index} 条 warning 的 detail 无法 JSON 序列化：{exc}")

        return problems

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        """完整字典形式，供任务查询接口直接返回。"""

        return {
            "title": self.title,
            "source_type": self.source_type,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "metadata": dict(self.metadata),
            "warnings": [warning.to_dict() for warning in self.warnings],
            "blocks": [block.to_dict() for block in self.blocks],
        }


class ParserError(Exception):
    """结构化解析错误。

    调用方（流水线 / API）据此把 ``error_code`` 写进 ``ingestion_jobs.error_code``，
    把 ``message`` 写进 ``error_message``，无需解析异常字符串。

    继承 ``Exception`` 而不是返回错误对象：解析失败不是一种「返回值」，
    它是控制流的中断。返回错误对象会让调用方有「忘记检查」的机会，
    而忘记检查的后果是把一份空文档当成成功结果发到索引里。
    """

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        parser_name: str = "",
        filename: str = "",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        if error_code not in ERROR_CODES:
            raise ValueError(f"未知的错误码：{error_code!r}")
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.parser_name = parser_name
        self.filename = filename
        self.detail: dict[str, Any] = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "parser_name": self.parser_name,
            "filename": self.filename,
            "detail": dict(self.detail),
        }

    def __str__(self) -> str:  # pragma: no cover - 只为日志可读
        location = f"{self.parser_name}@{self.filename}" if self.parser_name or self.filename else ""
        return f"[{self.error_code}] {self.message}" + (f" ({location})" if location else "")


# ---------------------------------------------------------------- 协议


@runtime_checkable
class DocumentParser(Protocol):
    """解析器协议（计划 2.1 的 ``DocumentParser``）。

    ``runtime_checkable`` 让测试可以对每个具体解析器做 ``isinstance`` 断言，
    从而保证「新加的解析器满足契约」这件事有测试兜底，而不是靠人眼比对。
    """

    #: 本解析器负责的来源类型，取值必须是 ``models.DOCUMENT_SOURCE_TYPES`` 的子集
    supported_types: tuple[str, ...]
    #: 解析器标识，会写进 ``document_versions.parser_name``
    parser_name: str
    #: 解析器实现版本；解析行为变化时必须提升，便于识别「同一文件的旧解析结果」
    parser_version: str

    def parse(self, source: bytes, filename: str) -> ParsedDocument:
        """把原始字节解析成 :class:`ParsedDocument`。

        失败抛 :class:`ParserError`。**不接收 tenant_id、不写数据库。**
        """
        ...


class TemplateDocumentParser:
    """把各解析器的共性逻辑收在一处的模板基类。

    子类只需实现 :meth:`_parse`，本类负责：

    1. 入参校验（非 bytes 直接报编程错误，空字节报 ``empty_document``）；
    2. 统一写入 ``content_hash`` / ``byte_size`` / ``filename`` / ``source_type``
       / ``block_count``，**不允许子类各写各的**；
    3. 统一重编号 ``block.order``，保证「连续且从 0 开始」；
    4. title 兜底（解析不出标题时回退到文件名主干）；
    5. 把非预期异常包装成 ``parse_failed`` 并 ``raise ... from exc`` 保留原始 traceback；
    6. 返回前跑 :meth:`ParsedDocument.validation_errors`，把半成品挡在源头。
    """

    supported_types: tuple[str, ...] = ()
    parser_name: str = "template"
    parser_version: str = "0.0"

    # ------------------------------------------------------------ 对外入口

    def parse(self, source: bytes, filename: str) -> ParsedDocument:
        if not isinstance(source, (bytes, bytearray, memoryview)):
            raise TypeError(f"{type(self).__name__}.parse 只接受 bytes，收到 {type(source).__name__}")
        raw = bytes(source)
        if not raw:
            raise ParserError(
                ERROR_EMPTY_DOCUMENT,
                "文件内容为空（0 字节）",
                parser_name=self.parser_name,
                filename=filename,
            )

        try:
            document = self._parse(raw, filename)
        except ParserError:
            # 已经结构化过了，原样上抛，不要二次包装丢信息
            raise
        except Exception as exc:
            raise ParserError(
                ERROR_PARSE_FAILED,
                f"解析时发生未预期异常：{type(exc).__name__}: {exc}",
                parser_name=self.parser_name,
                filename=filename,
                detail={"exception": type(exc).__name__},
            ) from exc

        return self._finalize(document, raw, filename)

    # ------------------------------------------------------------ 子类实现

    def _parse(self, source: bytes, filename: str) -> ParsedDocument:
        raise NotImplementedError

    # ------------------------------------------------------------ 收尾

    def _finalize(self, document: ParsedDocument, source: bytes, filename: str) -> ParsedDocument:
        source_type = self.supported_types[0] if self.supported_types else document.source_type

        blocks = list(document.blocks)
        for index, block in enumerate(blocks):
            block.order = index

        metadata: dict[str, Any] = dict(document.metadata)
        metadata.update(
            {
                "content_hash": compute_content_hash(source),
                "byte_size": len(source),
                "filename": filename,
                "source_type": source_type,
                "block_count": len(blocks),
                "parser_contract_version": PARSER_CONTRACT_VERSION,
            }
        )
        # 稳定键序：让 metadata_json 的 diff 保持可读，也便于人工比对
        metadata = {key: metadata[key] for key in sorted(metadata)}

        title = document.title.strip() or _filename_stem(filename)

        finalized = replace(
            document,
            title=title,
            blocks=tuple(blocks),
            metadata=metadata,
            warnings=tuple(document.warnings),
            source_type=source_type,
            parser_name=self.parser_name,
            parser_version=self.parser_version,
        )

        problems = finalized.validation_errors()
        if problems:
            raise ParserError(
                ERROR_PARSE_FAILED,
                "解析产物不满足契约：" + "；".join(problems),
                parser_name=self.parser_name,
                filename=filename,
                detail={"problems": problems},
            )
        return finalized


def _filename_stem(filename: str) -> str:
    """从文件名取主干，用于标题兜底。只做字符串处理，不去碰文件系统。"""

    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return stem.strip() or "untitled"


__all__ = [
    "BLOCK_CODE",
    "BLOCK_HEADING",
    "BLOCK_IMAGE",
    "BLOCK_LIST",
    "BLOCK_PAGE_BREAK",
    "BLOCK_PARAGRAPH",
    "BLOCK_QUOTE",
    "BLOCK_TABLE",
    "BLOCK_TYPES",
    "ERROR_CODES",
    "ERROR_CORRUPT_DOCUMENT",
    "ERROR_EMPTY_DOCUMENT",
    "ERROR_MISSING_DEPENDENCY",
    "ERROR_OCR_UNAVAILABLE",
    "ERROR_PARSE_FAILED",
    "ERROR_UNSUPPORTED_TYPE",
    "PARSER_CONTRACT_VERSION",
    "REQUIRED_METADATA_KEYS",
    "WARNING_CODES",
    "WARNING_DEGRADED_EXTRACTION",
    "WARNING_EMPTY_SECTION",
    "WARNING_ENCODING_FALLBACK",
    "WARNING_IMAGE_SKIPPED",
    "WARNING_NO_TEXT_LAYER",
    "Block",
    "DocumentParser",
    "ParseWarning",
    "ParsedDocument",
    "ParserError",
    "TemplateDocumentParser",
    "build_table_json",
    "compute_content_hash",
    "render_table_text",
]
