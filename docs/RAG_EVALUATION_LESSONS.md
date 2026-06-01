# 外卖客服 RAG 评估与迭代复盘

本文记录当前外卖客服 RAG 项目从“能回答”推进到“可评估、可归因、可稳定优化”的关键学习结论。

这轮迭代的重点不是继续堆功能，而是学会用工程方法判断一个坏回答到底应该改哪一层。

## 当前结果

最新 grounding 评估集规模：

```text
total_cases = 30
```

最新核心指标：

```text
top1_intent_hit_rate = 1.0
judge_pass_count = 30/30
judge_pass_rate = 1.0
forbidden_hit_count = 0
direct_answer yes = 30/30
grounded yes = 30/30
useful yes = 30/30
```

这说明当前固定评估集上的主要链路已经跑通：

```text
retrieval
-> primary evidence
-> answer_composer
-> reply_rules
-> judge calibration
-> grounding analysis
```

需要注意：`30/30` 不代表系统已经泛化完美，只代表当前固定评估集已经收敛。后续如果扩充相邻表达、长尾问题或真实线上问题，还需要继续评估。

## RAG 链路分层

当前聊天链路可以理解为：

```text
用户问题
-> hybrid retrieval + bge reranker
-> Top1 primary evidence
-> prompt / local model generation
-> answer_composer 稳定渲染
-> reply_rules 高风险兜底
-> LLM-as-judge / grounding report
```

每一层职责不同，不能混在一起改。

## 1. Retrieval 负责找对证据

Retrieval 层回答的问题是：

```text
系统有没有找到正确业务意图和相关证据？
```

主要观察字段：

```text
retrieved_items
top1_intent_hit_rate
expected_intent
matched_evidence_keywords
missing_evidence_keywords
```

如果 `top1_intent_hit_rate` 低，优先改：

```text
知识库样本
query 表达覆盖
keyword bonus
direction penalty
reranker 权重
```

这轮中：

```text
top1_intent_hit_rate = 1.0
```

所以后续 bad case 不应该优先归因到 retrieval。继续调检索不仅收益小，还可能破坏已稳定的 Top1 排序。

关键经验：

```text
Top1 intent 已经正确时，不要凭感觉继续改 retrieval。
先看 reply 是否真正使用好了 evidence。
```

## 2. Prompt 和模型负责生成，但不负责最终稳定性

模型生成层回答的问题是：

```text
模型有没有根据证据生成自然语言回复？
```

但模型天然有不稳定性：

```text
同一条证据可能换不同说法
有时先说原因，有时先说步骤
有时加泛化尾巴
有时漏掉必要步骤
```

所以不能把所有稳定性要求都交给模型。

这轮也做过在线 API A/B 实验，结论是：

```text
更强模型不一定让当前链路更稳。
如果 answer rendering 层没有收束，模型可能仍然输出不够直接或重复的回答。
```

关键经验：

```text
模型能力强不等于产品答案稳定。
RAG 应用需要工程层把模型输出收束成可控形态。
```

## 3. Answer Composer 负责把证据稳定渲染成答案

`answer_composer` 的职责不是“坏回复替换器”，而是：

```text
top1 evidence
-> conclusion
-> action
-> caveat
-> 短答渲染
```

它解决的是：

```text
同一条证据如何稳定说给用户听？
```

本轮 composer 的主要改动：

```text
1. 固定部分 intent 的直接首句
2. 补稳定 required action
3. 补 caveat
4. 去掉重复或重叠句子
```

典型例子：

```text
用户：我取消订单后为什么只退了一部分钱

稳定回答：
订单取消后只退一部分，通常与商家是否已制作、配送是否已开始、优惠券抵扣或平台规则有关。
您可以在退款详情页查看扣除原因，如有疑问可提交售后复核。
```

这里第一句先回答“为什么”，第二句再给操作入口。

再比如：

```text
用户：骑手一直停在一个地方不动怎么办

稳定回答：
骑手位置一直停在一个地方，通常可能与等待出餐、网络延迟或路况有关。
建议您先刷新订单详情页并尝试联系骑手；如果位置长时间不更新、联系不上或仍无进展，可以在订单详情页反馈配送异常。
```

这里去掉了容易触发 forbidden 的“不送了”表达，同时保留用户需要的下一步。

关键经验：

```text
当 retrieval 正确但回答不直接，优先改 composer。
Composer 是 evidence 到 user-facing answer 的确定性渲染层。
```

## 4. Reply Rules 只做高风险兜底

`reply_rules` 不应该承担所有话术优化。

适合放进 `reply_rules` 的是：

```text
验证码
私下转账
食品安全
明显高风险赔付承诺
商家拒绝退款等需要强兜底的场景
```

不适合放进 `reply_rules` 的是：

```text
普通首句不够直接
普通步骤顺序不稳定
普通表达重复
```

这些应该归 `answer_composer`。

原因很简单：如果把普通话术都堆到 reply rules，系统会变成 case-by-case 规则表，后续维护成本很高，也容易相互覆盖。

关键经验：

```text
reply_rules 是安全边界，不是通用写作层。
```

## 5. Judge 也需要校准

LLM-as-judge 不是绝对真理，它也是模型，也会误判。

这轮最典型的 case：

```text
用户：商家电话在哪里看
```

系统回复：

```text
商家电话一般在订单详情页或商家主页的“联系商家”入口查看，有电话时会在该入口展示。
建议您优先通过平台内电话或在线联系功能沟通。
如果页面没有展示电话，说明商家可能未开放电话联系或使用平台虚拟号。
```

Top1 证据本身就是：

```text
商家电话或联系商家入口一般可以在订单详情页查看，也可以进入商家主页查找“联系商家”入口。
```

但 judge 曾经判断：

```text
文档未明确商家电话位置
```

这个判断和证据冲突，所以这里应该校准 judge，而不是继续强行改 composer。

本轮已有 calibration 类型：

```text
退款时间：证据没有固定天数时，不要求回答编造具体时间
验证码/隐私/私下转账：保守安全回答可视为有效
商家电话：联系商家入口就是证据支持的查看路径
```

关键经验：

```text
评估系统也需要被评估。
不要把 judge 的每个 no 都当成业务链路错误。
```

## 6. Bad Case 分析顺序

不要看到 bad case 就直接改 prompt。

推荐顺序：

```text
1. 看 retrieved_items[0].intent 是否正确
2. 看 top1 evidence 是否包含必要关键词
3. 看 final_prompt 是否正确组织 primary evidence
4. 看 reply 是否用了证据
5. 看 reply 是否直接、有用、无风险
6. 看 judge_reason 是否合理
```

对应修复层：

```text
Top1 错误 -> retrieval / rerank / knowledge
Top1 正确但证据不足 -> knowledge base
证据足够但回答没用好 -> prompt / answer_composer
高风险表达 -> reply_rules
回答正确但 judge 误判 -> judge calibration
```

这轮从 `25/30` 到 `30/30`，主要不是靠换模型，而是靠：

```text
answer_composer refinement
judge calibration
少量高风险边界修正
```

## 7. 为什么要保存完整报告

Grounding report 不只是看一个分数。

它应该保存：

```text
query
retrieved_items
prompt_context_items
final_prompt
reply
trace
manual_judgment
raw_judge_response
judge_error
```

这样每个 bad case 才能复盘：

```text
是检索错？
是证据不够？
是 prompt 没组织好？
是模型没用证据？
是 composer 渲染不稳？
是 reply_rules 没兜住？
还是 judge 误判？
```

关键经验：

```text
没有中间状态，就没有可靠归因。
没有可靠归因，就只能凭感觉改 prompt。
```

## 8. 当前项目的工程亮点

当前项目已经具备一个学习型 RAG 系统的完整闭环：

```text
FastAPI service
JSONL knowledge base
keyword retrieval
vector retrieval
hybrid search
bge reranker
FAISS persistence
prompt preview
retrieved_items / final_prompt / trace
answer_composer
reply_rules
30-case grounding evaluation
bad case attribution
judge calibration
```

这已经不是一个简单 Demo，而是一个可以解释、评估、复盘的大模型应用工程项目。

## 9. 下一阶段建议

当前 30 条固定集已经全过，下一步不要继续只调旧 case。

更好的方向是：

```text
扩充 5-10 条相邻表达 case
增加真实用户口语化问法
加入多轮上下文变体
观察是否仍然保持高通过率
```

也可以补一个 grounding report compare 工具，对比两次报告：

```text
judge_pass_rate 变化
forbidden_hit_count 变化
improved cases
worsened cases
unchanged bad cases
```

但扩集前最好先记住这条原则：

```text
不要为了固定评估集 30/30 而过拟合。
30/30 是阶段性收敛信号，不是最终质量证明。
```

## 面试讲解版本

可以这样介绍这段项目经历：

```text
我做了一个外卖客服 RAG 项目，不只是调用大模型回答问题，而是搭了一条可观测、可评估、可归因的应用链路。

检索层使用 hybrid retrieval 和 bge reranker，接口会返回 retrieved_items、score breakdown、final_prompt 和 trace。

在评估侧，我构建了 30 条固定 grounding evaluation case，使用 LLM-as-judge 判断 direct_answer、grounded 和 useful，同时保留 raw_judge_response 和 bad case 归因。

当 top1_intent_hit_rate 达到 1.0 后，我没有继续调 retrieval，而是把问题定位到 answer rendering。于是新增 answer_composer，根据 top1 evidence 稳定抽取 conclusion、action、caveat，并做去重和必要步骤补全。

同时我区分了 reply_rules 和 composer 的职责：reply_rules 只做高风险兜底，composer 负责普通答案稳定渲染。最后对明显不合理的 judge 误判做 calibration，比如退款时间没有固定天数、验证码安全回答、商家电话入口说明等。

最终固定评估集从 25/30 提升到 30/30，forbidden_hit_count 归零。这个过程让我理解了真实 LLM 应用不是只调 prompt，而是要分层定位 retrieval、generation、rendering、safety 和 evaluation 的责任边界。
```

