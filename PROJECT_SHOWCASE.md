# 外卖客服 RAG 智能问答系统项目展示

## 项目定位

这是一个面向外卖售后场景的 RAG 智能客服系统。项目目标不是简单调用大模型，而是构建一条可调试、可评测、可运营的 AI 应用工程链路：知识库检索、证据组织、Prompt 构造、本地/在线模型生成、风险兜底、反馈采集和自动化评测。

## 核心链路

```text
用户问题
-> FastAPI /chat/prompt
-> hybrid retrieval
-> reranker 精排
-> primary evidence / supporting evidence
-> Context Builder 构造 final_prompt
-> Qwen / online model 生成回复
-> reply_rules 高风险兜底
-> 返回回复、证据、Prompt、trace 和诊断信息
```

## 核心能力

- Hybrid Retrieval：向量召回结合关键词加权和方向惩罚，降低相似意图混淆。
- Reranker 精排：对候选知识进行二次排序，提高证据相关性。
- Context Builder：区分 primary evidence 和 supporting evidence，减少模型串意图。
- Reply Rules：对退款、食品安全、隐私、私下转账等高风险场景做兜底。
- Grounding Evaluation：记录检索、Prompt、回复和 judge 结果，支持坏案例定位。
- Knowledge Ops：支持知识草稿、审核、发布、回滚，模拟真实客服知识运营流程。

## 技术栈

- Python 3.11+
- FastAPI
- Pydantic
- Transformers / PEFT / LoRA
- Qwen2.5-1.5B-Instruct
- BAAI/bge-small-zh-v1.5
- BAAI/bge-reranker-base
- FAISS
- SQLite
- unittest

## 面试亮点

1. 完整覆盖 RAG 工程链路，不停留在 Prompt Demo。
2. 能解释回答来源，并通过评测报告定位问题层级。
3. 将客服系统需要的安全兜底、知识运营和反馈闭环纳入项目设计。

## 本地运行

```powershell
python -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

前端工作台位于 `D:\llm\front`。
