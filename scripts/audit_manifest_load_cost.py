"""F6 前置取证：把「检索延迟」拆到**组件级**，验证瓶颈是否真的是「每次重读 manifest」。

## 为什么要先量这个（踩坑 D28 的直接产物）

登记里写的根因是「``load_active_manifest`` 无缓存 → 每次检索重读 19.0MB manifest」。
但 B17 的教训是：**登记里的"修法方向"本身也可能过期甚至方向反了**，
所以动手改缓存之前，必须先把这句话变成一个可复现的数字 ——
如果真正吃掉时间的是别的环节（``faiss.read_index`` / 句向量推理 / 进程启动），
那么给 manifest 加缓存就是**一次无效优化**，应当如实报告并停下讨论。

## 量什么

1. **组件级**（各自独立计时，互不包含）：
   ``read_pointer`` / ``read_manifest`` / ``load_active_manifest`` /
   ``faiss.read_index`` / ``load_chunk_index`` / ``load_sparse_index``；
2. **端到端**：同一 query 集上 dense / sparse / hybrid 的单条耗时
   （走生产代码 ``search_hybrid_chunks``，与 ``evaluate_hybrid_retrieval.py`` 同路径）；
3. **每次请求的 manifest 读取次数**：用计数包装器实测，不靠读代码猜（B20 的教训：别让
   校验的两边来自同一个来源）。

## 一个刻意的口径（别把它读成"磁盘慢"）

19MB 文件在**同一进程内连读**时，第二次起由操作系统页缓存供给，
所以"平均 130ms"里**主要不是磁盘 I/O，而是 JSON 反序列化 + 9229 个对象构造**。
首轮（冷读）单独记一列 —— 它才是部署后首个请求的真实成本。

## 用法

```bash
RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+ \\
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \\
./venv/Scripts/python.exe scripts/audit_manifest_load_cost.py --label before
```

``--label`` 用于把"状态"编进证据文件名（踩坑 B22），**默认拒绝覆盖**已存在的证据文件。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.ingestion.index_manifest import (  # noqa: E402
    load_active_manifest,
    read_manifest,
    read_pointer,
)
from services.ingestion.pipeline import (  # noqa: E402
    default_embedder,
    default_embedding_model,
    default_index_root,
)
from utils.hybrid_retriever import search_hybrid_chunks  # noqa: E402
from utils.sparse_retriever import load_sparse_index  # noqa: E402
from utils.vector_retriever import (  # noqa: E402
    CHUNK_INDEX_NAME,
    ChunkAccessFilter,
    load_chunk_index,
)

REPORT_DIR = PROJECT_ROOT / "reports" / "rag_ingestion_auth_review"
GOLD_PATH = PROJECT_ROOT / "data" / "retrieval_gold_cases.jsonl"
COLLOQUIAL_PATH = PROJECT_ROOT / "data" / "retrieval_colloquial_cases.jsonl"
MODES = ("dense", "sparse", "hybrid")


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class Timer:
    """记录一组耗时（毫秒），首轮单独留一份 —— 它是冷读成本。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.samples: list[float] = []

    def run(self, func: Callable[[], Any], repeat: int) -> None:
        for _ in range(repeat):
            started = time.perf_counter()
            func()
            self.samples.append((time.perf_counter() - started) * 1000)

    def summary(self) -> dict[str, Any]:
        if not self.samples:
            return {"name": self.name, "n": 0}
        ordered = sorted(self.samples)
        p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        return {
            "name": self.name,
            "n": len(self.samples),
            "first_ms": self.samples[0],
            "mean_ms": statistics.mean(self.samples),
            "median_ms": statistics.median(self.samples),
            "p95_ms": ordered[p95_index],
            "max_ms": max(self.samples),
        }


def instrument_manifest_reads() -> dict[str, int]:
    """用计数包装器实测「每次请求读几次 manifest」。"""

    counter = {"count": 0}
    real = load_active_manifest

    def counting(*args: Any, **kwargs: Any):
        counter["count"] += 1
        return real(*args, **kwargs)

    import utils.sparse_retriever as sparse_module
    import utils.vector_retriever as vector_module

    vector_module.load_active_manifest = counting  # type: ignore[assignment]
    sparse_module.load_active_manifest = counting  # type: ignore[assignment]
    return counter


def restore_manifest_reads() -> None:
    import utils.sparse_retriever as sparse_module
    import utils.vector_retriever as vector_module

    vector_module.load_active_manifest = load_active_manifest  # type: ignore[assignment]
    sparse_module.load_active_manifest = load_active_manifest  # type: ignore[assignment]


def measure_components(root: Path, index_name: str, repeat: int) -> list[dict[str, Any]]:
    manifest_path = None
    pointer = read_pointer(root, index_name)
    manifest_path = root / pointer.manifest_path
    index_file = root / pointer.index_path

    faiss_module = __import__("faiss")

    timers = {
        "read_pointer": Timer("read_pointer"),
        "read_manifest": Timer("read_manifest(仅反序列化)"),
        "load_active_manifest": Timer("load_active_manifest(指针+反序列化+指纹校验)"),
        "faiss.read_index": Timer("faiss.read_index(18.9MB 向量)"),
        "load_chunk_index": Timer("load_chunk_index(manifest+faiss，稠密路真实成本)"),
        "load_sparse_index": Timer("load_sparse_index(manifest+sqlite)"),
    }
    timers["read_pointer"].run(lambda: read_pointer(root, index_name), repeat)
    timers["read_manifest"].run(lambda: read_manifest(manifest_path), repeat)
    timers["load_active_manifest"].run(lambda: load_active_manifest(root, index_name), repeat)
    timers["faiss.read_index"].run(lambda: faiss_module.read_index(str(index_file)), repeat)
    timers["load_chunk_index"].run(lambda: load_chunk_index(root=root, index_name=index_name), repeat)
    timers["load_sparse_index"].run(lambda: load_sparse_index(root=root, index_name=index_name), repeat)

    sizes = {
        "manifest_bytes": manifest_path.stat().st_size,
        "vectors_bytes": index_file.stat().st_size,
    }
    sparse_file = index_file.parent / "sparse.sqlite"
    if sparse_file.exists():
        sizes["sparse_bytes"] = sparse_file.stat().st_size
    return [t.summary() for t in timers.values()], sizes, manifest_path


def measure_requests(root: Path, index_name: str, access: ChunkAccessFilter, embedding_model: str,
                     cases: list[dict]) -> dict[str, Any]:
    """每次请求读几次 manifest（实测），以及端到端单条耗时。"""

    counts: dict[str, int] = {}
    latencies: dict[str, list[float]] = {mode: [] for mode in MODES}
    counter = instrument_manifest_reads()
    try:
        for mode in MODES:
            counter["count"] = 0
            started = time.perf_counter()
            search_hybrid_chunks(
                cases[0]["query"],
                access=access,
                top_k=10,
                mode=mode,
                embedder=default_embedder,
                embedding_model=embedding_model,
                root=root,
                index_name=index_name,
            )
            counts[mode] = counter["count"]
            latencies[mode].append((time.perf_counter() - started) * 1000)

        for case in cases[1:]:
            for mode in MODES:
                started = time.perf_counter()
                search_hybrid_chunks(
                    case["query"],
                    access=access,
                    top_k=10,
                    mode=mode,
                    embedder=default_embedder,
                    embedding_model=embedding_model,
                    root=root,
                    index_name=index_name,
                )
                latencies[mode].append((time.perf_counter() - started) * 1000)
    finally:
        restore_manifest_reads()

    summary: dict[str, Any] = {"manifest_loads_per_request": counts, "latency": {}}
    for mode in MODES:
        samples = latencies[mode]
        ordered = sorted(samples)
        p95_index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
        summary["latency"][mode] = {
            "n": len(samples),
            "mean_ms": statistics.mean(samples),
            "median_ms": statistics.median(samples),
            "p95_ms": ordered[p95_index],
            "max_ms": max(samples),
        }
    return summary


def build_text(label: str, components: list[dict], sizes: dict, request_stats: dict) -> str:
    lines: list[str] = []
    lines.append("=" * 88)
    lines.append(f"F6 前置取证：检索延迟的组件级拆分（状态：{label}）")
    lines.append(f"日期：{time.strftime('%Y-%m-%d')}    生成时间：{time.strftime('%Y-%m-%dT%H:%M:%S')}")
    lines.append("=" * 88)
    lines.append("")
    lines.append("索引文件体积")
    lines.append(f"  index_manifest.json : {sizes['manifest_bytes']:,} 字节")
    lines.append(f"  vectors.faiss       : {sizes['vectors_bytes']:,} 字节")
    if "sparse_bytes" in sizes:
        lines.append(f"  sparse.sqlite       : {sizes['sparse_bytes']:,} 字节")
    lines.append("")
    lines.append("组件级耗时（毫秒，同进程连读 —— 第 2 次起由 OS 页缓存供给）")
    lines.append("-" * 88)
    lines.append(f"{'组件':<44}{'first':>10}{'mean':>10}{'median':>10}{'p95':>10}{'max':>10}")
    for item in components:
        if not item.get("n"):
            continue
        lines.append(
            f"{item['name']:<44}{item['first_ms']:>10.2f}{item['mean_ms']:>10.2f}"
            f"{item['median_ms']:>10.2f}{item['p95_ms']:>10.2f}{item['max_ms']:>10.2f}"
        )
    lines.append("")
    lines.append("★ 口径：first = 冷读（部署后第一个请求的真实成本）；mean/median = 稳态。")
    lines.append("  19MB 文件连读时缺的不是磁盘带宽，而是 JSON 反序列化 + 9229 个对象构造 ——")
    lines.append("  所以「加缓存省掉的是 CPU/分配，不是 I/O」这个判断必须靠数字说话。")
    lines.append("")
    lines.append("每次请求的 manifest 读取次数（实测，非读代码推断）")
    lines.append("-" * 88)
    for mode in MODES:
        lines.append(f"  {mode:<8} {request_stats['manifest_loads_per_request'][mode]} 次")
    lines.append("")
    lines.append("端到端单条检索耗时（毫秒，走生产代码 search_hybrid_chunks）")
    lines.append("-" * 88)
    lines.append(f"{'mode':<10}{'n':>6}{'mean':>10}{'median':>10}{'p95':>10}{'max':>10}")
    for mode in MODES:
        item = request_stats["latency"][mode]
        lines.append(
            f"{mode:<10}{item['n']:>6}{item['mean_ms']:>10.1f}{item['median_ms']:>10.1f}"
            f"{item['p95_ms']:>10.1f}{item['max_ms']:>10.1f}"
        )
    dense_mean = request_stats["latency"]["dense"]["mean_ms"]
    sparse_mean = request_stats["latency"]["sparse"]["mean_ms"]
    hybrid_mean = request_stats["latency"]["hybrid"]["mean_ms"]
    lines.append("")
    lines.append(
        f"加法关系核对：dense + sparse = {dense_mean + sparse_mean:.1f} ms "
        f"vs hybrid {hybrid_mean:.1f} ms（差 {hybrid_mean - dense_mean - sparse_mean:+.1f} ms）"
    )
    lines.append("  若两者接近，说明两路在**各自重复支付同一笔加载成本**（F6 的结构性证据）。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="F6 前置取证：检索延迟组件级拆分")
    parser.add_argument("--label", required=True, help="状态标签，编进证据文件名（如 before / after）")
    parser.add_argument("--tenant-id", default="tenant-dev")
    parser.add_argument("--repeat", type=int, default=15, help="组件级重复次数")
    parser.add_argument("--query-limit", type=int, default=30, help="端到端用的 query 条数（0 = 全量）")
    args = parser.parse_args()

    json_path = REPORT_DIR / f"F6_manifest_load_cost_{args.label}_{time.strftime('%Y%m%d')}.json"
    txt_path = REPORT_DIR / f"F6_manifest_load_cost_{args.label}_{time.strftime('%Y%m%d')}.txt"
    for path in (json_path, txt_path):
        if path.exists():
            print(
                f"证据文件已存在，拒绝覆盖（踩坑 B22）：{path}\n"
                f"换一个 --label，或先移走旧文件。",
                file=sys.stderr,
            )
            return 2

    root = default_index_root()
    embedding_model = default_embedding_model()
    access = ChunkAccessFilter(tenant_id=args.tenant_id)
    cases = load_jsonl(GOLD_PATH)
    if args.query_limit and args.query_limit < len(cases):
        cases = cases[: args.query_limit]

    components, sizes, _manifest_path = measure_components(root, CHUNK_INDEX_NAME, args.repeat)
    request_stats = measure_requests(root, CHUNK_INDEX_NAME, access, embedding_model, cases)

    payload = {
        "label": args.label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "index_name": CHUNK_INDEX_NAME,
        "query_count": len(cases),
        "repeat": args.repeat,
        "sizes": sizes,
        "components": components,
        "requests": request_stats,
    }
    text = build_text(args.label, components, sizes, request_stats)

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    txt_path.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n证据已落盘：{json_path.name} / {txt_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
