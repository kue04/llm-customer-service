"""稀疏（词法）索引：SQLite FTS5 + 中文 bigram。

## 为什么是 FTS5 + bigram，而不是别的

计划要求 B 轨检索具备「独立的词法召回路」。候选方案与取舍：

| 方案 | 否决理由 |
| --- | --- |
| 现成的 ``calculate_keyword_bonus``（``utils/vector_retriever.py``） | 它是**16 个硬编码词的 ``in`` 子串匹配**，没有分词/词频/IDF，只做加权不做召回 —— 不是一路召回 |
| ``rank_bm25`` / Elasticsearch | 引入新依赖 / 新服务。SQLite 3.49.1 自带 FTS5 + ``bm25()``，零新依赖 |
| FTS5 ``trigram`` tokenizer | **实测对中文二字词直接失效**：trigram 要求查询至少 3 个字符，``MATCH '退款'`` 返回空。中文高频词大量是二字词 |
| FTS5 默认 tokenizer + 整句 | 中文没有空格，整句会被切成一个巨型 token，只有完全相同的串才命中 |

因此：**默认 tokenizer（unicode61）+ 自己切 bigram + OR 查询**。
bigram 把「退款流程」变成 ``退款 款流 流程``，任一命中即入选 ——
召回优先，精度交给融合阶段的权重去压（见 ``utils/hybrid_retriever.py``）。

**降权不是随便定的**：实测等权 RRF 在人工改写的口语提问上会把 R@1 从 0.4000 拉到 0.2667
（因为 OR 语义天然带噪声）。权重扫描见 ``tmp/probe_hybrid_weights.py`` 的输出，
结论落在 ``reports/retrieval_hybrid/``。

## 为什么索引文件与稠密索引同目录

稀疏索引必须与稠密索引**同版本**：融合时的 rank 来自两路，
若一路是 v3 一路是 v4，融合结果没有意义（且是静默的 —— 只是"效果变差"）。
放进同一个 ``v{n}/`` 目录后，``publish_build_directory`` 一次 rename 带动两者，
``switch_pointer`` 一次 ``os.replace`` 切换两者，**回滚也一起回滚**。

## 为什么 ``text`` 只取 chunk 正文、不拼 heading_path

与稠密路保持一致：``build_entries`` 送进 embedding 的就是 ``entry.text``。
若稀疏路额外吃 heading，口语化评测里它会拿到稠密路没有的信息，
两路就不是"同一输入的两种检索方式"了，对比数字失去意义。
（真实系统里给稀疏路加 heading 是合理的优化，但那是**下一步**的事，
且必须重跑评测，不能凭直觉宣称有效。）
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from services.ingestion.index_manifest import (
    IndexManifest,
    IndexManifestError,
    ManifestEntry,
)

# 与 vectors.faiss 同级、同版本目录
SPARSE_FILENAME = "sparse.sqlite"
SPARSE_TABLE = "chunk_grams"
SPARSE_META_TABLE = "sparse_meta"

# 变更算法（切词方式/权重/表结构）必须改这里，否则新旧索引会被当成同构。
SPARSE_SCHEMA_VERSION = "1"
GRAM_ALGORITHM = "unicode61-bigram-v1"
TOKENIZER = "unicode61"

ERROR_SPARSE_UNAVAILABLE = "sparse_index_unavailable"
ERROR_SPARSE_MISMATCH = "sparse_index_mismatch"
ERROR_FTS5_UNAVAILABLE = "fts5_unavailable"

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SPLIT = re.compile(r"[^\w\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")


def tokenize_grams(text: str) -> list[str]:
    """把一段文本切成 FTS5 的索引单元。

    * 含中日韩字符的片段 → **bigram**（``退款流程`` → ``退款``/``款流``/``流程``），
      单字片段原样保留；
    * 其余（英文/数字）→ 整词保留（``rag`` 不切，避免 ``ra``/``ag`` 噪声命中）。

    这是 ``GRAM_ALGORITHM`` 的唯一实现处：构建与查询**必须**调同一个函数，
    否则「索引里的 gram」和「查询里的 gram」不是一套东西，命中率为 0 且不报错。
    """

    out: list[str] = []
    for piece in _SPLIT.split((text or "").lower()):
        if not piece:
            continue
        if _CJK.search(piece):
            if len(piece) == 1:
                out.append(piece)
            else:
                out.extend(piece[i : i + 2] for i in range(len(piece) - 1))
        else:
            out.append(piece)
    return out


def build_match_query(query: str) -> str:
    """把用户查询变成 FTS5 ``MATCH`` 表达式（OR 语义、逐 gram 加引号）。

    为什么必须加引号：FTS5 的裸词会被当成语法（``AND``/``OR``/``NEAR``/``*``
    都是运算符），查询里带一个 ``or`` 就会变成语法错误并**抛异常**。
    引号把它降级成普通短语。
    """

    grams = tokenize_grams(query)
    if not grams:
        return ""
    return " OR ".join(f'"{gram}"' for gram in grams)


def _row_id_checksum(row_ids: Iterable[int]) -> str:
    """``row_id`` 有序集合的精确指纹（用来自证「索引条目 == manifest 条目」）。"""

    payload = ",".join(str(int(x)) for x in row_ids)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_fts5(connection: sqlite3.Connection) -> None:
    """FTS5 缺席时**明确报错**，不静默退化成"没有稀疏路"。"""

    try:
        connection.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {SPARSE_TABLE} USING fts5(grams)")
        connection.execute(f"DROP TABLE IF EXISTS {SPARSE_TABLE}")
    except sqlite3.OperationalError as error:  # pragma: no cover - 依赖 SQLite 编译选项
        raise IndexManifestError(
            ERROR_FTS5_UNAVAILABLE,
            f"当前 SQLite 未启用 FTS5，无法构建稀疏索引：{error}"
            f"（sqlite 版本 {sqlite3.sqlite_version}）",
            detail={"sqlite_version": sqlite3.sqlite_version},
        ) from error


def build_sparse_index(
    directory: str | Path,
    entries: Sequence[ManifestEntry],
) -> dict[str, Any]:
    """在 ``directory`` 下写 ``sparse.sqlite``，返回要放进 manifest ``extra`` 的元数据。

    返回的元数据不是"顺手记一下"：``hybrid_retriever`` 靠它判断
    「manifest 声明的稀疏索引与磁盘上的那一份是否同构」，缺了它就只能靠猜。

    ``row_id`` 直接用作 FTS5 的 ``rowid`` —— 这正是 manifest 坚持
    「row_id 不得再隐式等于 JSONL 行号」之后换来的好处：
    两路索引可以用**同一个整数键**对齐，融合时不需要任何映射表。
    """

    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / SPARSE_FILENAME
    # 重跑时覆盖：残留旧文件会让"构建成功"变成假象
    if path.exists():
        path.unlink()

    connection = sqlite3.connect(str(path))
    try:
        _require_fts5(connection)
        connection.execute(
            f"CREATE VIRTUAL TABLE {SPARSE_TABLE} USING fts5(grams, tokenize='{TOKENIZER}')"
        )
        connection.execute(
            f"CREATE TABLE {SPARSE_META_TABLE}(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.executemany(
            f"INSERT INTO {SPARSE_TABLE}(rowid, grams) VALUES (?, ?)",
            ((int(entry.row_id), " ".join(tokenize_grams(entry.text))) for entry in entries),
        )
        row_ids = sorted(int(entry.row_id) for entry in entries)
        connection.executemany(
            f"INSERT INTO {SPARSE_META_TABLE}(key, value) VALUES (?, ?)",
            [
                ("schema_version", SPARSE_SCHEMA_VERSION),
                ("gram_algorithm", GRAM_ALGORITHM),
                ("tokenizer", TOKENIZER),
                ("chunk_count", str(len(entries))),
                ("row_id_checksum", _row_id_checksum(row_ids)),
                ("row_id_min", str(row_ids[0] if row_ids else -1)),
                ("row_id_max", str(row_ids[-1] if row_ids else -1)),
                # 空正文的 chunk 也会占一行（grams 为空）——**故意保留**，
                # 否则 row_id 集合与 manifest 不等，校验会红。
                ("empty_text_rows", str(sum(1 for e in entries if not tokenize_grams(e.text)))),
            ],
        )
        connection.commit()
    finally:
        connection.close()

    return {
        "file": SPARSE_FILENAME,
        "schema_version": SPARSE_SCHEMA_VERSION,
        "gram_algorithm": GRAM_ALGORITHM,
        "tokenizer": TOKENIZER,
        "chunk_count": len(entries),
        "row_id_checksum": _row_id_checksum(sorted(int(e.row_id) for e in entries)),
    }


def verify_sparse_index(path: str | Path, manifest: IndexManifest) -> int:
    """校验稀疏索引与 manifest **逐条对齐**，返回条目数。

    这里查四项而不是一项，理由是每项漏掉都会产生一类静默损坏：

    * 只查条数 → 少写一条、多写一条但总数对不上最常见，可抓；
    * 只查条数，不查 ``row_id`` 集合 → **条数相同但 id 错位**（例如从 1 开始而不是
      从 manifest 的 row_id 开始）会让融合把 A 的稀疏分判给 B，检索结果整体错配；
    * 不查 ``gram_algorithm`` → 换了切词方式后旧索引仍被当成有效，
      表现为"混合检索突然变差"，没有任何报错。
    """

    target = Path(path)
    if not target.exists():
        raise IndexManifestError(
            ERROR_SPARSE_UNAVAILABLE,
            f"稀疏索引文件不存在：{target}",
            detail={"path": str(target)},
        )

    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        try:
            meta = dict(connection.execute(f"SELECT key, value FROM {SPARSE_META_TABLE}"))
        except sqlite3.OperationalError as error:
            raise IndexManifestError(
                ERROR_SPARSE_MISMATCH,
                f"稀疏索引缺少元数据表（文件损坏或非本模块产物）：{error}",
                detail={"path": str(target)},
            ) from error
        if meta.get("schema_version") != SPARSE_SCHEMA_VERSION:
            raise IndexManifestError(
                ERROR_SPARSE_MISMATCH,
                "稀疏索引 schema 版本与当前实现不一致",
                detail={"index": meta.get("schema_version"), "code": SPARSE_SCHEMA_VERSION},
            )
        if meta.get("gram_algorithm") != GRAM_ALGORITHM:
            raise IndexManifestError(
                ERROR_SPARSE_MISMATCH,
                "稀疏索引的切词算法与当前实现不一致（索引需重建）",
                detail={"index": meta.get("gram_algorithm"), "code": GRAM_ALGORITHM},
            )
        total = int(connection.execute(f"SELECT count(*) FROM {SPARSE_TABLE}").fetchone()[0])
    finally:
        connection.close()

    if total != manifest.chunk_count:
        raise IndexManifestError(
            ERROR_SPARSE_MISMATCH,
            "稀疏索引条目数与 manifest 不一致",
            detail={"sparse_count": total, "manifest_count": manifest.chunk_count},
        )
    expected = _row_id_checksum(sorted(int(e.row_id) for e in manifest.entries))
    if meta.get("row_id_checksum") != expected:
        raise IndexManifestError(
            ERROR_SPARSE_MISMATCH,
            "稀疏索引的 row_id 集合与 manifest 不一致（索引与 manifest 错配）",
            detail={"index": meta.get("row_id_checksum"), "manifest": expected},
        )
    if int(meta.get("chunk_count", -1)) != manifest.chunk_count:
        raise IndexManifestError(
            ERROR_SPARSE_MISMATCH,
            "稀疏索引自述条数与 manifest 不一致",
            detail={"meta": meta.get("chunk_count"), "manifest": manifest.chunk_count},
        )
    return total


def read_sparse_meta(path: str | Path) -> dict[str, str]:
    """读元数据表（供排障 / 接口 ``describe`` 使用），不校验一致性。"""

    target = Path(path)
    connection = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        return dict(connection.execute(f"SELECT key, value FROM {SPARSE_META_TABLE}"))
    except sqlite3.OperationalError as error:
        raise IndexManifestError(
            ERROR_SPARSE_MISMATCH,
            f"无法读取稀疏索引元数据：{error}",
            detail={"path": str(target)},
        ) from error
    finally:
        connection.close()


def sparse_meta_from_manifest(manifest: IndexManifest) -> dict[str, Any]:
    """取出 manifest ``extra`` 里的稀疏索引声明；没有则返回空 dict。

    返回值是判断「这份索引是否带稀疏路」的**唯一**依据。
    ``hybrid_retriever`` 不允许靠"文件是否存在"来猜 —— 文件可能存在但是孤儿
    （上一次构建残留），猜错的后果是拿着 v3 的稀疏分去融合 v4 的稠密分。
    """

    extra = manifest.extra or {}
    declared = extra.get("sparse")
    if isinstance(declared, str):
        # 兼容「只记了文件名」的写法
        try:
            declared = json.loads(declared)
        except json.JSONDecodeError:
            return {}
    return dict(declared) if isinstance(declared, dict) else {}


def declared_sparse_filename(manifest: IndexManifest) -> str:
    return str(sparse_meta_from_manifest(manifest).get("file") or SPARSE_FILENAME)
