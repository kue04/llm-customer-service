"""扫描件 OCR 接口（阶段 2.2）。

**默认不安装 OCR 引擎**：``rapidocr-onnxruntime`` 会拖进 onnxruntime 和一组模型权重，
体积远超「一份能跑通 CI 的解析器」应有的成本。因此本模块只定义接口与判定逻辑，
具体引擎按需安装（``pip install rapidocr-onnxruntime``）后即可生效，不需要改代码。

调用时机（计划 2.2 的「OCR 仅在可提取文本为空或低于配置阈值时调用」）
--------------------------------------------------------------------
判定逻辑统一收在 :func:`should_use_ocr`：可提取文本字符数低于阈值才判定需要 OCR。
PDF 解析器在「文本层为空或过少」时才调用引擎；文本层充足的电子版 PDF
**不会**触发 OCR —— 对已经能直接取到文字的页面做 OCR，既慢又会引入识别错误。

引擎不可用时的行为：**不是报错，而是不 OCR + 写警告**。
理由是这个项目里 OCR 是增强手段而非必需路径：用户上传了一份扫描件、
服务端没装 OCR 引擎，正确结果是「入库成功但带一条 ``no_text_layer`` 警告」，
让任务查询接口告诉用户「这份文件没有文本层，检索不到内容」；
直接让整个上传任务失败反而更糟 —— 文件已经存进对象存储了，
失败只会让用户反复重传同一个必然失败的文件。
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from services.ingestion.parsers.base import ERROR_OCR_UNAVAILABLE, ParserError


#: 覆盖 OCR 触发阈值的环境变量（单位：字符）
OCR_MIN_TEXT_CHARS_ENV = "RAG_OCR_MIN_TEXT_CHARS"

#: 默认阈值。取 32 是因为：标题 + 一两个词（如扫描件只识别出页眉）不足以支撑检索，
#: 这种「有文本但等于没有」的情况应当继续走 OCR。
DEFAULT_OCR_MIN_TEXT_CHARS = 32


@runtime_checkable
class OcrEngine(Protocol):
    """OCR 引擎协议。

    只要求「给一张图的字节，还一段文字」，不规定引擎怎么实现
    （本地方案用 rapidocr，将来接云端 OCR 也实现这一层即可）。
    """

    engine_name: str

    def is_available(self) -> bool:
        """引擎是否真的可用（已安装、模型已就绪）。"""

    def recognize_image(self, image_bytes: bytes) -> str:
        """识别单张图片，返回纯文本。失败抛 :class:`ParserError`。"""


def ocr_min_text_chars() -> int:
    """读取 OCR 触发阈值。

    值非法时**直接报错**而不是静默用默认值：一个写错的 ``RAG_OCR_MIN_TEXT_CHARS``
    会让「该走 OCR 的扫描件没走」，而这种问题在结果层面极难发现
    （表现为「检索不到某份文件」，而不是任何报错）。
    """

    raw = os.environ.get(OCR_MIN_TEXT_CHARS_ENV)
    if raw is None or raw.strip() == "":
        return DEFAULT_OCR_MIN_TEXT_CHARS
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{OCR_MIN_TEXT_CHARS_ENV} 必须是整数，当前值：{raw!r}") from exc
    if value < 0:
        raise ValueError(f"{OCR_MIN_TEXT_CHARS_ENV} 不能为负数，当前值：{value}")
    return value


def should_use_ocr(text: str, min_chars: int | None = None) -> bool:
    """可提取文本是否少到需要 OCR 兜底。

    注意是「低于」而不是「为空」：只识别出页眉页脚的扫描件文本量很少但非零，
    如果只判空就永远不会触发 OCR，这类文件会静默地变成检索不到的空壳文档。
    """

    threshold = ocr_min_text_chars() if min_chars is None else min_chars
    return len(text.strip()) < threshold


class RapidOcrEngine:
    """基于 ``rapidocr-onnxruntime`` 的本地 OCR 引擎（延迟导入）。"""

    engine_name = "rapidocr-onnxruntime"

    def __init__(self) -> None:
        self._instance: object | None = None

    def is_available(self) -> bool:
        try:
            import rapidocr_onnxruntime  # noqa: F401
        except ImportError:
            return False
        return True

    def _load(self) -> object:
        if self._instance is not None:
            return self._instance
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ParserError(
                ERROR_OCR_UNAVAILABLE,
                f"未安装 {self.engine_name}，无法执行 OCR",
                parser_name="ocr",
                detail={"missing_package": "rapidocr-onnxruntime"},
            ) from exc
        self._instance = RapidOCR()
        return self._instance

    def recognize_image(self, image_bytes: bytes) -> str:
        if not image_bytes:
            raise ParserError(ERROR_OCR_UNAVAILABLE, "传给 OCR 的图片是空的", parser_name="ocr")

        import numpy as np

        engine = self._load()
        # RapidOCR 接受路径 / ndarray / bytes；这里统一成 ndarray，
        # 避免为了识别把图片落盘（扫描件可能包含敏感内容，不落地更安全）
        array = np.frombuffer(image_bytes, dtype=np.uint8)
        try:
            result, _ = engine(array)  # type: ignore[operator]
        except Exception as exc:
            raise ParserError(
                ERROR_OCR_UNAVAILABLE,
                f"OCR 识别失败：{type(exc).__name__}: {exc}",
                parser_name="ocr",
                detail={"exception": type(exc).__name__},
            ) from exc

        if not result:
            return ""
        # RapidOCR 返回 [(box, text, score), ...]
        return "\n".join(str(item[1]) for item in result if len(item) > 1).strip()


def load_default_engine() -> OcrEngine | None:
    """返回默认 OCR 引擎；未安装时返回 ``None``（调用方据此只写警告，不中断解析）。"""

    engine = RapidOcrEngine()
    return engine if engine.is_available() else None


__all__ = [
    "DEFAULT_OCR_MIN_TEXT_CHARS",
    "OCR_MIN_TEXT_CHARS_ENV",
    "OcrEngine",
    "RapidOcrEngine",
    "load_default_engine",
    "ocr_min_text_chars",
    "should_use_ocr",
]
