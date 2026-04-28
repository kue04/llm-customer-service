from __future__ import annotations

import argparse

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load a LoRA adapter and run one chat generation.")
    parser.add_argument("--model-path", default="local_models/qwen2.5-1.5b-instruct")
    parser.add_argument("--adapter-path", default="models/takeout-qwen-lora-minimal")
    parser.add_argument("--prompt", default="我的外卖已经超时 30 分钟了，应该怎么办？")
    parser.add_argument("--max-new-tokens", type=int, default=128)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    device = "cuda" if torch.cuda.is_available() else "cpu"

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

    messages = [
        {
            "role": "system",
            "content": "你是外卖平台中文客服。回答要礼貌、准确、简洁，先安抚用户，再说明原因，最后给出可执行的下一步。",
        },
        {"role": "user", "content": args.prompt},
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
            max_new_tokens=args.max_new_tokens,
            do_sample=False,
            pad_token_id=tokenizer.eos_token_id,
        )
    reply_ids = outputs[0, inputs["input_ids"].shape[-1] :]
    print(tokenizer.decode(reply_ids, skip_special_tokens=True).strip())


if __name__ == "__main__":
    main()
