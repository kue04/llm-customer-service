r"""端到端验收：`/chat/prompt` 的 `full_trace` 内容层（B1~B4）+ 意图识别（B5~B7）。

用法
----
服务端与本脚本必须使用同一个 ``RAG_JWT_SECRET``（否则 401）::

    export RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+
    ./venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
    ./venv/Scripts/python.exe scripts/mint_dev_token.py --role admin --format token

    export TRACE_FIX_TOKEN=<上面签出的令牌>
    ./venv/Scripts/python.exe scripts/verify_trace_fix.py

    # 跑在非默认端口
    TRACE_FIX_BASE=http://127.0.0.1:8002 ./venv/Scripts/python.exe scripts/verify_trace_fix.py

为什么令牌走环境变量
--------------------
原提示词里令牌是硬编码在脚本里的。那样一旦提交进仓库就等于把一把 admin
令牌永久留在历史里（撤销不了），所以改成环境变量：缺了直接报错退出，
不给默认值、不退化成「跳过鉴权」。

完整响应报文会落到 ``reports/rag_ingestion_auth_review/``，前端拿它复核字段名。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = os.environ.get("TRACE_FIX_BASE", "http://127.0.0.1:8001")
TOKEN = os.environ.get("TRACE_FIX_TOKEN", "")

REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "rag_ingestion_auth_review"
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

failures: list[str] = []


def ask(message: str, session: str, order_id: str | None = "WM73865302") -> dict:
    body = {
        "message": message,
        "user_id": "demo_user",
        "session_id": session,
        "order_id": order_id,
        "channel": "app",
    }
    request = urllib.request.Request(
        f"{BASE}/chat/prompt",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def step_of(data: dict, name: str) -> dict:
    return next((item for item in data.get("full_trace", []) if item.get("step") == name), {})


def check(label: str, condition: bool, detail: str = "") -> None:
    mark = "  PASS  " if condition else "  FAIL  "
    suffix = f"  <- {detail}" if detail and not condition else ""
    print(mark + label + suffix)
    if not condition:
        failures.append(label)


def dump(label: str, data: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"trace_after_{label}_{STAMP}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  响应报文已存：{path}")


def main() -> int:
    if not TOKEN:
        print("缺少 TRACE_FIX_TOKEN：先跑 scripts/mint_dev_token.py 签令牌并 export", file=sys.stderr)
        return 2

    print(f"BASE = {BASE}")
    print("=== B1 memory_loaded ===")
    data = ask("会员退款多久到账", "sess_trace_verify_1")
    dump("b1_b2_b3_b4", data)
    meta = step_of(data, "memory_loaded").get("metadata", {})
    check("recent_preview 是 list", isinstance(meta.get("recent_preview"), list), type(meta.get("recent_preview")).__name__)
    check("long_term_summary 是 str（无画像时缺键算通过）",
          isinstance(meta.get("long_term_summary"), str) or "long_term_summary" not in meta,
          repr(meta.get("long_term_summary")))

    print("=== B2 intent_detected ===")
    meta = step_of(data, "intent_detected").get("metadata", {})
    check("primary_intent 是 str", isinstance(meta.get("primary_intent"), str))
    check("confidence 是 float", isinstance(meta.get("confidence"), float), repr(meta.get("confidence")))
    check("evidence 是 list", isinstance(meta.get("evidence"), list))
    check("secondary_intents 是 list", isinstance(meta.get("secondary_intents"), list))

    print("=== B3 risk_precheck ===")
    meta = step_of(data, "risk_precheck").get("metadata", {})
    check("routing 是 str", isinstance(meta.get("routing"), str))
    check("risk_level 是 str", isinstance(meta.get("risk_level"), str))
    check("risk_source 标明来自 intent_detected", meta.get("risk_source") == "intent_detected")
    check("matched_high_risk_intents 是 list", isinstance(meta.get("matched_high_risk_intents"), list))
    check(
        "requires_safety_prefix 是 int（不是 bool）",
        isinstance(meta.get("requires_safety_prefix"), int)
        and not isinstance(meta.get("requires_safety_prefix"), bool),
        repr(meta.get("requires_safety_prefix")),
    )

    print("=== B4 order_tool_called ===")
    summary = step_of(data, "order_tool_called").get("output_summary", "")
    parts = [part.strip() for part in summary.split("；") if part.strip()]
    check("摘要带工具名（含 ':'）", all(":" in part or "：" in part for part in parts), summary)
    check("摘要无重复（同工具名+同摘要才算重复）", len(set(parts)) == len(parts), summary)
    tools = step_of(data, "order_tool_called").get("metadata", {}).get("tools", [])
    check("tools 是 object[] 且带 latency_ms",
          isinstance(tools, list) and all("latency_ms" in item for item in tools), repr(tools))

    print("=== B6 多意图（修饰词归一化）===")
    data = ask("我想退款，另外骑手一直联系不上", "sess_trace_verify_2")
    check(
        "secondary 含 配送异常追问",
        "配送异常追问" in (data.get("intent_analysis", {}).get("secondary_intents") or []),
        str(data.get("intent_analysis", {}).get("secondary_intents")),
    )

    print("=== B5 上下文继承 ===")
    ask("我想退款要多久", "sess_trace_verify_3")
    data = ask("那要多久才能到", "sess_trace_verify_3")
    dump("b5_inherited", data)
    analysis = data.get("intent_analysis", {})
    check("第二条主意图继承 退款进度", analysis.get("primary_intent") == "退款进度", str(analysis.get("primary_intent")))
    check("带 inherited_from_context 标记", bool(analysis.get("inherited_from_context")))
    check("继承置信度 0.6（低于直接命中）",
          analysis.get("intents", [{}])[0].get("confidence") == 0.6,
          str(analysis.get("intents", [{}])[0].get("confidence")))

    print("=== B7 低置信度走 clarify ===")
    data = ask("你们几点上班", "sess_trace_verify_4")
    analysis = data.get("intent_analysis", {})
    check("兜底意图 routing = clarify", analysis.get("routing") == "clarify", str(analysis.get("routing")))
    check("risk_precheck.metadata.routing 同步为 clarify",
          step_of(data, "risk_precheck").get("metadata", {}).get("routing") == "clarify",
          str(step_of(data, "risk_precheck").get("metadata", {}).get("routing")))

    print()
    if failures:
        print("FAILED: " + ", ".join(failures))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as error:
        print(f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')}", file=sys.stderr)
        sys.exit(3)
