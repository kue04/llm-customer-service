"""构建「形态补缺」语料样本（2026-09-24 新增）。

为什么要有第二个生成器
----------------------
``scripts/build_corpus_documents.py`` 解决的是「**有没有真实语料**」，
本脚本解决的是「**解析器的每个分支有没有被真实语料走到过**」。

起因是 ``tmp/audit_corpus_coverage.py`` 的实测：209 个主语料文件里

* ``table``  只有 **2.9%**（6/209）的文件有表格块；
* ``code``   **0%** —— 从未走到；
* ``image``  **0%** —— 从未走到；
* 无文本层 / OCR 分支 **0%**；非 UTF-8 编码回退 **0%**；
* 标题层级最深只到 **h2**，``h3``/``h4`` 从未出现；
* PDF 最长只有 8 页，跨页与页码连续性没压到。

**测试里绿不等于"支持"**：没被真实语料走到的分支，只能说"单测覆盖了"，
不能说"在真实文档上验证过"。本脚本就是去补这些真实样本。

做法
----
1. **能抓真的一律抓真的** —— 统计局统计发布（表格 60+ 行）、快递 API 文档
   （代码块 + 表格 + 参数表）、餐饮行业资讯（图片）、MDN 中文（代码块 40+）。
   走 trafilatura 的 markdown 输出，表格 / 代码块 / 图片**原样保留**，不做二次渲染。
2. **真实语料拿不到的形态才派生** —— 扫描件 PDF、GBK 编码文本、四层标题法规。
   派生样本在 manifest 里用 ``purpose`` 明确标注，避免和真实语料混为一谈。
3. 抓取结果落 ``tmp/sample_cache/``，重跑不联网（踩坑 A11 的同款处理）。

用法
----
    ./venv/Scripts/python.exe scripts/build_corpus_samples.py
    ./venv/Scripts/python.exe scripts/build_corpus_samples.py --per-source 4 --sleep 0.8
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import trafilatura  # noqa: E402

DEFAULT_OUTDIR = PROJECT_ROOT / "data" / "corpus_samples"
CACHE_DIR = PROJECT_ROOT / "tmp" / "sample_cache"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"

# ------------------------------------------------------------------ 来源定义
#
# 每个来源标注它**主要补哪个缺口**，方便后面回溯"这条样本为什么存在"。
SOURCES: dict[str, dict] = {
    "stats": {
        "label": "国家统计局 · 统计数据发布",
        "purpose": "table_coverage",
        "category": "数据报表/统计发布",
        "list_url": "https://www.stats.gov.cn/sj/zxfb/",
        "link_pattern": re.compile(r"/sj/zxfb/\d{6}/t\d{8}_\d+\.html"),
        "seed": [
            "https://www.stats.gov.cn/sj/zxfb/202609/t20260915_1965307.html",
            "https://www.stats.gov.cn/sj/zxfb/202609/t20260915_1965308.html",
            "https://www.stats.gov.cn/sj/zxfb/202609/t20260915_1965309.html",
        ],
        "formats": ("md", "html"),
    },
    "api": {
        "label": "快递 / 地图开放平台 API 文档",
        "purpose": "code_and_table_coverage",
        "category": "技术文档/开放平台",
        "list_url": "https://www.kdniao.com/api-track",
        "link_pattern": re.compile(r"/api-[a-z0-9\-]+|/document/\d+"),
        "seed": [
            "https://www.kdniao.com/api-track",
            "https://lbs.amap.com/api/webservice/summary",
        ],
        "formats": ("md", "html"),
    },
    "mdn": {
        "label": "MDN 中文文档（代码块形态样本）",
        "purpose": "code_coverage",
        "category": "技术文档/Web API",
        "list_url": "",
        "link_pattern": re.compile(r"^$"),
        "seed": [
            "https://developer.mozilla.org/zh-CN/docs/Web/API/Fetch_API/Using_Fetch",
            "https://developer.mozilla.org/zh-CN/docs/Web/API/Streams_API",
            "https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Cookies",
        ],
        "formats": ("md",),
    },
    "news": {
        "label": "餐饮行业资讯（图片形态样本）",
        "purpose": "image_coverage",
        "category": "行业资讯/餐饮",
        "list_url": "https://www.canyin88.com/",
        "link_pattern": re.compile(r"/(zixun|zhuanlan)/\d{4}/\d{2}/\d{2}/\d+\.html"),
        "seed": [],
        "formats": ("html", "md"),
    },
}

ARTICLE_MIN_CHARS = 600


# ------------------------------------------------------------------ 抓取


def fetch_html(url: str, timeout: int = 25, retries: int = 3) -> str:
    """抓取 HTML，带退避重试 + 落盘缓存。

    缓存是**必需的**而不是优化：对端在连续请求下会中途掐断 TLS
    （踩坑 A11），缓存让失败可以增量补齐而不是整批重来。
    """

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    cache_file = CACHE_DIR / f"{key}.html"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8", errors="ignore")

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "*/*"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            for encoding in ("utf-8", "gbk", "gb18030"):
                try:
                    html = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                html = raw.decode("utf-8", errors="ignore")
            cache_file.write_text(html, encoding="utf-8")
            return html
        except Exception as error:  # noqa: BLE001 - 网络抖动一律重试
            last_error = error
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))
    raise last_error  # type: ignore[misc]


def discover_links(source: dict, limit: int) -> list[str]:
    """从列表页发现文章链接；列表页拿不到就回退到内置种子。"""

    links: list[str] = []
    if source["list_url"]:
        try:
            html = fetch_html(source["list_url"])
            for href in re.findall(r'href="([^"]+)"', html):
                if source["link_pattern"].search(href):
                    full = urljoin(source["list_url"], href)
                    if full not in links:
                        links.append(full)
        except Exception as error:  # noqa: BLE001
            print(f"    [列表页失败] {type(error).__name__}: {error}", file=sys.stderr)

    for seed in source["seed"]:
        if seed not in links:
            links.append(seed)
    return links[: max(limit * 3, limit)]


def to_markdown(html: str) -> str:
    """抽取正文为 markdown（表格 / 代码块 / 图片原样保留）。"""

    md = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_images=True,
        include_comments=False,
    )
    return (md or "").strip()


# ------------------------------------------------------------------ 落盘


def write_markdown(path: Path, title: str, category: str, source_url: str, body: str) -> None:
    text = f"# {title}\n\n> 分类：{category}  \n> 来源：{source_url}\n\n{body}\n"
    path.write_text(text, encoding="utf-8")


def write_html(path: Path, title: str, body_md: str) -> None:
    """把 markdown 正文包成最小 HTML。

    只做**够用的**转换（标题 / 表格行 / 代码块 / 图片 / 段落），
    目的是让 HTML 解析器也能吃到同样的结构，不是为了还原原站样式。
    """

    out: list[str] = ["<!doctype html>", "<html><head><meta charset=\"utf-8\">", f"<title>{title}</title></head><body>"]
    in_code = False
    for line in body_md.splitlines():
        if line.startswith("```"):
            out.append("</pre><pre><code>" if not in_code else "</code></pre>")
            if not in_code:
                out[-1] = "<pre><code>"
            else:
                out[-1] = "</code></pre>"
            in_code = not in_code
            continue
        if in_code:
            out.append(line.replace("&", "&amp;").replace("<", "&lt;"))
            continue
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)
            out.append(f"<h{level}>{heading.group(2)}</h{level}>")
        elif stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            tag = "th" if not any("<td>" in o for o in out[-12:]) else "td"
            out.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        else:
            image = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
            if image:
                out.append(f'<img src="{image.group(2)}" alt="{image.group(1)}">')
            else:
                out.append(f"<p>{stripped}</p>")
    out.append("</body></html>")
    path.write_text("\n".join(out), encoding="utf-8")


def emit(manifest: list[dict], outdir: Path, *, doc_id: str, title: str, category: str,
         source: str, formats: tuple[str, ...], body_md: str, purpose: str) -> int:
    """按格式落盘并登记 manifest，返回产出文件数。"""

    written = 0
    for fmt in formats:
        target_dir = outdir / fmt
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{doc_id}.{fmt}"
        try:
            if fmt == "md":
                write_markdown(path, title, category, source, body_md)
            elif fmt == "html":
                write_html(path, title, body_md)
            else:
                continue
        except Exception as error:  # noqa: BLE001
            print(f"    [落盘失败] {path.name}: {type(error).__name__}: {error}", file=sys.stderr)
            continue
        manifest.append(
            {
                "doc_id": doc_id,
                "title": title,
                "category": category,
                "source": source,
                "format": fmt,
                "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "char_count": len(body_md),
                "section_count": len(re.findall(r"^#{1,6}\s", body_md, re.M)),
                "purpose": purpose,
            }
        )
        written += 1
    return written


# ------------------------------------------------------------------ 派生样本


def build_scanned_pdf(outdir: Path, manifest: list[dict], source_pdf: Path, doc_id: str) -> int:
    """把一份已有 PDF 渲染成**无文本层**的图片型 PDF（扫描件形态）。

    扫描件的判定入口是 ``services/ingestion/parsers/ocr.py`` 的
    ``should_use_ocr``：可提取文本量低于阈值才走 OCR。
    现有的 209 份语料**全是数字原生 PDF**，这条分支一次都没被真实语料触发过 ——
    所以这里造一份真的"只有图片没有文本层"的 PDF 去触发它。
    """

    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    target_dir = outdir / "pdf"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{doc_id}.pdf"

    # 只取前 3 页：这是**形态样本**，目的是让 OCR 分支被真实文件触发，
    # 不需要整本；而且未压缩的位图按整本算会到 50MB，纯属浪费（实测教训）。
    src = fitz.open(str(source_pdf))
    out = fitz.open()
    for page in list(src)[:3]:
        pix = page.get_pixmap(dpi=110)
        new_page = out.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(new_page.rect, stream=pix.tobytes("jpeg", jpg_quality=72))
    out.save(str(path), deflate=True, garbage=4)
    out.close()
    src.close()

    manifest.append(
        {
            "doc_id": doc_id,
            "title": f"{source_pdf.stem}（扫描件·无文本层）",
            "category": "形态样本/扫描件",
            "source": f"scanned-from:{source_pdf.name}",
            "format": "pdf",
            "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "char_count": 0,
            "section_count": 0,
            "purpose": "ocr_no_text_layer",
        }
    )
    return 1


def build_gbk_text(outdir: Path, manifest: list[dict], source_md: Path, doc_id: str) -> int:
    """把一份 UTF-8 文本存成 **GBK 编码**的 .txt，触发编码回退告警。

    ``plain_text.py`` 在非 UTF-8 时会走 fallback 解码并写 ``encoding_fallback``
    警告；现有语料全是 UTF-8，这条分支同样从未被真实文件触发。
    """

    target_dir = outdir / "txt"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{doc_id}.txt"

    text = source_md.read_text(encoding="utf-8")
    path.write_bytes(text.encode("gbk", errors="replace"))

    manifest.append(
        {
            "doc_id": doc_id,
            "title": f"{source_md.stem}（GBK 编码）",
            "category": "形态样本/非UTF-8编码",
            "source": f"transcoded-from:{source_md.name}",
            "format": "txt",
            "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
            "bytes": path.stat().st_size,
            "char_count": len(text),
            "section_count": 0,
            "purpose": "encoding_fallback",
        }
    )
    return 1


DEEP_ARTICLE_RE = re.compile(r"^\s*(第[一二三四五六七八九十百零]+条)\s*(.*)$")
DEEP_ITEM_RE = re.compile(r"^\s*（([一二三四五六七八九十]+)）\s*(.*)$")


def build_deep_heading_doc(outdir: Path, manifest: list[dict], law_txt: Path, doc_id: str) -> int:
    """把法规正文按「章 → 节 → 条 → 款」生成 **四层标题**文档。

    现有语料的 heading_path 最深只有 2 层（文档标题 + section），
    h3 / h4 一次都没出现过。法规原文天然是四层结构，用它来补最合适 ——
    内容是真实的，只是把层级显式化了。
    """

    raw = law_txt.read_text(encoding="utf-8")
    lines: list[str] = [f"# {law_txt.stem}", ""]
    chapter = section = ""
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^第[一二三四五六七八九十百零]+章", stripped) or stripped in ("附则", "目录"):
            chapter, section = stripped, ""
            lines += [f"# {chapter}", ""]
        elif re.match(r"^第[一二三四五六七八九十百零]+节", stripped):
            section = stripped
            lines += [f"## {section}", ""]
        elif DEEP_ARTICLE_RE.match(stripped) and (chapter or section):
            lines += [f"### {stripped}", ""]
        elif DEEP_ITEM_RE.match(stripped) and lines and lines[-2].startswith("### "):
            lines += [f"#### {stripped}", ""]
        else:
            lines.append(stripped)

    body = "\n".join(lines).strip()
    if len(body) < ARTICLE_MIN_CHARS:
        return 0
    return emit(
        manifest, outdir,
        doc_id=doc_id,
        title=f"{law_txt.stem}（四层标题）",
        category="法律法规/深层结构",
        source=f"restructured-from:{law_txt.name}",
        formats=("md", "html"),
        body_md=body,
        purpose="deep_heading_levels",
    )


# ------------------------------------------------------------------ main


def main() -> int:
    parser = argparse.ArgumentParser(description="构建形态补缺语料样本")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--per-source", type=int, default=4, help="每个来源取多少篇")
    parser.add_argument("--sleep", type=float, default=0.8, help="抓取间隔")
    parser.add_argument("--only", default="", help="只跑指定来源（逗号分隔）")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    only = {item.strip() for item in args.only.split(",") if item.strip()}

    for name, source in SOURCES.items():
        if only and name not in only:
            continue
        print(f"\n[{name}] {source['label']}", file=sys.stderr)
        links = discover_links(source, args.per_source)
        print(f"  候选 {len(links)} 篇", file=sys.stderr)
        got = 0
        for url in links:
            if got >= args.per_source:
                break
            try:
                html = fetch_html(url)
                body = to_markdown(html)
            except Exception as error:  # noqa: BLE001
                print(f"    [跳过] {type(error).__name__}", file=sys.stderr)
                continue
            if len(body) < ARTICLE_MIN_CHARS:
                continue
            title_match = re.search(r"<title>(.*?)</title>", html, re.S)
            title = (title_match.group(1).strip() if title_match else url.rsplit("/", 1)[-1])[:60]
            title = re.sub(r"\s+", " ", title).split(" - ")[0].strip() or url
            doc_id = f"{name}_{hashlib.sha1(url.encode()).hexdigest()[:10]}"
            got += emit(
                manifest, outdir,
                doc_id=doc_id, title=title, category=source["category"],
                source=url, formats=source["formats"], body_md=body,
                purpose=source["purpose"],
            )
            print(f"    [{got}] {title[:40]} ({len(body):,} 字)", file=sys.stderr)
            time.sleep(args.sleep)
        print(f"  产出 {got} 篇", file=sys.stderr)

    # ---- 派生样本
    if not only or "derived" in only:
        print("\n[derived] 派生形态样本", file=sys.stderr)

        pdfs = sorted((PROJECT_ROOT / "data" / "corpus" / "pdf").glob("*.pdf"))
        if pdfs:
            build_scanned_pdf(outdir, manifest, pdfs[0], "scan_law_sample")
            print("    扫描件 PDF（无文本层）× 1", file=sys.stderr)

        mds = sorted((PROJECT_ROOT / "data" / "corpus" / "md").glob("*.md"))
        if mds:
            build_gbk_text(outdir, manifest, mds[0], "gbk_faq_sample")
            print("    GBK 编码 txt × 1", file=sys.stderr)

        laws = sorted((PROJECT_ROOT / "tmp" / "law_cache").glob("*.txt"))
        made = 0
        for law in laws:
            if made >= 3:
                break
            made += bool(build_deep_heading_doc(outdir, manifest, law, f"deep_law_{law.stem}"))
        print(f"    四层标题法规 × {made}", file=sys.stderr)

    manifest_path = outdir / "manifest.jsonl"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for item in manifest:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")

    total = sum(item["bytes"] for item in manifest)
    print(
        f"\n完成：{len(manifest)} 个文件 · {total / 1024 / 1024:.2f} MB\n清单：{manifest_path}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
