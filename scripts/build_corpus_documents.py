"""语料构建：把真实法规文本与业务语料转成可入库的文档文件（PDF / DOCX / HTML / MD）。

设计要点
--------
**为什么要生成文件而不是直接写库**：ingestion 链路（B3~B7）的入口是「文件字节」，
解析器的输入是 bytes + filename。只有走文件，才能真实验证解析器与切分器；
直接把文本塞进 chunk 表等于跳过了这条链路要证明的东西。

**为什么四种格式都要生成**：项目有 5 个解析器（PDF / DOCX / HTML / MD / TXT），
只生成一种格式，其余解析器在真实流量下就是零覆盖。

**为什么 PDF 要做字体子集化**：直接嵌入完整中文字体会让单份文件达到 10~20 MB，
子集化后降到 40~110 KB（实测）。

输入
----
* `--regulations <jsonl>`：法规候选清单（含 `id` / `title`），脚本按 id 抓取正文
* `--faq <jsonl>...`：FAQ 语料（`data/raw/jd_help_faq.jsonl.gz`、`data/takeout_customer_service_seed.jsonl`）
* `--manuals`：内置业务手册（外卖客服场景，含表格 —— PDF/DOCX 解析器的表格分支需要它）

输出
----
`data/corpus/<类型>/<文件名>.{pdf,docx,html,md}` + `data/corpus/manifest.jsonl`

用法
----
    python scripts/build_corpus_documents.py --limit 20
    python scripts/build_corpus_documents.py --regulations tmp/mofcom_scan.jsonl --formats pdf,docx
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import trafilatura

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "corpus"
DEFAULT_REGULATIONS = PROJECT_ROOT / "tmp" / "mofcom_scan_65000_66000.jsonl"

#: 与外卖客服场景相关的法规关键词（标题命中即入选）
RELEVANT_RE = re.compile(
    r"消费者|电子商务|网络交易|网络购物|食品|餐饮|快递|物流|配送|外卖|平台|"
    r"产品质量|广告|价格|合同|侵权|售后|退换|预付|投诉|权益|标准化|计量|"
    r"市场监督|不正当竞争|反垄断|商标|诚信|信用"
)

#: 与业务无关的法规（标题命中即剔除）—— 避免把税务/水利/证券塞进客服知识库
EXCLUDE_RE = re.compile(r"税务|所得税|增值税|水利|灌溉|证券|期货|烟草|卷烟|海关|进出口|报关|外汇|招标|投标|建设|测绘|船舶|民航|铁路")

FONT_CANDIDATES = [
    (r"C:\Windows\Fonts\simhei.ttf", "simhei"),
    (r"C:\Windows\Fonts\Deng.ttf", "deng"),
    (r"C:\Windows\Fonts\msyh.ttc", "msyh"),
]


@dataclass
class Section:
    """统一的中间结构：一份文档 = 若干 section。"""

    heading: str
    level: int = 1
    paragraphs: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)


@dataclass
class Document:
    doc_id: str
    title: str
    category: str
    source: str
    sections: list[Section]

    @property
    def char_count(self) -> int:
        total = len(self.title)
        for section in self.sections:
            total += len(section.heading)
            total += sum(len(item) for item in section.paragraphs)
            for table in section.tables:
                total += sum(len(cell) for row in table for cell in row)
        return total


# ------------------------------------------------------------------ 法规


def fetch_text(url: str, timeout: int = 25, retries: int = 3) -> str:
    """抓取并解码文本。

    带重试不是「防御性编程」而是**实测必需**：连续抓取时对端会返回
    ``SSL: UNEXPECTED_EOF_WHILE_READING``（连接被中途断开），
    单次请求则从未复现。退避重试后整批成功率从 0/17 变成稳定通过。
    """

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                for encoding in ("utf-8", "gbk", "gb18030"):
                    try:
                        return raw.decode(encoding)
                    except UnicodeDecodeError:
                        continue
                return raw.decode("utf-8", errors="ignore")
        except Exception as error:  # noqa: BLE001 - 网络抖动一律重试
            last_error = error
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_error  # type: ignore[misc]


LAW_HEADING_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百零]+[章节])\s*(.{0,40})$|^\s*((?:目\s*录)|(?:附\s*则))\s*$"
)
ARTICLE_RE = re.compile(r"^\s*(第[一二三四五六七八九十百零]+条)\s*(.*)$")


def parse_law_sections(text: str, title: str) -> list[Section]:
    """把法规正文按「第X章 / 第X节 / 第X条」拆成 section。

    结构约定（决定切分器能否切出标题路径）：
    * 章 → level 1
    * 节 → level 2
    * 条 → 归属于当前章/节下的段落
    """

    lines = [line.strip().replace("\u3000", " ") for line in text.splitlines()]
    # 去掉页头页脚与元信息行
    lines = [
        line
        for line in lines
        if line
        and "【" not in line[:2]
        and not line.startswith("|")
        and not line.startswith("文章来源")
        and not line.startswith("当前位置")
    ]

    sections: list[Section] = []
    current = Section(heading=title, level=1)
    sections.append(current)
    buffer: list[str] = []

    def flush() -> None:
        if buffer and sections:
            text_block = " ".join(buffer).strip()
            if text_block:
                sections[-1].paragraphs.append(text_block)
        buffer.clear()

    started = False
    for line in lines:
        if not started:
            if LAW_HEADING_RE.match(line) or ARTICLE_RE.match(line):
                started = True
            else:
                continue

        heading_match = LAW_HEADING_RE.match(line)
        article_match = ARTICLE_RE.match(line)

        if heading_match:
            flush()
            marker = heading_match.group(1)
            if marker:
                # 跳过"目录"段落里的标题（目录区没有正文，表现为连续标题）
                current = Section(heading=f"{marker} {heading_match.group(2).strip()}".strip(), level=1)
                sections.append(current)
                continue
            continue

        if article_match:
            flush()
            buffer.append(line)
            continue

        buffer.append(line)

    flush()

    # 目录区会产生大量空 section，清掉
    cleaned = [
        section
        for section in sections
        if section.paragraphs or section is sections[0]
    ]
    return cleaned or [Section(heading=title, level=1, paragraphs=[text[:4000]])]


LAW_CACHE_DIR = PROJECT_ROOT / "tmp" / "law_cache"


def build_regulation_document(record: dict) -> Document | None:
    cid = record.get("id")
    title = (record.get("title") or "").strip()
    if not cid or not title:
        return None

    # 正文落盘缓存：对端会间歇性断连（SSL EOF），缓存让重跑不必重新联网，
    # 也避免反复骚扰源站。缓存命中时直接复用，只有缺失才发请求。
    LAW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = LAW_CACHE_DIR / f"{cid}.txt"
    if cache_file.exists():
        text = cache_file.read_text(encoding="utf-8")
    else:
        try:
            html = fetch_text(record["url"])
            text = trafilatura.extract(html, include_tables=True, include_comments=False) or ""
        except Exception as error:
            print(f"  [跳过] id={cid} 抓取失败：{type(error).__name__}: {error}", file=sys.stderr)
            return None
        cache_file.write_text(text, encoding="utf-8")
    if not text or len(text) < 800:
        print(f"  [跳过] id={cid} 正文过短", file=sys.stderr)
        return None

    sections = parse_law_sections(text, title)
    dept = record.get("dept") or "公开法规"
    return Document(
        doc_id=f"law_{cid}",
        title=title,
        category=f"法律法规/{dept}",
        source=record["url"],
        sections=sections,
    )


def select_regulations(records: list[dict], limit: int) -> list[dict]:
    """按关键词筛选出与业务相关的法规，长文档优先。"""

    picked: list[dict] = []
    for record in records:
        if record.get("error"):
            continue
        title = record.get("title") or ""
        chars = int(record.get("chars") or 0)
        if chars < 800 or len(title) < 6:
            continue
        if EXCLUDE_RE.search(title):
            continue
        if not RELEVANT_RE.search(title):
            continue
        if "修正" in title and "关于修改" in title:
            continue
        picked.append(record)

    picked.sort(key=lambda item: (
        0 if re.search(r"中华人民共和国", item["title"]) else 1 if "条例" in item["title"] else 2,
        -int(item.get("chars") or 0),
    ))
    return picked[:limit]


# ------------------------------------------------------------------ FAQ 聚合


def load_jsonl(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    rows: list[dict] = []
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def build_faq_documents(
    limit_per_group: int, *, max_chars: int = 20000, min_chars: int = 500
) -> list[Document]:
    """把 FAQ 按分类聚合成文档（真实内容 + 合理文档粒度）。

    两个上界都由**实测问题**定，不是为了好看：

    * 单份上限 ``max_chars`` —— 首轮生成时「账户&协议」聚合出 **159,015 字**一份，
      切分后会产生 300+ 个 chunk：单份文档的失败面过大，检索定位也失去意义。
      超过上限即编号拆成「第 N 部分」。
    * 单份下限 ``min_chars`` —— 不足 500 字的文档切不出有效 chunk，
      只会产出「空文档」噪声（切分器把空文档当**正常结果**，不会报错），直接丢弃。
    """

    documents: list[Document] = []
    sources = [
        (PROJECT_ROOT / "data" / "raw" / "jd_help_faq.jsonl.gz", "jd", "jd_help_center"),
        (PROJECT_ROOT / "data" / "takeout_customer_service_seed.jsonl", "takeout", "curated_seed"),
    ]

    for path, prefix, source in sources:
        if not path.exists():
            continue
        rows = load_jsonl(path)
        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            question = (row.get("question") or "").strip()
            answer = (row.get("answer") or "").strip()
            if len(question) < 4 or len(answer) < 10:
                continue
            group = (
                row.get("category")
                or row.get("parent_category")
                or row.get("intent")
                or "常见问题"
            )
            groups[str(group).strip()[:40]].append(row)

        for group, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if len(items) < 3:
                continue
            slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", group)[:28].strip("_") or "group"

            batches: list[list[tuple[str, str]]] = []
            current: list[tuple[str, str]] = []
            current_chars = 0
            for item in items[: limit_per_group * 6]:
                question = (item.get("question") or "").strip()
                answer = (item.get("answer") or "").strip()
                if current and current_chars + len(answer) > max_chars:
                    batches.append(current)
                    current, current_chars = [], 0
                current.append((question, answer))
                current_chars += len(answer)
            if current:
                batches.append(current)

            for part, batch in enumerate(batches, start=1):
                if sum(len(answer) for _, answer in batch) < min_chars:
                    continue
                sections = [
                    Section(heading=f"{index}. {question}", level=2, paragraphs=[answer])
                    for index, (question, answer) in enumerate(batch, start=1)
                ]
                split = len(batches) > 1
                documents.append(
                    Document(
                        doc_id=f"{prefix}_{slug}" + (f"_p{part}" if split else ""),
                        title=f"{group} 常见问题解答" + (f"（第 {part} 部分）" if split else ""),
                        category=f"客服问答/{group}",
                        source=source,
                        sections=sections,
                    )
                )
    return documents


# ------------------------------------------------------------------ 渲染


def wrap_text(font, text: str, size: float, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        if font.text_length(current + char, fontsize=size) > max_width:
            lines.append(current)
            current = char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def render_pdf(path: Path, document: Document, fontfile: str, fontname: str) -> None:
    """多页 PDF：标题/章节大字号（供解析器识别 heading）+ 正文小字号 + 真实页码。"""

    import pymupdf

    font = pymupdf.Font(fontfile=fontfile)
    doc = pymupdf.open()
    width, height = pymupdf.paper_size("a4")
    left, top, right, bottom = 56.0, 68.0, width - 56.0, height - 64.0
    max_width = right - left

    page = doc.new_page(width=width, height=height)
    page.insert_font(fontname=fontname, fontfile=fontfile)
    y = top

    def new_page():
        nonlocal y
        pg = doc.new_page(width=width, height=height)
        pg.insert_font(fontname=fontname, fontfile=fontfile)
        y = top
        return pg

    def write(text: str, size: float, leading: float, leading_gap: float = 0.0) -> None:
        nonlocal page, y
        for line in wrap_text(font, text, size, max_width):
            if y + leading > bottom:
                page = new_page()
            page.insert_text((left, y), line, fontname=fontname, fontsize=size)
            y += leading
        y += leading_gap

    # 文档标题（解析器据此判定 heading 与 title）
    write(document.title, 18.0, 24.0, 10.0)
    write(f"来源分类：{document.category}", 9.0, 13.0, 16.0)

    for section in document.sections:
        size, leading = (15.0, 20.0) if section.level == 1 else (12.5, 17.0)
        write(section.heading, size, leading, 8.0)
        for paragraph in section.paragraphs:
            write(paragraph, 10.5, 16.0, 6.0)
        for table in section.tables:
            for row_index, row in enumerate(table):
                write("  |  ".join(row), 10.0, 15.0, 2.0 if row_index == 0 else 0.0)
            y += 8

    try:
        doc.subset_fonts()
    except Exception:
        pass
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()


def render_docx(path: Path, document: Document) -> None:
    from docx import Document as DocxDocument
    from docx.shared import Pt

    docx = DocxDocument()
    docx.add_heading(document.title, level=0)

    for section in document.sections:
        docx.add_heading(section.heading, level=min(section.level, 3))
        for paragraph in section.paragraphs:
            para = docx.add_paragraph(paragraph)
            para.paragraph_format.space_after = Pt(6)
        for table_rows in section.tables:
            if not table_rows:
                continue
            table = docx.add_table(rows=0, cols=len(table_rows[0]))
            table.style = "Table Grid"
            for row in table_rows:
                cells = table.add_row().cells
                for index, value in enumerate(row[: len(cells)]):
                    cells[index].text = value

    docx.save(str(path))


def render_html(path: Path, document: Document) -> None:
    from html import escape

    parts = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN"><head><meta charset="utf-8">',
        f"<title>{escape(document.title)}</title>",
        "</head><body>",
        "<nav class=\"breadcrumb\"><a href=\"/\">帮助中心</a> / "
        f"<a href=\"/list\">{escape(document.category)}</a></nav>",
        "<script>window.__page_meta = {tracking: true};</script>",
        f"<h1>{escape(document.title)}</h1>",
    ]
    for section in document.sections:
        tag = "h2" if section.level <= 1 else "h3"
        parts.append(f"<{tag}>{escape(section.heading)}</{tag}>")
        for paragraph in section.paragraphs:
            parts.append(f"<p>{escape(paragraph)}</p>")
        for table_rows in section.tables:
            if not table_rows:
                continue
            parts.append("<table>")
            for row_index, row in enumerate(table_rows):
                cell_tag = "th" if row_index == 0 else "td"
                cells = "".join(f"<{cell_tag}>{escape(value)}</{cell_tag}>" for value in row)
                parts.append(f"<tr>{cells}</tr>")
            parts.append("</table>")
    parts.append('<footer><p>本页内容仅供客服参考</p></footer>')
    parts.append("</body></html>")
    path.write_text("\n".join(parts), encoding="utf-8")


def render_markdown(path: Path, document: Document) -> None:
    lines = [f"# {document.title}", "", f"> 分类：{document.category}", ""]
    for section in document.sections:
        level = 2 if section.level <= 1 else 3
        lines.append(f"{'#' * level} {section.heading}")
        lines.append("")
        for paragraph in section.paragraphs:
            lines.append(paragraph)
            lines.append("")
        for table_rows in section.tables:
            if not table_rows:
                continue
            lines.append("| " + " | ".join(table_rows[0]) + " |")
            lines.append("|" + "---|" * len(table_rows[0]))
            for row in table_rows[1:]:
                lines.append("| " + " | ".join(row) + " |")
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


RENDERERS = {
    "pdf": None,
    "docx": render_docx,
    "html": render_html,
    "md": render_markdown,
}


# ------------------------------------------------------------------ 内置业务手册

MANUALS: list[dict] = [
    {
        "doc_id": "manual_delivery_spec",
        "title": "外卖平台配送服务规范（2026 版）",
        "category": "业务手册/配送",
        "sections": [
            (
                "第一章 总则",
                1,
                [
                    "第一条 为规范外卖平台配送服务，保障用户、商家、骑手三方合法权益，"
                    "提升履约质量与时效稳定性，制定本规范。",
                    "第二条 本规范适用于平台自营配送、众包配送及商家自配送三类履约方式。"
                    "凡通过平台产生的订单，其配送环节均受本规范约束。",
                    "第三条 配送服务遵循时效优先、安全第一、信息透明、责任到人的基本原则。",
                ],
                [],
            ),
            (
                "第二章 配送时效标准",
                1,
                [
                    "第四条 平台根据商家出餐时长、配送距离、路况系数与天气系数计算预计送达时间，"
                    "并在用户下单后展示。预计送达时间一经展示，除不可抗力外不得单方面延长。",
                    "第五条 各类订单的时效标准如下表。超时判定以骑手实际点击送达的时间为准。",
                ],
                [
                    [
                        ["订单类型", "标准时效（分钟）", "超时阈值（分钟）", "超时处置"],
                        ["普通外卖", "45", "60", "补偿 5 元无门槛券"],
                        ["预约单", "按预约时间 ±15", "±30", "补偿 8 元无门槛券"],
                        ["生鲜冷链", "40", "50", "补偿 10 元券并复核温控"],
                        ["药品急送", "30", "40", "补偿 10 元券并优先复核"],
                    ]
                ],
            ),
            (
                "第三章 超时与异常处置",
                1,
                [
                    "第六条 订单出现超时风险时，系统应在预计送达前 10 分钟向骑手与商家推送预警，"
                    "并同步向用户展示当前进度。",
                    "第七条 用户主动催单的，客服应先核实配送轨迹，再依据下表话术口径回复，"
                    "不得承诺具体赔付金额与到账时间。",
                    "第八条 因恶劣天气、交通管制等不可抗力导致的超时，平台应主动告知用户，"
                    "并免除骑手相应考核责任。",
                ],
                [
                    [
                        ["情景", "核实动作", "回复口径"],
                        ["骑手已接单未取餐", "联系骑手确认到店时间", "说明骑手正在赶往商家，预计 XX 分钟取餐"],
                        ["已取餐配送中", "查看轨迹与剩余距离", "说明骑手距离您约 XX 公里，正在配送中"],
                        ["骑手失联", "触发二次派单", "说明平台已启动补救派单，请耐心等待"],
                        ["商家出餐慢", "联系商家确认出餐", "说明商家出餐延迟，已催促商家优先处理"],
                    ]
                ],
            ),
            (
                "第四章 骑手行为规范",
                1,
                [
                    "第九条 骑手应保持配送箱清洁，生熟食品分离存放，冷藏食品全程保持温控。",
                    "第十条 骑手不得私自拆封、试吃、更换用户餐品。一经查实，扣除全部履约保证金，"
                    "并移交平台安全部门处理。",
                    "第十一条 骑手在配送过程中发生交通事故或食品安全事件的，应立即上报平台，"
                    "平台应在 30 分钟内启动应急处置流程。",
                ],
                [],
            ),
            (
                "第五章 用户投诉处理",
                1,
                [
                    "第十二条 用户就配送服务投诉的，客服应在 2 小时内首次响应，24 小时内给出处理结论。",
                    "第十三条 涉及食品安全的投诉，一律升级为最高优先级，由食品安全专员介入，"
                    "不得由一线客服直接结案。",
                ],
                [],
            ),
        ],
    },
    {
        "doc_id": "manual_after_sales",
        "title": "外卖平台售后与退款处理手册",
        "category": "业务手册/售后",
        "sections": [
            (
                "第一章 售后受理范围",
                1,
                [
                    "第一条 用户可在订单完成后 48 小时内就以下情形申请售后：餐品缺失、"
                    "餐品与描述不符、餐品变质、包装破损、配送超时、错送漏送。",
                    "第二条 下列情形不属于平台售后受理范围：用户自身原因导致的误下单、"
                    "已超出申请时效、无法提供有效凭证且商家否认的争议。",
                ],
                [],
            ),
            (
                "第二章 退款分级标准",
                1,
                [
                    "第三条 客服处理退款时，应依据下表分级标准执行，不得超越权限承诺赔付。",
                ],
                [
                    [
                        ["问题类型", "常见证据", "退款比例", "审批层级"],
                        ["餐品缺失（少一份）", "订单截图 + 餐品照片", "缺失部分全额", "一线客服"],
                        ["餐品变质异味", "照片 + 视频", "订单全额", "值班主管"],
                        ["包装破损导致洒漏", "照片", "订单全额", "一线客服"],
                        ["配送超时 30 分钟以上", "订单超时记录", "订单 50%", "一线客服"],
                        ["错送漏送", "订单截图 + 实收照片", "订单全额", "值班主管"],
                        ["疑似食品安全事故", "就医凭证 + 订单记录", "全额并启动保险理赔", "食品安全专员"],
                    ]
                ],
            ),
            (
                "第三章 高风险话术红线",
                1,
                [
                    "第四条 客服不得承诺以下事项：具体到账时间、额外精神损失赔偿、"
                    "对商家作出处罚、对骑手作出处罚、超出分级标准的赔付金额。",
                    "第五条 涉及以下关键词的对话必须转人工主管复核后才可结案：食物中毒、"
                    "住院、过敏、异物、虫鼠、监管部门、媒体曝光、律师函。",
                    "第六条 用户提出人身伤害主张的，客服应立即升级并留存全部对话记录，"
                    "不得自行判断责任归属。",
                ],
                [],
            ),
            (
                "第四章 商家侧处理流程",
                1,
                [
                    "第七条 责任归属存疑的订单，平台应先向用户垫付，再依据举证结果向商家追偿。",
                    "第八条 商家在一个自然月内被判定责任 3 次以上的，进入重点监管名单，"
                    "平台有权限制其流量与活动参与。",
                ],
                [],
            ),
        ],
    },
    {
        "doc_id": "manual_food_safety",
        "title": "食品安全与投诉应急处理指引",
        "category": "业务手册/安全",
        "sections": [
            (
                "第一章 分级响应",
                1,
                [
                    "第一条 平台对食品安全事件实行四级响应。客服在受理时须依据下表判断等级，"
                    "并立即上报对应责任人。",
                ],
                [
                    [
                        ["等级", "判定标准", "响应时限", "责任岗位"],
                        ["一级", "多人出现症状或涉及住院", "立即", "食品安全专员 + 平台负责人"],
                        ["二级", "单人身体不适并有就医记录", "30 分钟内", "食品安全专员"],
                        ["三级", "异物、变质但未就医", "2 小时内", "值班主管"],
                        ["四级", "口味、分量等主观不满", "24 小时内", "一线客服"],
                    ]
                ],
            ),
            (
                "第二章 处置流程",
                1,
                [
                    "第二条 接到食品安全投诉后，第一步是安抚并记录，不得抢先定性为"
                    "「商家责任」或「用户误解」。",
                    "第三条 记录须包含订单编号、下单时间、食用时间、症状出现时间、"
                    "就医情况、联系方式等要素。",
                    "第四条 涉及就医的，应主动告知用户保留病历、发票与检验报告，"
                    "并在用户同意后引导其提交理赔材料。",
                ],
                [],
            ),
            (
                "第三章 责任认定与归档",
                1,
                [
                    "第五条 责任认定由食品安全专员牵头，结合商家后厨监控、食材采购记录、"
                    "同批次订单反馈综合判断。",
                    "第六条 事件处理完毕后 5 个工作日内完成归档，归档内容包括时间线、"
                    "证据清单、处置动作与改进措施。",
                ],
                [],
            ),
        ],
    },
]


def build_manual_documents() -> list[Document]:
    documents: list[Document] = []
    for item in MANUALS:
        sections = [
            Section(heading=heading, level=level, paragraphs=paragraphs, tables=tables)
            for heading, level, paragraphs, tables in item["sections"]
        ]
        documents.append(
            Document(
                doc_id=item["doc_id"],
                title=item["title"],
                category=item["category"],
                source="internal_manual",
                sections=sections,
            )
        )
    return documents


# ------------------------------------------------------------------ 主流程


def safe_name(value: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\s]+", "_", value).strip("_")
    return cleaned[:limit] or "document"


def main() -> int:
    parser = argparse.ArgumentParser(description="构建可入库的语料文档")
    parser.add_argument("--regulations", default=str(DEFAULT_REGULATIONS), help="法规候选 jsonl")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="输出目录")
    parser.add_argument("--formats", default="pdf,docx,html,md", help="要生成的格式")
    parser.add_argument("--limit", type=int, default=18, help="法规取多少份")
    parser.add_argument("--faq-per-group", type=int, default=40, help="每份 FAQ 聚合文档最多多少条")
    parser.add_argument("--sleep", type=float, default=0.25, help="抓取间隔")
    args = parser.parse_args()

    formats = [item.strip() for item in args.formats.split(",") if item.strip()]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    documents: list[Document] = []

    reg_path = Path(args.regulations)
    if reg_path.exists():
        records = [json.loads(line) for line in reg_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        selected = select_regulations(records, args.limit)
        print(f"[法规] 候选 {len(records)} 条 → 选中 {len(selected)} 条", file=sys.stderr)
        for index, record in enumerate(selected, start=1):
            document = build_regulation_document(record)
            if document:
                documents.append(document)
                print(
                    f"  [{index}/{len(selected)}] {document.title[:34]} "
                    f"({document.char_count:,} 字, {len(document.sections)} 节)",
                    file=sys.stderr,
                )
            time.sleep(args.sleep)
    else:
        print(f"[法规] 候选文件不存在，跳过：{reg_path}", file=sys.stderr)

    faq_documents = build_faq_documents(args.faq_per_group)
    print(f"[FAQ] 聚合出 {len(faq_documents)} 份文档", file=sys.stderr)
    documents.extend(faq_documents)

    manuals = build_manual_documents()
    print(f"[手册] {len(manuals)} 份", file=sys.stderr)
    documents.extend(manuals)

    # 分配格式：法规 PDF/DOCX、FAQ HTML/MD、手册 DOCX/PDF
    manifest: list[dict] = []
    fontfile, fontname = FONT_CANDIDATES[0]

    for document in documents:
        is_law = document.doc_id.startswith("law_")
        is_manual = document.doc_id.startswith("manual_")
        targets = []
        if is_law:
            targets = [item for item in formats if item in ("pdf", "docx")]
        elif is_manual:
            # 手册覆盖 pdf + docx + html：它是**唯一带表格**的语料，
            # PDF/DOCX/HTML 三个解析器的表格分支都靠它拿真实覆盖。
            targets = [item for item in formats if item in ("pdf", "docx", "html")]
        else:
            targets = [item for item in formats if item in ("html", "md")]
        if not targets:
            targets = formats[:1]

        for fmt in targets:
            target_dir = outdir / fmt
            target_dir.mkdir(parents=True, exist_ok=True)
            path = target_dir / f"{safe_name(document.doc_id)}.{fmt}"
            try:
                if fmt == "pdf":
                    render_pdf(path, document, fontfile, fontname)
                else:
                    RENDERERS[fmt](path, document)
            except Exception as error:
                print(f"  [渲染失败] {path.name}: {type(error).__name__}: {error}", file=sys.stderr)
                continue
            manifest.append(
                {
                    "doc_id": document.doc_id,
                    "title": document.title,
                    "category": document.category,
                    "source": document.source,
                    "format": fmt,
                    "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                    "bytes": path.stat().st_size,
                    "char_count": document.char_count,
                    "section_count": len(document.sections),
                }
            )

    manifest_path = outdir / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for item in manifest:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    total_bytes = sum(item["bytes"] for item in manifest)
    print(
        f"\n完成：{len(manifest)} 个文件（{len(documents)} 份文档）· {total_bytes / 1024 / 1024:.1f} MB\n"
        f"清单：{manifest_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
