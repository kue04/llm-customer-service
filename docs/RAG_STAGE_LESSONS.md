# 这一阶段的学习整理

## 这阶段学到了什么

我们把外卖客服 RAG 的问题，从“检索准不准”推进到了“回答怎么更像人、评估怎么更像工程”。

当前链路可以理解成：

```text
用户问题
-> intent hint / retrieval
-> rerank
-> answer_composer
-> reply_rules
-> grounding evaluation
-> report compare
```

## 关键结论

1. `top1_intent_hit_rate = 1.0` 以后，继续盯检索通常不是最优解。
2. 真正影响用户体验的，往往是 `answer_composer` 的开头、去重、步骤顺序和措辞稳定性。
3. `reply_rules` 适合高风险兜底，不适合做通用话术修补。
4. `judge` 也会有偏严格或过保守的情况，必要时要做 calibration。
5. 评估报告要支持 compare，不然你只能看单次结果，没法判断改动有没有回归。

## 为什么先做 intent hint

我们遇到的两个边界 case 很典型：

- `我都没吃上凭什么还扣我钱`
- `别让我点联系商家了，你能直接给我店家手机号吗`

这类问题不是模型不会说，而是检索先被表面词带偏了。  
所以先做 `intent hint`，比直接加大模型、改 composer 更划算。

`intent hint` 的职责很窄：

- 不改用户原句
- 不重写知识库
- 只给检索一个轻微偏置

它的目标是把正确 intent 拉到 Top1，而不是替代检索。

## 为什么还要 answer_composer

当 Top1 intent 已经对了，剩下的问题通常是：

- 第一句不够直接
- 同义句重复
- 必要步骤顺序不稳
- 结论太泛

这些都是 `answer_composer` 的职责，不该继续丢给检索。

一句话：

```text
retrieval 负责找对 evidence
composer 负责把 evidence 说清楚
```

## 为什么 compare 很重要

`report compare` 让我们能看到：

- 哪些 case 真的变好了
- 哪些 case 回归了
- 改动是救了检索，还是救了渲染，还是只是校准了 judge

这次最重要的学习点就是：

```text
intent hint 让 top1 回到 38/38
composer 让回答更贴用户问题
judge calibration 负责处理误判
```

如果没有 compare，我们很容易把这三件事混在一起。

## 现在这阶段的最佳实践

1. 先看 `top1_intent_hit_rate`。
2. 如果已经稳定到 1.0，优先看回答渲染。
3. 边界表达先用 `intent hint` 小步兜住。
4. 只给必要 intent 加小 bonus，不要把规则做胖。
5. 每次改动都跑 `report compare` 看有没有回归。
6. 最后再决定要不要做 judge calibration。

## 这阶段最值得记住的一句话

```text
RAG 工程不是不停加能力，而是把每一层的职责切清楚。
```

