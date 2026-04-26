# app/services/chat_service.py
from pathlib import Path

from models.prompt import create_prompt
from transformers import AutoModelForCausalLM, AutoTokenizer
from utils.retriever import retrieve_documents


MODEL_PATH = Path(__file__).resolve().parents[1] / "local_models" / "qwen2.5-1.5b-instruct"

tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True)
model = AutoModelForCausalLM.from_pretrained(MODEL_PATH, local_files_only=True)


def generate_reply(prompt: str) -> str:
    messages = [{"role": "user", "content": prompt}]
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(text, return_tensors="pt")
    input_length = inputs["input_ids"].shape[-1]
    outputs = model.generate(**inputs, max_new_tokens=256, pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(outputs[0][input_length:], skip_special_tokens=True).strip()


def get_answer_from_rag(query: str):
    documents = retrieve_documents(query)
    if documents:
        prompt = create_prompt(query, documents)
        confidence_score = 0.95
    else:
        prompt = (
            "You are a helpful customer service assistant. No matching knowledge base "
            "document was found for this question, so answer from general knowledge. "
            "If the question is outside customer service, keep the answer brief.\n\n"
            f"Question: {query}\n\nAnswer:"
        )
        confidence_score = 0.5

    return {"reply": generate_reply(prompt), "confidence_score": confidence_score}
