"""只重建 chunk 索引（稠密 + 稀疏），不重新解析、不重新入库。

## 什么时候用它

`rebuild_index` 是**从 `document_chunks` 表派生**索引：真源是数据库，
索引只是查询加速结构。因此凡是「只改索引、不改内容」的变更，都该走这个脚本：

* 改了稀疏路的切词算法（``GRAM_ALGORITHM``）→ **必须**重建，
  否则旧索引里的 gram 与新查询的 gram 不是一套东西，命中率塌到 0 且不报错；
* 换了 embedding 模型 / 维度 → 必须重建（向量空间变了）；
* 手工修过数据库里的 chunk 正文 → 重建让索引跟上；
* 想验证「索引构建 + 校验 + 原子切换」这条链路本身是否完好。

而 ``scripts/bulk_import_documents.py`` 是**从原始文件**开始的全链路
（解析 → 切分 → 入库 → 建索引），代价是分钟级。只改索引时用它属于白跑，
且会顺带改动入库时间戳等无关状态。

## 用法

```bash
RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+ \\
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
./venv/Scripts/python.exe scripts/rebuild_chunk_index.py

# 只建稠密路（用于对照"混合检索到底带来了什么"）
./venv/Scripts/python.exe scripts/rebuild_chunk_index.py --no-sparse
```

成功时打印版本号、条目数、两路的可用性；失败时**非零退出**并把失败原因打全
（构建失败会留下 ``index_builds`` 里的 ``failed`` 记录，指针不动 —— 当前索引不受影响）。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.ingestion import index_builder
from services.ingestion.db import session_scope
from services.ingestion.pipeline import (
    default_embedder,
    default_embedding_model,
    default_index_root,
)

logger = logging.getLogger("rebuild_chunk_index")


def main() -> int:
    parser = argparse.ArgumentParser(description="只重建 chunk 索引（稠密 + 稀疏）")
    parser.add_argument("--tenant-id", default="tenant-dev", help="留痕用的租户（索引是全局一份）")
    parser.add_argument(
        "--no-sparse",
        action="store_true",
        help="只建稠密路（构建出的索引 manifest 不含稀疏声明，混合模式会显式拒绝）",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    root = default_index_root()
    model = default_embedding_model()
    print(f"索引根目录：{root}")
    print(f"embedding 模型：{model}")
    print(f"稀疏路：{'关闭（--no-sparse）' if args.no_sparse else '开启'}")
    print("开始重建（全量 embedding）...", file=sys.stderr)

    started = time.perf_counter()
    with session_scope() as session:
        try:
            build = index_builder.rebuild_index(
                session,
                tenant_id=args.tenant_id,
                root=root,
                embedder=default_embedder,
                embedding_model=model,
                sparse=not args.no_sparse,
            )
        except Exception as error:  # noqa: BLE001 - 脚本边界，要打全信息
            print(f"\n索引重建失败：{type(error).__name__}: {error}", file=sys.stderr)
            detail = getattr(error, "detail", None)
            if detail:
                print(f"  detail: {detail}", file=sys.stderr)
            return 1

    elapsed = time.perf_counter() - started

    if build.skipped:
        print(f"\n索引未构建（{build.skip_reason}）：库里没有可索引的 chunk。", file=sys.stderr)
        return 1

    print(f"\n重建完成（{elapsed:.1f}s）")
    print(
        f"  生效版本 v{build.index_version} · {build.chunk_count} 个 chunk · "
        f"{build.embedding_model} ({build.embedding_dimension} 维)"
    )
    print(f"  稀疏路：{'已构建' if build.sparse else '未构建'}")
    print(f"  manifest：{build.manifest_uri}")

    # 立刻用检索层的入口复核一次可用性 —— 「构建成功」与「检索层真的能加载」
    # 是两件事：manifest 声明了但文件路径算错，构建照样成功。
    from utils.sparse_retriever import describe_sparse_index

    if build.sparse:
        described = describe_sparse_index(root=root)
        ok = bool(described.get("available"))
        print(
            f"  检索层复核：{'可加载' if ok else '不可加载'}"
            f"{'' if ok else ' → ' + str(described.get('error'))}"
        )
        if not ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
