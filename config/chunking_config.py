"""Chunk 切分配置（计划 3.1）。

本模块是**纯 stdlib** 的：它只负责「配置的取值、覆盖、校验、落库载荷」，
不 import 任何 `services/` 下的模块。这样做的理由是分层方向不能反 ——
`services.ingestion.chunkers` 依赖本模块，本模块不能反过来依赖 chunkers
（tokenizer 注册表在 `services/ingestion/chunkers/tokenizer.py`，见下方说明）。

设计要点
--------
1. **默认值即计划 3.1 的初始值**：``target_tokens=450`` / ``min_tokens=120`` /
   ``max_tokens=700`` / ``overlap_tokens=80`` / ``preserve_heading_path=True`` /
   ``parent_chunk_enabled=True``。
2. **越界即失败，不静默夹取**：非法取值抛 :class:`ChunkConfigError`，
   错误对象里带**逐字段**的 ``field`` / ``message``，
   便于启动时直接打印「哪个字段错了、错在哪」。
   静默 ``min(max(...))`` 的坏处是不报错但行为与配置不符 —— 配置成了摆设。
3. **未知字段也报错**：``from_mapping`` 遇到没见过的键直接失败。
   否则 ``{"max_token": 700}``（少个 s）会被静默忽略，用默认值跑完全程，
   而写配置的人以为生效了。这类拼写错误只能靠「不认识就报错」兜住。
4. **启动失败**：模块导入时就构造 :data:`CHUNK_CONFIG` 并校验，
   因此任何进程只要 import 本模块就会带着非法配置一起启动失败，
   而不是等到第一次切分文档时才炸（fail fast）。
5. **每个文档版本保存「实际生效配置 + tokenizer 标识」**：
   :meth:`ChunkConfig.storage_payload` 返回可直接放进
   ``document_versions.metadata_json["chunking"]`` 的字典。

关于 ``tokenizer_id`` 的校验边界
--------------------------------
本模块只校验它是**非空字符串**，不校验「该 id 是否已注册」——
注册表在 chunkers 包里，反向导入会造成分层倒置。
「默认 tokenizer_id 一定可用」由测试
``tests/test_chunking.py::test_default_tokenizer_id_is_registered`` 保证，
「给了未注册的 id」由切分器抛出结构化错误，
两者合起来把「配置写错」这件事仍然钉死在可观测的失败上。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import os
from typing import Any


#: 配置结构自身的版本。字段增删或语义变化时提升，随生效配置一起落库，
#: 供以后解释「历史 chunk 是用什么规则切出来的」。
CONFIG_VERSION = "1.0"

#: 默认 tokenizer 标识。实现见 ``services/ingestion/chunkers/tokenizer.py``。
#: 这里刻意不 import 它（分层方向），改用测试断言「该 id 一定已注册」。
DEFAULT_TOKENIZER_ID = "heuristic-zh-v1"

#: 包级常量：便于把「切分相关配置」与 ``rag_config.py`` 的检索配置区分开。
ENV_PREFIX = "RAG_CHUNK_"


# ---------------------------------------------------------------- 错误


@dataclass(frozen=True, slots=True)
class ConfigFieldError:
    """一条**字段级**配置错误（计划 3.1 要求「给出字段错误」）。"""

    field: str
    message: str
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "message": self.message, "value": self.value}

    def __str__(self) -> str:
        hint = "" if self.value is None else f"（收到 {self.value!r}）"
        return f"{self.field}: {self.message}{hint}"


class ChunkConfigError(ValueError):
    """切分配置非法。**启动期**抛出，不做静默兜底。"""

    error_code = "invalid_chunking_config"

    def __init__(self, errors: list[ConfigFieldError] | tuple[ConfigFieldError, ...]) -> None:
        self.errors: tuple[ConfigFieldError, ...] = tuple(errors)
        super().__init__("Chunk 配置非法 —— " + "；".join(str(item) for item in self.errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "errors": [item.to_dict() for item in self.errors],
        }


# ---------------------------------------------------------------- 配置


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    """切分参数。``frozen`` 是刻意的：一次切分内的参数不允许中途变化。

    字段边界值不是随手定的，都对应切分算法里的一处硬约束：

    ======================  =========================================================
    ``target_tokens``       软目标：超过就该收口，但为了不切碎语义单元允许略超
    ``min_tokens``          软下限：最后一块小于它就把尾巴并回上一块（避免孤儿碎片）
    ``max_tokens``          **硬上限**：任何 child chunk 都不得超过（唯一例外见下）
    ``overlap_tokens``      相邻 child chunk 的目标重叠量（按单元边界取整）
    ``preserve_heading_path`` 是否把标题路径作为文本前缀（结构字段始终保留）
    ``parent_chunk_enabled`` 是否产出 parent chunk
    ``parent_max_tokens``   parent chunk 的上限（计划外新增，理由见 3.1 记录）
    ``tokenizer_id``        tokenizer 标识，随生效配置落库
    ======================  =========================================================

    唯一的硬上限例外：**代码块不从内部截断**（计划 3.2 第 3 条）——
    一个超过 ``max_tokens`` 的代码块会整块输出并标记 ``oversize=True``，
    计入 ``ChunkingStats.oversize_chunks``。两条要求冲突时选择「不损坏内容 + 可观测」，
    而不是「悄悄把代码切成两半」。
    """

    target_tokens: int = 450
    min_tokens: int = 120
    max_tokens: int = 700
    overlap_tokens: int = 80
    preserve_heading_path: bool = True
    parent_chunk_enabled: bool = True
    parent_max_tokens: int = 2800
    tokenizer_id: str = DEFAULT_TOKENIZER_ID

    # ------------------------------------------------------------ 校验

    def __post_init__(self) -> None:
        errors = self.validation_errors()
        if errors:
            raise ChunkConfigError(errors)

    def validation_errors(self) -> list[ConfigFieldError]:
        """返回字段错误清单；空列表 = 合法。"""

        problems: list[ConfigFieldError] = []

        for name in ("target_tokens", "min_tokens", "max_tokens", "parent_max_tokens"):
            value = getattr(self, name)
            if not _is_plain_int(value):
                problems.append(ConfigFieldError(name, "必须是整数（不接受布尔值 / 字符串 / 浮点）", value))
            elif value < 1:
                problems.append(ConfigFieldError(name, "必须大于 0", value))

        if not _is_plain_int(self.overlap_tokens):
            problems.append(ConfigFieldError("overlap_tokens", "必须是整数（不接受布尔值 / 字符串 / 浮点）", self.overlap_tokens))
        elif self.overlap_tokens < 0:
            problems.append(ConfigFieldError("overlap_tokens", "不得为负数", self.overlap_tokens))

        for name in ("preserve_heading_path", "parent_chunk_enabled"):
            if not isinstance(getattr(self, name), bool):
                problems.append(
                    ConfigFieldError(name, "必须是布尔值（JSON 里请用 true / false，不要用字符串）", getattr(self, name))
                )

        if not isinstance(self.tokenizer_id, str):
            problems.append(ConfigFieldError("tokenizer_id", "必须是字符串", self.tokenizer_id))
        elif not self.tokenizer_id.strip():
            problems.append(ConfigFieldError("tokenizer_id", "不得为空", self.tokenizer_id))

        # ---- 跨字段约束：只有在单字段都合法时才判断，避免一次报一堆噪声

        if not problems:
            if self.min_tokens > self.target_tokens:
                problems.append(
                    ConfigFieldError("min_tokens", f"不得大于 target_tokens（{self.target_tokens}）；否则「合并小尾巴」永远无法触发", self.min_tokens)
                )
            if self.target_tokens > self.max_tokens:
                problems.append(
                    ConfigFieldError("max_tokens", f"不得小于 target_tokens（{self.target_tokens}）；否则软目标就已经越界", self.max_tokens)
                )
            if self.overlap_tokens * 2 > self.max_tokens:
                problems.append(
                    ConfigFieldError(
                        "overlap_tokens",
                        f"不得超过 max_tokens 的一半（{self.max_tokens // 2}）；否则重叠会把新内容挤出 chunk",
                        self.overlap_tokens,
                    )
                )
            if self.parent_max_tokens < self.max_tokens:
                problems.append(
                    ConfigFieldError(
                        "parent_max_tokens",
                        f"不得小于 max_tokens（{self.max_tokens}）；parent 至少要装得下一个 child",
                        self.parent_max_tokens,
                    )
                )

        return problems

    # ------------------------------------------------------------ 覆盖与序列化

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}

    def storage_payload(self) -> dict[str, Any]:
        """「实际生效配置 + tokenizer 标识」，直接放进 ``document_versions.metadata_json["chunking"]``。

        带上 ``config_version`` 的原因：以后字段语义变了，
        历史版本记录仍能解释「当时是按什么规则切的」。
        """

        payload = {"config_version": CONFIG_VERSION}
        payload.update(self.to_dict())
        return payload

    def replace_overrides(self, overrides: Mapping[str, Any]) -> ChunkConfig:
        """在**当前配置**基础上应用覆盖（知识库级 ``chunking_config_json``）。"""

        return ChunkConfig.from_mapping(overrides, base=self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, base: ChunkConfig | None = None) -> ChunkConfig:
        """按映射构造配置：未知键与非法值都报**字段级**错误。

        ``base`` 省略时以默认值为底，因此 ``{"max_tokens": 900}``
        表达的是「只改这一项」，而不是「其余项为空」。
        """

        if not isinstance(data, Mapping):
            raise ChunkConfigError([ConfigFieldError("<root>", "必须是映射（dict / JSON object）", data)])

        known = {item.name for item in fields(cls)}
        errors: list[ConfigFieldError] = []
        merged = (base or cls()).to_dict()
        for key, value in data.items():
            if key not in known:
                errors.append(
                    ConfigFieldError(str(key), "未知配置项；合法字段：" + ", ".join(sorted(known)), value)
                )
                continue
            merged[key] = value
        if errors:
            raise ChunkConfigError(errors)

        # ``cls(**merged)`` 会在构造期跑完整校验：类型错与越界都会以字段错误抛出
        return cls(**merged)

    @classmethod
    def from_env(cls, base: ChunkConfig | None = None) -> ChunkConfig:
        """从 ``RAG_CHUNK_*`` 环境变量读取覆盖。

        环境变量的默认来源是部署环境（人写的东西），所以这里**不宽容**：
        写错类型同样报字段错误，而不是回退默认值 ——
        否则「以为改小了 max_tokens，其实没生效」会一直到线上才发现。
        """

        seed = (base or cls()).to_dict()
        overrides: dict[str, Any] = {}
        errors: list[ConfigFieldError] = []

        for name, env_name, kind in _ENV_SPECS:
            raw = os.getenv(env_name)
            if raw is None or not raw.strip():
                continue
            try:
                overrides[name] = _coerce(raw, kind)
            except ValueError:
                errors.append(
                    ConfigFieldError(
                        name,
                        f"环境变量 {env_name} 的值无法解析为{'布尔值' if kind is bool else '整数'}",
                        raw,
                    )
                )
        if errors:
            raise ChunkConfigError(errors)

        merged = dict(seed)
        merged.update(overrides)
        return cls(**merged)

    # ------------------------------------------------------------ 便捷

    def summary(self) -> str:
        """一行可读摘要，用于启动日志。"""

        return (
            f"target={self.target_tokens} min={self.min_tokens} max={self.max_tokens} "
            f"overlap={self.overlap_tokens} parent={'on' if self.parent_chunk_enabled else 'off'} "
            f"(parent_max={self.parent_max_tokens}) heading_prefix={'on' if self.preserve_heading_path else 'off'} "
            f"tokenizer={self.tokenizer_id}"
        )


# ---------------------------------------------------------------- 解析工具

#: ``(字段名, 环境变量名, 解析类型)``
_ENV_SPECS: tuple[tuple[str, str, type], ...] = (
    ("target_tokens", f"{ENV_PREFIX}TARGET_TOKENS", int),
    ("min_tokens", f"{ENV_PREFIX}MIN_TOKENS", int),
    ("max_tokens", f"{ENV_PREFIX}MAX_TOKENS", int),
    ("overlap_tokens", f"{ENV_PREFIX}OVERLAP_TOKENS", int),
    ("parent_max_tokens", f"{ENV_PREFIX}PARENT_MAX_TOKENS", int),
    ("preserve_heading_path", f"{ENV_PREFIX}PRESERVE_HEADING_PATH", bool),
    ("parent_chunk_enabled", f"{ENV_PREFIX}PARENT_CHUNK_ENABLED", bool),
    ("tokenizer_id", f"{ENV_PREFIX}TOKENIZER_ID", str),
)

_BOOL_TRUE = {"1", "true", "yes", "on"}
_BOOL_FALSE = {"0", "false", "no", "off"}


def _coerce(raw: str, kind: type) -> Any:
    value = raw.strip()
    if kind is bool:
        lowered = value.lower()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
        raise ValueError(value)
    if kind is int:
        return int(value)
    return value


def _is_plain_int(value: Any) -> bool:
    """``bool`` 是 ``int`` 的子类，直接 ``isinstance(v, int)`` 会让 ``True`` 通过。"""

    return isinstance(value, int) and not isinstance(value, bool)


def with_overrides(config: ChunkConfig, overrides: Mapping[str, Any] | None) -> ChunkConfig:
    """语义化入口：无覆盖时原样返回（避免无谓地重建对象）。"""

    if not overrides:
        return config
    return config.replace_overrides(overrides)


# ---------------------------------------------------------------- 生效配置

def build_config(overrides: Mapping[str, Any] | None = None) -> ChunkConfig:
    """``环境变量 → 知识库覆盖 → 校验``，任一步非法都抛 :class:`ChunkConfigError`。"""

    config = ChunkConfig.from_env()
    if overrides:
        config = config.replace_overrides(overrides)
    return config


#: 进程启动时生效的配置。**import 即校验**：非法配置会让进程启动失败（fail fast），
#: 而不是等第一次切分文档时才炸。
CHUNK_CONFIG: ChunkConfig = ChunkConfig.from_env()


def get_chunking_config() -> ChunkConfig:
    return CHUNK_CONFIG


def get_chunking_config_dict() -> dict[str, Any]:
    return CHUNK_CONFIG.storage_payload()


__all__ = [
    "CHUNK_CONFIG",
    "CONFIG_VERSION",
    "DEFAULT_TOKENIZER_ID",
    "ENV_PREFIX",
    "ChunkConfig",
    "ChunkConfigError",
    "ConfigFieldError",
    "build_config",
    "get_chunking_config",
    "get_chunking_config_dict",
    "with_overrides",
]
