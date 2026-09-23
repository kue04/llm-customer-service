"""验收规范交付前校验：渲染完整性（五项）+ 编号与引用一致性（追加三项），共 8 项。

用途：``docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md`` 每次改动后的机械校验。
依据：``skills/markdown-longdoc-verify`` 的五项校验 +
      本项目对「章节编号连续 / 交叉引用有效 / 表格列数一致」的追加要求。

用法::

    python scripts/verify_acceptance_spec.py [文档路径]
    # 不传路径时默认校验 docs/RAG_ENTERPRISE_ACCEPTANCE_SPEC.md
    # 末行打印 RESULT: OK 表示 8 项全过

背景：该 skill 记录的核心风险是「未闭合 / 多余的围栏让大段内容被静默吞进代码块」——
源文件肉眼看完全正常，只有渲染才暴露。本项目实测撞过一次同类故障
（§17.3 列表内缩进代码块被渲染成行内代码），因此把「围栏按 h2 分段必须偶数」
也纳入校验。
"""

from __future__ import annotations

import html as html_mod
import pathlib
import re
import sys

import markdown
from markdown.extensions.toc import TocExtension

_ROOT = pathlib.Path(__file__).resolve().parents[1]
DOC = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else (
    _ROOT / "docs" / "RAG_ENTERPRISE_ACCEPTANCE_SPEC.md")

text = DOC.read_text(encoding="utf-8")
html = markdown.Markdown(
    extensions=["extra", "sane_lists", "admonition", "attr_list",
                TocExtension(toc_depth="2-4"), "codehilite",
                "fenced_code", "tables"],
    extension_configs={"codehilite": {"noclasses": True}},
).convert(text)

fails: list[tuple[str, str]] = []

# ---------------- 1) 所有 h2~h4 真实标题都渲染出来（两侧都去标记后比对）
def strip_md(s: str) -> str:
    return s.replace("`", "").replace("**", "").replace("*", "")

in_fence, md_heads = False, []
for line in text.split("\n"):
    if line.strip().startswith("```"):
        in_fence = not in_fence
        continue
    if not in_fence and re.match(r"^#{2,4} ", line):
        md_heads.append(strip_md(line.lstrip("#").strip()))

got = {html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
       for m in re.finditer(r"<h[234][^>]*>(.*?)</h[234]>", html, re.S)}
miss = [h for h in md_heads if h not in got]
if miss:
    fails.append(("headings", f"{len(miss)} 个标题未渲染: {miss[:5]}"))

# ---------------- 2) 围栏成对，且代码块数 == 围栏数/2
fences = sum(1 for ln in text.split("\n") if ln.strip().startswith("```"))
blocks = html.count("<pre")
if fences % 2 or blocks != fences // 2:
    fails.append(("codeblocks", f"fences={fences} blocks={blocks}"))

# ---------------- 3) 代码内容未泄漏到正文
body = re.sub(r'<div class="codehilite"[^>]*>.*?</div>', "", html, flags=re.S)
leak = re.findall(r"def \w+\(|CREATE INDEX|SELECT .* FROM", body)
if leak:
    fails.append(("leak", f"正文出现代码特征: {sorted(set(leak))[:5]}"))

# ---------------- 4) 表格数一致（含引用块 `> |` 内的表格）
md_tbl = len(re.findall(r"^[> \t]*\|[ :\-|]*-{2,}", text, re.M))
if md_tbl != html.count("<table>"):
    fails.append(("tables", f"md={md_tbl} html={html.count('<table>')}"))

# ---------------- 5) 围栏按 h2 章节分段，每段必须偶数（代码块不跨章节）
L, cur, cnt, start, odd_sec = text.split("\n"), "(pre)", 0, 1, []
for i, ln in enumerate(L, 1):
    if re.match(r"^## ", ln):
        if cnt % 2:
            odd_sec.append((cur, start, i - 1, cnt))
        cur, cnt, start = ln[:40], 0, i
    elif ln.strip().startswith("```"):
        cnt += 1
if cnt % 2:
    odd_sec.append((cur, start, len(L), cnt))
if odd_sec:
    fails.append(("fences_by_section", str(odd_sec)))

# ---------------- 6) 章节编号：## N. 连续且唯一 / ### N.M 唯一
h2_nums, h3_nums = [], []
for line in L:
    m2 = re.match(r"^## (\d+)\. ", line)
    if m2:
        h2_nums.append(int(m2.group(1)))
    m3 = re.match(r"^### (\d+\.\d+) ", line)
    if m3:
        h3_nums.append(m3.group(1))
if h2_nums != list(range(1, len(h2_nums) + 1)):
    fails.append(("h2_sequence", f"顶层编号非连续: {h2_nums}"))
if len(h3_nums) != len(set(h3_nums)):
    dup = [x for x in set(h3_nums) if h3_nums.count(x) > 1]
    fails.append(("h3_dup", f"重复小节号: {dup}"))

# ---------------- 7) 交叉引用 §X 或 §X.Y 必须存在（排除对执行计划/阶段编号的引用）
valid = {str(n) for n in h2_nums} | {n.split(".")[0] for n in h3_nums} | set(h3_nums)
refs = set(re.findall(r"[§](\d+(?:\.\d+)?)", text))
# 去掉范围写法捕获出的孤立数字（如「§2~§15」只会捕到 2 和 15，都是合法顶层号）
bad_refs = sorted(r for r in refs if r not in valid)
if bad_refs:
    fails.append(("dangling_refs", f"引用了不存在的章节: {bad_refs}"))

# ---------------- 8) 表格列数一致（同一表格内每行 pipe 数相同；\| 为转义不算分隔）
def _dequote(line: str) -> str:
    """去掉引用块前缀，便于把 `> | a | b |` 也当作表格行。"""
    s = line.strip()
    if s.startswith(">"):
        s = s.lstrip(">").strip()
    return s


def cols(line: str) -> int:
    return _dequote(line).replace("\\|", "\x00").count("|")


tbl_groups, cur_g, in_t = [], [], False
for line in L:
    if _dequote(line).startswith("|"):
        cur_g.append(line)
        in_t = True
    else:
        if in_t:
            tbl_groups.append(cur_g)
            cur_g, in_t = [], False
if cur_g:
    tbl_groups.append(cur_g)

for idx, g in enumerate(tbl_groups, 1):
    widths = {cols(x) for x in g if set(_dequote(x)) - set("|-: \t")}
    if len(widths) > 1:
        head = g[0][:60]
        fails.append(("table_width", f"第 {idx} 个表格列数不一致 {sorted(widths)}: {head}"))

# ---------------- 输出
print(f"文档: {DOC.name}  行数={len(L)}")
print(f"标题: md={len(md_heads)} html命中={len(got)} | 围栏={fences} 代码块={blocks} | 表格 md={md_tbl} html={html.count('<table>')}")
print(f"顶层章节={len(h2_nums)} 小节={len(h3_nums)} | 交叉引用={len(refs)} 个，其中悬空={len(bad_refs)}")
print("-" * 60)
if not fails:
    print("RESULT: OK —— 8 项校验全部通过")
else:
    print("RESULT: FAILED")
    for name, detail in fails:
        print(f"  [{name}] {detail}")
