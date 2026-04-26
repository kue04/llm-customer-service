# app/models/prompt.py
from typing import List

def create_prompt(query: str, documents: List[str]) -> str:
    # 创建结合检索结果和用户输入的提示词
    prompt = f"Answer the following question based on the context:\n\n"
    prompt += f"Question: {query}\n\n"
    prompt += f"Context:\n"
    for doc in documents:
        prompt += f"- {doc}\n"
    prompt += f"Answer:"
    return prompt