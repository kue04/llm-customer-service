"""批量导入语料文档到 ingestion 链路（内部路径）。

为什么需要这个脚本
------------------
`routers/documents.py` 的上传接口一次只接一个文件。灌 179 个文件要发 179 次带 JWT 的
HTTP 请求 —— 不是不能做，但不是「把一批语料放进库里」该有的形态。本脚本走**内部路径**：
直接调仓储层建 document / job，再用流水线推进到 published，最后统一重建一次索引。

它不绕过任何业务约束，只是省掉 HTTP 与鉴权这一层：

1. **对象 key 必须用 `object_store.build_object_key`**。流水线 `stored` 阶段会用
   同一个函数反解 key 去取字节；自己拼 key 会让它抛 `object_missing`，
   而那个错误看起来像「文件丢了」，实际是 key 拼错 —— 极具误导性。
2. **不建版本、不建 chunk**。版本由流水线在 `parsed` 阶段创建（[D-6] 已定论），
   导入器抢着建会撞 `uq(tenant_id, content_hash)`。
3. **幂等靠内容 hash**。同一内容再次导入会在 `parsed` 阶段判为 `duplicate`
   并复用已有版本，不产生第二份有效数据。

索引为什么只在最后重建一次
--------------------------
`rebuild_index` 是**全量重建**（从 `document_chunks` 读全部 chunk 重算向量）。
N 份文档逐个跑流水线会触发 N 次全量重建，embedding 计算量是 O(N²)；
179 个文件足以让这件事从「等一会儿」变成「等不动」。
因此这里注入一个**延迟 runner** 占住 `indexed` 阶段，让所有文档先把
`parsed → normalized → chunked → persisted` 走完，最后统一重建。

用法
----
    # 先建开发租户（只需一次）
    ./venv/Scripts/python.exe scripts/seed_dev_tenant.py

    # 导入
    ./venv/Scripts/python.exe scripts/bulk_import_documents.py --dry-run
    ./venv/Scripts/python.exe scripts/bulk_import_documents.py
    ./venv/Scripts/python.exe scripts/bulk_import_documents.py --formats pdf,docx
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from services.ingestion import index_builder, parser_registry, repository
from services.ingestion.content_sniff import matches_declared_type, sniff_content_type
from services.ingestion.db import session_scope
from services.ingestion.index_builder import IndexBuildResult
from services.ingestion.index_manifest import DEFAULT_INDEX_NAME
from services.ingestion.object_store import (
    build_object_key,
    build_object_store,
    logical_source_uri,
)
from services.ingestion.pipeline import (
    IngestionPipeline,
    default_embedder,
    default_embedding_model,
    default_index_root,
)

logger = logging.getLogger("bulk_import")

DEFAULT_MANIFEST = PROJECT_ROOT / "data" / "corpus" / "manifest.jsonl"
SUPPORTED_FORMATS = ("pdf", "docx", "html", "md", "txt")


class DeferredIndexRunner:
    """占位索引 runner：不建索引，只记账。

    流水线的 `indexed` 阶段会拿它的返回值当作构建结果。返回 `skipped=True`
    是流水线**自身支持**的正常路径（「空文档跳过索引」走的就是它），
    因此不会把任务标成失败，也不会写坏任何状态。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.deferred_chunks = 0

    def __call__(self, session, **kwargs: Any) -> IndexBuildResult:
        self.calls += 1
        return IndexBuildResult(
            index_name=str(kwargs.get("index_name") or DEFAULT_INDEX_NAME),
            tenant_id=str(kwargs.get("tenant_id") or ""),
            embedding_model=str(kwargs.get("embedding_model") or ""),
            skipped=True,
            skip_reason="deferred_to_batch_end",
        )


def load_manifest(path: Path, formats: tuple[str, ...]) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if formats and record.get("format") not in formats:
                continue
            rows.append(record)
    return rows


def import_one(
    session,
    *,
    store,
    pipeline: IngestionPipeline,
    tenant_id: str,
    knowledge_base_id: str,
    file_path: Path,
    title: str,
) -> dict[str, Any]:
    """导入单个文件：建文档 → 存对象 → 建任务 → 推进流水线。"""

    data = file_path.read_bytes()
    filename = file_path.name

    try:
        source_type = parser_registry.detect_source_type(filename, None)
    except Exception as error:
        return {"status": "rejected", "reason": f"unsupported:{type(error).__name__}", "file": filename}

    if not matches_declared_type(source_type, data):
        return {"status": "rejected", "reason": f"content_mismatch:{source_type}", "file": filename}

    source_uri = logical_source_uri(filename)
    document = repository.get_document_by_source_uri(session, tenant_id, source_uri)
    reused = document is not None
    if document is None:
        document = repository.create_document(
            session,
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            source_uri=source_uri,
            source_type=source_type,
            title=title[:200],
        )

    object_key = build_object_key(tenant_id, document.id, filename)
    store.put(object_key, data, content_type=sniff_content_type(data))

    job = repository.create_ingestion_job(
        session,
        tenant_id=tenant_id,
        document_id=document.id,
        status="pending",
        stage="received",
    )
    # 先提交再推进：流水线的 stored 阶段要确认对象存在，
    # 而 job/document 行必须先落库（与上传接口同一顺序，见 routers/documents.py）
    session.commit()

    outcome = pipeline.process_job(tenant_id=tenant_id, job_id=job.id)
    return {
        "status": "ok" if outcome.succeeded else "failed",
        "file": filename,
        "doc_id": document.id,
        "reused_document": reused,
        "duplicate": outcome.duplicate,
        "chunks": outcome.chunk_count,
        "stage": outcome.stage,
        "error_code": outcome.error_code,
        "error": outcome.error_message[:160],
        "warnings": len(outcome.warnings),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="批量导入语料文档")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--tenant-id", default="tenant-dev")
    parser.add_argument("--kb-slug", default="takeout-policy")
    parser.add_argument("--formats", default="", help="逗号分隔，默认全部")
    parser.add_argument("--limit", type=int, default=0, help="最多导入多少个文件（0=不限）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不写库")
    parser.add_argument("--sleep", type=float, default=0.0, help="每个文件之间的间隔")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    formats = tuple(item.strip() for item in args.formats.split(",") if item.strip())
    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"清单不存在：{manifest_path}\n先运行 scripts/build_corpus_documents.py", file=sys.stderr)
        return 2

    records = load_manifest(manifest_path, formats)
    missing = [item for item in records if not (PROJECT_ROOT / item["path"]).exists()]
    records = [item for item in records if (PROJECT_ROOT / item["path"]).exists()]
    if args.limit:
        records = records[: args.limit]

    print(f"计划导入 {len(records)} 个文件" + (f"（{len(missing)} 个文件缺失已跳过）" if missing else ""))
    print(f"  格式分布：{dict(Counter(item['format'] for item in records))}")
    print(f"  租户：{args.tenant_id}  知识库 slug：{args.kb_slug}")
    if args.dry_run:
        for item in records[:12]:
            print(f"    - [{item['format']:<4}] {item['path']}")
        if len(records) > 12:
            print(f"    ... 其余 {len(records) - 12} 个")
        return 0

    store = build_object_store()
    deferred = DeferredIndexRunner()
    results: list[dict] = []
    started = time.perf_counter()

    with session_scope() as session:
        knowledge_base = repository.get_knowledge_base_by_slug(session, args.tenant_id, args.kb_slug)
        if knowledge_base is None:
            print(
                f"知识库不存在：tenant={args.tenant_id} slug={args.kb_slug}\n"
                f"先运行：./venv/Scripts/python.exe scripts/seed_dev_tenant.py --tenant-id {args.tenant_id} --kb-slug {args.kb_slug}",
                file=sys.stderr,
            )
            return 2

        pipeline = IngestionPipeline(
            session=session,
            store=store,
            index_runner=deferred,
        )

        for index, item in enumerate(records, start=1):
            file_path = PROJECT_ROOT / item["path"]
            try:
                result = import_one(
                    session,
                    store=store,
                    pipeline=pipeline,
                    tenant_id=args.tenant_id,
                    knowledge_base_id=knowledge_base.id,
                    file_path=file_path,
                    title=item.get("title") or file_path.stem,
                )
            except Exception as error:  # noqa: BLE001 - 单个文件失败不该中断整批
                session.rollback()
                result = {
                    "status": "error",
                    "file": file_path.name,
                    "reason": f"{type(error).__name__}: {error}"[:200],
                }
            results.append(result)
            if index % 20 == 0 or index == len(records):
                ok = sum(1 for item in results if item["status"] == "ok")
                print(f"  ... {index}/{len(records)} 完成（成功 {ok}）", file=sys.stderr)
            if args.sleep:
                time.sleep(args.sleep)

    elapsed = time.perf_counter() - started
    ok = [item for item in results if item["status"] == "ok"]
    dup = [item for item in ok if item.get("duplicate")]
    failed = [item for item in results if item["status"] in ("failed", "error", "rejected")]
    chunks = sum(int(item.get("chunks") or 0) for item in ok)

    print(f"\n导入结束（{elapsed:.1f}s）")
    print(f"  成功 {len(ok)}（其中判重 {len(dup)}）· 失败 {len(failed)} · 产出 chunk {chunks}")
    print(f"  延迟的索引构建次数：{deferred.calls}")
    if failed:
        print("  失败明细（前 10）：")
        for item in failed[:10]:
            print(f"    - {item.get('file')}: {item.get('error_code') or item.get('reason')} {item.get('error', '')}")

    if not ok:
        print("\n没有任何文档成功入库，跳过索引重建。", file=sys.stderr)
        return 1

    # 统一重建索引（此时才真正算 embedding）
    print("\n开始重建索引（全量 embedding）...", file=sys.stderr)
    with session_scope() as session:
        try:
            build = index_builder.rebuild_index(
                session,
                tenant_id=args.tenant_id,
                root=default_index_root(),
                embedder=default_embedder,
                embedding_model=default_embedding_model(),
            )
        except Exception as error:  # noqa: BLE001
            print(f"索引重建失败：{type(error).__name__}: {error}", file=sys.stderr)
            return 1

    if build.skipped:
        print(f"索引未构建（{build.skip_reason}）", file=sys.stderr)
        return 1

    print(
        f"索引已切换：版本 v{build.index_version} · {build.chunk_count} 个 chunk · "
        f"{build.embedding_model} ({build.embedding_dimension} 维)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
