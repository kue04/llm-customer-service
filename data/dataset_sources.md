# 外卖平台中文客服数据说明

本项目的数据用于学习中文客服问答、RAG、SFT、LoRA 和 QLoRA 流程。当前数据为合成数据，不来自真实外卖平台，不包含真实用户手机号、地址、订单号、支付流水号等隐私信息。

## 当前数据文件

主数据集：

```text
data/takeout_customer_service_seed.jsonl
```

训练格式数据：

```text
data/messages/takeout_sft_messages_all.jsonl
data/messages/takeout_sft_train.jsonl
data/messages/takeout_sft_val.jsonl
data/messages/takeout_sft_test.jsonl
```

生成脚本：

```text
scripts/build_takeout_training_data.py
```

## 当前数据规模

```text
主数据集：500 条
messages 全量：500 条
train：400 条
val：50 条
test：50 条
单轮样本：376 条
多轮样本：124 条
quality=high：188 条
quality=medium：312 条
```

## 主数据字段

每一行是一个 JSON 对象：

```json
{
  "id": "takeout_0001",
  "source": "curated_seed",
  "dialogue_type": "single_turn",
  "quality": "high",
  "question": "我的外卖怎么还没到？已经超过预计时间了。",
  "answer": "很抱歉让您久等了...",
  "category": "配送进度",
  "intent": "催单",
  "sentiment": "negative",
  "entities": {
    "order_status": "超时",
    "risk": "低"
  }
}
```

字段含义：

- `id`：样本编号。
- `source`：数据来源，当前包括人工种子数据和合成扩增数据。
- `dialogue_type`：`single_turn` 或 `multi_turn`。
- `quality`：当前为 `high` 或 `medium`。
- `question`：用户问题。
- `answer`：客服回答。
- `category`：问题大类。
- `intent`：用户意图。
- `sentiment`：用户情绪，当前包括 `negative`、`neutral`、`positive`。
- `entities`：结构化实体，例如订单状态、风险等级、支付状态等。

## 覆盖场景

当前数据覆盖：

- 配送进度
- 订单取消
- 退款售后
- 用户投诉
- 订单支付问题
- 优惠券和促销问题
- 常见问答
- 订单信息修改
- 商家问题
- 平台安全
- 会员服务
- 评价反馈
- 售后流程
- 复杂多轮对话

## 数据来源建议

真实客服对话通常包含隐私，不建议直接爬取或使用来源不明的数据包。更合理的来源包括：

- 自建合成数据：最适合个人学习阶段。
- 平台公开帮助中心：适合整理成 RAG 知识库。
- 公开中文电商客服数据：适合学习对话结构和回复风格。
- 自有业务数据：必须获得授权，并完成隐私脱敏。

可参考公开资料：

- JDDC 中文电商客服多轮对话数据集：https://huggingface.co/papers/1911.09969
- JDDC 2.0 多模态中文电商客服数据集：https://papersgraph.com/datasets/jddc-20
- Nexdata 多领域中文客服对话数据：https://github.com/Nexdata-AI/80000-sets-Multi-domain-Customer-Service-Dialogue-Text-Data

## 数据质量注意事项

当前数据已经足够用于学习训练流程，但还不是生产级客服数据。

后续优化方向：

- 扩展到 1000 到 5000 条。
- 增加更多真实口语表达、错别字、模糊描述和强情绪投诉。
- 增加更多食品安全、隐私安全、支付异常等高风险样本。
- 增加更多多轮对话。
- 人工抽检高风险样本，避免错误承诺退款、赔偿或平台规则。
- 统一客服话术风格：先安抚，再说明，再给操作路径。

## LoRA/QLoRA 使用建议

当前 `messages` 数据可以用于 SFT、LoRA 或 QLoRA 训练。第一次训练时建议目标放低：先跑通流程，而不是追求模型效果。

推荐流程：

1. 安装训练依赖：`datasets`、`peft`、`trl`、`accelerate`。
2. 使用 `data/messages/takeout_sft_train.jsonl` 作为训练集。
3. 使用 `data/messages/takeout_sft_val.jsonl` 作为验证集。
4. 训练 LoRA adapter。
5. 用 `data/messages/takeout_sft_test.jsonl` 做人工评估。
6. 将 adapter 接回 FastAPI 推理服务。
