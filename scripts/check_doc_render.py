"""交付文档的渲染风险自检（纯标准库，不依赖第三方包）。

为什么需要这个脚本
------------------
本仓库多次出现「源文件看着完全正常、渲染后内容消失」的静默故障：
**列表项内带缩进的围栏块**会被 Python-Markdown 吞成行内代码（踩坑 D13）。

它危险的地方在于同时骗过两道人工防线：

1. 在编辑器里看 —— 围栏成对、缩进整齐，完全正常；
2. 只统计围栏**总数** —— 是偶数，同样放行。

更麻烦的是：这个坑**记进踩坑文件之后仍然复发**（2026-09-23 在同一份文件里
又写错一次，见 D13 补记）。所以判断标准不能靠记忆，必须机械检查。

检查项
------
1. **围栏缩进**：行首有空白的围栏 —— 会被吞（最危险的一项）；
2. **围栏成对**：奇数个围栏说明有未闭合，其后整段会被当作代码吞掉；
3. **表格列数一致**：同一表格内每行的 `|` 数应相同。

用法
----
    python scripts/check_doc_render.py                  # 检查仓库内交付文档
    python scripts/check_doc_render.py docs/foo.md ...  # 只查指定文件

退出码：0 = 全部通过；1 = 存在问题。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FENCE = re.compile(r"^(\s*)```")


def default_targets() -> list[Path]:
    """默认检查范围：仓库根 README + docs/ 下全部 Markdown。"""
    readme = REPO_ROOT / "README.md"
    targets = [readme] if readme.exists() else []
    targets += sorted((REPO_ROOT / "docs").rglob("*.md"))
    return targets


def check_file(path: Path) -> list[str]:
    """返回问题描述列表；空列表 = 通过。"""
    lines = path.read_text(encoding="utf-8").split("\n")
    problems: list[str] = []

    # ---- 1) 围栏缩进 + 2) 围栏成对
    outer_open_at: int | None = None
    for lineno, line in enumerate(lines, 1):
        matched = FENCE.match(line)
        if not matched:
            continue
        indent = matched.group(1)
        if indent:
            problems.append(
                f"第 {lineno} 行：围栏前有 {len(indent)} 个空白 —— "
                "在列表项内会被渲染成行内代码，代码内容静默消失。"
                "修法：把围栏与代码内容一起移到行首（顶格）。"
            )
        outer_open_at = None if outer_open_at else lineno
    if outer_open_at is not None:
        problems.append(
            f"第 {outer_open_at} 行的围栏没有闭合 —— 其后全部内容会被当作代码吞掉。"
        )

    # ---- 3) 表格列数一致
    group: list[str] = []
    group_start = 0

    def flush(rows: list[str], first_line: int) -> None:
        if len(rows) < 2:
            return
        widths = {
            row.replace("\\|", "\x00").count("|")
            for row in rows
            if set(row) - set("|-: \t")
        }
        if len(widths) > 1:
            problems.append(
                f"第 {first_line} 行起的表格列数不一致 {sorted(widths)}：{rows[0][:50]}"
            )

    for lineno, line in enumerate(lines, 1):
        if line.lstrip().startswith("|"):
            if not group:
                group_start = lineno
            group.append(line.strip())
        else:
            if group:
                flush(group, group_start)
                group = []
    if group:
        flush(group, group_start)

    return problems


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or default_targets()
    targets = [p for p in targets if p.exists()]
    if not targets:
        print("没有可检查的文件")
        return 1

    failed = 0
    for path in targets:
        try:
            shown = path.relative_to(REPO_ROOT)
        except ValueError:
            shown = path
        problems = check_file(path)
        if problems:
            failed += 1
            print(f"[FAIL] {shown}")
            for item in problems:
                print(f"       - {item}")
        else:
            print(f"[ ok ] {shown}")

    print()
    if failed:
        print(f"结果：{failed} / {len(targets)} 个文件存在渲染风险（详见上方 [FAIL]）")
        return 1
    print(f"结果：{len(targets)} 个文件全部通过渲染风险检查")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
