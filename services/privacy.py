from __future__ import annotations

import re


PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
CODE_RE = re.compile(r"(验证码|校验码|验正码)[：: ]?\d{4,8}")
ID_CARD_CONTEXT_RE = re.compile(r"(身份证号|身份证)[：: ]?\d{17}[\dXx]")
ID_CARD_X_RE = re.compile(r"(?<![\dA-Za-z])\d{17}[Xx](?![\dA-Za-z])")
BANK_CARD_CONTEXT_RE = re.compile(r"(银行卡号|银行账户|银行卡|卡号)[：: ]?[\d -]{12,29}")
PAYMENT_CONTEXT_RE = re.compile(r"(支付流水|流水号|交易号|支付单号)[：: ]?[A-Za-z0-9_-]{6,}")
PAYMENT_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])(?:pay|trade|txn)_[A-Za-z0-9_-]{6,}", re.IGNORECASE)
ADDRESS_RE = re.compile(r"(收货地址|配送地址|地址)[：: ]?[^，。；\n]{4,80}")
NAME_RE = re.compile(r"(姓名|收货人|联系人)[：: ]?[\u4e00-\u9fa5]{2,4}")
ORDER_RE = re.compile(r"(?<!\d)\d{12,20}(?!\d)")
SENSITIVE_KEY_TOKENS = (
    "address",
    "receiver_address",
    "shipping_address",
    "phone",
    "mobile",
    "rider_phone",
    "id_card",
    "identity_card",
    "bank_card",
    "card_number",
    "payment_no",
    "payment_id",
    "transaction_id",
    "trade_no",
    "pay_no",
    "pay_id",
    "location",
    "user_location",
    "customer_name",
    "receiver_name",
    "user_name",
    "手机号",
    "电话",
    "骑手电话",
    "地址",
    "收货地址",
    "配送地址",
    "定位",
    "姓名",
    "收货人",
    "联系人",
    "身份证",
    "银行卡",
    "支付流水",
    "支付单号",
    "交易号",
)


def mask_sensitive_text(text: str) -> str:
    masked = PHONE_RE.sub("[手机号已脱敏]", text or "")
    masked = CODE_RE.sub(lambda match: f"{match.group(1)}[已脱敏]", masked)
    masked = ID_CARD_CONTEXT_RE.sub(lambda match: f"{match.group(1)}[已脱敏]", masked)
    masked = ID_CARD_X_RE.sub("[身份证已脱敏]", masked)
    masked = BANK_CARD_CONTEXT_RE.sub(lambda match: f"{match.group(1)}[已脱敏]", masked)
    masked = PAYMENT_CONTEXT_RE.sub(lambda match: f"{match.group(1)}[已脱敏]", masked)
    masked = PAYMENT_TOKEN_RE.sub("[支付流水已脱敏]", masked)
    masked = ADDRESS_RE.sub(lambda match: f"{match.group(1)}[已脱敏]", masked)
    masked = NAME_RE.sub(lambda match: f"{match.group(1)}[已脱敏]", masked)
    masked = ORDER_RE.sub("[订单号已脱敏]", masked)
    return masked


def is_sensitive_key(key: object) -> bool:
    normalized = str(key).lower()
    return any(token in normalized for token in SENSITIVE_KEY_TOKENS)


def mask_sensitive_value(value):
    if value is None:
        return value
    if isinstance(value, (dict, list)):
        return mask_sensitive_payload(value)
    return "[敏感信息已脱敏]"


def mask_sensitive_payload(payload):
    if isinstance(payload, str):
        return mask_sensitive_text(payload)
    if isinstance(payload, list):
        return [mask_sensitive_payload(item) for item in payload]
    if isinstance(payload, dict):
        return {
            key: mask_sensitive_value(value) if is_sensitive_key(key) else mask_sensitive_payload(value)
            for key, value in payload.items()
        }
    return payload
