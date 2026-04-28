# 外卖平台中文客服大模型项目

这是一个面向个人学习和原型验证的中文外卖客服问答项目。项目当前已经完成了 FastAPI 服务、本地 Qwen 模型接入、外卖客服数据集扩展、LoRA/QLoRA 最小训练链路，以及一组用于学习后端接口开发的数据查询 API。

本项目也是一个学习项目：除了训练和调用大模型，还会逐步练习 FastAPI、Pydantic、JSONL 数据处理、接口设计、异常处理、路由拆分和后续前端/RAG 能力。

## 当前进度

已完成：

- 使用 FastAPI 提供 `/chat/prompt` 问答接口。
- 将原先 GPT-2 替换为本地 `Qwen2.5-1.5B-Instruct`。
- 删除代码中的 Hugging Face Token 硬编码。
- 本地模型通过 `local_files_only=True` 离线加载。
- 支持检测并加载本地 LoRA adapter：`models/takeout-qwen-lora-minimal`。
- 提供 `/model/info` 接口，返回当前基础模型和 adapter 状态。
- 构建外卖平台中文客服数据集，共 `500` 条。
- 为数据补充 `category`、`intent`、`sentiment`、`entities`、`quality`、`dialogue_type` 等字段。
- 生成 LoRA/QLoRA 常用的 `messages` 训练格式。
- 按 `400/50/50` 切分为 train、val、test。
- 提供 `/examples/categories` 接口，返回数据集中所有客服分类。
- 提供 `/examples/by-category` 接口，按分类查询样本，并支持 `limit` 参数限制。
- 提供 `/examples/search` POST 接口，按关键词搜索问题和答案。
- 为新增接口添加 Pydantic `response_model`。
- 为 GET 查询参数添加 `Query(default=5, ge=1, le=20)` 校验。
- 为 POST 请求体添加 `Field(min_length=1)`、`Field(default=5, ge=1, le=20)` 校验。
- 分类不存在时使用 `HTTPException(status_code=404)` 返回业务错误。
- 配置 `.gitignore`，忽略 `venv/`、`local_models/`、`__pycache__/`、`.cache/`、`eval_outputs/` 等本地文件。
- 已在本地创建 Git 提交：`b8f2292 Initial takeout customer service LLM project`。

暂未完成：

- GitHub 远程推送。当前环境连接 GitHub 失败，报错为无法连接 `github.com:443`。
- examples 接口尚未拆分到独立 router；路由部分计划从下一阶段重新学习。
- 前端页面。
- 真正的向量检索 RAG。
- 更系统的 LoRA 训练评估和模型效果对比。

## 项目结构

```text
llm-customer-service/
├── main.py
├── requirements.txt
├── README.md
├── STUDY.md
├── .gitignore
├── data/
│   ├── dataset_sources.md
│   ├── eval_prompts.jsonl
│   ├── takeout_customer_service_seed.jsonl
│   └── messages/
│       ├── takeout_sft_high_risk_extra.jsonl
│       ├── takeout_sft_messages_all.jsonl
│       ├── takeout_sft_train.jsonl
│       ├── takeout_sft_val.jsonl
│       └── takeout_sft_test.jsonl
├── local_models/
│   └── qwen2.5-1.5b-instruct/
├── models/
│   ├── prompt.py
│   ├── takeout-qwen-lora-gpu-smoke/
│   └── takeout-qwen-lora-minimal/
├── routers/
│   └── chat.py
├── schemas/
│   ├── chat_schema.py
│   ├── example_schema.py
│   └── info_schema.py
├── scripts/
│   ├── build_takeout_training_data.py
│   ├── evaluate_lora_adapter.py
│   ├── infer_lora_adapter.py
│   └── train_qlora_minimal.py
├── services/
│   ├── chat_service.py
│   └── example_service.py
└── utils/
    └── retriever.py
```

注意：

- `local_models/` 是本地大模型目录，已经被 `.gitignore` 忽略，不应提交到 GitHub。
- `models/takeout-qwen-lora-*` 是训练产物，通常不建议直接提交到 GitHub。
- `eval_outputs/` 是评估输出，已经被 `.gitignore` 忽略。

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

### 聊天接口

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

### 模型信息接口

```text
GET /model/info
```

示例响应：

```json
{
  "base_model": "qwen2.5-1.5b-instruct",
  "adapter_enabled": true,
  "adapter_name": "takeout-qwen-lora-minimal"
}
```

### 分类列表接口

```text
GET /examples/categories
```

示例响应：

```json
{
  "categories": ["会员服务", "退款售后", "配送进度"],
  "count": 14
}
```

### 按分类查询样本

```text
GET /examples/by-category?category=会员服务&limit=5
```

说明：

- `category` 是必填查询参数。
- `limit` 默认 `5`，最小 `1`，最大 `20`。
- 分类不存在时返回 `404`。

### 搜索样本

```text
POST /examples/search
```

请求体：

```json
{
  "keyword": "退款",
  "limit": 5
}
```

说明：

- `keyword` 至少 1 个字符。
- `limit` 默认 `5`，最小 `1`，最大 `20`。

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
- 商家问题
- 订单信息修改
- 复杂对话

## LoRA/QLoRA 训练链路

当前仓库提供最小脚本，用来先跑通“训练、保存、加载、推理、评估”链路：

```powershell
.\venv\Scripts\python.exe scripts\train_qlora_minimal.py --max-steps 1 --max-samples 2 --max-length 128 --gradient-accumulation-steps 1
```

训练完成后会保存 LoRA adapter 到：

```text
models/takeout-qwen-lora-minimal
```

加载 adapter 并推理：

```powershell
.\venv\Scripts\python.exe scripts\infer_lora_adapter.py --adapter-path models\takeout-qwen-lora-minimal --prompt "我的外卖超时了，骑手也联系不上，怎么办？"
```

评估 adapter：

```powershell
.\venv\Scripts\python.exe scripts\evaluate_lora_adapter.py --adapter-path models\takeout-qwen-lora-minimal
```

说明：训练脚本默认 `--use-4bit auto`。如果本机有 CUDA 且安装了 `bitsandbytes`，会走 4bit QLoRA；否则会自动退回普通 LoRA，用于 CPU 环境下冒烟验证链路。

## 学习记录

学习进度详见：

```text
STUDY.md
```

昨天已经完成：

- FastAPI GET 接口。
- POST 请求体。
- Pydantic response model。
- Pydantic Field 校验。
- Query 参数校验。
- JSONL 文件读取。
- `set` 去重。
- `json.loads` 转 Python dict。
- `HTTPException` 业务错误。

下一阶段从“路由拆分”重新开始，不默认你已经掌握 router。

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

优先学习顺序：

1. 从头学习 FastAPI `APIRouter`，把 examples 相关接口从 `main.py` 拆到 `routers/example.py`。
2. 学习 service 层小重构，把重复的 `DATA_PATH` 提到文件顶部，并抽出读取 JSONL 的小函数。
3. 给 examples 接口补充更友好的错误信息和 OpenAPI 错误响应说明。
4. 学习 Git 整理：确认哪些文件应该提交，哪些训练产物应该忽略。
5. 再学习把 LoRA adapter 接入 `/chat/prompt` 的可配置开关。
6. 后续再做前端页面和向量检索 RAG。
