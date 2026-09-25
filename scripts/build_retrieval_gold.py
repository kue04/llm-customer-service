"""构造 B 轨（chunk 索引）的检索评测金标。

为什么需要这个
--------------
A 轨的检索指标用 ``candidate["source"]["intent"]`` 判相关 —— 那是**候选自带的字段**，
等于用检索器自己的一个输出给自己打分（规范 §1.0 第 13 行定性为「口径失真」）。
换成文档语料后那套口径直接算不了（台账 §4 待办第 3 条 / F2）。

本脚本从**现役索引的 manifest** 里抽「问答式小节」，把
``小节标题`` 当 query、``小节正文`` 当 gold 源 —— 这是标准 IR 评测构造，
query 来自外部结构（标题），不是候选自己的字段。

三点刻意的设计
--------------
1. **gold 用 span 而不是 chunk_id**：chunk_id 是内容哈希派生的，重切一次就全变，
   拿它当金标会让评测集随切分策略一起失效。这里记「答案的特征片段」，
   判定时只看「检索结果里有没有哪个 chunk 的正文包含这个片段」——
   重切、换索引版本、甚至换存储都还能用。
   （chunk_id 仍然落盘在 ``matched_chunk_ids`` 里，**仅供排查**，不参与判定。）
2. **三道去重**，否则指标不可信：
   - ``md`` 与 ``html`` 是同一份文档的两种渲染（54/59 个文档成对存在），
     按 ``(document_title, heading)`` 去掉；
   - 语料里存在**语义重复**的小节（「我取消订单后为什么只退了一部分钱？」
     与「我取消这单后为啥只退了一部分钱？」），若不去重，同一个正确答案
     只有一个被标 gold，检索到另一个会被算成**未命中** → 指标假性偏低。
     这里按**答案正文归一化后**去重；
   - parent / child 是同一段内容的两种粒度，只从 ``parent`` 抽，避免自我重复。
3. **span 唯一性校验**：短 span（如「请您理解哦」）会跨文档撞车，导致
   任意结果都被判命中。入选的 span 必须在**不同 document_id 数**上足够收敛。

负样本另存一个文件：它们是**语料外**的问题，用来回答「系统能不能说『没有资料』」——
这是检索质量里最容易被忽略、也最容易在真实场景翻车的一面。

用法::

    venv/Scripts/python.exe scripts/build_retrieval_gold.py
    venv/Scripts/python.exe scripts/build_retrieval_gold.py --per-doc 3 --total 80
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

DEFAULT_MANIFEST = Path(
    "data/faiss_store/chunk_index/document_chunks/v3/index_manifest.json"
)
DEFAULT_OUT = Path("data/retrieval_gold_cases.jsonl")
DEFAULT_NEG_OUT = Path("data/retrieval_negative_cases.jsonl")

HEADING_SEP = " > "

#: manifest 的 ``text`` 是「标题链 + **换行** + 正文」，**不是**竖线分隔。
#: 第一版脚本误以为分隔符是 ``" | "`` —— 那是我自己探索时 ``.replace("\n", " | ")``
#: 打印出来的假象，我却把它当成了原始格式，于是抽出的答案全是空串（踩坑 D21）。
#: 教训：**别拿自己加工过的输出当数据源**。
BODY_SEP = "\n"

PUNCT = "。！？；，、：）】"

#: 小节标题形如「1. 什么是PLUS会员？」「12、如何退款？」
QA_TITLE = re.compile(r"^\d+\s*[\.、]\s*(?P<question>.+)$")

#: 标题里带这些词的通常是协议 / 条款章节，不是问答，拿出来当 query 不合适
NON_QA_TITLE_HINTS = ("协议", "条款", "规则", "声明", "说明", "附件")

#: 答案太短 → 不是有效问答（语料里有 23 字的空壳小节）
MIN_ANSWER_CHARS = 24
MIN_QUERY_CHARS = 6
MAX_QUERY_CHARS = 48

#: span 长度窗口。太短会撞车，太长则对切分边界敏感。
MIN_SPAN_CHARS = 20
MAX_SPAN_CHARS = 44

#: 一条 span 最多允许出现在多少个**不同文档**里
MAX_SPAN_DOC_SPREAD = 2

#: 负样本：与语料领域（电商/外卖客服 + 消费相关法规）无关的问题。
#: 刻意避开法律类，因为语料里真有法规文档（广告法、消费者权益保护法）。
NEGATIVE_QUERIES = [
    "如何申请发明专利的实质审查",
    "个人所得税专项附加扣除怎么申报",
    "办理护照需要准备哪些材料",
    "高血压患者的日常饮食要注意什么",
    "Python 里怎么用装饰器缓存函数结果",
    "如何用 React 实现虚拟滚动列表",
    "去日本旅游需要提前多久订机票",
    "新能源汽车的电池衰减怎么检测",
    "考研数学应该怎么安排复习计划",
    "如何给家里的小猫做驱虫",
    "洗衣机脱水时剧烈晃动是什么原因",
    "怎么用 GIMP 给照片换背景",
    "滑雪初学者应该选单板还是双板",
    "冰箱冷藏室结冰了怎么处理",
    "小孩子几岁开始学游泳比较合适",
    "如何在家自己发豆芽",
    "吉他换弦的时候要注意什么",
    "种植多肉植物应该多久浇一次水",
    "怎么挑选适合跑步的运动鞋",
    "企业年会节目该怎么策划",
    "手机进水后第一时间应该做什么",
    "怎么判断家里是不是有白蚁",
    "学习油画需要准备哪些基础工具",
    "高铁上可以带宠物吗",
    "如何用 Photoshop 做证件照",
    "入职体检一般要查哪些项目",
    "怎么给老式自行车换链条",
    "露营的时候帐篷应该怎么选",
    "如何判断西瓜是不是熟了",
    "给宝宝冲奶粉的水温多少合适",
    "怎么练习自由泳的换气",
    "家里墙面发霉了要怎么处理",
    "如何挑选一款适合自己的键盘",
    "第一次去健身房应该练什么",
    "怎么用 Excel 做数据透视表",
    "养金鱼的水多久换一次",
    "怎样把旧手机的数据迁移到新手机",
    "冬天手部皮肤干裂怎么办",
    "如何准备一场半程马拉松",
    "怎么判断家里的甲醛是否超标",
]


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def flatten(text: str) -> str:
    """把所有空白序列压成单个空格。

    **判据必须用同一个函数处理两边**：``span`` 是从答案里抽的、已被压平；
    而 chunk 的 ``text`` 里正文是带 ``\\n`` 的原文。不归一化就会出现
    「肉眼看着一样、``in`` 判定为假」的静默未命中。
    """

    return " ".join(text.split())


def split_entry_text(entry: dict) -> tuple[str, str]:
    """把 manifest 的 ``text`` 拆成（标题链, 正文）。

    ``text`` 的形态是 ``"标题1 > 标题2\\n正文"``（见 ``ManifestEntry.text`` 的注释）。
    正文内部可能还有换行，所以**只切第一个分隔符**。
    """

    text = entry.get("text") or ""
    heading_path = entry.get("heading_path") or []
    prefix = HEADING_SEP.join(heading_path)
    if prefix and text.startswith(prefix):
        rest = text[len(prefix) :]
        if rest.startswith(BODY_SEP):
            return prefix, rest[len(BODY_SEP) :]
        # 标题后直接跟正文（没有分隔符）的形态也要认
        return prefix, rest
    if BODY_SEP in text:
        head, body = text.split(BODY_SEP, 1)
        return head, body
    return prefix, ""


def normalize_for_dedupe(text: str) -> str:
    """去重用的归一化：只留中日韩字符与字母数字，压缩空白。"""

    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text).lower()


def take_span_window(flat: str, start: int) -> str:
    """从 ``flat`` 的 ``start`` 处取窗口：起点对齐句首，终点尽量落在句读上。

    起点对齐是必要的 —— 直接从任意偏移切会产出
    「的京东】-点击【余额】；（3）微信支付」这种残句。它当判定锚点虽然能用
    （完全对不上的会被自检拦住），但**人没法复核它对不对**，报告里也没法看。
    """

    if start > 0:
        for index in range(start, min(start + 40, len(flat))):
            if flat[index] in PUNCT:
                start = index + 1
                break
    window = flat[start : start + MAX_SPAN_CHARS]
    if len(window) < MIN_SPAN_CHARS:
        return ""
    for index, char in enumerate(window):
        if index >= MIN_SPAN_CHARS and char in PUNCT:
            return window[:index]
    return window


def span_candidates(answer: str) -> list[str]:
    """给一条答案生成多个候选 span，按信息量排序。

    为什么不能只取开头：客服话术的开头高度模板化（「抱歉给您带来不好的体验」
    「如您已成功开通」），拿它当金标会跨文档撞车 —— 那样任何结果都会被判命中。
    所以**最长的句子排第一**（客服回复里最长的句子往往承载具体规则），
    开头只作为兜底候选。
    """

    flat = flatten(answer)
    if len(flat) < MIN_SPAN_CHARS:
        return []

    candidates: list[str] = []
    sentences = [
        part for part in re.split(r"[。！？；\n]", flat) if len(part) >= MIN_SPAN_CHARS
    ]
    if sentences:
        longest = max(sentences, key=len)[:MAX_SPAN_CHARS]
        candidates.append(longest)

    for offset in (0, len(flat) // 3, len(flat) // 2):
        window = take_span_window(flat, offset)
        if window and window not in candidates:
            candidates.append(window)
    return candidates


def extract_qa_pairs(entries: list[dict]) -> list[dict]:
    """抽出「问答式小节」候选（parent 级，已按标题去 md/html 双份）。"""

    seen: dict[tuple[str, str], dict] = {}
    for entry in entries:
        if entry.get("chunk_type") != "parent":
            continue
        heading_path = entry.get("heading_path") or []
        if len(heading_path) < 2:
            continue
        heading = heading_path[-1]
        if len(heading) > MAX_QUERY_CHARS + 12 or not heading.rstrip().endswith("？"):
            continue
        if any(hint in heading for hint in NON_QA_TITLE_HINTS):
            continue
        match = QA_TITLE.match(heading)
        if not match:
            continue

        question = match.group("question").strip()
        if not (MIN_QUERY_CHARS <= len(question) <= MAX_QUERY_CHARS):
            continue

        _prefix, answer = split_entry_text(entry)
        if len(" ".join(answer.split())) < MIN_ANSWER_CHARS:
            continue

        document_title = entry.get("document_title") or ""
        # md / html 是同一份文档的两种渲染 → 按（文档, 小节）保留一份
        key = (document_title, question)
        if key in seen:
            # 同一小节在 md/html 里都有；挑正文更长的那个（渲染差异可能截断）
            if len(answer) > len(seen[key]["answer"]):
                seen[key] = {"answer": answer, "entry": entry, "question": question,
                             "document_title": document_title}
            continue
        seen[key] = {"answer": answer, "entry": entry, "question": question,
                     "document_title": document_title}

    return list(seen.values())


def dedupe_by_answer(candidates: list[dict]) -> tuple[list[dict], int]:
    """按答案正文归一化去重 —— 语料里有语义重复的小节。"""

    kept: list[dict] = []
    seen_answers: dict[str, dict] = {}
    dropped = 0
    for item in candidates:
        key = normalize_for_dedupe(item["answer"])[:120]
        if key in seen_answers:
            dropped += 1
            continue
        seen_answers[key] = item
        kept.append(item)
    return kept, dropped


class SpanSpread:
    """惰性计算「一条 span 出现在多少个不同 document_id 的 chunk 里」。

    **按需计算**而不是一次性把所有候选都算完：绝大多数条目第一个候选就通过，
    全量预计算会把 ``候选数 × 条目数`` 次子串扫描全部跑一遍（约 200 万次）。
    """

    def __init__(self, entries: list[dict]) -> None:
        self._flat_entries = [
            (flatten(entry.get("text") or ""), entry.get("document_id")) for entry in entries
        ]
        self._cache: dict[str, int] = {}

    def of(self, span: str) -> int:
        cached = self._cache.get(span)
        if cached is not None:
            return cached
        seen_docs: set = set()
        limit = MAX_SPAN_DOC_SPREAD + 2
        for text, document_id in self._flat_entries:
            if span in text:
                seen_docs.add(document_id)
                if len(seen_docs) > limit:
                    break
        result = len(seen_docs)
        self._cache[span] = result
        return result


def dedupe_by_query(
    candidates: list[dict], *, min_overlap_ratio: float = 0.7
) -> tuple[list[dict], int]:
    """按 query 去重：短的那条被长的包含时，视为同一个问题。

    语料里存在「加了口语前缀的同一问题」：``「我点错门店了，距离很远怎么办？」``
    与 ``「麻烦问下，我点错门店了，距离很远怎么办？」`` —— 字面不同、语义相同，
    而两条的答案分属不同小节，所以按答案去重抓不住。

    ``min_overlap_ratio`` 是为了避免过度合并：``「如何退款」`` 与
    ``「如何退款并开发票」`` 也有子串关系，但它们是**不同的**问题。
    要求短的那条至少占长的 70% 才判重。
    """

    kept: list[dict] = []
    keys: list[str] = []
    dropped = 0
    for item in candidates:
        key = normalize_for_dedupe(item["question"])
        duplicate = False
        for existing in keys:
            shorter, longer = sorted((key, existing), key=len)
            if shorter and shorter in longer and len(shorter) >= len(longer) * min_overlap_ratio:
                duplicate = True
                break
        if duplicate:
            dropped += 1
            continue
        keys.append(key)
        kept.append(item)
    return kept, dropped


def stratified_sample(
    candidates: list[dict], *, per_doc: int, total: int, seed: int
) -> list[dict]:
    """按文档分层抽样，避免评测集被某个大文档（如「常见问答」105 条）垄断。"""

    by_doc: dict[str, list[dict]] = defaultdict(list)
    for item in candidates:
        by_doc[item["document_title"]].append(item)

    rng = random.Random(seed)
    picked: list[dict] = []
    for _doc, items in sorted(by_doc.items()):
        rng.shuffle(items)
        picked.extend(items[:per_doc])

    rng.shuffle(picked)
    return picked[:total]


def find_negative_spread(entries: list[dict], query: str) -> int:
    """负样本自检：确认语料里真的没有高度重合的 chunk。

    判据是「有多少 chunk 的正文完整包含这条 query 的核心片段」——
    如果为 0，说明语料里确实没有现成答案。
    """

    core = normalize_for_dedupe(query)
    if len(core) < 4:
        return -1
    probe = core[:8]
    hits = 0
    for entry in entries:
        if probe in normalize_for_dedupe(entry.get("text") or ""):
            hits += 1
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="构造 B 轨检索评测金标")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--neg-out", type=Path, default=DEFAULT_NEG_OUT)
    parser.add_argument("--per-doc", type=int, default=4, help="每个文档最多取几条")
    parser.add_argument("--total", type=int, default=100, help="正样本总条数上限")
    parser.add_argument("--seed", type=int, default=20260925)
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"[FAIL] manifest 不存在：{args.manifest}")
        return 2

    manifest = load_manifest(args.manifest)
    entries = manifest.get("entries") or []
    print(f"索引：{args.manifest}")
    print(f"  version={manifest.get('index_version')} entries={len(entries)} "
          f"model={manifest.get('embedding_model')}")

    raw = extract_qa_pairs(entries)
    print(f"\n[1] 抽到问题式小节（parent，标题去重后）：{len(raw)}")

    deduped, dropped = dedupe_by_answer(raw)
    print(f"[2] 按答案去重：保留 {len(deduped)}（丢弃语义重复 {dropped}）")

    deduped, dropped_query = dedupe_by_query(deduped)
    print(f"[2b] 按 query 去重：保留 {len(deduped)}（丢弃加前缀的同一问题 {dropped_query}）")

    sample = stratified_sample(deduped, per_doc=args.per_doc, total=args.total,
                               seed=args.seed)
    print(f"[3] 分层抽样：{len(sample)} 条，覆盖 "
          f"{len({item['document_title'] for item in sample})} 个文档")

    # span 唯一性校验：多候选逐个试，取第一个「够独特」的
    spread = SpanSpread(entries)
    cases = []
    rejected = 0
    used_spans: set[str] = set()
    for item in sample:
        chosen_span = ""
        chosen_spread = -1
        for candidate in span_candidates(item["answer"]):
            # 同一个答案被两条 query 共享时只保留一条 ——
            # 按答案文本去重抓不住「正文措辞微调、核心句一致」的重复
            # （如 g058 与 g069 曾共用同一条 span），这里按 span 再兜一次。
            if candidate in used_spans:
                continue
            doc_spread = spread.of(candidate)
            if doc_spread <= MAX_SPAN_DOC_SPREAD:
                chosen_span, chosen_spread = candidate, doc_spread
                break
        if not chosen_span:
            rejected += 1
            continue
        used_spans.add(chosen_span)

        entry = item["entry"]
        cases.append(
            {
                "id": f"g{len(cases) + 1:03d}",
                "query": item["question"],
                "query_type": "title",
                "gold_document_title": item["document_title"],
                "gold_document_id": entry.get("document_id"),
                "gold_heading": (entry.get("heading_path") or [""])[-1],
                "gold_span": chosen_span,
                "span_doc_spread": chosen_spread,
                "matched_chunk_ids": [entry.get("chunk_id")],
                "source": "weak-supervision",
            }
        )

    print(f"[4] span 唯一性校验：保留 {len(cases)}，剔除 {rejected}"
          f"（候选 span 被超 {MAX_SPAN_DOC_SPREAD} 个文档共用，或与已入选条目重复）")

    # 自检：每条 span 必须能在它**自己的 gold chunk** 里找到。
    # 找不到 = 抽取口径与判定口径不一致（例如一边压平了空白、另一边没有），
    # 那样评测会「跑通但全错」——每一条都判未命中，数字看着有、其实全是噪声。
    entry_by_chunk = {entry.get("chunk_id"): entry for entry in entries}
    selfcheck_failed = [
        case["id"]
        for case in cases
        if case["gold_span"]
        not in flatten(
            (entry_by_chunk.get(case["matched_chunk_ids"][0]) or {}).get("text") or ""
        )
    ]
    if selfcheck_failed:
        print(f"\n[FAIL] span 自检未通过 {len(selfcheck_failed)} 条：{selfcheck_failed[:5]}")
        print("       抽取口径与判定口径不一致，拒绝产出评测集。")
        return 1
    print(f"[5] span 自检：{len(cases)}/{len(cases)} 条都能在各自的 gold chunk 里找到")

    # 负样本
    neg_cases = []
    checked_clean = 0
    for index, query in enumerate(NEGATIVE_QUERIES, start=1):
        probe_hits = find_negative_spread(entries, query)
        if probe_hits == 0:
            checked_clean += 1
        neg_cases.append(
            {
                "id": f"n{index:03d}",
                "query": query,
                "query_type": "negative",
                "gold_span": None,
                "corpus_probe_hits": probe_hits,
                "note": "语料外问题，期望系统不给出高置信度答案",
                "source": "handwritten",
            }
        )
    print(f"[6] 负样本：{len(neg_cases)} 条，其中语料完全无重合 {checked_clean} 条")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")
    with args.neg_out.open("w", encoding="utf-8", newline="\n") as handle:
        for case in neg_cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(f"\n写出：{args.out}（{len(cases)} 条）")
    print(f"写出：{args.neg_out}（{len(neg_cases)} 条）")

    print("\n=== 前 6 条正样本 ===")
    for case in cases[:6]:
        print(f"  [{case['id']}] {case['query']}")
        print(f"        span: {case['gold_span']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
