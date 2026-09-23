"""导出 OpenAPI schema（计划 2.3 的「OpenAPI 导出更新」）。

为什么需要
----------
接口变了而 /docs 只在起服务后可见，评审与前端联调都需要一份**可 diff** 的 schema。
把 ``main.app.openapi()`` 落到文件后，「新增了哪些端点、请求体是什么形状」
在评审材料里就是一段 diff，不依赖任何人的口头说明。

用法
----
    python scripts/export_openapi.py
    python scripts/export_openapi.py --output docs/openapi.json

默认输出到 ``reports/rag_ingestion_auth_review/openapi.json``（评审证据目录，
已在 .gitignore 里从 ``reports/*`` 排除）。
端点是否齐全由 ``tests/test_document_upload_api.py::test_openapi_contains_document_endpoints``
断言，本脚本只负责导出，不做判定。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import main  # noqa: E402


DEFAULT_OUTPUT = Path("reports/rag_ingestion_auth_review/openapi.json")


def export_openapi(output: Path) -> Path:
    """把当前应用的 OpenAPI schema 写到 ``output``，返回实际落盘路径。"""

    schema = main.app.openapi()
    target = output if output.is_absolute() else PROJECT_ROOT / output
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def main_cli() -> int:
    parser = argparse.ArgumentParser(description="Export the FastAPI OpenAPI schema to a JSON file.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="输出路径（相对仓库根或绝对路径）")
    args = parser.parse_args()

    target = export_openapi(args.output)
    schema = json.loads(target.read_text(encoding="utf-8"))
    print(f"已导出：{target.relative_to(PROJECT_ROOT).as_posix()}")
    print(f"路径总数：{len(schema.get('paths', {}))}")
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
