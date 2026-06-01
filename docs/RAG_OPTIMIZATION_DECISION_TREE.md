# RAG 优化决策树

这份文档记录当前外卖客服 RAG 项目的优化判断方法。目标不是看到坏 case 就立刻改代码，而是先判断问题属于哪一层。

当前链路：

```text
用户问题
-> 知识库样本 / FAISS 召回
-> keyword bonus / direction penalty
-> intent hint / supplement
-> rerank
-> answer_composer
-> reply_rules
-> judge / grounding report
```

## 总原则

每次只改一层，改完用报告验证。

```text
先看证据是否找对
再看回答是否用好证据
最后看 judge 是否误判
```

不要因为一个 bad case 同时改样本、检索、composer 和 judge。这样虽然可能让 case 通过，但你不知道到底是哪一层起作用。

## 1. 什么时候补样本

优先看：

```text
top1_intent_hit_rate
retrieved_items
retrieval_trace
audit_knowledge_base.py
```

适合补样本的信号：

```text
1. 正确 intent 没被 FAISS 自然召回
2. hint_supplement_added=True，经常靠 supplement 救回来
3. 某个 intent sample_count 很低
4. 审计显示 missing_emotional_expression / missing_boundary_expression
5. 用户表达和知识库 question 表达差异很大
```

例子：

```text
用户：我都没吃上凭什么还扣我钱
问题：原始召回更像“银行卡扣款异常”
处理：给“退款金额咨询”补情绪/边界表达样本
```

不要补样本的情况：

```text
1. Top1 intent 已经正确
2. 证据里已经有答案
3. 只是回答开头不直接
4. 只是 judge 没认可同义表达
```

## 2. 什么时候改检索 / rerank

适合改检索的信号：

```text
1. 多个 case 的 Top1 intent 系统性错误
2. 正确样本在候选里，但长期排不到前面
3. 相近 intent 经常互相抢 Top1
4. vector_score 接近，但业务方向明显错
```

可选动作：

```text
1. 调 keyword bonus
2. 调 direction penalty
3. 调 rerank_weight
4. 增加很窄的 intent hint
5. 重建向量库
```

不要轻易做：

```text
1. 大范围 query rewrite
2. 大幅提高 hint bonus
3. 用规则强行指定最终答案
```

判断标准：

```text
intent hint 是轻推排序或补召回，不是替代检索。
```

## 3. 什么时候改 answer_composer

适合改 answer_composer 的信号：

```text
1. Top1 intent 正确
2. evidence 里有关键句
3. reply 没把关键句说在开头
4. reply 重复、泛化、步骤顺序不稳
5. judge_reason 指向“不直接”“缺必要步骤”
```

典型问题：

```text
用户：你能直接给我店家手机号吗？
证据：平台客服不能直接提供未在页面展示的商家手机号。
坏回复：商家电话一般在订单详情页查看。
好回复：不能直接提供页面未展示的店家手机号。
```

composer 的职责：

```text
从 Top1 evidence 里选关键句
组织成 conclusion + action + caveat
让回答先解决用户当前问法
```

注意：

```text
composer 不负责找资料。
composer 只处理已经检索到的 evidence。
```

## 4. 什么时候用 reply_rules

reply_rules 只适合高风险兜底。

适合：

```text
1. 验证码
2. 私下转账
3. 明确隐私风险
4. 明确安全风险
```

不适合：

```text
1. 普通话术美化
2. 每个 intent 都写模板
3. 修所有不直接的回答
```

判断标准：

```text
如果只是回答不够顺，优先 composer。
如果涉及安全边界，才考虑 reply_rules。
```

## 5. 什么时候校准 judge

适合校准 judge 的信号：

```text
1. retrieved evidence 明确支持 reply
2. reply 保守、直接、可执行
3. judge 因“文档未明确某个字面说法”判 no
4. 问题属于安全/隐私/退款时间等容易被 judge 过严的场景
```

例子：

```text
证据：不能直接提供未在页面展示的商家手机号。
回答：不能直接提供页面未展示的店家手机号。
judge：参考文档未明确是否能直接提供店家手机号。
处理：校准为通过。
```

不要校准 judge 的情况：

```text
1. evidence 本身没有支持
2. reply 真的答非所问
3. reply 承诺了平台没有承诺的结果
4. 只是为了刷 pass rate
```

校准原则：

```text
只放宽“证据明确支持的保守回答”，不要放宽“没有证据的猜测”。
```

## 6. 什么时候不要动

应该暂停的信号：

```text
1. judge_pass_rate 已经很高
2. 只剩 1 个边界 case
3. 继续改会影响多个已通过 case
4. bad case 更像 judge 表达偏差
5. 当前评估集太小，继续优化容易过拟合
```

暂停不是放弃，而是记录：

```text
后续扩充评估集后再系统优化。
```

## 7. 推荐排查顺序

看到 bad case 时按这个顺序问：

```text
1. Top1 intent 对吗？
   不对 -> 看样本 / 检索 / hint / rerank

2. 正确 evidence 在候选里吗？
   在但没排上去 -> 看 rerank / bonus / penalty
   不在 -> 补样本或 supplement

3. evidence 里有答案吗？
   没有 -> 补知识库 answer 内容
   有 -> 看 answer_composer

4. reply 是否把关键句说出来？
   没有 -> 改 answer_composer

5. reply 是否有安全风险？
   有 -> reply_rules 或安全规则

6. reply 明明被 evidence 支持但 judge 判 no？
   是 -> judge calibration
```

## 8. 当前项目的经验结论

这轮 38 case 收敛过程说明：

```text
补边界样本 -> 让 FAISS 自然召回更稳
retrieval_trace -> 看清 hint 是 boost 还是 supplement
answer_composer -> 把关键证据句放到开头
judge calibration -> 处理保守安全回答被误杀
```

最终当前评估结果：

```text
total_cases = 38
judge_pass_count = 38
judge_pass_rate = 1.0
forbidden_hit_count = 0
```

但这不代表系统已经完美，只代表当前固定评估集收敛。后续应该继续扩充边界 case，再按这份决策树逐层判断。
