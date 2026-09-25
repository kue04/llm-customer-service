"""稀疏路检索：在**授权范围内**用 FTS5 做词法召回。

这是 B 轨混合检索的第二路。它与 :func:`utils.vector_retriever.search_chunk_index`
的关系是「同输入的两种检索方式」而不是「两个独立系统」：

* 同一个 ``manifest``（同一次构建、同一个 ``v{n}/`` 目录）；
* 同一个 :class:`~utils.vector_retriever.ChunkAccessFilter`（服务端构造）；
* 同一个 :class:`~utils.vector_retriever.ChunkHit` 返回类型。

为什么返回类型要复用 ``ChunkHit`` 而不是另造一个：融合层需要把两路结果放进
同一个容器里比较，若两路各返回一套类型，融合代码就得写一层映射 ——
映射一旦漏字段，融合后的命中就会缺少 ``tenant_id`` / ``acl`` 这类**安全字段**，
而缺字段这件事在类型检查之外是静默的。

## 权限过滤为什么不能省

稀疏路是**后加的路**，最容易犯的错误是"先把结果查出来，再在 Python 里过滤"。
那样有两个问题：① 越权内容已经进入进程内存（审计上就是一次泄漏）；
② 排序是在**全库**上做的 —— 授权范围内只有 3 条候选，但 top-10 被无权限的
高分成内容占满，用户拿到 0 条，表现为"查不到"，没有任何报错。

因此这里的做法与稠密路一致：**先在索引查询里把候选压到授权 row_id 集合**，
再做排序取 top_k。实现上用临时表 JOIN，而不是 ``rowid IN (?,?,...)``：

    实测 9229 个 row_id 时，``IN`` 列表参数版耗时 1.60s，临时表 JOIN 0.0037s
    （**约 400 倍**）。原因是后者只编译一次 SQL，前者要为近万个占位符
    逐个绑定参数并做一次 O(n) 的 rowid 归并查找。

## 为什么这里没有 ``min_score``

稠密路的 ``min_score`` 是余弦相似度阈值，有固定含义（[-1,1]，可比）；
FTS5 的 ``bm25()`` 是**无上界的负分**，其量级取决于文档长度分布与语料规模，
跨语料、跨索引版本都不可比。给一个"看起来像阈值"的数字只会造成
"阈值调好了、换了批语料全废"的假象。跨路比较交给**按 rank 融合**
（``utils/hybrid_retriever.py``）—— rank 是无量纲的，这是它存在的理由。
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from config.rag_config import get_rag_config
from services.ingestion.index_manifest import IndexManifest, load_active_manifest
from services.ingestion.sparse_index import (
    ERROR_SPARSE_MISMATCH,
    ERROR_SPARSE_UNAVAILABLE,
    SPARSE_FILENAME,
    SPARSE_META_TABLE,
    SPARSE_TABLE,
    build_match_query,
    declared_sparse_filename,
    sparse_meta_from_manifest,
)
from utils.vector_retriever import (
    CHUNK_INDEX_NAME,
    ERROR_FILTER_REQUIRED,
    ChunkAccessFilter,
    ChunkHit,
    ChunkRetrievalError,
)

logger = logging.getLogger(__name__)

#: 授权候选集的临时表名（每次查询前重建，随连接销毁）
_ALLOWED_TABLE = "allowed_chunk_rows"


def sparse_index_root() -> Path:
    """与稠密索引同一个根目录（同一份指针、同一套版本目录）。"""

    return Path(get_rag_config().faiss_store_dir)


def _open_readonly(path: Path) -> sqlite3.Connection:
    """只读打开稀疏索引。

    ``mode=ro`` 不是洁癖：检索是**读**路径，一旦某天有人在这里写了一句
    ``INSERT``，生效索引就会被就地改动，而 manifest 的校验和不会跟着变 ——
    等于在只读目录里制造了一个不被察觉的写入口。
    """

    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


class SparseIndexUnavailable(ChunkRetrievalError):
    """稀疏索引不可用（文件缺失 / manifest 未声明 / 与 manifest 不匹配）。"""


def load_sparse_index(
    *,
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> tuple[IndexManifest, sqlite3.Connection]:
    """按**当前生效指针**加载稀疏索引；不可用时抛错（不降级成纯稠密）。

    这里的严格是刻意的。混合检索若在稀疏索引缺席时静默退回稠密单路，
    接口仍会报告 ``retrieval_origin=hybrid`` —— 那就是 F1 那类
    「承诺变假」问题：调用方以为自己拿到的是两路融合结果，实际上不是，
    而且从返回内容上完全看不出来。宁可明确失败。
    """

    root_path = Path(root) if root is not None else sparse_index_root()
    try:
        manifest, _pointer, index_file = load_active_manifest(root_path, index_name)
    except Exception as error:  # noqa: BLE001 - 统一翻译成检索侧错误码
        raise SparseIndexUnavailable(
            ERROR_SPARSE_UNAVAILABLE,
            f"没有可用的 chunk 索引，稀疏路无法加载：{error}",
        ) from error

    declared = sparse_meta_from_manifest(manifest)
    if not declared:
        raise SparseIndexUnavailable(
            ERROR_SPARSE_UNAVAILABLE,
            "当前生效索引是纯稠密构建（manifest 未声明稀疏路），不能做混合检索",
            detail={"index_version": manifest.index_version},
        )

    # 目录**从指针返回的索引文件推**，不要自己拼 ``root/index_name/v{n}/``。
    # 初版就是自己拼的，结果少了中间一层 ``chunk_index/``
    # （真实布局是 ``{root}/chunk_index/{index_name}/v{n}/``，
    # 由 ``index_manifest.version_dir`` 定义）。
    # 那次构建本身是成功的 —— 因为构建侧用的是 ``version_dir``，路径天然正确；
    # 只有查询侧拼错了，表现为"索引建好了却加载不了"。
    # 布局知识只应存在一处，这里复用它而不是复制它。
    path = Path(index_file).parent / declared_sparse_filename(manifest)
    if not path.exists():
        raise SparseIndexUnavailable(
            ERROR_SPARSE_UNAVAILABLE,
            f"manifest 声明了稀疏索引但文件不存在：{path}",
            detail={"path": str(path), "index_version": manifest.index_version},
        )

    connection = _open_readonly(path)
    # 打开后立刻核对「文件自述」与「manifest 声明」是否同构。
    # 这一步不是多余的：文件可能存在但**不属于这个 manifest**
    # （上一次构建的残骸、人工替换、备份恢复时拷错目录），
    # 此时 row_id 恰好错位，融合会把 A 的稀疏分派给 B —— 结果整体错配且不报错。
    # 只比对切词算法与 row_id 校验和两个廉价字段即可判定。
    try:
        actual = dict(connection.execute(f"SELECT key, value FROM {SPARSE_META_TABLE}"))
    except sqlite3.OperationalError as error:
        connection.close()
        raise SparseIndexUnavailable(
            ERROR_SPARSE_MISMATCH,
            f"稀疏索引缺少元数据表（文件损坏或非本项目产物）：{error}",
            detail={"path": str(path)},
        ) from error
    for key in ("gram_algorithm", "row_id_checksum", "schema_version"):
        if str(actual.get(key, "")) != str(declared.get(key, "")):
            connection.close()
            raise SparseIndexUnavailable(
                ERROR_SPARSE_MISMATCH,
                f"稀疏索引的 {key} 与 manifest 声明不一致（文件与 manifest 错配）",
                detail={
                    "key": key,
                    "file": actual.get(key),
                    "manifest": declared.get(key),
                    "path": str(path),
                },
            )

    return manifest, connection


def search_sparse_index(
    query: str,
    *,
    access: ChunkAccessFilter,
    top_k: int = 10,
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> list[ChunkHit]:
    """在**授权范围内**做词法召回，返回按相关度升序的 :class:`ChunkHit`。

    ``score`` 字段放的是 ``-bm25()``，即**越大越相关** —— 这样两路 hit
    的 ``score`` 方向一致，融合层不必为某一路特判符号。
    但请注意它**不与余弦相似度可比**，跨路比较一律用 rank。
    """

    if access is None:
        raise ChunkRetrievalError(
            ERROR_FILTER_REQUIRED,
            "稀疏检索必须传入服务端构造的 access filter；缺失 filter 一律拒绝（不默认全库检索）",
        )
    if not isinstance(access, ChunkAccessFilter):
        raise ChunkRetrievalError(
            ERROR_FILTER_REQUIRED,
            f"access filter 类型不对：{type(access).__name__}；必须由服务端构造 ChunkAccessFilter",
        )

    manifest, connection = load_sparse_index(root=root, index_name=index_name)
    try:
        allowed_entries = [
            entry
            for entry in manifest.entries
            if entry.tenant_id == access.tenant_id
            and (access.allowed_chunk_ids is None or entry.chunk_id in access.allowed_chunk_ids)
        ]
        if not allowed_entries:
            # **fail closed**：授权范围内没有候选 → 直接空，绝不退化成全库检索。
            # 与稠密路 search_chunk_index 的同一判断，两路必须一致 ——
            # 否则"哪一路漏了判断"就成了绕过隔离的后门。
            return []

        match = build_match_query(query)
        if not match:
            # 查询里没有任何可索引单元（例如全是标点）→ 空，而不是"不限条件"。
            return []

        connection.execute(f"CREATE TEMP TABLE IF NOT EXISTS {_ALLOWED_TABLE}(row_id INTEGER PRIMARY KEY)")
        connection.execute(f"DELETE FROM {_ALLOWED_TABLE}")
        connection.executemany(
            f"INSERT INTO {_ALLOWED_TABLE}(row_id) VALUES (?)",
            ((int(entry.row_id),) for entry in allowed_entries),
        )

        rows = connection.execute(
            f"SELECT c.rowid, bm25({SPARSE_TABLE}) AS score FROM {SPARSE_TABLE} c "
            f"JOIN {_ALLOWED_TABLE} a ON a.row_id = c.rowid "
            f"WHERE c.grams MATCH ? ORDER BY score LIMIT ?",
            (match, max(int(top_k), 1)),
        ).fetchall()
    except sqlite3.OperationalError as error:
        # FTS5 查询语法/表结构问题：索引坏了必须报出来，不能当成"没命中"
        raise SparseIndexUnavailable(
            ERROR_SPARSE_MISMATCH,
            f"稀疏索引查询失败：{error}",
            detail={"query": query},
        ) from error
    finally:
        connection.close()

    allowed_by_row = {int(entry.row_id): entry for entry in allowed_entries}
    hits: list[ChunkHit] = []
    for row_id, score in rows:
        position = int(row_id)
        entry = allowed_by_row.get(position)
        if entry is None:  # 双保险：与稠密路同样的兜底断言
            logger.warning("稀疏检索返回了授权范围外的 row_id=%s，已丢弃", position)
            continue
        hits.append(
            ChunkHit(
                score=-float(score),
                row_id=entry.row_id,
                chunk_id=entry.chunk_id,
                text=entry.text,
                tenant_id=entry.tenant_id,
                document_id=entry.document_id,
                document_version=entry.document_version,
                document_version_id=entry.document_version_id,
                chunk_type=entry.chunk_type,
                ordinal=entry.ordinal,
                heading_path=entry.heading_path,
                page_start=entry.page_start,
                page_end=entry.page_end,
                acl=entry.acl,
                content_hash=entry.content_hash,
                token_count=entry.token_count,
                source_uri=entry.source_uri,
                filename=entry.filename,
                document_title=entry.document_title,
                source_type=entry.source_type,
                oversize=entry.oversize,
            )
        )
    return hits


def retrieve_sparse_items(
    query: str,
    *,
    access: ChunkAccessFilter,
    limit: int = 3,
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> list[dict]:
    """稀疏路的「条目」形态（形状对齐 ``retrieve_chunk_items``）。"""

    top_k = max(int(limit) * 5, 20)
    hits = search_sparse_index(query, access=access, top_k=top_k, root=root, index_name=index_name)
    items: list[dict] = []
    for rank, hit in enumerate(hits[: max(int(limit), 0)], start=1):
        items.append(
            {
                "rank": rank,
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "document_version": hit.document_version,
                "document_version_id": hit.document_version_id,
                "tenant_id": hit.tenant_id,
                "title": hit.heading_text or hit.document_title or hit.chunk_id,
                "document_title": hit.document_title,
                "chunk_type": hit.chunk_type,
                "heading_path": list(hit.heading_path),
                "page_start": hit.page_start,
                "page_end": hit.page_end,
                "acl": [dict(item) for item in hit.acl],
                "source_uri": hit.source_uri,
                "filename": hit.filename,
                "source_type": hit.source_type,
                "content_hash": hit.content_hash,
                "token_count": hit.token_count,
                "text": hit.text,
                "score": hit.score,
                "retrieval_origin": "chunk-index-sparse",
            }
        )
    return items


def describe_sparse_index(
    *,
    root: str | Path | None = None,
    index_name: str = CHUNK_INDEX_NAME,
) -> dict:
    """给运维/接口看的稀疏索引自述（不抛错，只报告可用性）。"""

    payload: dict = {
        "index_name": index_name,
        "available": False,
        "file": SPARSE_FILENAME,
        "gram_algorithm": "",
        "chunk_count": 0,
        "error": "",
    }
    try:
        manifest, connection = load_sparse_index(root=root, index_name=index_name)
    except ChunkRetrievalError as error:
        payload["error"] = f"{error.error_code}: {error}"
        return payload
    try:
        declared = sparse_meta_from_manifest(manifest)
        payload["available"] = True
        payload["file"] = declared_sparse_filename(manifest)
        payload["gram_algorithm"] = str(declared.get("gram_algorithm") or "")
        payload["chunk_count"] = int(declared.get("chunk_count") or 0)
        payload["index_version"] = manifest.index_version
        payload["sqlite_version"] = sqlite3.sqlite_version
    finally:
        connection.close()
    return payload
