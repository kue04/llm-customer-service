"""内容嗅探（magic bytes）—— 计划 2.3 的准入检查之一。

与 B3「扩展名优先于 MIME」是两件不同的事
----------------------------------------
* B3 解决的是**解析路径**不被伪造的 ``Content-Type`` 带偏
  （否则「报表.pdf + Content-Type: text/html」会被 HTML 解析器处理）；
* 这里解决的是「声明的**扩展名**与文件**真实内容**是否一致」，
  对应计划 2.3 要求的「MIME 与文件内容不一致」用例。

判定能力（刻意保守）
--------------------
* ``%PDF-``            -> ``"pdf"``（强特征，文件头魔数）
* ``PK\\x03\\x04`` 且 zip 内确有 ``word/`` 条目 -> ``"docx"``（强特征；
  只看 zip 魔数不够 —— pptx / xlsx / 普通 zip 同样以 ``PK\\x03\\x04`` 开头）
* ``<!DOCTYPE html`` / ``<html`` 前缀 -> ``"html"``（启发式，**弱特征**）
* md / txt **没有任何魔数**，一律返回 ``None``：
  不做近似判断，否则一份正常的 UTF-8 / GBK 中文文本会被误判成别的东西。

放行策略：宁可放过，也不错杀
----------------------------
``sniff_content_type`` 的语义就是「能确定就返回类型，不确定返回 ``None``」，
**不负责决定是否放行**。调用方（上传接口）据此采用如下策略：

* 扩展名指向**强特征格式**（pdf / docx）时，内容必须与之一致，
  否则拒绝 —— 这两类文件冒充成本最低、且解析失败发生在异步 worker 里，
  在上传期拦住能给用户一个明确的 415，而不是一个事后失败的 job；
* 扩展名指向**无魔数格式**（md / txt / html）时**一律放行**，
  因为这些格式的内容边界本来就是模糊的（Markdown 常以内嵌 HTML 开头），
  按内容拒收会造成「正常文档传不上去」这种更难接受的错误。

因此 ``STRONG_FORMATS`` 是策略开关，``"html"`` 返回值只用于诊断与日志。
"""

from __future__ import annotations

import io
import zipfile

#: 取文件头多少字节做前缀匹配（zip / pdf 魔数都在最前面）
SNIFF_PREFIX_BYTES = 4096

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"
UTF8_BOM = b"\xef\xbb\xbf"

HTML_PREFIXES: tuple[bytes, ...] = (b"<!doctype html", b"<html")

#: 需要内容与扩展名严格一致（有强特征）的格式
STRONG_FORMATS: tuple[str, ...] = ("pdf", "docx")

#: OOXML 里 word 处理的条目前缀，用来把 docx 从其他 zip 容器里区分出来
_DOCX_MEMBER_PREFIX = "word/"


def sniff_content_type(data: bytes) -> str | None:
    """按文件头判断真实格式；无法确定时返回 ``None``。

    返回 ``None`` 不是错误，调用方按放行策略处理（见模块 docstring）。
    """

    if not data:
        return None

    head = bytes(data[:SNIFF_PREFIX_BYTES])

    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        return "docx" if _is_docx_zip(data) else None

    normalized = head[len(UTF8_BOM):] if head.startswith(UTF8_BOM) else head
    normalized = normalized.lstrip(b" \t\r\n").lower()
    if normalized.startswith(HTML_PREFIXES):
        return "html"

    return None


def matches_declared_type(source_type: str, data: bytes) -> bool:
    """内容是否与声明的来源类型一致（只对强特征格式做要求）。

    * ``source_type`` 不是强特征格式 -> 恒为 ``True``（放行）；
    * 是强特征格式 -> 要求嗅探结果与之一致（嗅探不出也算不一致）。
    """

    if source_type not in STRONG_FORMATS:
        return True
    return sniff_content_type(data) == source_type


def _is_docx_zip(data: bytes) -> bool:
    """zip 容器里是否存在 ``word/`` 条目（docx 的判别特征）。"""

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        # 以 PK\\x03\\x04 开头但不是合法 zip（例如被截断）：无法确定类型
        return False
    return any(name.startswith(_DOCX_MEMBER_PREFIX) for name in names)


__all__ = [
    "HTML_PREFIXES",
    "PDF_MAGIC",
    "SNIFF_PREFIX_BYTES",
    "STRONG_FORMATS",
    "ZIP_MAGIC",
    "matches_declared_type",
    "sniff_content_type",
]
