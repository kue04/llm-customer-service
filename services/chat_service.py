# app/services/chat_service.py
from pathlib import Path

from models.prompt import create_prompt
from peft import PeftModel
from services.reply_rules import apply_reply_rules
from transformers import AutoModelForCausalLM, AutoTokenizer
from utils.vector_retriever import retrieve_rag_items


MODEL_PATH = Path(__file__).resolve().parents[1] / "local_models" / "qwen2.5-1.5b-instruct"
ADAPTER_PATH = Path(__file__).resolve().parents[1] / "models" / "takeout-qwen-lora-minimal"
SYSTEM_PROMPT = (
    "你是外卖平台中文客服。回答要礼貌、准确、简洁，先安抚用户，再说明原因，"
    "最后给出可执行的下一步。不要编造平台规则；遇到支付、隐私、食品安全、"
    "站外交易等高风险问题时要提醒用户保留证据并通过官方渠道处理。"
)

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, local_files_only=True)
if ADAPTER_PATH.exists() and (ADAPTER_PATH / "adapter_config.json").exists():
    model = PeftModel.from_pretrained(model, ADAPTER_PATH, local_files_only=True)
model.eval()


def generate_reply(prompt: str) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt")
    input_length = inputs["input_ids"].shape[-1]
    outputs = model.generate(
        **inputs,
        max_new_tokens=256,
        do_sample=False,
        pad_token_id=tokenizer.eos_token_id,
    )
    return tokenizer.decode(outputs[0][input_length:], skip_special_tokens=True).strip()


def get_answer_from_rag(query: str):
    retrieved_items = retrieve_rag_items(query)
    documents = [item["answer"] for item in retrieved_items]
    if documents:
        prompt = create_prompt(query, documents)
        confidence_score = 0.95
    else:
        prompt = (
            "没有检索到完全匹配的知识库材料。请只基于外卖客服通用处理原则回答，"
            "不要编造具体平台政策；如果需要平台核实，请建议用户在订单页或官方客服渠道处理。\n\n"
            f"用户问题：{query}"
        )
        confidence_score = 0.5

    reply = generate_reply(prompt)
    reply = apply_reply_rules(query, reply, retrieved_items)

    return {
        "reply": reply,
        "confidence_score": confidence_score,
        "retrieved_documents": documents,
        "retrieved_items": retrieved_items,
    }


def get_model_info():
    adapter_enabled = ADAPTER_PATH.exists() and (ADAPTER_PATH / "adapter_config.json").exists()

    return {
        "base_model": MODEL_PATH.name,
        "adapter_enabled": adapter_enabled,
        "adapter_name": ADAPTER_PATH.name if adapter_enabled else None,
    }
