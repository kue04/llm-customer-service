from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


SYSTEM_PROMPT = (
    "你是外卖平台中文客服。回答要礼貌、准确、简洁，先安抚用户，再说明原因，"
    "最后给出可执行的下一步。不要编造平台规则；遇到支付、隐私、食品安全、"
    "站外交易等高风险问题时要提醒用户保留证据并通过官方渠道处理。"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a fixed prompt set against a LoRA adapter.")
    parser.add_argument("--model-path", default="local_models/qwen2.5-1.5b-instruct")
    parser.add_argument("--adapter-path", default="models/takeout-qwen-lora-minimal")
    parser.add_argument("--eval-file", default="data/eval_prompts.jsonl")
    parser.add_argument("--output-dir", default="eval_outputs")
    parser.add_argument("--max-new-tokens", type=int, default=160)
    return parser.parse_args()


def load_eval_rows(path: str) -> list[dict]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def keyword_score(reply: str, keywords: list[str]) -> tuple[int, int, list[str]]:
    hits = [keyword for keyword in keywords if keyword in reply]
    return len(hits), len(keywords), hits


def generate_reply(model, tokenizer, prompt: str, device: str, max_new_tokens: int) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    inputs = tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
    )
    inputs = {key: value.to(device) for key, value in inputs.items()}

    with torch.inference_mode():
        outputs = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    reply_ids = outputs[0, inputs["input_ids"].shape[-1] :]
    return tokenizer.decode(reply_ids, skip_special_tokens=True).strip()


def write_markdown(path: Path, results: list[dict]) -> None:
    lines = [
        "# LoRA Adapter Evaluation",
        "",
        "| ID | Category | Score | Prompt | Reply |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for item in results:
        reply = item["reply"].replace("\n", "<br>")
        prompt = item["prompt"].replace("|", "\\|")
        score = f'{item["keyword_hits"]}/{item["keyword_total"]}'
        lines.append(f'| {item["id"]} | {item["category"]} | {score} | {prompt} | {reply} |')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float32

    tokenizer = AutoTokenizer.from_pretrained(args.adapter_path, local_files_only=True, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    base_model = AutoModelForCausalLM.from_pretrained(
        args.model_path,
        local_files_only=True,
        trust_remote_code=True,
        dtype=dtype,
    ).to(device)
    model = PeftModel.from_pretrained(base_model, args.adapter_path, local_files_only=True).to(device)
    model.eval()
    model.generation_config.temperature = None
    model.generation_config.top_p = None
    model.generation_config.top_k = None

    rows = load_eval_rows(args.eval_file)
    results = []
    for index, row in enumerate(rows, start=1):
        reply = generate_reply(model, tokenizer, row["prompt"], device, args.max_new_tokens)
        hits, total, hit_keywords = keyword_score(reply, row.get("must_have_keywords", []))
        result = {
            **row,
            "reply": reply,
            "keyword_hits": hits,
            "keyword_total": total,
            "hit_keywords": hit_keywords,
        }
        results.append(result)
        print(f'[{index:02d}/{len(rows)}] {row["id"]}: {hits}/{total} {reply[:80]}')

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = output_dir / f"adapter_eval_{stamp}.jsonl"
    md_path = output_dir / f"adapter_eval_{stamp}.md"

    with jsonl_path.open("w", encoding="utf-8") as file:
        for item in results:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
    write_markdown(md_path, results)

    total_hits = sum(item["keyword_hits"] for item in results)
    total_keywords = sum(item["keyword_total"] for item in results)
    print(f"Saved JSONL: {jsonl_path}")
    print(f"Saved Markdown: {md_path}")
    print(f"Keyword score: {total_hits}/{total_keywords}")


if __name__ == "__main__":
    main()
