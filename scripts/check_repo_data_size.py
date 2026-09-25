"""检查被 Git 跟踪的文件是否超过体积上限。

为什么需要
----------
这个项目的数据全部是合成数据，但 data/ 目录会随知识运营发布、训练数据重建不断增长。
一旦某个 JSONL / 索引 / 模型权重被 commit 进去，仓库体积会不可逆地膨胀（git filter-branch
清理成本远高于预防）。所以把「单个文件不超过 N MB」做成一条可在 CI 里执行的硬规则，
而不是写进 README 靠自觉。

当前状态
--------
仓库中最大的被跟踪文件是 data/messages/takeout_sft_messages_all.jsonl（约 485KB），
没有任何文件超过 1MB，因此本检查当前通过。如果哪天超标：
  1. 优先改用下载脚本 + .gitignore（适合可重新生成的数据）；
  2. 或者用 Git LFS（适合必须版本化且无法生成的二进制）。
无论选哪种，都要同步更新 README 的「数据获取」小节。

用法
----
    python scripts/check_repo_data_size.py
    python scripts/check_repo_data_size.py --max-mb 5
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_MB = 1.0


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [root / name for name in names if name]


def oversized_files(root: Path, max_bytes: int) -> list[tuple[Path, int]]:
    offenders = []
    for path in tracked_files(root):
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > max_bytes:
            offenders.append((path, size))
    return offenders


def human_readable(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if any git-tracked file exceeds the size limit.")
    parser.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="Per-file size limit in MB.")
    args = parser.parse_args()

    max_bytes = int(args.max_mb * 1024 * 1024)
    offenders = oversized_files(PROJECT_ROOT, max_bytes)

    print(f"检查目录：{PROJECT_ROOT}")
    print(f"单文件上限：{args.max_mb} MB")

    if not offenders:
        print("结果：通过，没有超标文件。")
        return 0

    print(f"结果：失败，{len(offenders)} 个文件超过 {args.max_mb} MB：")
    for path, size in sorted(offenders, key=lambda item: item[1], reverse=True):
        relative = path.relative_to(PROJECT_ROOT)
        print(f"  {relative}  {human_readable(size)}")
    print()
    print("处理方式：改用下载/生成脚本 + .gitignore，或使用 Git LFS，并同步更新 README 的数据获取说明。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
