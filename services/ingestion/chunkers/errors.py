"""切分器的结构化错误（与 ``parsers/base.py`` 的 ``ParserError`` 同源思路）。

为什么仍然用**异常**而不是返回错误对象：切分失败的后果是把「半截内容」写进
``document_chunks`` 并进而进索引 —— 检索时表现为「资料明明在库里却检索不到正文」，
极难排查。异常默认中断，返回值可以被忽略，这里必须选不容易被忽略的那个。

错误码是**封闭集合**：调用方（流水线 3.3）要把 ``error_code`` 直接写进
``ingestion_jobs.error_code``，所以它必须是稳定标识，不接受随手新增字符串。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


#: tokenizer 标识未注册（配置写错 / 注册表被换过）
ERROR_UNKNOWN_TOKENIZER = "unknown_tokenizer"
#: 传入的上下文非法（缺 tenant / 版本号非正等）
ERROR_INVALID_CONTEXT = "invalid_context"
#: 切分内部不变量被破坏（属于编程错误，出现即代表实现有 bug）
ERROR_INTERNAL = "chunking_internal_error"

ERROR_CODES: tuple[str, ...] = (
    ERROR_UNKNOWN_TOKENIZER,
    ERROR_INVALID_CONTEXT,
    ERROR_INTERNAL,
)


class ChunkingError(Exception):
    """带稳定 ``error_code`` 的切分错误。"""

    def __init__(self, error_code: str, message: str, *, detail: Mapping[str, Any] | None = None) -> None:
        if error_code not in ERROR_CODES:
            raise ValueError(f"未知的切分错误码：{error_code!r}")
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.detail: dict[str, Any] = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {"error_code": self.error_code, "message": self.message, "detail": dict(self.detail)}

    def __str__(self) -> str:  # pragma: no cover - 只为日志可读
        return f"[{self.error_code}] {self.message}"


__all__ = [
    "ERROR_CODES",
    "ERROR_INTERNAL",
    "ERROR_INVALID_CONTEXT",
    "ERROR_UNKNOWN_TOKENIZER",
    "ChunkingError",
]
