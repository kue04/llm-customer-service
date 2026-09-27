from __future__ import annotations

import json
import os
from urllib import request
from services.request_budget import checkpoint, remaining_timeout
from services.call_ledger import tracked_model


def build_online_chat_completions_url(api_base_url: str) -> str:
    normalized_url = api_base_url.strip().rstrip("/")
    if not normalized_url:
        raise ValueError("online_api_base_url is empty")
    if normalized_url.endswith("/chat/completions"):
        return normalized_url
    return f"{normalized_url}/chat/completions"


def generate_online_chat_completion(
    prompt: str,
    system_prompt: str,
    model_name: str,
    api_base_url: str,
    api_key_env: str,
) -> str:
    return generate_online_chat_completion_with_usage(
        prompt=prompt,
        system_prompt=system_prompt,
        model_name=model_name,
        api_base_url=api_base_url,
        api_key_env=api_key_env,
    )["text"]


@tracked_model('online', 'generation', lambda *a, **kw: kw.get('model_name') or a[2])
def generate_online_chat_completion_with_usage(
    prompt: str,
    system_prompt: str,
    model_name: str,
    api_base_url: str,
    api_key_env: str,
) -> dict:
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        raise ValueError(f"{api_key_env} is not set")
    if not model_name:
        raise ValueError("online_model_name is empty")

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        build_online_chat_completions_url(api_base_url),
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    checkpoint()
    with request.urlopen(http_request, timeout=remaining_timeout(60)) as response:
        response_body = response.read().decode("utf-8")
    checkpoint()
    data = json.loads(response_body)
    usage = data.get("usage") or {}
    return {
        "text": data["choices"][0]["message"]["content"].strip(),
        "usage": {
            "counting_method": 'provider_usage' if usage else 'unknown',
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }
