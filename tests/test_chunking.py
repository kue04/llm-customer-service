"""阶段 3.1 / 3.2 的切分器测试（批次 B5）。

覆盖范围
--------
1. **配置（3.1）**：默认为计划给定值、越界报字段错误（不静默夹取）、
   知识库覆盖、环境变量覆盖、import 期即失败、生效配置 + tokenizer 标识可落库；
2. **清洗（3.2 第 8 条）**：空白、页码行、重复页眉页脚、完全重复段落，
   每条规则连同**它的边界**（阈值差一页会怎样）一起断言；
3. **切分（3.2 第 1~7 条）**：标题 / 页码分组、超长段落四级拆分、表格重复表头、
   代码块不内部截断、列表项完整、重叠、parent/child、标题前缀不重复正文；
4. **产物（3.2 第 9 条）**：12 个字段、租户 / ACL / 页码 / 标题路径全部落进
   ``metadata_json``（B7 检索过滤的唯一来源）；
5. **确定性**：同样的输入必须得到同样的 ``chunk_id``（幂等重跑的前提）；
6. **守卫断言**：切分器不依赖数据库、不接收裸 ``tenant_id``（用 AST，不扫字符串 ——
   见踩坑记录 D1：字符串匹配会误伤 docstring）；
7. **落库兼容**：把切分产物真的喂给 ``repository.insert_chunks``（真 SQLite + 打开外键），
   验证「父先于子」「``chunk_id`` 版本内唯一」「必填字段齐全」这三条 B6 要依赖的约束。

tokenizer 一律用固定 mock（``mock-word-v1``：1 个空白分隔的词 = 1 token），
**测试不下载任何模型**，因此所有边界值都可以手算。
"""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import sys

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.orm import sessionmaker
import pytest

from config import chunking_config
from config.chunking_config import (
    CHUNK_CONFIG,
    CONFIG_VERSION,
    DEFAULT_TOKENIZER_ID,
    ChunkConfig,
    ChunkConfigError,
    ConfigFieldError,
    build_config,
    with_overrides,
)
from services.ingestion import db, models, repository
from services.ingestion.chunkers import (
    CHUNK_TYPE_CHILD,
    CHUNK_TYPE_PARENT,
    ERROR_UNKNOWN_TOKENIZER,
    HEADING_SEPARATOR,
    TOKEN_COUNTERS,
    AclEntry,
    CharTokenCounter,
    Chunk,
    ChunkContext,
    ChunkingError,
    ChunkingResult,
    HeuristicTokenCounter,
    TokenCounter,
    WordTokenCounter,
    chunk_document,
    clean_blocks,
    get_token_counter,
    normalize_text,
    register_token_counter,
)
from services.ingestion.chunkers import cleaner as cleaner_module
from services.ingestion.chunkers import models as chunk_models
from services.ingestion.parsers.base import (
    BLOCK_CODE,
    BLOCK_HEADING,
    BLOCK_IMAGE,
    BLOCK_LIST,
    BLOCK_PAGE_BREAK,
    BLOCK_PARAGRAPH,
    BLOCK_QUOTE,
    BLOCK_TABLE,
    Block,
    ParsedDocument,
    build_table_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHUNKER_DIR = PROJECT_ROOT / "services" / "ingestion" / "chunkers"

#: 固定 mock：1 个空白分隔的词 = 1 token，让所有边界值都能手算
MOCK_TOKENIZER = "mock-word-v1"


# ---------------------------------------------------------------- 测试基建


def words(count: int, *, start: int = 1, prefix: str = "w") -> str:
    """生成 ``count`` 个词（token 数 = count）。"""

    return " ".join(f"{prefix}{index}" for index in range(start, start + count))


def sentences(count: int, *, tokens_each: int = 3, start: int = 1) -> str:
    """生成 ``count`` 句，每句 ``tokens_each`` 个词并以句号结尾。"""

    out = []
    cursor = start
    for _ in range(count):
        chunk = words(tokens_each, start=cursor)
        cursor += tokens_each
        out.append(f"{chunk}。")
    return " ".join(out)


def block(
    block_type: str,
    text: str = "",
    *,
    order: int = 0,
    page: int | None = None,
    heading_path: tuple[str, ...] = (),
    table_json: dict | None = None,
    image_ref: str | None = None,
) -> Block:
    return Block(
        type=block_type,
        text=text,
        order=order,
        page=page,
        heading_path=heading_path,
        table_json=table_json,
        image_ref=image_ref,
    )


def para(text: str, **kwargs) -> Block:
    return block(BLOCK_PARAGRAPH, text, **kwargs)


def heading(text: str, *, heading_path: tuple[str, ...] | None = None, **kwargs) -> Block:
    return block(BLOCK_HEADING, text, heading_path=heading_path or (text,), **kwargs)


def code(text: str, **kwargs) -> Block:
    return block(BLOCK_CODE, text, **kwargs)


def listing(text: str, **kwargs) -> Block:
    return block(BLOCK_LIST, text, **kwargs)


def table(columns: list[str], rows: list[list[str]], **kwargs) -> Block:
    table_json = build_table_json(columns, rows)
    from services.ingestion.parsers.base import render_table_text

    return block(BLOCK_TABLE, render_table_text(table_json), table_json=table_json, **kwargs)


def make_doc(blocks: list[Block], *, title: str = "测试文档", filename: str = "sample.md", source_type: str = "md") -> ParsedDocument:
    renumbered = [replace(item, order=index) for index, item in enumerate(blocks)]
    return ParsedDocument(
        title=title,
        blocks=tuple(renumbered),
        metadata={
            "content_hash": "d" * 64,
            "byte_size": 1024,
            "filename": filename,
            "source_type": source_type,
            "block_count": len(renumbered),
        },
        source_type=source_type,
        parser_name="test_parser",
        parser_version="0.1",
    )


def make_context(**overrides) -> ChunkContext:
    payload = {
        "tenant_id": "tenant-a",
        "document_id": "doc-1",
        "document_version_id": "ver-1",
        "document_version": 1,
        "acl": (AclEntry("user", "user-1", "read"),),
        "source_uri": "upload://sample.md",
        "filename": "sample.md",
        "source_type": "md",
        "document_title": "测试文档",
    }
    payload.update(overrides)
    return ChunkContext(**payload)


def word_config(**overrides) -> ChunkConfig:
    payload = {"tokenizer_id": MOCK_TOKENIZER}
    payload.update(overrides)
    return ChunkConfig(**payload)


def chunk(blocks: list[Block], *, config: ChunkConfig | None = None, context: ChunkContext | None = None, **doc_kwargs) -> ChunkingResult:
    return chunk_document(
        make_doc(blocks, **doc_kwargs),
        context=context or make_context(),
        config=config or word_config(),
    )


def bodies(result: ChunkingResult, *, prefix: str = "") -> list[str]:
    """去掉标题前缀后的正文（前缀是第一行，且与正文用单个换行分隔）。"""

    out = []
    for item in result.chunks:
        text = item.text
        if prefix and text.startswith(prefix):
            text = text[len(prefix):].lstrip("\n")
        out.append(text)
    return out


def flat(text: str) -> str:
    """把任意空白压成单空格。

    chunk 文本在**单元边界**用的是换行（段落之间保留原结构），
    而「重叠是否逐字出现」关心的是词序列，
    所以比较前统一压空白 —— 否则断言会因为「换行 vs 空格」这种无关差异失败。
    """

    return " ".join(text.split())


def strip_prefix(text: str, prefix: str) -> str:
    return text[len(prefix):].lstrip("\n") if prefix and text.startswith(prefix) else text


def shared_junction(previous: str, following: str, *, prefix: str = "") -> str:
    """接缝处真正共享的文本 = 「上一块的最长后缀」∩「下一块的最长前缀」。

    用**字符级**而不是按空白切词：CJK 文本没有空格，按词算会让「80 tokens」
    在中文里只剩几个词（中文一「词」就是十几个 token），度量口径直接错位。
    字符级取到共享串之后，再交给 tokenizer 计 token 数。
    """

    left = strip_prefix(previous, prefix)
    right = strip_prefix(following, prefix)
    for size in range(min(len(left), len(right)), 0, -1):
        if left[-size:] == right[:size]:
            return left[-size:]
    return ""


def junction_overlap(previous: str, following: str, *, prefix: str = "", counter: TokenCounter | None = None) -> int:
    """两块在接缝处的共享 token 数。"""

    counter = counter or WordTokenCounter()
    return counter.count(shared_junction(previous, following, prefix=prefix))


# ================================================================ 3.1 配置


class TestChunkConfigDefaults:
    def test_defaults_match_plan_3_1(self):
        config = ChunkConfig()

        assert config.target_tokens == 450
        assert config.min_tokens == 120
        assert config.max_tokens == 700
        assert config.overlap_tokens == 80
        assert config.preserve_heading_path is True
        assert config.parent_chunk_enabled is True

    def test_parent_max_tokens_is_at_least_max_tokens(self):
        assert ChunkConfig().parent_max_tokens >= ChunkConfig().max_tokens

    def test_module_level_config_is_valid(self):
        assert CHUNK_CONFIG.validation_errors() == []

    def test_storage_payload_carries_tokenizer_and_version(self):
        payload = ChunkConfig().storage_payload()

        assert payload["config_version"] == CONFIG_VERSION
        assert payload["tokenizer_id"] == DEFAULT_TOKENIZER_ID
        # 「每个文档版本保存实际生效配置」要求全部字段都在
        for field in ChunkConfig().to_dict():
            assert field in payload

    def test_summary_is_human_readable(self):
        summary = ChunkConfig().summary()

        assert "target=450" in summary and "tokenizer=" in summary


class TestChunkConfigValidation:
    @pytest.mark.parametrize(
        "overrides,expected_field",
        [
            ({"target_tokens": 0}, "target_tokens"),
            ({"target_tokens": -1}, "target_tokens"),
            ({"min_tokens": 0}, "min_tokens"),
            ({"min_tokens": 500}, "min_tokens"),  # min > target
            ({"max_tokens": 100}, "max_tokens"),  # max < target
            ({"overlap_tokens": -1}, "overlap_tokens"),
            ({"overlap_tokens": 400}, "overlap_tokens"),  # > max/2
            ({"parent_max_tokens": 100}, "parent_max_tokens"),  # < max
            ({"tokenizer_id": "   "}, "tokenizer_id"),
        ],
    )
    def test_out_of_range_values_raise_field_errors(self, overrides, expected_field):
        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig(**overrides)

        assert expected_field in {item.field for item in excinfo.value.errors}

    @pytest.mark.parametrize("field", ["target_tokens", "min_tokens", "max_tokens", "overlap_tokens", "parent_max_tokens"])
    def test_non_integer_numbers_are_rejected(self, field):
        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig(**{field: "700"})

        assert {item.field for item in excinfo.value.errors} == {field}

    @pytest.mark.parametrize("field", ["target_tokens", "max_tokens"])
    def test_boolean_is_not_an_integer(self, field):
        """``bool`` 是 ``int`` 的子类，不特判就会让 ``True`` 通过。"""

        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig(**{field: True})

        assert {item.field for item in excinfo.value.errors} == {field}

    @pytest.mark.parametrize("field", ["preserve_heading_path", "parent_chunk_enabled"])
    def test_string_booleans_are_rejected(self, field):
        """JSON 里写了 ``"false"`` 字符串时不能当真是 False —— 它不是假值而是真值。"""

        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig(**{field: "false"})

        assert {item.field for item in excinfo.value.errors} == {field}

    def test_error_message_contains_field_and_value(self):
        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig(max_tokens=10)

        rendered = str(excinfo.value)
        assert "max_tokens" in rendered and "10" in rendered
        assert excinfo.value.to_dict()["error_code"] == "invalid_chunking_config"

    def test_error_collects_every_bad_field_at_once(self):
        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig(target_tokens=-5, overlap_tokens=-1, tokenizer_id="")

        fields = {item.field for item in excinfo.value.errors}
        assert {"target_tokens", "overlap_tokens", "tokenizer_id"} <= fields

    def test_config_field_error_serializes(self):
        item = ConfigFieldError("max_tokens", "太小", 3)

        assert item.to_dict() == {"field": "max_tokens", "message": "太小", "value": 3}
        assert "max_tokens" in str(item)

    def test_cross_field_rules_are_skipped_when_single_fields_are_broken(self):
        """单字段就错时不再叠加跨字段噪声，错误列表保持可读。"""

        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig(target_tokens="abc")

        assert [item.field for item in excinfo.value.errors] == ["target_tokens"]


class TestChunkConfigOverrides:
    def test_from_mapping_keeps_other_defaults(self):
        config = ChunkConfig.from_mapping({"max_tokens": 900})

        assert config.max_tokens == 900
        assert config.target_tokens == 450

    def test_from_mapping_rejects_unknown_field(self):
        """少写一个 s 的 ``max_token`` 必须报错，否则「改了配置却用默认值跑」。"""

        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig.from_mapping({"max_token": 900})

        assert "max_token" in {item.field for item in excinfo.value.errors}

    def test_from_mapping_rejects_non_mapping(self):
        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig.from_mapping([("max_tokens", 900)])  # type: ignore[arg-type]

        assert excinfo.value.errors[0].field == "<root>"

    def test_knowledge_base_override_layers_on_top_of_base(self):
        base = word_config(target_tokens=100, min_tokens=10, max_tokens=200, overlap_tokens=5)
        resolved = base.replace_overrides({"target_tokens": 120})

        assert resolved.target_tokens == 120
        assert resolved.max_tokens == 200
        assert resolved.tokenizer_id == MOCK_TOKENIZER

    def test_override_cannot_bypass_validation(self):
        base = word_config()

        with pytest.raises(ChunkConfigError):
            base.replace_overrides({"overlap_tokens": 5000})

    def test_with_overrides_passthrough_when_empty(self):
        base = word_config()

        assert with_overrides(base, None) is base
        assert with_overrides(base, {}) is base

    def test_build_config_combines_env_and_override(self, monkeypatch):
        monkeypatch.setenv("RAG_CHUNK_MAX_TOKENS", "800")
        monkeypatch.setenv("RAG_CHUNK_TOKENIZER_ID", MOCK_TOKENIZER)

        config = build_config({"target_tokens": 200})

        assert config.max_tokens == 800
        assert config.target_tokens == 200

    @pytest.mark.parametrize("value", ["abc", "12.5", ""])
    def test_from_env_reports_field_error_on_bad_number(self, monkeypatch, value):
        monkeypatch.setenv("RAG_CHUNK_TARGET_TOKENS", value)

        expected_field = "target_tokens"
        if value == "":
            # 空串按「未设置」处理（部署脚本里常见的 "" 占位），不应报错
            assert ChunkConfig.from_env().target_tokens == 450
            return

        with pytest.raises(ChunkConfigError) as excinfo:
            ChunkConfig.from_env()
        assert expected_field in {item.field for item in excinfo.value.errors}

    def test_from_env_parses_booleans(self, monkeypatch):
        monkeypatch.setenv("RAG_CHUNK_PARENT_CHUNK_ENABLED", "false")
        monkeypatch.setenv("RAG_CHUNK_PRESERVE_HEADING_PATH", "on")

        config = ChunkConfig.from_env()

        assert config.parent_chunk_enabled is False
        assert config.preserve_heading_path is True

    def test_import_time_failure_on_bad_env(self):
        """非法配置必须在**启动期**失败，而不是等到第一次切分文档。

        用子进程而不是 ``importlib.reload``：reload 会把模块里的类对象换成新的，
        于是「测试自己持有的 ``ChunkConfig``」与「模块里的 ``ChunkConfig``」不再是同一个类，
        后续测试会以一个和被测行为无关的原因失败（跨测试污染）。
        子进程还更贴近真实场景 —— 部署时就是「进程起不来」。
        """

        env = dict(os.environ)
        env["RAG_CHUNK_MAX_TOKENS"] = "not-a-number"
        env["PYTHONPATH"] = str(PROJECT_ROOT)

        completed = subprocess.run(
            [sys.executable, "-c", "import config.chunking_config"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert completed.returncode != 0
        assert "ChunkConfigError" in completed.stderr
        assert "max_tokens" in completed.stderr
        assert "not-a-number" in completed.stderr

    def test_import_succeeds_with_valid_env(self):
        env = dict(os.environ)
        env.pop("RAG_CHUNK_MAX_TOKENS", None)
        env["PYTHONPATH"] = str(PROJECT_ROOT)

        completed = subprocess.run(
            [sys.executable, "-c", "import config.chunking_config as c; print(c.CHUNK_CONFIG.max_tokens)"],
            cwd=str(PROJECT_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == "700"

    def test_default_tokenizer_id_is_registered(self):
        """跨模块漂移守卫：``config`` 不 import chunkers，靠这条测试保证两边一致。"""

        assert DEFAULT_TOKENIZER_ID in TOKEN_COUNTERS
        assert chunking_config.DEFAULT_TOKENIZER_ID == DEFAULT_TOKENIZER_ID


# ================================================================ tokenizer


class TestTokenizers:
    def test_word_counter_counts_whitespace_tokens(self):
        counter = WordTokenCounter()

        assert counter.count("a b c") == 3
        assert counter.count("") == 0
        assert counter.identifier == MOCK_TOKENIZER

    def test_char_counter_counts_characters(self):
        assert CharTokenCounter().count("中文a") == 3

    def test_heuristic_counter_counts_cjk_by_character(self):
        counter = HeuristicTokenCounter()

        assert counter.count("退款政策") == 4
        assert counter.count("") == 0

    def test_heuristic_counter_groups_latin_runs(self):
        counter = HeuristicTokenCounter()

        assert counter.count("refund2024") == 1
        assert counter.count("refund 2024") == 2
        assert counter.count("a-b") == 3  # 连字符算独立标点

    def test_heuristic_counter_ignores_whitespace(self):
        counter = HeuristicTokenCounter()

        assert counter.count("  a   b  ") == 2

    def test_every_registered_counter_satisfies_protocol(self):
        for identifier, counter in TOKEN_COUNTERS.items():
            assert counter.identifier == identifier
            assert counter.count("a b") >= 0

    def test_unknown_tokenizer_raises_structured_error(self):
        with pytest.raises(ChunkingError) as excinfo:
            get_token_counter("no-such-tokenizer")

        assert excinfo.value.error_code == ERROR_UNKNOWN_TOKENIZER
        assert "no-such-tokenizer" in excinfo.value.detail["registered"][-1] or excinfo.value.detail

    def test_chunk_document_reports_unknown_tokenizer(self):
        with pytest.raises(ChunkingError) as excinfo:
            chunk([para("内容")], config=word_config(tokenizer_id="does-not-exist"))

        assert excinfo.value.error_code == ERROR_UNKNOWN_TOKENIZER

    def test_register_token_counter_refuses_silent_override(self):
        class Extra:
            identifier = "extra-counter-v1"

            def count(self, text: str) -> int:
                return 0

        extra = Extra()
        try:
            register_token_counter(extra)
            assert get_token_counter("extra-counter-v1") is extra
            with pytest.raises(ChunkingError):
                register_token_counter(extra)
            register_token_counter(extra, override=True)
        finally:
            TOKEN_COUNTERS.pop("extra-counter-v1", None)

    def test_register_token_counter_rejects_empty_identifier(self):
        class Bad:
            identifier = "  "

            def count(self, text: str) -> int:
                return 0

        with pytest.raises(ChunkingError):
            register_token_counter(Bad())


# ================================================================ 清洗


class TestNormalizeText:
    def test_strips_trailing_spaces_and_invisibles(self):
        assert normalize_text("正文  \u200b\n第二行\t ") == "正文\n第二行"

    def test_collapses_blank_lines_and_trims_edges(self):
        assert normalize_text("\n\n第一段\n\n\n\n第二段\n\n") == "第一段\n\n第二段"

    def test_keeps_indent_for_code(self):
        assert normalize_text("def f():\n    return 1", keep_indent=True) == "def f():\n    return 1"

    def test_drops_indent_for_prose(self):
        assert normalize_text("    正文", keep_indent=False) == "正文"

    def test_returns_empty_for_blank(self):
        assert normalize_text("   \n \t \n") == ""
        assert normalize_text("") == ""
        assert normalize_text(None) == ""  # type: ignore[arg-type]


class TestPageNumberDetection:
    @pytest.mark.parametrize("text", ["12", "- 12 -", "12 / 30", "第 12 页", "第12页 共30页", "Page 3", "page 3 of 10"])
    def test_recognises_page_number_forms(self, text):
        assert cleaner_module.is_page_number_like(text) is True

    @pytest.mark.parametrize("text", ["2024 年销售额", "第 12 条", "订单 12 号", "12:30", ""])
    def test_rejects_non_page_number_text(self, text):
        assert cleaner_module.is_page_number_like(text) is False

    def test_rejects_over_long_text(self):
        assert cleaner_module.is_page_number_like("1" * 20) is False


class TestCleanBlocks:
    def test_drops_page_number_only_at_page_edges(self):
        blocks = [
            para("1", page=1),
            para("2024", page=1),  # 页中位置的纯数字：可能是正文，必须保留
            para("结尾一", page=1),
            para("2", page=2),
            para("正文二", page=2),
            para("结尾二", page=2),
        ]

        result = clean_blocks(blocks, token_counter=WordTokenCounter())
        texts = [item.text for item in result.blocks]

        assert "1" not in texts and "2" not in texts
        assert "2024" in texts
        assert result.counters.dropped_page_number == 2

    def test_keeps_page_number_like_text_in_unpaged_documents(self):
        """无页码概念的文档（md / html）里 ``12`` 就是正文，不能当页码删。"""

        blocks = [para("12", page=None), para("正文", page=None)]

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert [item.text for item in result.blocks] == ["12", "正文"]
        assert result.counters.dropped_page_number == 0

    def test_drops_running_header_repeated_on_three_pages(self):
        blocks = []
        for page in (1, 2, 3):
            blocks.append(para("公司内部资料", page=page))
            blocks.append(para(f"第{page}页正文 {words(30, start=page * 100)}", page=page))

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert result.counters.dropped_running_header == 3
        assert "公司内部资料" not in [item.text for item in result.blocks]

    def test_does_not_drop_text_repeated_on_only_two_pages(self):
        """阈值边界：只有两页重复不算页眉页脚（否则「上下两页各说什么」会被误杀）。"""

        blocks = [para("共同的话", page=1), para("正文一", page=1), para("共同的话", page=2), para("正文二", page=2)]

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert result.counters.dropped_running_header == 0
        # 但「完全重复段落」仍然会去掉第二次出现
        assert result.counters.dropped_duplicate == 1

    def test_long_repeated_text_is_not_a_running_header(self):
        long_text = words(60)
        blocks = []
        for page in (1, 2, 3):
            blocks.append(para(long_text, page=page))
            blocks.append(para(f"正文{page}", page=page))

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert result.counters.dropped_running_header == 0

    def test_headings_are_never_treated_as_running_headers(self):
        blocks = []
        for page in (1, 2, 3):
            blocks.append(heading("注意事项", page=page))
            blocks.append(para(f"正文{page}", page=page))

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert result.counters.dropped_running_header == 0
        assert sum(1 for item in result.blocks if item.type == BLOCK_HEADING) == 3

    def test_drops_duplicate_paragraphs_keeping_first(self):
        blocks = [para("重复的话"), para("别的内容"), para("重复的话")]

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert [item.text for item in result.blocks] == ["重复的话", "别的内容"]
        assert result.counters.dropped_duplicate == 1

    @pytest.mark.parametrize("block_type", [BLOCK_LIST, BLOCK_TABLE, BLOCK_CODE, BLOCK_QUOTE])
    def test_exact_duplicates_are_only_removed_for_paragraphs(self, block_type):
        """刻意收窄：结构块重复出现往往是有意义的（同一张表在两个章节各出现一次）。"""

        if block_type == BLOCK_TABLE:
            items = [table(["A"], [["1"]]), table(["A"], [["1"]])]
        else:
            items = [block(block_type, "同样的内容"), block(block_type, "同样的内容")]

        result = clean_blocks(items, token_counter=WordTokenCounter())

        assert len(result.blocks) == 2
        assert result.counters.dropped_duplicate == 0

    def test_drops_empty_and_non_text_blocks_with_counters(self):
        blocks = [
            para("   "),
            para("正文"),
            block(BLOCK_IMAGE, image_ref="img/1.png"),
            block(BLOCK_PAGE_BREAK, ""),
        ]

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert [item.text for item in result.blocks] == ["正文"]
        assert result.counters.dropped_empty == 1
        assert result.counters.skipped_non_text_blocks == 2

    def test_normalizes_block_text_in_place(self):
        result = clean_blocks([para("  正文  \n\n\n第二段   ")], token_counter=WordTokenCounter())

        assert result.blocks[0].text == "正文\n\n第二段"

    def test_page_count_counts_distinct_pages(self):
        blocks = [para("a", page=1), para("b", page=5), para("c", page=None)]

        result = clean_blocks(blocks, token_counter=WordTokenCounter())

        assert result.counters.page_count == 2

    def test_counters_to_mapping_is_plain_ints(self):
        result = clean_blocks([para("正文")], token_counter=WordTokenCounter())

        mapping = cleaner_module.counters_to_mapping(result.counters)
        assert all(isinstance(value, int) for value in mapping.values())
        assert mapping["input_blocks"] == 1


# ================================================================ 分组与结构


class TestGrouping:
    def test_different_heading_paths_produce_separate_parents(self):
        blocks = [
            heading("甲", heading_path=("甲",)),
            para("甲的内容", heading_path=("甲",)),
            heading("乙", heading_path=("乙",)),
            para("乙的内容", heading_path=("乙",)),
        ]

        result = chunk(blocks, config=word_config(target_tokens=3, min_tokens=1, max_tokens=10, overlap_tokens=0))

        assert result.stats.section_count == 2
        assert len(result.parents) == 2
        assert {item.heading_path for item in result.parents} == {("甲",), ("乙",)}

    def test_same_heading_on_different_pages_splits_sections(self):
        """要求 1 的前半句：先按标题层级**和页码**分组。"""

        blocks = [
            heading("政策", heading_path=("政策",), page=1),
            para("第一页内容", heading_path=("政策",), page=1),
            para("第二页内容", heading_path=("政策",), page=2),
        ]

        result = chunk(blocks, config=word_config(target_tokens=2, min_tokens=1, max_tokens=10, overlap_tokens=0))

        assert result.stats.section_count == 2
        assert [(item.page_start, item.page_end) for item in result.parents] == [(1, 1), (2, 2)]

    def test_same_heading_in_two_places_is_not_merged(self):
        """同名标题出现在两个不相邻位置时必须分成两组，否则 parent 会跨越无关内容。"""

        blocks = [
            para("甲的内容", heading_path=("注意事项",)),
            para("乙的内容", heading_path=("乙",)),
            para("甲的第二处", heading_path=("注意事项",)),
        ]

        result = chunk(blocks, config=word_config(target_tokens=2, min_tokens=1, max_tokens=10, overlap_tokens=0))

        assert result.stats.section_count == 3

    def test_unpaged_document_keeps_page_none(self):
        """md / html / docx 没有页码概念 —— 不能退化成 0 或 1。"""

        blocks = [heading("政策", heading_path=("政策",)), para("正文", heading_path=("政策",))]

        result = chunk(blocks)

        for item in result.chunks:
            assert item.page_start is None
            assert item.page_end is None

    def test_page_range_spans_contributing_units(self):
        blocks = [
            heading("政策", heading_path=("政策",), page=2),
            para("第二页", heading_path=("政策",), page=2),
        ]

        result = chunk(blocks)

        assert (result.chunks[0].page_start, result.chunks[0].page_end) == (2, 2)


# ================================================================ 超长段落


class TestLongParagraphSplitting:
    def test_short_paragraph_is_untouched(self):
        text = words(10)

        result = chunk([para(text)], config=word_config(target_tokens=50, min_tokens=1, max_tokens=60, overlap_tokens=0))

        assert len(result.children) == 1
        assert result.children[0].text == text
        assert result.stats.hard_split_units == 0

    def test_exactly_at_the_limit_is_not_split(self):
        """边界：恰好等于 ``max_tokens`` 不该触发任何拆分。"""

        text = words(20)

        result = chunk([para(text)], config=word_config(target_tokens=20, min_tokens=1, max_tokens=20, overlap_tokens=0))

        assert len(result.children) == 1
        assert result.children[0].token_count == 20
        assert result.stats.hard_split_units == 0

    def test_splits_on_sentence_endings(self):
        text = sentences(10, tokens_each=3)

        result = chunk([para(text)], config=word_config(target_tokens=9, min_tokens=1, max_tokens=10, overlap_tokens=0))

        assert len(result.children) >= 3
        assert all(item.token_count <= 10 for item in result.children)
        assert all(item.text.endswith("。") for item in result.children)
        assert result.stats.hard_split_units == 0

    def test_falls_back_to_semicolons(self):
        text = f"{words(3, start=1)}；{words(3, start=4)}；{words(3, start=7)}"

        result = chunk([para(text)], config=word_config(target_tokens=3, min_tokens=1, max_tokens=4, overlap_tokens=0))

        assert all(item.token_count <= 4 for item in result.children)
        assert result.stats.hard_split_units == 0

    def test_falls_back_to_newlines(self):
        text = "\n".join(words(3, start=index * 3 + 1) for index in range(3))

        result = chunk([para(text)], config=word_config(target_tokens=3, min_tokens=1, max_tokens=4, overlap_tokens=0))

        assert all(item.token_count <= 4 for item in result.children)
        assert result.stats.hard_split_units == 0

    def test_hard_splits_when_there_is_no_punctuation_at_all(self):
        """计划只定义到「换行」，但 OCR 产物常是一整串无标点文本，必须兜底。"""

        text = words(9)

        result = chunk([para(text)], config=word_config(target_tokens=4, min_tokens=1, max_tokens=4, overlap_tokens=0))

        assert result.stats.hard_split_units == 1
        assert all(item.token_count <= 4 for item in result.children)
        assert " ".join(item.text for item in result.children).split() == text.split()

    def test_hard_split_never_loses_characters(self):
        text = "".join(f"{index}号" for index in range(50))

        result = chunk(
            [para(text)],
            config=word_config(tokenizer_id="mock-char-v1", target_tokens=30, min_tokens=1, max_tokens=30, overlap_tokens=0),
        )

        assert len(result.children) >= 5
        assert "".join(item.text for item in result.children) == text

    def test_quote_blocks_split_like_paragraphs(self):
        text = sentences(6, tokens_each=3)

        result = chunk([block(BLOCK_QUOTE, text)], config=word_config(target_tokens=9, min_tokens=1, max_tokens=10, overlap_tokens=0))

        assert len(result.children) >= 2
        assert all(item.token_count <= 10 for item in result.children)


# ================================================================ 表格


class TestTableChunking:
    def test_header_is_repeated_in_every_chunk(self):
        rows = [[f"{index}", f"值{index}"] for index in range(4)]
        table_block = table(["列A", "列B"], rows)
        # 表头 3 token、每行 3 token：上限 6 → 每块 = 表头 + 一行
        result = chunk([table_block], config=word_config(target_tokens=6, min_tokens=1, max_tokens=6, overlap_tokens=0))

        header = "列A | 列B"
        kids = [item for item in result.children]
        assert len(kids) == 4
        assert all(item.text.split("\n")[0] == header for item in kids)

    def test_every_row_appears_exactly_once(self):
        rows = [[f"r{index}", f"v{index}"] for index in range(5)]
        result = chunk(
            [table(["列A", "列B"], rows)],
            config=word_config(target_tokens=6, min_tokens=1, max_tokens=6, overlap_tokens=0),
        )

        joined = "\n".join(item.text for item in result.children)
        for row in rows:
            assert joined.count(" | ".join(row)) == 1

    def test_table_content_is_rendered_from_table_json(self):
        result = chunk([table(["列A", "列B"], [["甲", "乙"]])])

        assert "列A | 列B" in result.children[0].text
        assert "甲 | 乙" in result.children[0].text

    def test_single_wide_row_becomes_oversize_chunk(self):
        result = chunk(
            [table(["列A", "列B"], [[f"{words(5)}"]])],
            config=word_config(target_tokens=2, min_tokens=1, max_tokens=3, overlap_tokens=0),
        )

        oversize = [item for item in result.children if item.is_oversize]
        assert len(oversize) == 1
        assert result.stats.oversize_chunks >= 1

    def test_header_only_table_keeps_header(self):
        result = chunk([table(["列A", "列B"], [])])

        assert "列A | 列B" in result.children[0].text

    def test_table_in_section_uses_heading_prefix(self):
        blocks = [
            heading("价目", heading_path=("价目",)),
            table(["列A"], [["1"]], heading_path=("价目",)),
        ]

        result = chunk(blocks)

        assert result.children[0].text.startswith("价目\n")

    def test_falls_back_to_text_when_table_json_missing(self):
        """契约保证表格块一定有 ``table_json``；这里验证兜底路径不会崩。"""

        fallback = block(BLOCK_TABLE, "列A | 列B\n1 | 2")

        result = chunk([fallback])

        assert "列A | 列B" in result.children[0].text
        assert "1 | 2" in result.children[0].text


# ================================================================ 代码块


class TestCodeChunking:
    def test_code_block_is_never_cut_internally(self):
        code_text = "\n".join(f"line{index}" for index in range(10))

        result = chunk([code(code_text)], config=word_config(target_tokens=5, min_tokens=1, max_tokens=5, overlap_tokens=0))

        assert len(result.children) == 1
        assert result.children[0].text == code_text
        assert result.children[0].is_oversize is True
        assert result.stats.oversize_chunks >= 1

    def test_oversize_code_gets_its_own_chunk(self):
        code_text = "\n".join(f"line{index}" for index in range(8))
        blocks = [para(words(3)), code(code_text), para(words(3))]

        result = chunk(blocks, config=word_config(target_tokens=4, min_tokens=1, max_tokens=4, overlap_tokens=0))

        containing = [item for item in result.children if code_text in item.text]
        assert len(containing) == 1
        assert containing[0].text == code_text

    def test_code_indentation_is_preserved(self):
        code_text = "def f():\n    return 1"

        result = chunk([code(code_text)])

        assert "    return 1" in result.children[0].text

    def test_document_without_code_has_no_oversize(self):
        result = chunk([para(words(5))])

        assert result.stats.oversize_chunks == 0


# ================================================================ 列表


class TestListChunking:
    def test_markdown_list_items_stay_whole(self):
        items = [f"- 第{index}项 {words(3, start=index * 3)}" for index in range(3)]
        text = "\n".join(items)

        result = chunk(
            [listing(text)],
            config=word_config(target_tokens=5, min_tokens=1, max_tokens=6, overlap_tokens=0),
        )

        for item in result.children:
            for line in item.text.split("\n"):
                assert line in items, f"列表项被切碎：{line!r}"
        joined = "\n".join(item.text for item in result.children)
        for item_text in items:
            assert joined.count(item_text) == 1

    def test_oversize_list_item_is_split_inside_and_counted(self):
        long_item = f"- {words(12)}"

        result = chunk(
            [listing(long_item)],
            config=word_config(target_tokens=5, min_tokens=1, max_tokens=6, overlap_tokens=0),
        )

        assert result.stats.list_items_split == 1
        assert all(item.token_count <= 6 for item in result.children)

    def test_html_style_single_item_blocks(self):
        """HTML 解析出来的列表是「每项一个块」，两种形态都要能处理。"""

        blocks = [listing("项目一"), listing("项目二"), listing("项目三")]

        result = chunk(blocks, config=word_config(target_tokens=1, min_tokens=1, max_tokens=2, overlap_tokens=0))

        assert [item.text for item in result.children] == ["项目一", "项目二", "项目三"]

    def test_list_without_markers_is_treated_as_one_item(self):
        result = chunk([listing("没有标记的文本")])

        assert "没有标记的文本" in result.children[0].text

    def test_continuation_lines_stay_with_their_item(self):
        text = "- 第一项\n  续行内容\n- 第二项"

        result = chunk([listing(text)], config=word_config(target_tokens=20, min_tokens=1, max_tokens=30, overlap_tokens=0))

        joined = "\n".join(item.text for item in result.children)
        assert "- 第一项\n  续行内容" in joined


# ================================================================ 装箱与重叠


class TestPackingAndOverlap:
    def test_every_child_respects_max_tokens(self):
        blocks = [para(words(100, start=index * 100 + 1)) for index in range(20)]

        result = chunk(blocks, config=word_config())

        assert result.children
        assert all(item.token_count <= 700 for item in result.children)

    def test_children_close_at_target_tokens(self):
        blocks = [para(words(50, start=index * 50 + 1)) for index in range(20)]

        result = chunk(blocks, config=word_config())

        # 软目标 450：每块都不该只是「一个 50 token 的段落」
        assert len(result.children) >= 2
        assert all(item.token_count >= 450 for item in result.children[:-1])

    def test_adjacent_children_overlap_by_target(self):
        blocks = [para(words(100, start=index * 100 + 1)) for index in range(10)]
        config = word_config()

        result = chunk(blocks, config=config)

        kids = result.children
        assert len(kids) >= 2
        for previous, following in zip(kids, kids[1:]):
            overlap = junction_overlap(previous.text, following.text, counter=WordTokenCounter())
            assert overlap >= config.overlap_tokens, f"{previous.ordinal} → {following.ordinal} 只重叠了 {overlap}"

    def test_overlap_appears_verbatim_in_the_next_chunk(self):
        blocks = [para(words(60, start=index * 60 + 1)) for index in range(20)]

        result = chunk(blocks, config=word_config())

        previous, following = result.children[0], result.children[1]
        tail = flat(" ".join(previous.text.split()[-80:]))
        assert tail in flat(following.text)

    def test_overlap_never_pushes_chunk_over_max(self):
        blocks = [para(words(300, start=index * 300 + 1)) for index in range(8)]
        config = word_config(target_tokens=200, min_tokens=10, max_tokens=220, overlap_tokens=80)

        result = chunk(blocks, config=config)

        assert all(item.token_count <= config.max_tokens for item in result.children)

    def test_overlap_is_skipped_when_it_would_crowd_out_content(self):
        """上一块尾部是一个大单元（如长代码块）时，不能把它整块搬进下一块。

        这条守的是「重叠量不得超过目标的两倍」—— 否则新块会有一半是重复内容，
        而检索返回的「证据」看起来像是同一段文字被引用了两次。
        """

        code_text = "\n".join(f"line{index}" for index in range(200))  # 200 tokens > 2×80
        blocks = [para(words(20)), code(code_text), para(words(20, start=100))]
        config = word_config(target_tokens=15, min_tokens=1, max_tokens=700, overlap_tokens=80)

        result = chunk(blocks, config=config)

        assert result.stats.overlap_skipped >= 1
        assert all(item.token_count <= 700 for item in result.children)
        assert code_text in "".join(item.text for item in result.children)

    def test_overlap_stays_within_twice_the_target(self):
        code_text = "\n".join(f"line{index}" for index in range(200))
        blocks = [para(words(20)), code(code_text), para(words(20, start=100))]
        config = word_config(target_tokens=15, min_tokens=1, max_tokens=700, overlap_tokens=80)

        result = chunk(blocks, config=config)

        kids = result.children
        for previous, following in zip(kids, kids[1:]):
            assert junction_overlap(previous.text, following.text) <= 2 * config.overlap_tokens

    def test_overlap_can_be_disabled(self):
        blocks = [para(words(50, start=index * 50 + 1)) for index in range(6)]

        result = chunk(blocks, config=word_config(target_tokens=100, min_tokens=1, max_tokens=120, overlap_tokens=0))

        assert result.stats.overlap_applied == 0
        kids = result.children
        for previous, following in zip(kids, kids[1:]):
            assert junction_overlap(previous.text, following.text) == 0

    def test_small_tail_is_merged_back(self):
        blocks = [para(words(10, start=index * 10 + 1)) for index in range(5)]
        config = word_config(target_tokens=20, min_tokens=15, max_tokens=40, overlap_tokens=0)

        result = chunk(blocks, config=config)

        assert result.stats.merged_short_tail == 1
        assert len(result.children) == 2

    def test_unmergeable_short_tail_is_reported(self):
        blocks = [para(words(10, start=index * 10 + 1)) for index in range(5)]
        config = word_config(target_tokens=20, min_tokens=15, max_tokens=20, overlap_tokens=0)

        result = chunk(blocks, config=config)

        assert result.stats.below_min_chunks == 1
        assert len(result.children) == 3

    def test_parent_chunk_enabled_by_default(self):
        result = chunk([para(words(5))])

        assert len(result.parents) == 1
        assert result.children[0].parent_chunk_id == result.parents[0].chunk_id

    def test_parent_can_be_disabled(self):
        result = chunk([para(words(5))], config=word_config(parent_chunk_enabled=False))

        assert result.parents == ()
        assert all(item.parent_chunk_id is None for item in result.children)
        assert all(item.chunk_type == CHUNK_TYPE_CHILD for item in result.chunks)

    def test_every_child_points_to_an_existing_parent_of_its_own_section(self):
        blocks = [
            heading("甲", heading_path=("甲",)),
            para(words(300, start=1), heading_path=("甲",)),
            heading("乙", heading_path=("乙",)),
            para(words(300, start=1000), heading_path=("乙",)),
        ]

        result = chunk(blocks, config=word_config())

        parent_ids = {item.chunk_id for item in result.parents}
        assert parent_ids
        for item in result.children:
            assert item.parent_chunk_id in parent_ids

    def test_child_body_is_a_contiguous_part_of_its_parent(self):
        blocks = [para(words(80, start=index * 80 + 1)) for index in range(6)]

        result = chunk(blocks, config=word_config())

        parents = {item.chunk_id: item for item in result.parents}
        for item in result.children:
            parent = parents[item.parent_chunk_id]
            body = bodies(ChunkingResult(chunks=(item,)))[0]
            parent_body = bodies(ChunkingResult(chunks=(parent,)))[0]
            assert body in parent_body

    def test_parents_come_before_their_children(self):
        """``insert_chunks`` 的复合自引用外键要求父先于子写入。"""

        blocks = [
            heading("甲", heading_path=("甲",)),
            para(words(400, start=1), heading_path=("甲",)),
            heading("乙", heading_path=("乙",)),
            para(words(400, start=1000), heading_path=("乙",)),
        ]

        result = chunk(blocks, config=word_config())

        seen: set[str] = set()
        for item in result.chunks:
            if item.chunk_type == CHUNK_TYPE_CHILD:
                assert item.parent_chunk_id in seen, "child 出现在它的 parent 之前"
            else:
                seen.add(item.chunk_id)

    def test_parent_max_tokens_splits_giant_sections(self):
        blocks = [para(words(50, start=index * 50 + 1)) for index in range(10)]
        config = word_config(target_tokens=50, min_tokens=1, max_tokens=60, overlap_tokens=0, parent_max_tokens=120)

        result = chunk(blocks, config=config)

        assert len(result.parents) >= 4
        assert all(item.token_count <= 120 for item in result.parents)

    def test_parent_and_child_carry_the_same_tenant_and_page_scope(self):
        blocks = [para("第一段", page=1), para("第二段", page=1)]

        result = chunk(blocks, config=word_config(target_tokens=100, min_tokens=1, max_tokens=200, overlap_tokens=0))

        assert result.chunks
        for item in result.chunks:
            assert item.tenant_id == "tenant-a"
            # 页码是分组键，因此同一个 parent/child 的页范围必然落在同一页上
            assert item.page_start == item.page_end == 1


# ================================================================ 标题前缀


class TestHeadingPrefix:
    def test_prefix_is_the_flattened_heading_path(self):
        blocks = [
            heading("退款", heading_path=("售后", "退款")),
            para("正文内容", heading_path=("售后", "退款")),
        ]

        result = chunk(blocks)

        assert result.children[0].text.startswith(f"售后{HEADING_SEPARATOR}退款\n")

    def test_metadata_keeps_structured_heading_path(self):
        blocks = [
            heading("退款", heading_path=("售后", "退款")),
            para("正文内容", heading_path=("售后", "退款")),
        ]

        result = chunk(blocks)

        assert result.children[0].heading_path == ("售后", "退款")
        assert result.children[0].heading_text == "退款"

    def test_prefix_is_not_repeated_when_body_already_starts_with_heading(self):
        """要求 7 的后半句：标题路径作前缀，但**不得重复原文主体**。"""

        blocks = [
            heading("政策", heading_path=("政策",)),
            para("政策\n正文内容", heading_path=("政策",)),
        ]

        result = chunk(blocks)

        text = result.children[0].text
        assert text.count("政策") == 1
        assert text.startswith("政策")

    def test_prefix_is_not_repeated_for_last_heading_component_only(self):
        blocks = [
            heading("退款", heading_path=("售后", "退款")),
            para("退款\n具体说明", heading_path=("售后", "退款")),
        ]

        result = chunk(blocks)

        assert result.children[0].text.startswith("退款\n")

    def test_prefix_can_be_disabled_but_heading_text_is_kept(self):
        blocks = [
            heading("政策", heading_path=("政策",)),
            para("正文内容", heading_path=("政策",)),
        ]

        result = chunk(blocks, config=word_config(preserve_heading_path=False))

        text = result.children[0].text
        assert " > " not in text
        assert text.startswith("政策")
        assert "正文内容" in text

    def test_prefix_is_dropped_when_it_would_break_the_limit(self):
        blocks = [heading("甲", heading_path=("甲",)), code("\n".join(f"l{index}" for index in range(5)), heading_path=("甲",))]

        result = chunk(blocks, config=word_config(target_tokens=2, min_tokens=1, max_tokens=4, overlap_tokens=0))

        assert result.stats.prefix_dropped == 1
        assert all(item.token_count <= 4 or item.is_oversize for item in result.children)
        assert "甲\n" not in result.children[0].text

    def test_prefix_counts_towards_token_budget(self):
        blocks = [para(words(20), heading_path=("很长的标题",))]

        result = chunk(blocks, config=word_config(target_tokens=20, min_tokens=1, max_tokens=22, overlap_tokens=0))

        assert all(item.token_count <= 22 for item in result.children)

    def test_section_without_heading_has_no_prefix(self):
        result = chunk([para("正文")])

        assert not result.children[0].text.startswith(" > ")


# ================================================================ 产物字段


class TestChunkFieldContract:
    def test_output_contains_every_required_field(self):
        result = chunk([heading("政策", heading_path=("政策",)), para("正文", heading_path=("政策",))])

        payload = result.children[0].to_dict()
        for field in (
            "chunk_id",
            "parent_chunk_id",
            "text",
            "token_count",
            "tenant_id",
            "document_id",
            "document_version",
            "page_start",
            "page_end",
            "heading_path",
            "acl",
            "content_hash",
        ):
            assert field in payload

    def test_tenant_and_version_are_inherited_from_context(self):
        context = make_context(tenant_id="tenant-b", document_id="doc-9", document_version_id="ver-9", document_version=3)

        result = chunk([para("正文")], context=context)

        item = result.children[0]
        assert item.tenant_id == "tenant-b"
        assert item.document_id == "doc-9"
        assert item.document_version_id == "ver-9"
        assert item.document_version == 3

    def test_acl_entries_are_inherited(self):
        context = make_context(
            acl=(AclEntry("user", "u1", "read"), AclEntry("role", "reviewer", "review"), AclEntry("tenant", "t1", "publish"))
        )

        result = chunk([para("正文")], context=context)

        item = result.children[0]
        assert {entry.permission for entry in item.acl} == {"read", "review", "publish"}
        assert item.metadata_json["acl"] == context.acl_dicts

    def test_empty_acl_is_allowed(self):
        result = chunk([para("正文")], context=make_context(acl=()))

        assert result.children[0].acl == ()
        assert result.children[0].metadata_json["acl"] == []

    def test_acl_is_kept_in_metadata_json_because_there_is_no_column(self):
        result = chunk([para("正文")])

        metadata = result.children[0].metadata_json
        for field in ("tenant_id", "acl", "page_start", "page_end", "heading_path", "char_count"):
            assert field in metadata

    def test_char_and_token_counts_match_the_text(self):
        result = chunk([para(words(30))])

        item = result.children[0]
        assert item.char_count == len(item.text)
        assert item.token_count == WordTokenCounter().count(item.text)

    def test_content_hash_is_sha256_of_text(self):
        result = chunk([para("正文内容")])

        item = result.children[0]
        assert item.content_hash == hashlib.sha256(item.text.encode("utf-8")).hexdigest()

    def test_metadata_records_tokenizer_and_versions(self):
        result = chunk([para("正文")])

        metadata = result.children[0].metadata_json
        assert metadata["tokenizer_id"] == MOCK_TOKENIZER
        assert metadata["chunker_version"]
        assert metadata["chunking_config_version"] == CONFIG_VERSION
        assert metadata["overlap_tokens"] == 80

    def test_metadata_is_json_serializable(self):
        import json

        result = chunk([para("正文")])

        json.dumps(result.children[0].metadata_json, ensure_ascii=False)

    def test_source_information_is_carried(self):
        result = chunk([para("正文")])

        metadata = result.children[0].metadata_json
        assert metadata["filename"] == "sample.md"
        assert metadata["source_type"] == "md"
        assert metadata["source_uri"] == "upload://sample.md"

    def test_document_title_falls_back_to_parsed_document(self):
        blocks = [para("正文")]
        document = make_doc(blocks, title="来自解析器的标题")

        result = chunk_document(document, context=make_context(document_title=""), config=word_config())

        assert result.context.document_title == "来自解析器的标题"

    def test_result_exposes_effective_config_for_version_storage(self):
        result = chunk([para("正文")], config=word_config(target_tokens=100, min_tokens=10))

        payload = result.config_payload
        assert payload["target_tokens"] == 100
        assert payload["tokenizer_id"] == MOCK_TOKENIZER

    def test_chunk_dataclass_rejects_unknown_type(self):
        with pytest.raises(ValueError):
            Chunk(
                chunk_id="x",
                text="t",
                token_count=1,
                char_count=1,
                parent_chunk_id=None,
                chunk_type="grandchild",
                tenant_id="t",
                document_id="d",
                document_version=1,
                document_version_id="v",
                page_start=None,
                page_end=None,
                heading_path=(),
                acl=(),
                content_hash="h",
                ordinal=0,
            )

    def test_parent_chunk_may_not_have_a_parent(self):
        with pytest.raises(ValueError):
            Chunk(
                chunk_id="x",
                text="t",
                token_count=1,
                char_count=1,
                parent_chunk_id="p",
                chunk_type=CHUNK_TYPE_PARENT,
                tenant_id="t",
                document_id="d",
                document_version=1,
                document_version_id="v",
                page_start=None,
                page_end=None,
                heading_path=(),
                acl=(),
                content_hash="h",
                ordinal=0,
            )


class TestChunkContextValidation:
    @pytest.mark.parametrize("field", ["tenant_id", "document_id", "document_version_id"])
    def test_empty_identifier_is_rejected(self, field):
        with pytest.raises(ChunkingError) as excinfo:
            make_context(**{field: "   "})

        assert excinfo.value.error_code == "invalid_context"

    def test_version_must_be_positive(self):
        with pytest.raises(ChunkingError):
            make_context(document_version=0)

    def test_version_must_be_integer(self):
        with pytest.raises(ChunkingError):
            make_context(document_version="1")

    def test_acl_accepts_mappings(self):
        context = ChunkContext(
            tenant_id="t",
            document_id="d",
            document_version_id="v",
            document_version=1,
            acl=[{"subject_type": "user", "subject_id": "u1", "permission": "read"}],  # type: ignore[list-item]
        )

        assert context.acl == (AclEntry("user", "u1", "read"),)

    def test_acl_rejects_unknown_permission(self):
        with pytest.raises(ValueError):
            AclEntry("user", "u1", "sudo")

    def test_acl_rejects_unknown_subject_type(self):
        with pytest.raises(ValueError):
            AclEntry("robot", "u1", "read")

    def test_acl_requires_subject_id(self):
        with pytest.raises(ValueError):
            AclEntry("user", "  ", "read")

    def test_acl_mapping_missing_field_names_the_field(self):
        with pytest.raises(ValueError) as excinfo:
            AclEntry.from_mapping({"subject_type": "user"})

        assert "subject_id" in str(excinfo.value)

    def test_acl_enums_mirror_models(self):
        """镜像 + 断言：保持切分器不依赖数据库，同时不让两份枚举漂移。"""

        assert chunk_models.ACL_SUBJECT_TYPES == models.ACL_SUBJECT_TYPES
        assert chunk_models.ACL_PERMISSIONS == models.ACL_PERMISSIONS

    def test_chunk_document_rejects_wrong_argument_types(self):
        document = make_doc([para("正文")])

        with pytest.raises(ChunkingError):
            chunk_document(document, context=None, config=word_config())  # type: ignore[arg-type]
        with pytest.raises(ChunkingError):
            chunk_document(document, context=make_context(), config=None)  # type: ignore[arg-type]
        with pytest.raises(ChunkingError):
            chunk_document({"title": "x"}, context=make_context(), config=word_config())  # type: ignore[arg-type]


# ================================================================ 确定性


class TestDeterminism:
    def test_same_input_gives_identical_ids_and_texts(self):
        blocks = [heading("政策", heading_path=("政策",)), para(words(600), heading_path=("政策",))]

        first = chunk(blocks, config=word_config())
        second = chunk(blocks, config=word_config())

        assert [item.chunk_id for item in first.chunks] == [item.chunk_id for item in second.chunks]
        assert [item.text for item in first.chunks] == [item.text for item in second.chunks]

    def test_chunk_ids_are_unique_within_a_version(self):
        blocks = [para(words(50, start=index * 50 + 1)) for index in range(10)]

        result = chunk(blocks, config=word_config())

        ids = [item.chunk_id for item in result.chunks]
        assert len(ids) == len(set(ids))

    def test_chunk_ids_differ_across_tenants_even_for_identical_text(self):
        blocks = [para("完全一样的内容")]

        first = chunk(blocks, context=make_context(tenant_id="tenant-a"))
        second = chunk(blocks, context=make_context(tenant_id="tenant-b"))

        assert first.chunks[0].chunk_id != second.chunks[0].chunk_id

    def test_chunk_ids_differ_across_versions(self):
        blocks = [para("完全一样的内容")]

        first = chunk(blocks, context=make_context(document_version_id="v1", document_version=1))
        second = chunk(blocks, context=make_context(document_version_id="v2", document_version=2))

        assert first.chunks[0].chunk_id != second.chunks[0].chunk_id

    def test_repeated_identical_texts_get_distinct_ids(self):
        """同一版本里出现两段完全相同的文本（同一张表在两处各出现一次）不能撞唯一约束。"""

        blocks = [table(["A"], [["1"]]), table(["A"], [["1"]])]

        result = chunk(blocks, config=word_config(target_tokens=2, min_tokens=1, max_tokens=6, overlap_tokens=0))

        texts = [item.text for item in result.children]
        assert len(texts) == 2 and texts[0] == texts[1], "前置条件：两段文本必须完全相同"
        ids = [item.chunk_id for item in result.chunks]
        assert len(ids) == len(set(ids))

    def test_parent_id_prefix_and_shape(self):
        result = chunk([para("正文")])

        assert result.parents[0].chunk_id.startswith("p_")
        assert len(result.parents[0].chunk_id) == 34
        assert result.children[0].chunk_id.startswith("c_")

    def test_different_config_gives_different_ids(self):
        """切分策略变了、切出的内容也就变了，id 必须跟着变（B6 增量重建索引的依据）。"""

        blocks = [para(words(30, start=index * 30 + 1)) for index in range(5)]

        fine = chunk(blocks, config=word_config(target_tokens=30, min_tokens=1, max_tokens=40, overlap_tokens=0))
        coarse = chunk(blocks, config=word_config(target_tokens=200, min_tokens=1, max_tokens=250, overlap_tokens=0))

        assert len(fine.children) != len(coarse.children)
        assert [item.chunk_id for item in fine.chunks] != [item.chunk_id for item in coarse.chunks]

    def test_same_segmentation_with_irrelevant_config_keeps_ids(self):
        """只改了不影响切分结果的阈值时，id 不该变 —— 否则「调整配置」会无意义地让全库失效。

        ``tokenizer_id`` 参与 id 是因为它决定 token 计数口径；
        ``target_tokens`` 只在真的改变切分结果时才影响后续 id（因为内容变了）。
        """

        blocks = [para(words(5))]

        default = chunk(blocks, config=word_config())
        tweaked = chunk(blocks, config=word_config(target_tokens=400))

        assert [item.chunk_id for item in default.chunks] == [item.chunk_id for item in tweaked.chunks]


# ================================================================ 空文档


class TestEmptyDocuments:
    def test_no_blocks_returns_empty_result_without_raising(self):
        result = chunk([])

        assert result.empty
        assert result.chunks == ()
        assert result.stats.input_blocks == 0

    def test_only_images_returns_empty_result(self):
        blocks = [block(BLOCK_IMAGE, image_ref="a.png"), block(BLOCK_IMAGE, image_ref="b.png")]

        result = chunk(blocks)

        assert result.empty
        assert result.stats.skipped_non_text_blocks == 2

    def test_only_blank_blocks_returns_empty_result(self):
        result = chunk([para("   "), para("\n\n")])

        assert result.empty
        assert result.stats.dropped_empty == 2

    def test_only_headings_keeps_heading_text(self):
        """目录式文档：标题不能因为「标题不单独成块」而整体消失。"""

        blocks = [heading("第一章", heading_path=("第一章",))]

        result = chunk(blocks)

        assert len(result.children) == 1
        assert result.children[0].text == "第一章"
        assert result.children[0].heading_path == ("第一章",)

    def test_empty_document_stats_are_zeroed(self):
        stats = chunk([]).stats.to_dict()

        assert stats["child_count"] == 0
        assert stats["parent_count"] == 0
        assert stats["oversize_chunks"] == 0

    def test_result_iterates_like_a_list(self):
        result = chunk([para("正文")])

        assert len(result) == len(result.chunks)
        assert [item.chunk_id for item in result] == [item.chunk_id for item in result.chunks]
        assert result[-1] is result.chunks[-1]


# ================================================================ 枚举真值表


#: 「块类型 → 这个块最终该产出几个 child chunk」的真值表。
#: 用真值表而不是抽样：以后往 ``BLOCK_TYPES`` 加一个类型时，
#: 要么它被这张表覆盖、要么本测试立刻失败提醒「这里有决策要做」
#: （踩坑记录 D2 的教训：抽样测试的盲区正好落在新增枚举值上）。
BLOCK_TYPE_TRUTH_TABLE: dict[str, int] = {
    BLOCK_HEADING: 1,  # 只有标题的组走兜底：标题文本必须留下来
    BLOCK_PARAGRAPH: 1,
    BLOCK_LIST: 1,
    BLOCK_TABLE: 1,
    BLOCK_CODE: 1,
    BLOCK_QUOTE: 1,
    BLOCK_IMAGE: 0,  # 没有可检索文本，不进 chunk
    BLOCK_PAGE_BREAK: 0,  # 只是结构标记
}


def sample_block(block_type: str) -> Block:
    """为每种块类型造一个最小合法样本。"""

    if block_type == BLOCK_TABLE:
        return table(["列A", "列B"], [["甲", "乙"]])
    if block_type == BLOCK_IMAGE:
        return block(BLOCK_IMAGE, "", image_ref="images/1.png")
    if block_type == BLOCK_PAGE_BREAK:
        return block(BLOCK_PAGE_BREAK, "")
    if block_type == BLOCK_HEADING:
        return heading("标题")
    return block(block_type, "示例内容")


#: ``ChunkingStats`` 的字段全集（用元组列出，让「新增字段」必须显式更新这里）
ChunkingStatsFields = (
    "input_blocks",
    "skipped_non_text_blocks",
    "dropped_empty",
    "dropped_page_number",
    "dropped_running_header",
    "dropped_duplicate",
    "section_count",
    "parent_count",
    "child_count",
    "oversize_chunks",
    "overlap_applied",
    "overlap_skipped",
    "merged_short_tail",
    "below_min_chunks",
    "hard_split_units",
    "list_items_split",
    "prefix_dropped",
)


class TestBlockTypeTruthTable:
    def test_truth_table_covers_every_block_type(self):
        """这张表必须覆盖 ``BLOCK_TYPES`` 全集，并且没有多余项。"""

        from services.ingestion.parsers.base import BLOCK_TYPES

        assert set(BLOCK_TYPE_TRUTH_TABLE) == set(BLOCK_TYPES)

    @pytest.mark.parametrize("block_type", sorted(BLOCK_TYPE_TRUTH_TABLE))
    def test_every_block_type_produces_the_expected_chunks(self, block_type):
        result = chunk([sample_block(block_type)])

        assert len(result.children) == BLOCK_TYPE_TRUTH_TABLE[block_type]

    @pytest.mark.parametrize("block_type", sorted(BLOCK_TYPE_TRUTH_TABLE))
    def test_every_block_type_never_raises_and_keeps_text(self, block_type):
        item = sample_block(block_type)

        result = chunk([item])

        if item.text.strip():
            joined = flat("".join(entry.text for entry in result.chunks))
            assert flat(item.text) in joined or block_type == BLOCK_HEADING

    def test_only_non_text_blocks_yields_no_parents(self):
        """全是图片的文档不该产出一个「空 parent」占位记录。"""

        result = chunk([sample_block(BLOCK_IMAGE), sample_block(BLOCK_PAGE_BREAK)])

        assert result.parents == ()
        assert result.chunks == ()

    def test_stats_keys_are_a_closed_set_of_ints(self):
        stats = chunk([para("正文")]).stats

        assert set(stats.to_dict()) == set(ChunkingStatsFields)
        assert all(isinstance(value, int) for value in stats.to_dict().values())


class TestAclTruthTable:
    @pytest.mark.parametrize("permission", list(chunk_models.ACL_PERMISSIONS))
    def test_every_declared_permission_is_accepted(self, permission):
        assert AclEntry("user", "u1", permission).permission == permission

    @pytest.mark.parametrize("subject_type", list(chunk_models.ACL_SUBJECT_TYPES))
    def test_every_declared_subject_type_is_accepted(self, subject_type):
        assert AclEntry(subject_type, "s1", "read").subject_type == subject_type

    @pytest.mark.parametrize("permission", ["", "READ", "admin", "delete_all"])
    def test_undeclared_permissions_are_rejected(self, permission):
        with pytest.raises(ValueError):
            AclEntry("user", "u1", permission)

    def test_all_permissions_survive_into_chunk_metadata(self):
        entries = tuple(AclEntry("user", f"u{index}", permission) for index, permission in enumerate(chunk_models.ACL_PERMISSIONS))

        result = chunk([para("正文")], context=make_context(acl=entries))

        assert [entry["permission"] for entry in result.children[0].metadata_json["acl"]] == list(chunk_models.ACL_PERMISSIONS)


# ================================================================ 生产默认路径


class TestProductionDefaults:
    """前面的用例都用固定 mock 计数；这里必须用**生产默认配置 + 默认 tokenizer** 跑一遍。

    否则会出现「mock 下全绿，真实配置一跑就出问题」这种最尴尬的缺口。
    """

    def test_default_config_chunks_cjk_document(self):
        chinese = "退款政策规定" * 300  # 1800 个汉字 → 启发式计数 1800 tokens

        result = chunk([para(chinese)], config=ChunkConfig())

        assert len(result.children) >= 3
        assert all(item.token_count <= 700 for item in result.children)
        assert "".join(item.text for item in result.children) == chinese

    def test_default_config_keeps_heading_prefix_and_stays_within_limit(self):
        body = "订单退款流程说明。" * 120  # 1200 tokens
        blocks = [heading("退款", heading_path=("售后", "退款")), para(body, heading_path=("售后", "退款"))]

        result = chunk(blocks, config=ChunkConfig())

        for item in result.children:
            assert item.text.startswith(f"售后{HEADING_SEPARATOR}退款\n")
            assert item.token_count <= ChunkConfig().max_tokens

    def test_default_config_overlap_is_applied(self):
        # 句子各不相同：否则「最长公共后缀/前缀」在重复文本上是歧义的，
        # 度量会失真（重叠本来就不该靠"文本恰好相同"来判断）
        body = "".join(f"第{index}号说明文字内容。" for index in range(200))
        blocks = [para(body)]

        result = chunk(blocks, config=ChunkConfig())

        kids = result.children
        assert len(kids) >= 2
        for previous, following in zip(kids, kids[1:]):
            overlap = junction_overlap(previous.text, following.text, counter=HeuristicTokenCounter())
            assert overlap >= ChunkConfig().overlap_tokens, f"接缝只共享 {overlap} tokens"

    def test_shared_junction_measures_the_true_overlap(self):
        """度量工具本身的自检 —— 度量错了，上面那些断言全都不可信。"""

        assert shared_junction("一二三四五六七八九十", "七八九十甲乙丙丁") == "七八九十"
        assert shared_junction("甲甲甲", "乙乙乙") == ""
        assert shared_junction("标题\n甲乙丙", "标题\n乙丙丁", prefix="标题") == "乙丙"

    def test_shared_junction_is_counted_in_tokens_not_words(self):
        """CJK 文本下按空白切词会把 80 tokens 算成几个词 —— 必须按 tokenizer 计。"""

        shared = "七八九十"
        assert HeuristicTokenCounter().count(shared) == 4
        assert len(shared.split()) == 1

    def test_default_tokenizer_is_the_heuristic_one(self):
        result = chunk([para("退款政策")], config=ChunkConfig())

        assert result.children[0].metadata_json["tokenizer_id"] == DEFAULT_TOKENIZER_ID

    def test_cleaning_is_idempotent(self):
        """清洗两次结果相同：否则「重跑一次任务」会产生不同的 chunk 边界。"""

        blocks = [
            heading("政策", heading_path=("政策",)),
            para(" 正文一 \n\n\n 正文二 ", heading_path=("政策",)),
            para("正文一", heading_path=("政策",)),
            block(BLOCK_IMAGE, "", image_ref="a.png"),
        ]
        first = clean_blocks(blocks, token_counter=WordTokenCounter())
        second = clean_blocks(first.blocks, token_counter=WordTokenCounter())

        assert [(item.type, item.text) for item in second.blocks] == [(item.type, item.text) for item in first.blocks]
        assert second.counters.dropped_duplicate == 0


# ================================================================ 守卫断言


def _parse_module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _chunker_modules() -> list[Path]:
    return sorted(CHUNKER_DIR.glob("*.py"))


def _imported_modules(tree: ast.Module) -> set[str]:
    """只取 import 语句里的模块名（**不碰 docstring**，见踩坑 D1）。"""

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


class TestChunkerGuards:
    @pytest.mark.parametrize(
        "forbidden",
        ["sqlalchemy", "services.ingestion.repository", "services.ingestion.db", "services.ingestion.models"],
    )
    def test_chunker_modules_do_not_depend_on_database(self, forbidden):
        offenders = [
            path.name
            for path in _chunker_modules()
            if any(
                name == forbidden or name.startswith(f"{forbidden}.")
                for name in _imported_modules(_parse_module(path))
            )
        ]

        assert offenders == [], f"以下切分器模块依赖了数据库：{offenders}"

    def test_chunker_modules_do_not_import_services_or_routers(self):
        """切分器与解析器同类：只能依赖契约与配置，不能反向依赖上层。"""

        allowed = ("services.ingestion.parsers",)
        offenders = [
            path.name
            for path in _chunker_modules()
            if any(
                name.startswith("services.ingestion.") and not name.startswith(allowed)
                for name in _imported_modules(_parse_module(path))
            )
        ]

        assert offenders == [], f"以下切分器模块依赖了上层模块：{offenders}"

    def test_chunker_modules_do_not_use_dynamic_imports(self):
        """``importlib.import_module("services.ingestion.repository")`` 也要挡住。"""

        offenders = []
        for path in _chunker_modules():
            for node in ast.walk(_parse_module(path)):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Name) and func.id == "__import__":
                    offenders.append(f"{path.name}:__import__")
                if isinstance(func, ast.Attribute) and func.attr == "import_module":
                    offenders.append(f"{path.name}:import_module")

        assert offenders == []

    @pytest.mark.parametrize("parameter", ["tenant_id", "acl"])
    def test_no_chunker_function_takes_bare_tenant_parameters(self, parameter):
        """守卫：租户 / ACL 只能通过 ``ChunkContext`` 传入（见 [D-9]）。"""

        offenders: list[str] = []
        for path in _chunker_modules():
            for node in ast.walk(_parse_module(path)):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                names = {arg.arg for arg in node.args.posonlyargs}
                names |= {arg.arg for arg in node.args.args}
                names |= {arg.arg for arg in node.args.kwonlyargs}
                if parameter in names:
                    offenders.append(f"{path.name}:{node.name}")

        assert offenders == [], f"以下函数接收了裸 {parameter} 参数：{offenders}"

    def test_chunk_document_signature_is_frozen(self):
        tree = _parse_module(CHUNKER_DIR / "chunker.py")
        function = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "chunk_document"
        )

        assert [arg.arg for arg in function.args.args] == ["document"]
        assert [arg.arg for arg in function.args.kwonlyargs] == ["context", "config"]
        # 两个 kw-only 参数都没有默认值：调用方必须显式给上下文与配置，
        # 不能出现「忘了传 config 就用默认值悄悄跑」这种路径
        assert function.args.kw_defaults == [None, None]
        assert function.args.vararg is None
        assert function.args.kwarg is None

    def test_chunkers_package_is_exported_from_ingestion(self):
        from services.ingestion import chunkers

        assert chunkers.chunk_document is chunk_document

    def test_chunker_reads_no_files(self):
        """切分器只吃 ``ParsedDocument``：源码里不该出现文件读取。"""

        forbidden_calls = {"open"}

        offenders: list[str] = []
        for path in _chunker_modules():
            for node in ast.walk(_parse_module(path)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                    offenders.append(f"{path.name}:{node.func.id}")

        assert offenders == []


# ================================================================ 落库兼容


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    """建一个**打开了外键约束**的临时 SQLite（复合自引用外键才会真的生效）。"""

    url = f"sqlite:///{(tmp_path / 'chunking.db').as_posix()}"
    monkeypatch.setenv(db.DATABASE_URL_ENV, url)
    config = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")

    engine = db.create_db_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        db.dispose_engines()


def seed_version(session, *, tenant_id: str = "tenant-a"):
    knowledge_base = repository.create_knowledge_base(session, tenant_id=tenant_id, slug="kb")
    document = repository.create_document(
        session,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        source_uri="upload://sample.md",
        source_type="md",
    )
    version = repository.create_document_version(
        session,
        tenant_id=tenant_id,
        document_id=document.id,
        content_hash="c" * 64,
    )
    return document, version


def seed_tenant(session, *, tenant_id: str = "tenant-a"):
    repository.create_tenant(session, tenant_id=tenant_id, name="示例租户")


class TestInsertChunksCompatibility:
    """B6 要用 ``repository.insert_chunks`` 落库，因此本批产出必须满足它的约束。"""

    def test_output_can_be_inserted_into_document_chunks(self, db_session):
        seed_tenant(db_session)
        document, version = seed_version(db_session)
        blocks = [
            heading("政策", heading_path=("政策",)),
            para(words(400, start=1), heading_path=("政策",)),
        ]
        context = make_context(
            tenant_id=document.tenant_id,
            document_id=document.id,
            document_version_id=version.id,
            document_version=version.version,
        )
        result = chunk_document(make_doc(blocks), context=context, config=word_config())

        inserted = repository.insert_chunks(
            db_session,
            tenant_id=document.tenant_id,
            document_version_id=version.id,
            chunks=result.to_insert_plan(),
        )

        assert inserted == len(result.chunks)
        assert repository.count_chunks(db_session, document.tenant_id, version.id) == len(result.chunks)

    def test_parents_are_inserted_before_children(self, db_session):
        """复合自引用外键 ``(document_version_id, parent_chunk_id)`` 要求父先入库。"""

        seed_tenant(db_session)
        document, version = seed_version(db_session)
        blocks = [para(words(300, start=index * 300 + 1)) for index in range(4)]
        context = make_context(
            tenant_id=document.tenant_id,
            document_id=document.id,
            document_version_id=version.id,
            document_version=version.version,
        )
        result = chunk_document(make_doc(blocks), context=context, config=word_config())

        repository.insert_chunks(
            db_session,
            tenant_id=document.tenant_id,
            document_version_id=version.id,
            chunks=result.to_insert_plan(),
        )

        rows = repository.list_chunks(db_session, document.tenant_id, version.id)
        by_id = {row.chunk_id: row for row in rows}
        for row in rows:
            if row.parent_chunk_id:
                assert row.parent_chunk_id in by_id

    def test_metadata_json_round_trips_through_the_database(self, db_session):
        seed_tenant(db_session)
        document, version = seed_version(db_session)
        context = make_context(
            tenant_id=document.tenant_id,
            document_id=document.id,
            document_version_id=version.id,
            document_version=version.version,
        )
        result = chunk_document(make_doc([para("正文")]), context=context, config=word_config())

        repository.insert_chunks(
            db_session,
            tenant_id=document.tenant_id,
            document_version_id=version.id,
            chunks=result.to_insert_plan(),
        )

        row = repository.list_chunks(db_session, document.tenant_id, version.id)[0]
        assert row.metadata_json["tenant_id"] == document.tenant_id
        assert row.metadata_json["acl"] == context.acl_dicts
        assert row.metadata_json["heading_path"] == []
        assert row.metadata_json["page_start"] is None

    def test_duplicate_chunk_id_is_rejected_by_the_database(self, db_session):
        seed_tenant(db_session)
        document, version = seed_version(db_session)
        context = make_context(
            tenant_id=document.tenant_id,
            document_id=document.id,
            document_version_id=version.id,
            document_version=version.version,
        )
        result = chunk_document(make_doc([para("正文")]), context=context, config=word_config())
        payload = result.to_insert_plan()

        repository.insert_chunks(
            db_session,
            tenant_id=document.tenant_id,
            document_version_id=version.id,
            chunks=payload,
        )

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            repository.insert_chunks(
                db_session,
                tenant_id=document.tenant_id,
                document_version_id=version.id,
                chunks=payload,
            )
        db_session.rollback()

    def test_child_from_another_version_is_rejected(self, db_session):
        """父 chunk 必须与子 chunk 同版本（复合外键的第二个作用）。"""

        seed_tenant(db_session)
        document, version = seed_version(db_session)
        other = repository.create_document_version(
            db_session,
            tenant_id=document.tenant_id,
            document_id=document.id,
            content_hash="e" * 64,
        )
        context = make_context(
            tenant_id=document.tenant_id,
            document_id=document.id,
            document_version_id=version.id,
            document_version=version.version,
        )
        result = chunk_document(make_doc([para(words(300))]), context=context, config=word_config())
        child = next(item for item in result.children)

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            repository.insert_chunks(
                db_session,
                tenant_id=document.tenant_id,
                document_version_id=other.id,
                chunks=[child.to_insert_payload()],
            )
        db_session.rollback()

    def test_insert_plan_only_contains_declared_keys(self):
        result = chunk([para("正文")])

        for payload in result.to_insert_plan():
            assert set(payload) == {"chunk_id", "parent_chunk_id", "text", "token_count", "content_hash", "metadata_json"}
            assert payload["text"]
            assert payload["content_hash"]
            assert payload["token_count"] >= 0
