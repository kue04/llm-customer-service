# 外卖平台中文客服大模型项目

这是一个面向个人学习和原型验证的中文外卖客服问答项目。项目当前已经完成了 FastAPI 服务、本地 Qwen 模型接入、外卖客服数据集扩展，以及可用于 LoRA/QLoRA 的 `messages` 格式训练数据准备。

## 当前进度

已完成：

- 使用 FastAPI 提供 `/chat/prompt` 问答接口。
- 将原先 GPT-2 替换为本地 `Qwen2.5-1.5B-Instruct`。
- 删除代码中的 Hugging Face Token 硬编码。
- 本地模型通过 `local_files_only=True` 离线加载。
- 构建外卖平台中文客服数据集，共 `500` 条。
- 为数据补充 `category`、`intent`、`sentiment`、`entities`、`quality`、`dialogue_type` 等字段。
- 生成 LoRA/QLoRA 常用的 `messages` 训练格式。
- 按 `400/50/50` 切分为 train、val、test。
- 配置 `.gitignore`，忽略 `venv/`、`local_models/`、`__pycache__/` 等本地文件。
- 已在本地创建 Git 提交：`b8f2292 Initial takeout customer service LLM project`。

暂未完成：

- GitHub 远程推送。当前环境连接 GitHub 失败，报错为无法连接 `github.com:443`。
- QLoRA 微调训练脚本。
- 前端页面。
- 真正的向量检索 RAG。

## 项目结构

```text
llm-customer-service/
├── main.py
├── requirements.txt
├── README.md
├── .gitignore
├── data/
│   ├── dataset_sources.md
│   ├── takeout_customer_service_seed.jsonl
│   └── messages/
│       ├── takeout_sft_messages_all.jsonl
│       ├── takeout_sft_train.jsonl
│       ├── takeout_sft_val.jsonl
│       └── takeout_sft_test.jsonl
├── local_models/
│   └── qwen2.5-1.5b-instruct/
├── models/
│   └── prompt.py
├── routers/
│   └── chat.py
├── schemas/
│   └── chat_schema.py
├── scripts/
│   └── build_takeout_training_data.py
├── services/
│   └── chat_service.py
└── utils/
    └── retriever.py
```

注意：`local_models/` 是本地大模型目录，已经被 `.gitignore` 忽略，不应提交到 GitHub。

## 本地模型

当前服务默认加载：

```text
local_models/qwen2.5-1.5b-instruct
```

如果模型不存在，可以下载：

```powershell
$env:HF_ENDPOINT="https://hf-mirror.com"
.\venv\Scripts\hf.exe download --max-workers 1 --local-dir .\local_models\qwen2.5-1.5b-instruct --include "*.json" --include "*.safetensors" --include "*.txt" --include "*.md" Qwen/Qwen2.5-1.5B-Instruct
```

## 安装依赖

```powershell
cd D:\llm\llm-customer-service
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

## 启动服务

建议使用虚拟环境里的 Python 启动，避免误用全局环境：

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

访问：

```text
http://127.0.0.1:8000
http://127.0.0.1:8000/docs
```

## API 示例

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/chat/prompt -Method Post -ContentType "application/json" -Body '{"message":"我的外卖怎么还没到？"}'
```

响应格式：

```json
{
  "reply": "很抱歉让您久等了...",
  "confidence_score": 0.5
}
```

## 数据集

主数据文件：

```text
data/takeout_customer_service_seed.jsonl
```

当前规模：

```text
总条数：500
单轮样本：376
多轮样本：124
quality=high：188
quality=medium：312
```

字段格式：

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

覆盖场景：

- 用户投诉
- 订单支付问题
- 优惠券和促销问题
- 常见问答
- 配送进度
- 订单取消
- 退款售后
- 售后流程
- 平台安全
- 会员服务
- 评价反馈
- 多轮追问

## LoRA/QLoRA 训练数据

训练数据目录：

```text
data/messages/
```

文件说明：

```text
takeout_sft_messages_all.jsonl   # 全量 500 条
takeout_sft_train.jsonl          # 训练集 400 条
takeout_sft_val.jsonl            # 验证集 50 条
takeout_sft_test.jsonl           # 测试集 50 条
```

`messages` 格式示例：

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是外卖平台中文客服。回答要礼貌、准确、简洁..."
    },
    {
      "role": "user",
      "content": "我的外卖怎么还没到？已经超过预计时间了。"
    },
    {
      "role": "assistant",
      "content": "很抱歉让您久等了..."
    }
  ],
  "metadata": {
    "id": "takeout_0001",
    "category": "配送进度",
    "intent": "催单",
    "sentiment": "negative",
    "quality": "high",
    "dialogue_type": "single_turn",
    "entities": {
      "order_status": "超时",
      "risk": "低"
    }
  }
}
```

生成脚本：

```powershell
.\venv\Scripts\python.exe -B .\scripts\build_takeout_training_data.py
```

## GitHub 手动提交顺序

如果当前环境无法自动推送，可以在本机 PowerShell 手动执行：

```powershell
cd D:\llm\llm-customer-service
git status
git remote -v
git log --oneline -1
git push -u origin master
```

如果 GitHub 仓库默认分支希望使用 `main`，执行：

```powershell
git branch -M main
git push -u origin main
```

如果提示需要登录 GitHub：

1. 打开 GitHub 登录账号 `kue04`。
2. 确认仓库存在：`https://github.com/kue04/llm-customer-service`。
3. 使用 Git Credential Manager 登录，或使用 GitHub Personal Access Token。
4. 重新执行 `git push -u origin master` 或 `git push -u origin main`。

如果远程仓库已经有内容，先不要强推。建议先执行：

```powershell
git pull --rebase origin master
git push -u origin master
```

如果远程默认分支是 `main`，把上面的 `master` 改成 `main`。

## 下一步建议

优先顺序：

1. 手动把当前提交推送到 GitHub。
2. 增加 QLoRA 训练依赖，例如 `datasets`、`peft`、`trl`、`accelerate`。
3. 编写最小 QLoRA 训练脚本，先用 500 条数据跑通流程。
4. 编写 LoRA adapter 推理加载脚本。
5. 将微调后的 adapter 接回 FastAPI。
6. 再做一个简单前端页面，让用户通过网页聊天。
7. 后续再把关键词检索升级成向量检索 RAG。

当前最推荐的下一步是：先写一个最小 QLoRA 训练脚本，但不要急着追求效果，目标只是跑通“数据读取、训练、保存 adapter、加载 adapter 推理”这一整条链路。
