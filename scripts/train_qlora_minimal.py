from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

os.environ.setdefault("HF_DATASETS_CACHE", str(Path(".cache/huggingface/datasets").resolve()))
os.environ.setdefault("HF_HOME", str(Path(".cache/huggingface").resolve()))

from datasets import Dataset
import torch
from peft import LoraConfig, TaskType, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig


# TRL 1.2 reads bundled jinja templates without an explicit encoding. On
# Chinese Windows locales that defaults to GBK, so force UTF-8 for those reads.
_ORIGINAL_READ_TEXT = Path.read_text


def _read_text_utf8(self: Path, encoding: str | None = None, errors: str | None = None) -> str:
    return _ORIGINAL_READ_TEXT(self, encoding=encoding or "utf-8", errors=errors)


Path.read_text = _read_text_utf8

from trl import SFTConfig, SFTTrainer  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Minimal QLoRA/LoRA SFT smoke script.")
    parser.add_argument("--model-path", default="local_models/qwen2.5-1.5b-instruct")
    parser.add_argument("--train-file", default="data/messages/takeout_sft_train.jsonl")
    parser.add_argument("--extra-train-files", nargs="*", default=[])
    parser.add_argument("--extra-repeat", type=int, default=1)
    parser.add_argument("--eval-file", default="data/messages/takeout_sft_val.jsonl")
    parser.add_argument("--output-dir", default="models/takeout-qwen-lora-minimal")
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--max-samples", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=512)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--use-4bit", choices=["auto", "true", "false"], default="auto")
    return parser.parse_args()


def should_use_4bit(mode: str) -> bool:
    has_cuda = torch.cuda.is_available()
    has_bitsandbytes = importlib.util.find_spec("bitsandbytes") is not None
    if mode == "true" and not (has_cuda and has_bitsandbytes):
        raise RuntimeError("4-bit QLoRA needs CUDA and bitsandbytes. Install bitsandbytes and use a CUDA GPU.")
    return mode == "true" or (mode == "auto" and has_cuda and has_bitsandbytes)


def read_jsonl(path: str, max_rows: int | None = None) -> list[dict]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if line.strip():
                rows.append(json.loads(line))
            if max_rows is not None and len(rows) >= max_rows:
                break
    return rows


def build_text_dataset(
    path: str,
    tokenizer: AutoTokenizer,
    max_samples: int,
    extra_paths: list[str] | None = None,
    extra_repeat: int = 1,
):
    rows = read_jsonl(path, max_samples if max_samples > 0 else None)
    for extra_path in extra_paths or []:
        extra_rows = read_jsonl(extra_path)
        rows.extend(extra_rows * max(1, extra_repeat))

    dataset = Dataset.from_list(rows)

    def to_text(example: dict) -> dict:
        return {
            "text": tokenizer.apply_chat_template(
                example["messages"],
                tokenize=False,
                add_generation_prompt=False,
            )
        }

    return dataset.map(to_text, remove_columns=dataset.column_names)


def main() -> None:
    args = parse_args()
    use_4bit = should_use_4bit(args.use_4bit)
    device_dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float16
    model_dtype = device_dtype if torch.cuda.is_available() else torch.float32

    tokenizer = AutoTokenizer.from_pretrained(args.model_path, local_files_only=True, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    quantization_config = None
    device_map = None
    if use_4bit:
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=device_dtype,
        )
        device_map = "auto"

    model = AutoModelForCausalLM.from_pretrained(
        args.model_path,
        local_files_only=True,
        trust_remote_code=True,
        dtype=model_dtype,
        quantization_config=quantization_config,
        device_map=device_map,
    )
    if torch.cuda.is_available() and not use_4bit:
        model = model.to("cuda")
    model.config.use_cache = False
    if use_4bit:
        model = prepare_model_for_kbit_training(model)

    train_dataset = build_text_dataset(
        args.train_file,
        tokenizer,
        args.max_samples,
        args.extra_train_files,
        args.extra_repeat,
    )
    eval_dataset = build_text_dataset(args.eval_file, tokenizer, min(8, args.max_samples))

    peft_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )

    training_args = SFTConfig(
        output_dir=args.output_dir,
        max_steps=args.max_steps,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=args.learning_rate,
        logging_steps=1,
        save_strategy="no",
        eval_strategy="no",
        report_to="none",
        dataset_text_field="text",
        max_length=args.max_length,
        packing=False,
        fp16=torch.cuda.is_available() and model_dtype == torch.float16,
        bf16=torch.cuda.is_available() and model_dtype == torch.bfloat16,
        use_cpu=not torch.cuda.is_available(),
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
    )

    print(f"Training mode: {'QLoRA 4-bit' if use_4bit else 'LoRA'}")
    print(f"CUDA available: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"Model dtype: {model_dtype}")
    print(f"Train samples: {len(train_dataset)}")
    trainer.train()
    trainer.save_model(args.output_dir)
    tokenizer.save_pretrained(args.output_dir)
    print(f"Saved LoRA adapter to: {args.output_dir}")


if __name__ == "__main__":
    main()
