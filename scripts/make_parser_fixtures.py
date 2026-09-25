"""生成 ``tests/fixtures/`` 下的解析器测试样本。

为什么用脚本生成而不是直接提交「手工做的」样本文件
--------------------------------------------------
1. **可复现**：任何人 ``python scripts/make_parser_fixtures.py`` 都能重新生成，
   样本内容是人可读的代码，不是黑盒二进制；
2. **可控**：测试断言依赖具体内容（哪个字号是标题、表格有几列几行），
   内容必须写死在代码里，不能靠「随便找个 PDF」；
3. **不超限**：脚本末尾会校验每个样本都小于 1 MB —— 仓库有
   ``scripts/check_repo_data_size.py`` 这条 CI 硬门槛，样本超标会直接卡住流水线。

生成的内容刻意覆盖各解析器的关键分支：多级标题、段落、列表、表格、
代码块、图片引用、HTML 噪声标签、PDF 多页与字号差异。

用法::

    python scripts/make_parser_fixtures.py
    python scripts/make_parser_fixtures.py --check   # 只校验，不写盘
"""

from __future__ import annotations

import argparse
from datetime import datetime
import io
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures"

#: 单文件上限，必须与 scripts/check_repo_data_size.py 的默认值一致
MAX_BYTES = 1 * 1024 * 1024

#: 固定时间戳，保证生成的 DOCX 字节稳定（否则每次生成都产生不同二进制）
FIXED_TIMESTAMP = datetime(2026, 1, 1, 0, 0, 0)


# ---------------------------------------------------------------- Markdown 样本

SAMPLE_MD = """# 客户服务知识库测试文档

本文件用于解析器单元测试，覆盖标题、段落、列表、表格与代码块。

## 退款政策

标准退款周期为 **7 个工作日**，详见 [服务条款](https://example.com/terms)。

- 未发货订单可全额退款
- 已发货订单需扣除运费
- 超过 30 天的订单不予受理

| 订单状态 | 处理时限 | 责任人 |
| --- | --- | --- |
| 待发货 | 1 个工作日 | 客服组 |
| 已发货 | 3 个工作日 | 物流组 |

```python
def refund_days(status: str) -> int:
    return 7 if status == "paid" else 0
```

## 发票开具

电子发票在订单完成后自动开具。

### 抬头修改

抬头只允许在开具当月内修改一次。

![发票样例](images/invoice-sample.png)
"""


def write_markdown() -> bytes:
    return SAMPLE_MD.encode("utf-8")


# ---------------------------------------------------------------- HTML 样本

SAMPLE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>客户服务手册（HTML 样本）</title>
  <script>window.tracker = { sent: false };</script>
  <style>.hidden { display: none; }</style>
</head>
<body>
  <nav class="site-nav"><a href="/">首页</a><a href="/docs">文档</a></nav>
  <header><h1>站点页眉标题</h1></header>
  <main>
    <h1>客户服务手册</h1>
    <p>本页用于解析器的 HTML 分支测试，页眉、导航与脚本都不应出现在解析结果里。</p>
    <h2>响应时限</h2>
    <p>工作日 9:00 至 18:00 内提交的工单，<strong>两小时</strong>内首次响应。</p>
    <ul>
      <li>普通工单：两小时</li>
      <li>紧急工单：三十分钟</li>
    </ul>
    <h2>服务等级</h2>
    <table>
      <thead>
        <tr><th>等级</th><th>响应时限</th><th>适用客户</th></tr>
      </thead>
      <tbody>
        <tr><td>P0</td><td>30 分钟</td><td>签约客户</td></tr>
        <tr><td>P1</td><td>2 小时</td><td>全部客户</td></tr>
      </tbody>
    </table>
    <pre>curl -X POST https://api.example.com/v1/tickets</pre>
    <img src="/static/images/flow.png" alt="工单流转图">
  </main>
  <aside class="ads">广告位</aside>
  <form><input type="text" name="q"></form>
  <footer><p>版权所有 2026 示例公司</p></footer>
</body>
</html>
"""


def write_html() -> bytes:
    return SAMPLE_HTML.encode("utf-8")


# ---------------------------------------------------------------- DOCX 样本

DOCX_ROWS: tuple[tuple[str, ...], ...] = (
    ("班次", "上班时间", "下班时间"),
    ("早班", "09:00", "18:00"),
    ("晚班", "13:00", "22:00"),
)


def write_docx() -> bytes:
    from docx import Document

    document = Document()

    document.add_heading("员工手册（DOCX 样本）", level=1)
    document.add_paragraph("本文件用于解析器的 DOCX 分支测试，覆盖标题层级、列表与表格。")

    document.add_heading("考勤制度", level=2)
    document.add_paragraph("所有员工需在上班前完成打卡。")
    document.add_paragraph("迟到超过三次将计入绩效。", style="List Bullet")
    document.add_paragraph("请假需提前一个工作日提交。", style="List Bullet")

    table = document.add_table(rows=len(DOCX_ROWS), cols=len(DOCX_ROWS[0]))
    table.style = "Table Grid"
    for row_index, row in enumerate(DOCX_ROWS):
        for column_index, value in enumerate(row):
            table.cell(row_index, column_index).text = value

    document.add_heading("休假制度", level=2)
    document.add_paragraph("年假按司龄计算，入职满一年起享有。")
    document.add_heading("年假天数", level=3)
    document.add_paragraph("满一年五天，满三年十天，满五年十五天。")

    # 固定元数据：保证同样内容每次生成出同样的字节，
    # 这样样本文件进 git 后不会因为「时间戳变化」产生无意义的二进制 diff。
    properties = document.core_properties
    properties.author = "rag-parser-fixture"
    properties.last_modified_by = "rag-parser-fixture"
    properties.created = FIXED_TIMESTAMP
    properties.modified = FIXED_TIMESTAMP
    properties.revision = 1
    properties.title = ""

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------- PDF 样本

#: (文本, 字号, 距页面顶端基线位置) —— 位置刻意拉开，保证 PyMuPDF 把它们切成独立 block
PDF_PAGE_1: tuple[tuple[str, float, float], ...] = (
    ("年度服务报告", 20.0, 80.0),
    ("本报告用于解析器的 PDF 分支测试，正文使用 11 号字。", 11.0, 130.0),
    ("营收概览", 15.0, 185.0),
    ("全年营收较上一年度增长百分之十二，主要来自续约客户。", 11.0, 225.0),
)

PDF_PAGE_2: tuple[tuple[str, float, float], ...] = (
    ("下半年计划", 15.0, 80.0),
    ("下半年将新增两个服务网点，并上线自助工单系统。", 11.0, 120.0),
)

#: PDF 表格内容（首行为表头）
PDF_TABLE: tuple[tuple[str, ...], ...] = (
    ("季度", "营收", "同比"),
    ("第一季度", "1200", "+8%"),
    ("第二季度", "1350", "+12%"),
)

CJK_FONT = "china-s"


def write_pdf() -> bytes:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()

    for text, font_size, baseline in PDF_PAGE_1:
        page.insert_text((72, baseline), text, fontsize=font_size, fontname=CJK_FONT)

    _draw_pdf_table(page, origin_x=72, origin_y=300)

    second = document.new_page()
    for text, font_size, baseline in PDF_PAGE_2:
        second.insert_text((72, baseline), text, fontsize=font_size, fontname=CJK_FONT)

    payload = document.tobytes()
    document.close()
    return payload


def _draw_pdf_table(page: object, origin_x: float, origin_y: float) -> None:
    """用画线 + 插字手工画一张有框线的表。

    画框线而不是靠列对齐，是因为 PyMuPDF 的 ``find_tables()`` 默认走
    "lines" 策略，依赖实际存在的框线；只靠空格对齐的「表格」在 PDF 里
    本质上就是普通文本，不该被当成表格。
    """

    column_widths = (90.0, 80.0, 70.0)
    row_height = 24.0
    total_width = sum(column_widths)
    total_height = row_height * len(PDF_TABLE)

    for row_index in range(len(PDF_TABLE) + 1):
        y = origin_y + row_index * row_height
        page.draw_line((origin_x, y), (origin_x + total_width, y))  # type: ignore[attr-defined]

    x = origin_x
    page.draw_line((x, origin_y), (x, origin_y + total_height))  # type: ignore[attr-defined]
    for width in column_widths:
        x += width
        page.draw_line((x, origin_y), (x, origin_y + total_height))  # type: ignore[attr-defined]

    for row_index, row in enumerate(PDF_TABLE):
        cursor_x = origin_x
        for column_index, value in enumerate(row):
            page.insert_text(  # type: ignore[attr-defined]
                (cursor_x + 6, origin_y + row_index * row_height + 16),
                value,
                fontsize=10,
                fontname=CJK_FONT,
            )
            cursor_x += column_widths[column_index]


# ---------------------------------------------------------------- 入口

BUILDERS = {
    "sample.md": write_markdown,
    "sample.html": write_html,
    "sample.docx": write_docx,
    "sample.pdf": write_pdf,
}


def build_all() -> dict[str, bytes]:
    return {name: builder() for name, builder in BUILDERS.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成解析器测试样本")
    parser.add_argument("--check", action="store_true", help="只校验已存在的样本，不重新生成")
    args = parser.parse_args()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    if args.check:
        for name in BUILDERS:
            path = FIXTURE_DIR / name
            if not path.is_file():
                failures.append(f"{name} 不存在")
        payloads = {name: (FIXTURE_DIR / name).read_bytes() for name in BUILDERS if (FIXTURE_DIR / name).is_file()}
    else:
        payloads = build_all()
        for name, payload in payloads.items():
            (FIXTURE_DIR / name).write_bytes(payload)

    for name in BUILDERS:
        payload = payloads.get(name)
        if payload is None:
            continue
        size = len(payload)
        status = "OK" if size <= MAX_BYTES else "超标"
        if size > MAX_BYTES:
            failures.append(f"{name} 体积 {size} 字节，超过上限 {MAX_BYTES}")
        print(f"  {name:<14} {size:>8,} 字节  {status}")

    if failures:
        print()
        for failure in failures:
            print(f"失败：{failure}")
        return 1

    print()
    print(f"样本目录：{FIXTURE_DIR}")
    print("结果：通过，全部样本均小于 1 MB。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
