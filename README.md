# 本地中文客服问答 API

这是一个基于 FastAPI、Transformers 和本地大语言模型的客服问答示例项目。

项目当前使用本地 `Qwen2.5-1.5B-Instruct` 模型作为生成模型，并提供一个简单的 RAG 流程：

1. 用户向 `/chat/prompt` 发送问题。
2. 系统先从本地示例知识库中检索相关 FAQ。
3. 如果检索到相关内容，就把问题和上下文一起交给模型回答。
4. 如果没有检索到内容，就让模型根据通用知识兜底回答，并降低置信度。

## 当前模型

默认模型路径：

```text
local_models/qwen2.5-1.5b-instruct
```

模型代码位置：

```python
MODEL_PATH = Path(__file__).resolve().parents[1] / "local_models" / "qwen2.5-1.5b-instruct"
```

这个项目不再使用 GPT-2。GPT-2 不适合中文客服场景，当前已切换为 Qwen2.5-1.5B-Instruct。

## 项目结构

```text
llm-customer-service/
├── main.py                         # FastAPI 应用入口
├── requirements.txt                # Python 依赖
├── README.md                       # 项目说明
├── local_models/
│   └── qwen2.5-1.5b-instruct/      # 本地 Qwen 模型文件，不建议提交到 Git
├── models/
│   └── prompt.py                   # 构造 RAG 提示词
├── routers/
│   └── chat.py                     # 聊天接口路由
├── schemas/
│   └── chat_schema.py              # 请求和响应的数据结构
├── services/
│   └── chat_service.py             # 模型加载、RAG 调用、回答生成
└── utils/
    └── retriever.py                # 简单本地知识库和检索逻辑
```

## 环境要求

建议环境：

- Python 3.12
- Windows PowerShell
- 至少 8GB 内存
- 本地磁盘预留 4GB 以上空间

当前依赖：

```text
fastapi
uvicorn
transformers
torch
pydantic
```

## 安装依赖

进入项目目录：

```powershell
cd D:\llm\llm-customer-service
```

创建并激活虚拟环境：

```powershell
python -m venv venv
.\venv\Scripts\activate
```

安装依赖：

```powershell
pip install -r requirements.txt
```

## 下载本地模型

如果 `local_models/qwen2.5-1.5b-instruct` 已经存在，并且里面有 `model.safetensors`，可以跳过本步骤。

推荐使用 Hugging Face 镜像下载：

```powershell
$env:HF_ENDPOINT="https://hf-mirror.com"
.\venv\Scripts\hf.exe download --max-workers 1 --local-dir .\local_models\qwen2.5-1.5b-instruct --include "*.json" --include "*.safetensors" --include "*.txt" --include "*.md" Qwen/Qwen2.5-1.5B-Instruct
```

下载完成后，目录中应包含类似文件：

```text
config.json
generation_config.json
model.safetensors
tokenizer.json
tokenizer_config.json
vocab.json
```

其中 `model.safetensors` 大约 3GB。

## 启动服务

建议使用虚拟环境中的 Python 启动，避免误用全局环境：

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

启动成功后访问：

```text
http://127.0.0.1:8000
```

接口文档：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/redoc
```

## API 使用

### 根路径

请求：

```powershell
curl http://127.0.0.1:8000/
```

响应：

```json
{
  "message": "Welcome to the customer service API"
}
```

### 聊天接口

接口地址：

```text
POST /chat/prompt
```

请求体：

```json
{
  "message": "What is the capital of France?"
}
```

PowerShell 示例：

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/chat/prompt -Method Post -ContentType "application/json" -Body '{"message":"What is the capital of France?"}'
```

curl 示例：

```bash
curl -X POST "http://127.0.0.1:8000/chat/prompt" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -d '{"message":"What is the capital of France?"}'
```

响应示例：

```json
{
  "reply": "The capital of France is Paris.",
  "confidence_score": 0.5
}
```

## 置信度说明

当前项目使用一个简单的规则返回置信度：

- `0.95`：本地知识库检索到相关内容，模型基于检索上下文回答。
- `0.5`：本地知识库没有命中，模型使用通用知识兜底回答。

这个分数不是模型真实概率，只是当前示例项目中的业务标记。

## 当前知识库

知识库暂时写在 `utils/retriever.py` 中：

```python
knowledge_base = [
    {"question": "What is your return policy?", "answer": "You can return products within 30 days."},
    {"question": "How can I contact customer support?", "answer": "You can contact support at support@example.com."},
]
```

如果要做真实中文客服，可以把它替换成：

- JSON 文件
- CSV/Excel FAQ
- SQLite 数据库
- 向量数据库
- 企业知识库接口

## 常见问题

### 1. 为什么第一次启动比较慢？

服务启动时会加载本地 Qwen 模型。模型权重约 3GB，第一次加载需要一些时间。

### 2. 为什么不要直接运行 `uvicorn main:app --reload`？

PowerShell 中直接运行 `uvicorn` 可能会使用全局 Python 环境。建议使用：

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

这样可以确保使用当前项目的虚拟环境。

### 3. 为什么模型下载和 pip 镜像没有关系？

`pip` 只负责安装 Python 包，例如 `fastapi`、`torch`、`transformers`。

`Qwen2.5-1.5B-Instruct` 是模型权重，需要通过 Hugging Face 或镜像站单独下载。

### 4. 是否需要 Hugging Face Token？

当前模型是公开模型，通常不需要 Token。

项目中不应硬编码任何 Token 或 API Key。如果之前写过 Token，建议去 Hugging Face 后台撤销并重新生成。

### 5. 如果接口返回 500 怎么办？

先看终端日志。常见原因：

- 模型目录不存在
- `model.safetensors` 没下载完整
- 没有用虚拟环境启动服务
- 内存不足导致模型加载失败

可以先测试模型是否能离线加载：

```powershell
.\venv\Scripts\python.exe -B -c "from pathlib import Path; from transformers import AutoTokenizer, AutoModelForCausalLM; p=Path('local_models/qwen2.5-1.5b-instruct'); AutoTokenizer.from_pretrained(p, local_files_only=True); AutoModelForCausalLM.from_pretrained(p, local_files_only=True); print('model ok')"
```

## 后续改进方向

- 把示例知识库改成中文 FAQ。
- 使用向量检索替代当前的关键词匹配。
- 增加流式输出。
- 给模型调用增加超时、异常捕获和日志。
- 把模型加载改成应用启动生命周期管理。
- 增加单元测试和接口测试。
- 增加 `.gitignore`，避免提交 `venv/` 和 `local_models/`。
