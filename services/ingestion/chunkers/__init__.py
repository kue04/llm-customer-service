"""结构化切分器包（计划 3.2）。

模块划分
--------
- ``errors``    结构化错误（``error_code`` 可直落 ``ingestion_jobs.error_code``）
- ``tokenizer`` token 计数抽象 + 确定性实现（固定 mock，测试不下载模型）
- ``models``    ``Chunk`` / ``ChunkContext`` / ``AclEntry`` / 统计与结果对象
- ``cleaner``   块级清洗（空白、页码行、重复页眉页脚、完全重复段落）
- ``chunker``   主流程：分组 → 成单元 → 装箱 → parent/child → 元数据

**守卫约定（与 ``parsers`` 同源，由测试断言而不是靠纪律）**

1. 本包所有模块**不得**依赖数据库：源码里不允许出现 ``sqlalchemy`` /
   ``services.ingestion.repository`` / ``services.ingestion.db`` /
   ``services.ingestion.models``（见 ``test_chunker_modules_do_not_depend_on_database``）；
2. 切分入口**不得**接收裸的 ``tenant_id`` 参数：归属信息只能通过
   :class:`~.models.ChunkContext` 传入（见 ``test_chunker_does_not_accept_bare_tenant_id``）。

第 2 条不是「不许传租户信息」，而是「不许把租户当成一个可随手加进来的散装参数」——
计划 3.2 要求 chunk 输出必须带 ``tenant_id`` / ``acl``，
所以租户信息**必须**进得来，但它只能从一个不可变的上下文对象进，
这样切分器保持纯函数性质，测试里也永远不需要构造数据库会话。
"""

from __future__ import annotations

from .chunker import (
    CHUNKER_VERSION,
    PREFIX_SEPARATOR,
    UNIT_CODE,
    UNIT_LIST,
    UNIT_PARAGRAPH,
    UNIT_QUOTE,
    UNIT_TABLE,
    Unit,
    chunk_document,
)
from .cleaner import CleanCounters, CleanResult, clean_blocks, normalize_text
from .errors import (
    ERROR_CODES,
    ERROR_INTERNAL,
    ERROR_INVALID_CONTEXT,
    ERROR_UNKNOWN_TOKENIZER,
    ChunkingError,
)
from .models import (
    ACL_PERMISSIONS,
    ACL_SUBJECT_TYPES,
    CHUNK_TYPES,
    CHUNK_TYPE_CHILD,
    CHUNK_TYPE_PARENT,
    HEADING_SEPARATOR,
    AclEntry,
    Chunk,
    ChunkContext,
    ChunkingResult,
    ChunkingStats,
    hash_text,
    make_chunk_id,
    normalize_acl,
)
from .tokenizer import (
    TOKEN_COUNTERS,
    CharTokenCounter,
    HeuristicTokenCounter,
    TokenCounter,
    WordTokenCounter,
    get_token_counter,
    register_token_counter,
    registered_tokenizer_ids,
)

__all__ = [
    "ACL_PERMISSIONS",
    "ACL_SUBJECT_TYPES",
    "CHUNKER_VERSION",
    "CHUNK_TYPES",
    "CHUNK_TYPE_CHILD",
    "CHUNK_TYPE_PARENT",
    "ERROR_CODES",
    "ERROR_INTERNAL",
    "ERROR_INVALID_CONTEXT",
    "ERROR_UNKNOWN_TOKENIZER",
    "HEADING_SEPARATOR",
    "PREFIX_SEPARATOR",
    "TOKEN_COUNTERS",
    "UNIT_CODE",
    "UNIT_LIST",
    "UNIT_PARAGRAPH",
    "UNIT_QUOTE",
    "UNIT_TABLE",
    "AclEntry",
    "CharTokenCounter",
    "Chunk",
    "ChunkContext",
    "ChunkingError",
    "ChunkingResult",
    "ChunkingStats",
    "CleanCounters",
    "CleanResult",
    "HeuristicTokenCounter",
    "TokenCounter",
    "Unit",
    "WordTokenCounter",
    "chunk_document",
    "clean_blocks",
    "get_token_counter",
    "hash_text",
    "make_chunk_id",
    "normalize_acl",
    "normalize_text",
    "register_token_counter",
    "registered_tokenizer_ids",
]
