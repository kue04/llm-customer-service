"""爬取京东帮助中心 FAQ，作为知识库扩容的原始数据源（路线 B）。

目标结构：
  1. help.jd.com/user/issue.html 内嵌完整分类树（data-id/data-name/data-parent-*）
  2. 每个分类页 list-<id>.html 列出文章链接 <catid>-<aid>.html
  3. 每篇文章页：问题在 div.help-tit1，答案在 #pdfContainer .contxt 的段落里（GBK 编码）

输出：data/raw/jd_help_faq.jsonl.gz（gzip，控制仓库体积在 1MB 门槛内）
  {url, cat_id, category, parent_category, question, answer, crawled_at}

支持断点续爬：已存在的 URL 会被跳过（按 --resume）。

用法：
    python scripts/crawl_jd_help.py                  # 全量爬取
    python scripts/crawl_jd_help.py --limit 20       # 限条数冒烟
    python scripts/crawl_jd_help.py --resume         # 续爬
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
ISSUE_HOME = "https://help.jd.com/user/issue.html"
OUTPUT_PATH = PROJECT_ROOT / "data" / "raw" / "jd_help_faq.jsonl.gz"
SLEEP_SECONDS = 0.3

TREE_LI_RE = re.compile(
    r'<li class="list-item" data-id="(\d+)" data-name="([^"]+)" data-parent-id="(\d+)"\s*'
    r'data-parent-name="([^"]*)"'
)
LIST_URL_TMPL = "https://help.jd.com/user/issue/list-{cat_id}.html"
ARTICLE_HREF_RE = re.compile(r'href="(//help\.jd\.com/user/issue/(\d+)-(\d+)\.html)"')
QUESTION_RE = re.compile(r'<div class="help-tit1[^"]*"[^>]*>([\s\S]*?)</div>')
CONTXT_RE = re.compile(r'<div class="contxt"[^>]*>([\s\S]*?)</div>\s*(?:</div>|<div)')
P_TAG_RE = re.compile(r"<p[^>]*>([\s\S]*?)</p>")
TAG_RE = re.compile(r"<[^>]+>")


def fetch(url: str, retries: int = 2) -> bytes:
    last_err: Exception | None = None
    for _ in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(1.0)
    raise RuntimeError(f"fetch failed: {url}: {last_err}")


def decode_gbk(raw: bytes) -> str:
    return raw.decode("gbk", errors="ignore")


def clean_html(fragment: str) -> str:
    text = TAG_RE.sub("", fragment)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def parse_tree(html: str) -> dict[int, dict]:
    """解析分类树：{cat_id: {name, parent_id, parent_name}}"""
    tree: dict[int, dict] = {}
    for cat_id, name, parent_id, parent_name in TREE_LI_RE.findall(html):
        tree[int(cat_id)] = {
            "name": name.strip(),
            "parent_id": int(parent_id),
            "parent_name": parent_name.strip(),
        }
    return tree


def parse_article(html: str) -> tuple[str, str]:
    """从文章页提取 (question, answer)。"""
    q_match = QUESTION_RE.search(html)
    question = clean_html(q_match.group(1)) if q_match else ""

    c_match = CONTXT_RE.search(html)
    answer = ""
    if c_match:
        paragraphs = [clean_html(p) for p in P_TAG_RE.findall(c_match.group(1))]
        answer = "\n".join(p for p in paragraphs if p)
    if not answer:
        # 兜底：抓取 help-tit1 之后到投票区域之前的纯文本
        tail = html[q_match.end() :] if q_match else html
        stop = re.search(r"是否有帮助|投票|相关推荐|感兴趣", tail)
        region = tail[: stop.start()] if stop else tail[: 6000]
        answer = clean_html(re.sub(r"<[^>]+>", "\n", region))
        answer = "\n".join(line for line in answer.splitlines() if len(line) >= 6)
    return question, answer


def load_crawled_urls(path: Path) -> set[str]:
    if not path.exists():
        return set()
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return {json.loads(line)["url"] for line in f if line.strip()}


def crawl(args: argparse.Namespace) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    done = load_crawled_urls(OUTPUT_PATH) if args.resume else set()

    home = decode_gbk(fetch(ISSUE_HOME))
    tree = parse_tree(home)
    print(f"分类树：{len(tree)} 个分类节点")
    if not tree:
        print("未解析到分类树，退出")
        return

    # 收集所有分类页的文章链接
    articles: dict[str, dict] = {}  # url -> {cat_id, category, parent_category}
    for cat_id, meta in sorted(tree.items()):
        if args.categories and meta["parent_name"] not in args.categories and meta["name"] not in args.categories:
            continue
        time.sleep(SLEEP_SECONDS)
        list_html = decode_gbk(fetch(LIST_URL_TMPL.format(cat_id=cat_id)))
        for href, _aid, _cat in ARTICLE_HREF_RE.findall(list_html):
            url = "https:" + href
            if url not in articles:
                articles[url] = {
                    "cat_id": cat_id,
                    "category": meta["name"],
                    "parent_category": meta["parent_name"],
                }
    print(f"待爬文章：{len(articles)} 篇（已完成 {len(done)}）")

    written = 0
    with gzip.open(OUTPUT_PATH, "at", encoding="utf-8") as out:
        for url, meta in sorted(articles.items()):
            if url in done:
                continue
            if args.limit and written >= args.limit:
                break
            time.sleep(SLEEP_SECONDS)
            try:
                html = decode_gbk(fetch(url))
                question, answer = parse_article(html)
            except Exception as exc:  # noqa: BLE001
                print(f"  [skip] {url}: {exc}")
                continue
            if not question or len(answer) < 20:
                continue
            record = {
                "url": url,
                "cat_id": meta["cat_id"],
                "category": meta["category"],
                "parent_category": meta["parent_category"],
                "question": question,
                "answer": answer,
                "crawled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            written += 1
            if written % 20 == 0:
                print(f"  已爬 {written} 篇…")

    print(f"完成：本次新增 {written} 篇 → {OUTPUT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description="爬取京东帮助中心 FAQ")
    parser.add_argument("--limit", type=int, default=0, help="最多爬取条数（0=不限）")
    parser.add_argument("--resume", action="store_true", help="断点续爬")
    parser.add_argument(
        "--categories",
        nargs="*",
        default=None,
        help="只爬指定顶级/子分类（按 data-parent-name 或 data-name 过滤）",
    )
    args = parser.parse_args()
    crawl(args)


if __name__ == "__main__":
    main()
