"""从弱监督金标里派生出**口语化 query** 子集。

为什么必须做这一步
------------------
弱监督金标的 query 是**文档小节标题**，用词和正文高度重合。实测：
纯 BM25 在它上面的 Recall@1（0.6588）反而**高于**稠密向量（0.5529），
且 top-10 层面「仅稀疏命中」3 条、「仅稠密命中」**0 条** ——
稠密路看起来完全冗余。

但这几乎肯定是**评测集偏差**造成的：标题式 query 天然利好词法匹配。
真实用户不会说「售后返修单提交成功后多久可以进入审核阶段？」，
他说的是「返修的东西寄过去了，要等多久才开始审」。

所以这里人工改写一批 query，**刻意换掉与正文重合的词**（专有名词保留），
语义指向不变。这份集合才是「用户真实提问」的代理，
它能回答一个标题集回答不了的问题：**向量路的语义泛化到底值多少**。

改写原则
--------
- 换掉正文里的动词/名词短语，换成同义的口语说法；
- 保留专有名词（「京东E卡」「京享值」），否则语义指向就变了；
- 不复用正文里的连续 4 字以上片段（那等于还是词法匹配）；
- gold 不做任何改动 —— 换的是问法，不是答案。

用法::

    venv/Scripts/python.exe scripts/build_colloquial_cases.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_GOLD = Path("data/retrieval_gold_cases.jsonl")
DEFAULT_OUT = Path("data/retrieval_colloquial_cases.jsonl")

#: ``(金标里的原 query, 人工改写的口语化 query)``
#: 改写刻意避开正文用词 —— 见模块 docstring。
REWRITES: list[tuple[str, str]] = [
    ("京品加油可以开发票吗？", "加油那个服务能开票不"),
    ("京东卡/京东E卡能开具的发票抬头是什么？", "用卡付的钱，开票时抬头应该写谁"),
    ("如何提交售后申请？", "东西有问题想找你们处理，从哪儿提交"),
    ("自提订单是否收费（自提订单运费）？", "自己去拿的单子还要不要收运费"),
    ("何人有权发起知产投诉？", "商标被冒用这种，谁有资格去投诉"),
    ("评价晒单可以修改吗？", "我写的评价发出去了还想改，行不行"),
    ("京品加油支持哪些支付方式？是否支持使用优惠券？", "加油付款能用哪些方式，优惠券能不能用"),
    ("如何查看投诉进程和处理结果？", "投诉交上去了，怎么知道现在处理到哪一步"),
    ("货到付款可以POS机刷卡吗？", "东西送到门口再给钱，能不能刷银行卡"),
    ("怎么修改已经绑定的手机号？", "之前绑定的号码想换成新的，在哪儿改"),
    ("下单之后如何修改商品的数量和颜色？", "单子下了以后想改买几件、换个颜色行不行"),
    ("售后审核一般要多久？", "提交的售后多久能审完"),
    ("如何在我的营业厅办理流量包？", "想充个流量包，在哪个页面弄"),
    ("我购买了商家的商品，我想知道用的是什么快递给我配送的？", "帮我看看这单是哪家快递送过来的"),
    ("订单拒收后，退款什么时候返还？", "东西我没收退回去了，钱什么时候能回来"),
    ("一拍、二拍、变卖是什么？", "拍卖分的那几个阶段都是什么意思"),
    ("为什么我不能退换货？", "想退换结果说不让，这是为什么"),
    ("京豆的有效期是多久？", "那些积分攒着会不会过期"),
    ("一个京东账户可以关联几个增值税发票资质？", "一个账号最多能挂几个开专票的资质"),
    ("什么是电器延保险？", "买家电时推的那个延长保修是什么东西"),
    ("自提点/自提柜可以保留货物几天？", "东西放在柜子里最多能放几天"),
    ("京东支付如何申请退款？", "用这个付的钱想退回，怎么操作"),
    ("平台支付可支持哪些付款方式，如何使用？", "这个付款都能用哪些渠道，具体怎么用"),
    ("京东自营商品售后服务细则是什么？", "自己家卖的东西售后是按什么规矩来的"),
    ("买贵双倍赔什么时候完成审核？", "那个双倍赔差的申请要多久才审完"),
    ("我提交售后后还能修改理由吗？", "售后申请交上去了，之前选的原因填错了还能改吗"),
    ("增值税普通发票（电子）下载有没有次数限制？", "电子发票能重复下载几次，有没有限制"),
    ("家电产品可以预约多长时间之内配送？", "家电大概能约到几天后送"),
    ("预售商品可以同时购买多件吗？", "预售的东西能不能一次买好几件"),
    ("怎么查询我的保证金？", "交过的押金在哪儿能查到"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="派生口语化 query 子集")
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.gold.exists():
        print(f"[FAIL] 金标不存在：{args.gold}（先跑 build_retrieval_gold.py）")
        return 2

    rows = [
        json.loads(line)
        for line in args.gold.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_query = {row["query"]: row for row in rows}

    cases = []
    missing: list[str] = []
    for source_query, colloquial in REWRITES:
        source = by_query.get(source_query)
        if source is None:
            missing.append(source_query)
            continue
        cases.append(
            {
                "id": f"c{len(cases) + 1:03d}",
                "query": colloquial,
                "query_type": "colloquial",
                "rewritten_from_query": source_query,
                "gold_document_title": source["gold_document_title"],
                "gold_document_id": source["gold_document_id"],
                "gold_heading": source["gold_heading"],
                "gold_span": source["gold_span"],
                "source": "handwritten",
            }
        )

    # 断言：每条改写都必须能在金标里找到出处。
    # 不做这一步的话，金标一旦重新生成、query 变了，改写会**静默失效**
    # （产出条数变少但没人发现）。
    if missing:
        print(f"[FAIL] 有 {len(missing)} 条改写找不到对应金标条目：")
        for query in missing:
            print(f"       {query}")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(f"金标 {len(rows)} 条 → 口语化子集 {len(cases)} 条")
    print(f"写出：{args.out}")

    # 自检：改写后的 query 不应与 gold span 有长片段重合（否则等于没改写）
    spans = [case["gold_span"] for case in cases]
    overlap = 0
    for case in cases:
        query = case["query"]
        for offset in range(max(len(query) - 3, 1)):
            fragment = query[offset : offset + 4]
            if len(fragment) == 4 and any(fragment in span for span in spans):
                overlap += 1
                break
    print(f"自检：改写后仍有 4 字以上片段与 gold span 重合的条数 = {overlap}"
          f"（越少说明越「脱离原文用词」）")

    print("\n=== 前 8 条 ===")
    for case in cases[:8]:
        print(f"  [{case['id']}] {case['query']}")
        print(f"        原: {case['rewritten_from_query']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
