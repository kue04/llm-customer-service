from __future__ import annotations

import re


PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
CODE_RE = re.compile(r"(验证码|校验码|验正码)[：: ]?\d{4,8}")
ORDER_RE = re.compile(r"(?<!\d)\d{12,20}(?!\d)")


def mask_sensitive_text(text: str) -> str:
    masked = PHONE_RE.sub("[手机号已脱敏]", text or "")
    masked = CODE_RE.sub(lambda match: f"{match.group(1)}[已脱敏]", masked)
    masked = ORDER_RE.sub("[订单号已脱敏]", masked)
    return masked
