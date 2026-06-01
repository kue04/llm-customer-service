from __future__ import annotations

import json
import os
from urllib import request


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
    with request.urlopen(http_request, timeout=60) as response:
        response_body = response.read().decode("utf-8")
    data = json.loads(response_body)
    return data["choices"][0]["message"]["content"].strip()
