# 外卖客服 RAG 智能问答系统

这是一个面向外卖售后场景的 RAG 智能客服项目。系统围绕“用户问题能否被可靠、可解释、可评测地回答”来设计，覆盖知识库检索、证据组织、Prompt 构造、模型生成、规则兜底、反馈采集、知识运营和自动化评测闭环。

项目已完成 FastAPI 后端、RAG 检索链路、本地模型接入、知识库管理、反馈运营、评测脚本、Docker 配置和配套前端工作台，可作为大模型应用工程、RAG 系统和 AI 客服方向的面试展示项目。

## 可验证入口

- GitHub 后端仓库：https://github.com/kue04/llm-customer-service
- 配套前端仓库：https://github.com/kue04/takeout-rag-support-frontend
- 本地 API 文档：启动后访问 `http://127.0.0.1:8000/docs`
- 评测报告：[docs/EVALUATION.md](docs/EVALUATION.md)
- Bad case 复盘：[docs/BAD_CASES.md](docs/BAD_CASES.md)

## 系统功能

- 客服问答：围绕退款、配送进度、售后流程、食品安全、优惠券、平台规则等外卖客服场景生成中文回复。
- Hybrid Retrieval：支持关键词检索、向量检索、业务方向惩罚和综合排序。
- Reranker 精排：使用 reranker 对召回结果二次排序，提高证据相关性。
- Context Builder：区分 primary evidence 与 supporting evidence，减少相似意图混用。
- Prompt 预览：返回最终 Prompt、证据列表和诊断字段，便于调试每次回答。
- Reply Rules 兜底：对私下转账、食品安全、退款争议、隐私信息等高风险场景提供规则保护。
- 反馈闭环：保存会话、用户反馈和 bad case，可导出 eval case 继续优化。
- 知识运营：支持知识条目新增、修改、下架、审核、发布和回滚。
- 评测分析：提供 grounding evaluation、retrieval evaluation 和 bad case 分层诊断脚本。
- 工程化交付：包含测试、Dockerfile、docker-compose、环境变量模板和模块化路由设计。

## RAG 链路

```text
用户问题
-> FastAPI /chat/prompt
-> hybrid retrieval
-> keyword bonus + direction penalty
-> reranker 精排
-> Top1 标记为 primary evidence
-> 其余证据标记为 supporting evidence
-> Context Builder 构造 final_prompt
-> Qwen / online model 生成客服回复
-> reply_rules 做高风险兜底
-> 返回 reply、retrieved_items、prompt_context_items、final_prompt、trace
```

## 项目亮点

### 1. 完整 RAG 工程链路

项目不是简单的“向量库 + 大模型”Demo，而是把检索、重排、证据组织、Prompt 构造、模型生成、规则兜底、诊断返回和评测分析串成完整闭环。

### 2. 面向真实客服场景的检索优化

外卖客服问题经常存在相似表达和相反业务方向，例如“退款进度”“商家拒绝退款”“骑手未送达”“食品安全投诉”。系统通过关键词加权、方向惩罚和 reranker 精排，降低相似意图混淆。

### 3. 可解释的证据组织

Context Builder 将最相关证据标记为 primary evidence，其余资料作为 supporting evidence，并在 Prompt 中明确优先级，让模型回答更贴近主证据，也方便前端展示回答依据。

### 4. 高风险场景兜底

Reply Rules 对隐私、私下转账、食品安全、退款争议等场景提供规则级保护，避免模型在缺少证据或风险较高时给出不合适建议。

### 5. 可评测、可复盘、可迭代

评测脚本会保存检索结果、Prompt 上下文、最终回复、judge 结果和人工标注字段，并输出 suggested_layer，帮助判断问题应该修 retrieval、context builder、generation、reply rules 还是 judge。

### 6. 知识库运营闭环

系统提供知识草稿、审核、发布、回滚和发布历史能力，模拟真实客服知识库从编辑到影响 RAG 的运营流程。

## API 模块

```text
GET  /health
POST /chat/prompt
GET  /retrieval/search
POST /retrieval/prompt-preview
GET  /model/info
GET  /feedback/recent
POST /feedback
POST /feedback/export-eval-case
GET  /ops/metrics
GET  /knowledge/items
POST /knowledge/items
PUT  /knowledge/items/{id}
POST /knowledge/items/{id}/archive
POST /knowledge/items/{id}/review
GET  /knowledge/export-approved
POST /knowledge/publish-approved
GET  /knowledge/publish-history
POST /knowledge/rollback-latest
```

## 技术栈

- Python 3.11+
- FastAPI
- Pydantic
- Transformers
- PEFT / LoRA
- Qwen2.5-1.5B-Instruct
- BAAI/bge-small-zh-v1.5
- BAAI/bge-reranker-base
- FAISS
- SQLite
- JSONL knowledge base
- unittest
- Docker / docker-compose

## 项目结构

```text
llm-customer-service/
├── main.py                         # FastAPI 入口
├── config/                         # RAG 与模型配置
├── routers/                        # chat / retrieval / feedback / knowledge / ops 路由
├── schemas/                        # 请求与响应模型
├── services/                       # 问答、证据组织、反馈、知识运营、指标统计
├── utils/                          # 向量检索、RAG 上下文等工具
├── models/                         # Prompt 模板
├── scripts/                        # 评测、分析、训练和调试脚本
├── data/                           # 示例知识库与评测数据
├── tests/                          # 单元测试与接口测试
├── docs/                           # RAG 优化、前端集成和阶段经验文档
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 本地运行

安装依赖：

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
```

启动后端：

```powershell
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

健康检查：

```text
http://127.0.0.1:8000/health
```

接口文档：

```text
http://127.0.0.1:8000/docs
```

Docker 启动：

```powershell
docker compose up --build
```

## 环境变量

复制 `.env.example` 为 `.env` 后按需配置。真实 API Key、本地模型、LoRA 权重、向量索引和运行数据库不应提交到仓库。

```env
RAG_GENERATION_PROVIDER=local
RAG_ONLINE_API_KEY=
RAG_ONLINE_BASE_URL=
RAG_ONLINE_MODEL=
```

本地模型默认通过本机目录挂载或手动下载，不包含在仓库中。

## 评测与诊断

运行 grounding evaluation：

```powershell
python scripts/evaluate_chat_grounding.py
```

分析 grounding bad case：

```powershell
python scripts/analyze_grounding_report.py
```

运行向量检索评测：

```powershell
python scripts/evaluate_vector_retrieval.py
```

## 前端展示

配套前端仓库：[kue04/takeout-rag-support-frontend](https://github.com/kue04/takeout-rag-support-frontend)

前端工作台包含外卖业务模拟、客服对话、RAG 诊断面板、反馈采集、知识库运营和项目展示页。面试展示时可以先通过前端说明产品体验，再回到本仓库讲解 RAG 链路和后端工程实现。
