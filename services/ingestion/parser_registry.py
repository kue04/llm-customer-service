"""解析器注册表（计划 2.2）。

职责有三件：

1. **按来源类型找解析器**：``get_parser("pdf")`` → :class:`PdfParser` 实例；
2. **按文件名/MIME 判定来源类型**：``detect_source_type("年报.pdf")`` → ``"pdf"``；
3. **一站式解析**：``parse_document(source, filename)`` 自己判定类型再委派。

判定优先级（安全相关，不要随意调整）
------------------------------------
**扩展名永远优先于 MIME**。``content_type`` 只在「扩展名缺失或无法识别」时作为兜底。

反过来的话就会出现：攻击者上传名为 ``报表.pdf`` 的文件、同时把
``Content-Type`` 声明成 ``text/html``，如果 MIME 优先，服务端就会用 HTML 解析器
去处理一个 HTML 文件，从而绕过「只接受 PDF」这类基于扩展名的准入规则。
扩展名优先时，声明错 MIME 只会让「扩展名不认识」的文件得到一次兜底机会，
不会改变已识别文件的处理路径。

注册表是**实例对象**而不是全局字典常量：测试要能新建一个只装了某个解析器的注册表，
生产要能通过 :func:`default_registry` 拿到默认那一份。
默认实例**懒加载**，避免「环境变量配错」在 ``import`` 阶段就炸出一个看不懂的栈。
"""

from __future__ import annotations

from collections.abc import Iterable

from services.ingestion.models import DOCUMENT_SOURCE_TYPES
from services.ingestion.parsers.base import (
    ERROR_UNSUPPORTED_TYPE,
    DocumentParser,
    ParsedDocument,
    ParserError,
)
from services.ingestion.parsers.docx import DocxParser
from services.ingestion.parsers.html import HtmlParser
from services.ingestion.parsers.markdown import MarkdownParser
from services.ingestion.parsers.pdf import PdfParser
from services.ingestion.parsers.plain_text import PlainTextParser


#: 来源类型 → 扩展名（含前导点，全部小写）
SOURCE_TYPE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "docx": (".docx",),
    "html": (".html", ".htm", ".xhtml"),
    "md": (".md", ".markdown", ".mdown"),
    "txt": (".txt", ".text"),
}

#: 扩展名 → 来源类型（由 SOURCE_TYPE_EXTENSIONS 反向展开，避免两份手工表漂移）
EXTENSION_SOURCE_TYPES: dict[str, str] = {
    extension: source_type
    for source_type, extensions in SOURCE_TYPE_EXTENSIONS.items()
    for extension in extensions
}

#: MIME → 来源类型。**仅作扩展名无法识别时的兜底**，理由见模块 docstring。
MIME_SOURCE_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "application/x-pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/msword": "docx",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/markdown": "md",
    "text/x-markdown": "md",
    "text/plain": "txt",
}

#: 上传接口允许的扩展名（阶段 2.3 的准入列表；放在这里保证与解析能力同源）
ALLOWED_EXTENSIONS: tuple[str, ...] = tuple(sorted(EXTENSION_SOURCE_TYPES))


def default_parsers() -> list[DocumentParser]:
    """返回内置解析器集合。

    注意这里**没有** ``ocr``：OCR 不是一种来源类型，而是 PDF/DOCX 解析器的内部增强，
    没有独立入口（计划 2.2 也只要求它「定义接口」）。
    """

    return [PdfParser(), DocxParser(), HtmlParser(), MarkdownParser(), PlainTextParser()]


class ParserRegistry:
    """来源类型 → 解析器实例的映射。"""

    def __init__(self, parsers: Iterable[DocumentParser] | None = None) -> None:
        self._parsers: dict[str, DocumentParser] = {}
        for parser in parsers or ():
            self.register(parser)

    # ------------------------------------------------------------ 注册与查找

    def register(self, parser: DocumentParser) -> None:
        supported = tuple(parser.supported_types)
        if not supported:
            raise ValueError(f"解析器 {parser.parser_name!r} 没有声明任何 supported_types")

        unknown = [source_type for source_type in supported if source_type not in DOCUMENT_SOURCE_TYPES]
        if unknown:
            raise ValueError(
                f"解析器 {parser.parser_name!r} 声明了未知来源类型 {unknown}；"
                f"合法取值只有 {DOCUMENT_SOURCE_TYPES}"
            )

        conflicts = [source_type for source_type in supported if source_type in self._parsers]
        if conflicts:
            raise ValueError(f"来源类型 {conflicts} 已被注册，重复注册会让解析行为取决于注册顺序")

        for source_type in supported:
            self._parsers[source_type] = parser

    def get(self, source_type: str) -> DocumentParser:
        parser = self._parsers.get(source_type)
        if parser is None:
            raise ParserError(
                ERROR_UNSUPPORTED_TYPE,
                f"没有支持 {source_type!r} 的解析器，当前已注册：{sorted(self._parsers)}",
                detail={"requested": source_type, "available": sorted(self._parsers)},
            )
        return parser

    @property
    def available_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._parsers))

    def __contains__(self, source_type: object) -> bool:
        return source_type in self._parsers

    def __len__(self) -> int:
        return len(self._parsers)

    # ------------------------------------------------------------ 类型判定

    def detect_source_type(self, filename: str, content_type: str | None = None) -> str:
        """判定来源类型。扩展名优先，MIME 仅兜底。"""

        extension = _extension_of(filename)
        source_type = EXTENSION_SOURCE_TYPES.get(extension)
        if source_type is not None and source_type in self._parsers:
            return source_type

        if content_type:
            normalized = content_type.split(";", 1)[0].strip().lower()
            guessed = MIME_SOURCE_TYPES.get(normalized)
            if guessed is not None and guessed in self._parsers:
                return guessed

        raise ParserError(
            ERROR_UNSUPPORTED_TYPE,
            f"无法识别文件类型：文件名 {filename!r}（扩展名 {extension or '无'}）、"
            f"Content-Type {content_type or '未提供'}",
            filename=filename,
            detail={
                "extension": extension,
                "content_type": content_type,
                "allowed_extensions": list(ALLOWED_EXTENSIONS),
            },
        )

    # ------------------------------------------------------------ 一站式解析

    def parse(
        self,
        source: bytes,
        filename: str,
        *,
        source_type: str | None = None,
        content_type: str | None = None,
    ) -> ParsedDocument:
        """判定类型并解析。调用方已知道类型时传 ``source_type`` 可跳过判定。"""

        resolved = source_type or self.detect_source_type(filename, content_type)
        parser = self.get(resolved)
        document = parser.parse(source, filename)
        if document.source_type != resolved:  # pragma: no cover - 契约自检
            raise ParserError(
                ERROR_UNSUPPORTED_TYPE,
                f"解析器 {parser.parser_name!r} 返回的 source_type={document.source_type!r} 与请求的 {resolved!r} 不一致",
                filename=filename,
            )
        return document


def _extension_of(filename: str) -> str:
    """取小写扩展名（含点）。只做字符串处理，**不碰文件系统**。"""

    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    index = name.rfind(".")
    if index <= 0:
        return ""
    return name[index:].lower()


#: 默认注册表的懒加载缓存。做成函数而不是模块级常量，
#: 是为了让「OCR 阈值环境变量写错」这类配置错误在**首次真正解析时**暴露，
#: 而不是在任何人 ``import services.ingestion.parser_registry`` 的瞬间就抛出。
_DEFAULT_REGISTRY: ParserRegistry | None = None


def default_registry() -> ParserRegistry:
    """返回默认注册表（含全部内置解析器），首次调用时构建并缓存。"""

    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = ParserRegistry(default_parsers())
    return _DEFAULT_REGISTRY


def reset_default_registry() -> None:
    """清掉默认注册表缓存。仅测试使用（例如改动 OCR 阈值环境变量后需要重建）。"""

    global _DEFAULT_REGISTRY
    _DEFAULT_REGISTRY = None


def get_parser(source_type: str) -> DocumentParser:
    """从默认注册表取解析器。"""

    return default_registry().get(source_type)


def detect_source_type(filename: str, content_type: str | None = None) -> str:
    """用默认注册表判定来源类型。"""

    return default_registry().detect_source_type(filename, content_type)


def parse_document(
    source: bytes,
    filename: str,
    *,
    source_type: str | None = None,
    content_type: str | None = None,
) -> ParsedDocument:
    """用默认注册表一站式解析。这是流水线（阶段 3.3）应当调用的入口。"""

    return default_registry().parse(source, filename, source_type=source_type, content_type=content_type)


__all__ = [
    "ALLOWED_EXTENSIONS",
    "EXTENSION_SOURCE_TYPES",
    "MIME_SOURCE_TYPES",
    "SOURCE_TYPE_EXTENSIONS",
    "ParserRegistry",
    "default_parsers",
    "default_registry",
    "detect_source_type",
    "get_parser",
    "parse_document",
    "reset_default_registry",
]
