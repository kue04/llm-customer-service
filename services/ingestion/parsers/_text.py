"""解析器内部共用的文本工具（私有模块）。

为什么不放在 ``base.py``：``base`` 是对外契约，只应包含下游需要依赖的类型；
而「怎么猜编码」「怎么切段」是实现细节，随时可能调整，不该进入契约面。

为什么不让各解析器各写一份：编码回退链必须**完全一致**。纯文本、Markdown、
HTML 三个解析器都要处理「字节 → 文本」，如果各自写一份回退链，
同一个 GBK 文件在不同格式下可能得到不同的乱码结果，
而这种不一致极难排查（往往表现为「检索不到那份文档」）。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
import re


#: 带 BOM 时直接按 BOM 指定的编码解。顺序有讲究：UTF-32 的前缀包含 UTF-16 的前缀，
#: 必须先判 UTF-32，否则 UTF-32 文件会被误判成 UTF-16。
_BOM_ENCODINGS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

#: 无 BOM 时的回退链。``latin-1`` 永不抛错，放最后保证一定有结果。
_FALLBACK_CHAIN: tuple[str, ...] = ("utf-8", "gb18030", "big5", "latin-1")


@dataclass(frozen=True, slots=True)
class DecodedText:
    """解码结果。

    ``is_fallback`` 为 ``True`` 表示不是首选编码（UTF-8）解出来的，
    调用方应当据此产生 :data:`~services.ingestion.parsers.base.WARNING_ENCODING_FALLBACK`
    警告 —— 非 UTF-8 解出来的文本有乱码风险，必须让任务查询接口能看见。
    """

    text: str
    encoding: str
    is_fallback: bool


def decode_bytes(source: bytes) -> DecodedText:
    """把原始字节解码成文本，带 BOM 识别与编码回退。

    不做编码嗅探库依赖（如 chardet）：回退链已覆盖中文场景最常见的
    UTF-8 / GB18030 / Big5，且行为完全确定、可测试。
    """

    for bom, encoding in _BOM_ENCODINGS:
        if source.startswith(bom):
            try:
                return DecodedText(source.decode(encoding), encoding, False)
            except UnicodeDecodeError:
                # BOM 与内容不匹配（文件损坏或拼接错误），退到后面的回退链
                break

    for index, encoding in enumerate(_FALLBACK_CHAIN):
        try:
            return DecodedText(source.decode(encoding), encoding, index > 0)
        except UnicodeDecodeError:
            continue

    # 理论上不可达：latin-1 能解任何字节序列
    return DecodedText(source.decode("latin-1", errors="replace"), "latin-1", True)


def normalize_newlines(text: str) -> str:
    """统一换行符为 ``\\n``，并去掉 BOM 残留字符。"""

    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


#: 连续两个以上换行视为段落边界；只认空行，不认单换行。
_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n+")


def split_paragraphs(text: str) -> list[str]:
    """按空行切段。

    刻意**保留段内单换行**：硬折行的文本（终端输出、老式文档）在语义上仍是
    一整段，但把换行替换成空格会丢掉「这一行原本就换行了」的信息。
    保留原样是不丢信息的做法，切分器需要时再自行处理。
    """

    paragraphs: list[str] = []
    for chunk in _PARAGRAPH_SPLIT_RE.split(text):
        cleaned = chunk.strip("\n").rstrip()
        if cleaned.strip():
            paragraphs.append(cleaned)
    return paragraphs


def first_nonempty_line(text: str, limit: int = 120) -> str:
    """取第一行有内容的文本，用作标题兜底。超长时截断并加省略号。"""

    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped[:limit] + ("…" if len(stripped) > limit else "")
    return ""
