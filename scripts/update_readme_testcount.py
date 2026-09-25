"""把 README 里硬编码的测试规模数字改成本轮 **JUnit XML** 的实测值。

为什么专门写脚本：README 里分散着 4 处测试数（badge / 快速验证注释 / 质量门禁表 /
目录树），手改必漏 —— 测试数每加用例都会变，是**结构性漂移源**（踩坑 E8）。

**2026-09-25 换口径（重要）**：数字一律取自 JUnit XML 的
``tests`` / ``failures`` / ``errors``，不再引用 ``pytest -q`` 的汇总行 ——
那一行会被环境的安全删除守卫吞掉（踩坑 A6），也就是说
「932 passed」这种数字**根本没法从当轮运行复现**，只能靠推算。
顺带把「932 passed、含 4 个 subtests、JUnit 里 936」这套需要解释的双数字
收敛成单一口径（JUnit 的用例总数）。

用法：
    ./venv/Scripts/python.exe scripts/update_readme_testcount.py [junit.xml]

**2026-09-25 从 `tmp/` 移到这里**：`tmp/` 在 `.gitignore` 里，
而交接文件是让"下一个窗口"用这个脚本更新 README 的 —— 留在 `tmp/` 等于新克隆的仓库里没有它。

每个模式命中数不等于 1 就中止（宁可红，也不要静默改错地方）。
"""

from __future__ import annotations

from datetime import date
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "README.md"
DEFAULT_JUNIT = ROOT / "reports" / "retrieval_hybrid" / "final_junit_20260925.xml"


def read_junit(path: Path) -> tuple[int, int, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root)
    tests = failures = errors = 0
    for suite in suites:
        tests += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
    return tests, failures, errors


def count_test_files() -> int:
    return len(list((ROOT / "tests").glob("test_*.py")))


def main() -> int:
    junit = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JUNIT
    if not junit.exists():
        print(f"[FAIL] 找不到 JUnit XML：{junit}")
        return 1

    tests, failures, errors = read_junit(junit)
    files = count_test_files()
    today = date.today().strftime("%Y--%m--%d")
    print(f"[取数] {junit.name}: tests={tests} failures={failures} errors={errors}")
    print(f"[取数] tests/ 下测试文件 {files} 个")

    if failures or errors:
        print("[FAIL] 本轮门禁不是全绿，拒绝把红数字写进 README")
        return 1

    patterns: list[tuple[str, re.Pattern[str], str]] = [
        ("badge 测试数", re.compile(r"tests-\d+%20passed"), f"tests-{tests}%20passed"),
        (
            "快速验证注释",
            re.compile(r"# 实测：\d+ (?:passed|条)[^\n]*"),
            f"# 实测：{tests} 条（JUnit XML 口径），~40s",
        ),
        (
            "质量门禁表",
            re.compile(r"\| pytest 用例总数 / 通过率 \|[^\n]*"),
            f"| pytest 用例总数 / 通过率 | **{tests} / 100%**"
            f"（{failures} failures / {errors} errors）| "
            f"{files} 个测试文件，精简依赖热缓存 ~40s，完整依赖冷启动更久 |",
        ),
        (
            "质量门禁表·测试文件数",
            re.compile(r"\| 测试文件数 \| \d+ \|"),
            f"| 测试文件数 | {files} |",
        ),
        (
            "目录树",
            re.compile(r"# \d+ 个测试文件 / \d+ 用例"),
            f"# {files} 个测试文件 / {tests} 用例",
        ),
        ("更新日期 badge", re.compile(r"updated-\d{4}--\d{2}--\d{2}"), f"updated-{today}"),
    ]

    text = TARGET.read_text(encoding="utf-8")
    for label, pattern, replacement in patterns:
        hits = len(pattern.findall(text))
        if hits != 1:
            print(f"[FAIL] {label}：命中 {hits} 处（期望 1），中止")
            return 1
        text = pattern.sub(replacement, text, count=1)
        print(f"[OK]   {label}")

    TARGET.write_text(text, encoding="utf-8")

    back = TARGET.read_text(encoding="utf-8")
    if f"tests-{tests}%20passed" not in back:
        print("[WARN] 回读未找到新 badge 值")
        return 1
    leftovers = sorted({n for n in ("932", "936", "949") if n in back})
    if leftovers:
        print(f"[WARN] README 里仍有旧数字 {leftovers} —— 请人工确认是否别的语境")
    else:
        print("[OK]   回读：无残留旧数字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
