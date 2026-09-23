"""Token 计数抽象（计划 3.1「tokenizer 标识」+ 3.2「固定 tokenizer mock，测试不下载模型」）。

为什么自己定义计数接口，而不是直接调 transformers
--------------------------------------------------
1. **切分器必须是纯函数**：不能因为「本机没装模型 / 下不到权重」而无法切分；
2. **测试必须确定性**：真实 BPE 的 token 数会随版本变化，用它写「不得超过 700 tokens」
   这种边界断言，会在某次升级后无缘无故变红（同「测试资产可复现」的教训，见踩坑 D5）；
3. **tokenizer 标识要落库**：``document_versions.metadata_json`` 里要记下用的是哪个计数口径，
   否则以后无法解释「同一份文档为什么切法不同」。

因此这里给出三个**确定性实现**：
- :class:`HeuristicTokenCounter`（``heuristic-zh-v1``，默认）：中日韩字符按 1 token 计，
  拉丁字母/数字连写按 1 token 计，标点各计 1 —— 对中文语料是接近 BPE 的保守估计；
- :class:`WordTokenCounter`（``mock-word-v1``，固定 mock）：按空白切分计数，
  测试里构造 ``"w1 w2 w3"`` 就是 3 tokens，边界断言可以手算；
- :class:`CharTokenCounter`（``mock-char-v1``）：1 字符 = 1 token，用于验证「按 token 硬切」的分支。

三者都不联网、不读磁盘、不 import 任何第三方包。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .errors import ERROR_INTERNAL, ERROR_UNKNOWN_TOKENIZER, ChunkingError


#: 默认实现（与 ``config/chunking_config.py:DEFAULT_TOKENIZER_ID`` 保持一致，
#: 由 ``tests/test_chunking.py::test_default_tokenizer_id_is_registered`` 断言，
#: 这样「config 不 import chunkers」的分层约束与「两边不许漂移」同时成立）。
DEFAULT_TOKENIZER_ID = "heuristic-zh-v1"

#: CJK / 假名 / 谚文的码点区间（按 1 字符 ≈ 1 token 计）
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x1100, 0x11FF),  # 谚文字母
    (0x3040, 0x30FF),  # 平假名 / 片假名
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 基本区
    (0xAC00, 0xD7AF),  # 谚文音节
    (0xF900, 0xFAFF),  # CJK 兼容
    (0xFF00, 0xFF60),  # 全角标点
)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    for low, high in _CJK_RANGES:
        if low <= code <= high:
            return True
    return False


@runtime_checkable
class TokenCounter(Protocol):
    """计数接口。实现必须**无状态、确定性、零外部依赖**。"""

    #: 会写进 ``document_versions.metadata_json["chunking"]["tokenizer_id"]``
    identifier: str

    def count(self, text: str) -> int:
        """返回文本的 token 数（非负整数）。"""
        ...


class HeuristicTokenCounter:
    """确定性启发式计数，默认实现。"""

    identifier = DEFAULT_TOKENIZER_ID

    def count(self, text: str) -> int:
        if not text:
            return 0
        tokens = 0
        in_latin_run = False
        for char in text:
            if char.isspace():
                in_latin_run = False
                continue
            if _is_cjk(char):
                tokens += 1
                in_latin_run = False
                continue
            if char.isalnum() or char == "_":
                # 连续的拉丁字母 / 数字算一个 token（``refund2024`` = 1）
                if not in_latin_run:
                    tokens += 1
                    in_latin_run = True
                continue
            # 其余（标点、符号）各计一个
            tokens += 1
            in_latin_run = False
        return tokens


class WordTokenCounter:
    """固定 mock：1 个空白分隔的词 = 1 token。

    计划 3.2 明确要求「提供固定 tokenizer mock，测试不下载模型」，
    这个类就是那个 mock。它同时是**手算边界值**的工具：
    ``counter.count("a b c") == 3``。
    """

    identifier = "mock-word-v1"

    def count(self, text: str) -> int:
        return len(text.split()) if text else 0


class CharTokenCounter:
    """固定 mock：1 个字符 = 1 token。用于验证「按 token 硬切」等字符级分支。"""

    identifier = "mock-char-v1"

    def count(self, text: str) -> int:
        return len(text)


#: 注册表。测试需要额外口径时用 :func:`register_token_counter`，
#: **不要**改这里的默认项（改默认会让「落库的 tokenizer_id」与历史数据对不上）。
TOKEN_COUNTERS: dict[str, TokenCounter] = {
    HeuristicTokenCounter.identifier: HeuristicTokenCounter(),
    WordTokenCounter.identifier: WordTokenCounter(),
    CharTokenCounter.identifier: CharTokenCounter(),
}


def register_token_counter(counter: TokenCounter, *, override: bool = False) -> None:
    """注册一个计数实现。重复注册需显式 ``override=True``（防覆盖内置实现）。"""

    identifier = getattr(counter, "identifier", "")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ChunkingError(ERROR_INTERNAL, "TokenCounter.identifier 必须是非空字符串")
    if identifier in TOKEN_COUNTERS and not override:
        raise ChunkingError(ERROR_INTERNAL, f"tokenizer 标识已注册：{identifier}")
    TOKEN_COUNTERS[identifier] = counter


def get_token_counter(identifier: str) -> TokenCounter:
    """按标识取计数实现；未注册时抛结构化错误（而不是 KeyError）。

    为什么不在配置层就拦住：tokenizer 注册表在 chunkers 包内，
    让 ``config/`` 反向 import 会造成分层倒置。见 ``config/chunking_config.py`` 的说明。
    """

    counter = TOKEN_COUNTERS.get(identifier)
    if counter is None:
        raise ChunkingError(
            ERROR_UNKNOWN_TOKENIZER,
            f"未注册的 tokenizer 标识：{identifier!r}",
            detail={"tokenizer_id": identifier, "registered": sorted(TOKEN_COUNTERS)},
        )
    return counter


def registered_tokenizer_ids() -> tuple[str, ...]:
    return tuple(sorted(TOKEN_COUNTERS))


__all__ = [
    "DEFAULT_TOKENIZER_ID",
    "TOKEN_COUNTERS",
    "CharTokenCounter",
    "HeuristicTokenCounter",
    "TokenCounter",
    "WordTokenCounter",
    "get_token_counter",
    "register_token_counter",
    "registered_tokenizer_ids",
]
