# 后端改造提示词：/chat/prompt 的 trace 内容层 + 意图识别缺陷

> **用法：整份粘贴给后端窗口（或后端同学），即可开工。** 不需要额外上下文。
> 产出日期：2026-09-23　｜　适用分支：`optimize/interview-ready`
> 配套前端交付说明：`D:\llm\front\docs\DIAGNOSTIC_PANEL_FIX_DELIVERY_2026-09-23.md`
> 验收脚本（前端侧）：`D:\llm\front\docs\diag_panel_probe.cjs`

---

## 一、你要做的事（一句话）

把 `POST /chat/prompt` 返回的 `full_trace` 里 **4 个步骤的内容补实**，让前端诊断面板能真的解释链路；同时修掉意图识别的 3 个已知缺陷。

**前端已经改完并且不用你配合改。** 前端按「`step.metadata` 新字段优先 → 顶层字段兜底」的取值链写好了（见 `src/features/support/diagnosticDetails.ts`）。
**你只要往 `step.metadata` 里补字段，前端立刻生效——唯一前提是字段名不能变。**

---

## 二、硬约束（先读，违反等于白干）

1. **字段名不许改、不许换拼写。** 前端按约定名字读 `metadata`，改名的后果是**前端静默回落到兜底值，不报错、不告警**——你以为改好了，界面上什么都没变。这是本次前后端唯一的耦合点。
2. **不许把「字段缺失」变成「默认值」。** 这是项目硬纪律：缺数据和真实 0 是两件事。别写 `metadata.get("confidence", 0.5)` 这种兜底，缺了就不放这个键，前端会显示「未返回（原因）」。
3. **`trace_step()` 已有截断：`input_summary` / `output_summary` 会被截到 500 字符并做敏感信息掩码**（`chat_service.py:462-477`）。所以**大块内容放 `metadata`，不要塞 `output_summary`**。
4. **别改 `memory_snapshot` 的现有结构**（`chat_service.py:602-621`）。前端刚按真实形状对齐完，再动就要两端同时改。
5. **行号基于 2026-09-23 的仓库状态。** 若有偏移，按函数名 / step 名定位，别按行号硬改。

---

## 三、改动清单

### B1 · `memory_loaded` 只有计数，看不到读到了什么

**位置**：`services/chat_service.py:894-901`

**现状代码**：

```python
full_trace.append(
    trace_step(
        "memory_loaded",
        output_summary=f"recent={len(context.get('recent_messages', []))}, user_memory_fields={len(user_memory)}",
        started_at=memory_started_at,
        metadata={"session_id": context["session_id"]},
    )
)
```

**问题**：`context` 里明明有 `recent_messages`（含 role / content / 各自意图）、`facts`、`summary`，`user_memory` 里明明有画像字段，一个都没进 trace。界面上只有 `recent=0, user_memory_fields=0` 两个计数。

**期望**：

```python
recent_messages = context.get("recent_messages", []) or []
memory_metadata = {
    "session_id": context["session_id"],
    # list[str]，最多 2 条；本来是空的就传空列表，前端有专门的文案
    "recent_preview": [
        f"{'客服' if message.get('role') == 'assistant' else '用户'}：{str(message.get('content', ''))[:40]}"
        for message in recent_messages[-2:]
    ],
}
# 没有画像字段就【不放这个键】，不要放空串——空串和「没返回」在前端是两种文案
if user_memory:
    memory_metadata["long_term_summary"] = "；".join(
        f"{key}={value}" for key, value in list(user_memory.items())[:8]
    )
full_trace.append(
    trace_step(
        "memory_loaded",
        output_summary=f"recent={len(recent_messages)}, user_memory_fields={len(user_memory)}",
        started_at=memory_started_at,
        metadata=memory_metadata,
    )
)
```

**注意**：`context["facts"]` 不必在这里重复——前端已从顶层 `memory_snapshot.short_term.facts` 取全量。这里只补「步骤当时的预览」。

---

### B2 · `intent_detected` 只给主意图名，置信度/证据/次意图全丢

**位置**：`services/chat_service.py:904-911`

**现状代码**：

```python
full_trace.append(
    trace_step(
        "intent_detected",
        output_summary=str(intent_analysis.get("primary_intent", "")),
        started_at=intent_started_at,
        metadata={"risk_level": intent_analysis.get("risk_level", "low")},
    )
)
```

**问题**：`intent_analysis` 里 `intents[]` 每个都带 `confidence` / `evidence` / `risk_level`，`secondary_intents` 也在，全没进 trace。界面上只有一个光秃秃的「退款进度」——置信度 0.5 和 0.96 长得一模一样。

**期望**：

```python
primary_intent = intent_analysis.get("primary_intent", "")
primary_entry = next(
    (item for item in intent_analysis.get("intents", []) if item.get("name") == primary_intent),
    {},
)
intent_metadata = {
    "risk_level": intent_analysis.get("risk_level", "low"),
    "primary_intent": primary_intent,
    "evidence": list(primary_entry.get("evidence", [])),                       # list[str]，命中原文片段
    "secondary_intents": list(intent_analysis.get("secondary_intents", [])),   # list[str]
}
confidence = primary_entry.get("confidence")
if isinstance(confidence, (int, float)):        # 取不到就【不放这个键】，别兜底成 0.5
    intent_metadata["confidence"] = float(confidence)
full_trace.append(
    trace_step(
        "intent_detected",
        output_summary=str(primary_intent),
        started_at=intent_started_at,
        metadata=intent_metadata,
    )
)
```

**注意 `confidence`**：`primary_entry.get("confidence")` 取不到时**不要兜底成 0.5**，直接不放这个键（前端会显示「未返回」）。放了假值比不放更糟——本项目明令禁止把「缺失」伪装成文案。

---

### B3 · `risk_precheck` 这一步自身零检测逻辑

**位置**：`services/chat_service.py:912-928`

**现状代码**：

```python
risk_started_at = time.perf_counter()
get_redis_context_cache().cache_intent_analysis(request_id, intent_analysis)
get_redis_context_cache().set_risk_state(
    context["session_id"],
    {
        "risk_level": intent_analysis.get("risk_level", "low"),
        "primary_intent": intent_analysis.get("primary_intent", ""),
    },
)
full_trace.append(
    trace_step(
        "risk_precheck",
        status="high_risk" if intent_analysis.get("risk_level") in {"high", "critical"} else "success",
        output_summary=str(intent_analysis.get("routing", "rag")),
        started_at=risk_started_at,
    )
)
```

**问题（这是前端用户直接吐槽的点）**：这一步实际只做两件事——写 Redis、把 `routing` 枚举裸打出来。**自身没有任何风险检测**，`status` 也是从 `risk_level` 派生的。`routing` 回答的是「走哪条链路」而不是「风险多大」，界面上就显示一个孤零零的 `rag`，等于零信息。

**期望（补全信息，让它名副其实）**：

```python
risk_level = intent_analysis.get("risk_level", "low")
routing = intent_analysis.get("routing", "rag")
high_risk_intents = [
    item.get("name", "")
    for item in intent_analysis.get("intents", [])
    if item.get("risk_level") in {"high", "critical"}
]
full_trace.append(
    trace_step(
        "risk_precheck",
        status="high_risk" if risk_level in {"high", "critical"} else "success",
        output_summary=str(routing),
        started_at=risk_started_at,
        metadata={
            "routing": routing,
            "risk_level": risk_level,
            "risk_source": "intent_detected",                      # 明确写清风险不是本步判的
            "matched_high_risk_intents": high_risk_intents,        # list[str]
            "requires_safety_prefix": int(bool(intent_analysis.get("requires_safety_prefix"))),  # 0 或 1
        },
    )
)
```

**⚠️ `requires_safety_prefix` 必须传整数 `0` / `1`，不能传 `True` / `False`。** 前端的 `readMetaNumber()` 只认 `number`，传布尔会被静默忽略并回落到顶层字段。这一条踩了不会报错，只会白改。

**另一种选择**：如果你们认为这一步和上一步重复，**直接删掉这个 step 也可以**——但要让前端知道，前端 `BUILDERS` 里有它的解析器（`risk_precheck`），删了之后时间线少一步。二选一，别保留一个空壳。

---

### B4 · `order_tool_called` 摘要重复且不标工具名

**位置**：`services/chat_service.py:951-959`，相关函数 `summarize_tool_output`（`:480-482`）

**现状代码**：

```python
output_summary="；".join(filter(None, (summarize_tool_output(result) for result in tool_results))),
metadata={"tool_count": len(tool_results)},
```

**问题**：实测出现 `order_user_mismatch；order_user_mismatch` —— 两个工具返回相同文本被直接拼接，**且分不清是哪个工具返回的**（`summarize_tool_output` 优先取 `error_type`）。

**期望**：

```python
tool_summaries = []
for result in tool_results:
    name = result.get("tool_name", "unknown_tool")
    detail = summarize_tool_output(result) or "（无可读摘要）"
    tool_summaries.append(f"{name}: {detail}")
full_trace.append(
    trace_step(
        "order_tool_called",
        status="success" if all(result["status"] != "failed" for result in tool_results) else "degraded",
        output_summary="；".join(tool_summaries),
        started_at=order_tool_started_at,
        metadata={
            "tool_count": len(tool_results),
            "tools": [
                {
                    "tool_name": result.get("tool_name", ""),
                    "status": result.get("status", ""),
                    "latency_ms": result.get("latency_ms"),
                    "summary": summarize_tool_output(result),
                }
                for result in tool_results
            ],
        },
    )
)
```

**补充说明（不是 bug，是测试数据）**：`order_user_mismatch` 是因为请求里的 `user_id=demo_user` 与订单 `WM73865302` 的归属不匹配。这是造数问题，与 trace 无关，不用改。

---

### B5 · 意图识别显式丢弃上下文（**收益最大，建议优先**）

**位置**：`services/intent_service.py:141-142`

**现状代码**：

```python
def analyze_intents(query: str, conversation_context: dict | None = None) -> dict:
    del conversation_context
```

**问题**：函数签名收下了 `conversation_context`，**第一行就把它删了**。调用点 `chat_service.py:903` 传的是 `context`，而 `context["facts"]` 里现成就有 `last_primary_intent`（由 `conversation_service.py:109-120` 的 `_facts_from_turn` 写入）。

**实测**：先问「会员退款多久到账」→ 主意图「退款进度」；再问「那要多久才能到」→ 主意图「通用客服咨询」。上文白存了。

**期望**：用 `facts.last_primary_intent` 做指代消解——**当本句没匹配到任何规则（即将走兜底）且句子含指代词时，继承上文主意图**：

```python
def analyze_intents(query: str, conversation_context: dict | None = None) -> dict:
    context = conversation_context or {}
    facts = context.get("facts") or {}
    matched_intents = []
    for rule in INTENT_RULES:
        evidence = _matched_keywords(query, rule.keywords)
        if evidence:
            matched_intents.append(_build_intent(rule, evidence))

    inherited_from = ""
    if not matched_intents:
        last_intent = str(facts.get("last_primary_intent") or "")
        if last_intent and _looks_like_coreference(query):
            matched_intents.append(
                {
                    "name": last_intent,
                    "confidence": 0.6,          # 继承来的，必须低于直接命中，别伪装成高置信
                    "risk_level": facts.get("last_risk_level", "low"),
                    "priority": 50,
                    "evidence": [],             # 本句没有命中证据，留空
                    "inherited_from_context": True,
                }
            )
            inherited_from = last_intent

    if not matched_intents:
        matched_intents.append({... 现有兜底不动 ...})

    # ... 后续不变 ...
    result = {...}
    if inherited_from:
        result["inherited_from_context"] = inherited_from   # 让前端/日志能看出这是继承来的
    return result
```

`_looks_like_coreference` 建议实现（指代词白名单，放 `intent_service.py` 里）：

```python
COREFERENCE_HINTS = ("那", "这", "它", "他", "呢", "还要", "多久", "怎么办", "然后")

def _looks_like_coreference(query: str) -> bool:
    return any(hint in query for hint in COREFERENCE_HINTS)
```

**⚠️ 业务决策提醒**：继承来的意图置信度**必须显著低于直接命中**（建议 0.6），并且**要带上 `inherited_from_context` 标记**。否则「猜出来的意图」和「真命中的意图」在界面上分不出来——这正是本项目明令禁止的事。

---

### B6 · 关键词是精确子串匹配，多意图基本白给

**位置**：`services/intent_service.py:104-105` + `INTENT_RULES` 表（`:23-101`）

**现状代码**：

```python
def _matched_keywords(query: str, keywords: tuple[str, ...]) -> list[str]:
    return [keyword for keyword in keywords if keyword in query]
```

**问题**：`in` 是**精确子串**判断。规则表里 `配送异常追问` 的关键词是 `"骑手联系不上"`，于是：

**实测**：「我想退款，另外骑手一直联系不上，这个菜还不太新鲜」→ **只出「退款进度」，`secondary_intents` 为空**。
- `"骑手联系不上" in "骑手一直联系不上"` → **False**（中间多了「一直」）
- 规则表里没有「新鲜 / 不新鲜」这个词

一句话里的多个诉求就这样漏掉了。

**期望（两步走，第一步必做，第二步需要业务确认）**：

**第一步 — 修饰词归一化（低风险，先做这个）**：匹配前把插入语、程度副词、口语冗余删掉再比：

```python
FILLER_WORDS = ("一直", "老是", "总是", "真的", "非常", "特别", "有点", "一下", "还是", "都")

def _normalize(query: str) -> str:
    normalized = query
    for word in FILLER_WORDS:
        normalized = normalized.replace(word, "")
    return normalized

def _matched_keywords(query: str, keywords: tuple[str, ...]) -> list[str]:
    normalized = _normalize(query)
    return [keyword for keyword in keywords if keyword in query or keyword in normalized]
```

改动后 `"骑手联系不上" in _normalize("骑手一直联系不上")` → **True**，`secondary_intents` 就能出「配送异常追问」。

**第二步 — 补关键词（⚠️ 需要业务确认，别自己决定）**：
「这个菜还不太新鲜」要命中 `食品安全投诉`，得往它的关键词里加「不新鲜 / 新鲜」。

**但请注意**：`食品安全投诉` 是 `risk_level="high"`（`intent_service.py:31-37`）。加词之后，用户的这句话会被抬成 **high risk 链路**，触发安全前缀、可能触发转人工。**这是业务策略改变，不是纯技术修复**——做之前先跟产品/老霸确认，别默默加。

**测试用例（改完必须过）**：

| 输入 | 期望 |
|---|---|
| `我想退款，另外骑手一直联系不上` | `primary=退款进度`，`secondary` 含 `配送异常追问` |
| `会员退款多久到账` | `primary=退款进度`，`confidence≥0.84`（直接命中，别被 B5 影响） |
| `我想退款要多久` → 再 `那要多久才能到` | 第二条 `primary=退款进度`，且带 `inherited_from_context` |

---

### B7 · 低置信度没有「需澄清」通道

**位置**：`services/intent_service.py:149-179`

**现状**：无规则命中 → 兜底 `通用客服咨询` + `confidence=0.5`，`routing` 仍然算作 `"rag"`，照常检索生成。界面上 0.5 和 0.96 **长得一模一样**，没有任何「这条我拿不准」的信号。

**期望**：低置信度给出独立的 routing 值，让链路可以做澄清而不是硬猜：

```python
if primary_intent["confidence"] < 0.6:
    routing = "clarify"
elif RISK_RANK.get(risk_level, 0) >= RISK_RANK["high"]:
    routing = "high_risk_rag"
else:
    routing = "rag"
```

**⚠️ 前端同步要求**：`routing` 是白名单枚举，前端 `src/lib/status.ts` 的 `ROUTING_TEXT` 现在只收录了 `rag` / `high_risk_rag`。新增 `clarify` 后，前端会显示 **「clarify（未收录的路由值，词表待补）」**——不会瞎猜，但体验差。

**所以：新增任何 routing 值，必须同时通知前端补词表**（前端加 1 行即可）。建议一并给出该值的中文说明，例如 `clarify: "澄清链路（置信度过低，应先向用户确认诉求）"`。

---

## 四、前后端字段契约表（唯一耦合点，照抄别改名）

| step | metadata 键 | 类型 | 前端拿它做什么 |
|---|---|---|---|
| `memory_loaded` | `session_id` | `string` | 已有，保留 |
| | `recent_preview` | `string[]` | 列「本轮读到的历史消息」 |
| | `long_term_summary` | `string` | 长期画像字段的人话摘要 |
| `intent_detected` | `risk_level` | `string` | 已有，保留 |
| | `primary_intent` | `string` | 优先于 `output_summary` 显示主意图 |
| | `confidence` | `number`（0~1） | 显示百分比；**< 0.6 前端会标注「建议改走澄清」**；缺键显示「未返回」 |
| | `evidence` | `string[]` | 命中原文片段，空数组显示「兜底意图不携带证据」 |
| | `secondary_intents` | `string[]` | 空数组显示「本次只有 1 个意图命中」 |
| `risk_precheck` | `routing` | `string` | 经词表翻成「普通 RAG 链路（rag）」 |
| | `risk_level` | `string` | 显示风险等级，并注明「继承自上一步」 |
| | `requires_safety_prefix` | **`number` 0/1** | ⚠️ 传 boolean 会被前端忽略 |
| `order_tool_called` | `tool_count` | `number` | 已有，保留 |
| | `tools` | `object[]` | 暂未被前端消费（前端走顶层 `tool_results`），补上以便后续切换 |

**兜底逻辑说明**：以上所有键**缺了都不报错**，前端会回落到顶层字段（`memory_snapshot` / `intent_analysis` / `tool_results`）。所以「改了一半」看起来也正常运转——**必须用下面的验收脚本确认字段真的出现了**。

---

## 五、验收（必须真跑，别只看代码）

### 5.1 起服务

本机 8001 是有 `RAG_JWT_SECRET` 的实例（**8000 上有个没配密钥的遗留进程，全线 500 `authentication is misconfigured`，别用**）：

```bash
cd D:/llm/llm-customer-service
./venv/Scripts/python.exe scripts/mint_dev_token.py --role admin --format token
# 把输出填到下面脚本的 TOKEN 里
```

### 5.2 断言脚本

保存为 `scripts/verify_trace_fix.py`，用 `./venv/Scripts/python.exe scripts/verify_trace_fix.py` 跑：

```python
import json
import urllib.request

BASE = "http://127.0.0.1:8001"
TOKEN = "<粘贴 mint_dev_token 的输出>"
SESSION = "sess_trace_verify_1"

def ask(message, session=SESSION):
    body = json.dumps({
        "message": message, "user_id": "demo_user",
        "session_id": session, "order_id": "WM73865302", "channel": "app",
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}/chat/prompt", data=body,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))

def step_of(data, name):
    return next((s for s in data.get("full_trace", []) if s.get("step") == name), {})

failures = []

def check(label, condition, detail=""):
    print(("  PASS  " if condition else "  FAIL  ") + label + (f"  <- {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)

print("=== B1 memory_loaded ===")
data = ask("会员退款多久到账")
meta = step_of(data, "memory_loaded").get("metadata", {})
check("recent_preview 是 list", isinstance(meta.get("recent_preview"), list), type(meta.get("recent_preview")).__name__)
check("long_term_summary 是 str", isinstance(meta.get("long_term_summary"), str))

print("=== B2 intent_detected ===")
meta = step_of(data, "intent_detected").get("metadata", {})
check("confidence 是 float", isinstance(meta.get("confidence"), float), repr(meta.get("confidence")))
check("evidence 是 list", isinstance(meta.get("evidence"), list))
check("secondary_intents 是 list", isinstance(meta.get("secondary_intents"), list))

print("=== B3 risk_precheck ===")
meta = step_of(data, "risk_precheck").get("metadata", {})
check("routing 是 str", isinstance(meta.get("routing"), str))
check("risk_level 是 str", isinstance(meta.get("risk_level"), str))
check("requires_safety_prefix 是 int（不是 bool）",
      isinstance(meta.get("requires_safety_prefix"), int)
      and not isinstance(meta.get("requires_safety_prefix"), bool),
      repr(meta.get("requires_safety_prefix")))

print("=== B4 order_tool_called ===")
summary = step_of(data, "order_tool_called").get("output_summary", "")
parts = [p.strip() for p in summary.split("；") if p.strip()]
check("摘要带工具名（含 ':'）", all(":" in p or "：" in p for p in parts), summary)
check("摘要无重复", len(set(parts)) == len(parts), summary)

print("=== B6 多意图（修饰词归一化）===")
data = ask("我想退款，另外骑手一直联系不上", session="sess_trace_verify_2")
check("secondary 含 配送异常追问",
      "配送异常追问" in (data.get("intent_analysis", {}).get("secondary_intents") or []),
      str(data.get("intent_analysis", {}).get("secondary_intents")))

print("=== B5 上下文继承 ===")
ask("我想退款要多久", session="sess_trace_verify_3")
data = ask("那要多久才能到", session="sess_trace_verify_3")
analysis = data.get("intent_analysis", {})
check("第二条主意图继承 退款进度", analysis.get("primary_intent") == "退款进度",
      str(analysis.get("primary_intent")))
check("带 inherited_from_context 标记", bool(analysis.get("inherited_from_context")))

print()
print("FAILED: " + ", ".join(failures) if failures else "全部通过")
```

### 5.3 还要满足

- `./venv/Scripts/python.exe -m pytest tests -q` 不新增失败。
- 现有测试若断言了 `output_summary` 的旧格式（如 `recent=0, user_memory_fields=0`、`rag`），**改断言，不要为了让测试过而回退改动**。
- 改完把真实响应报文存一份（`curl ... -o trace_after.json`），前端要拿它复核字段名。

### 5.4 跨端复核

后端改完通知我，我跑 `docs/diag_panel_probe.cjs` 做界面回归（真实浏览器，抓请求体/截图）。
**在界面截图上看到内容之前，这件事不算完成**——字段在 JSON 里出现 ≠ 前端真的用上了。

---

## 六、不许做的事

1. **不许改字段名**（前文三-约束 1，出事不报错，最难查）。
2. **不许加兜底默认值**掩盖缺失（`metadata.get("confidence", 0.5)` 之类）。
3. **不许把 `requires_safety_prefix` 写成布尔**。
4. **不许自己决定「往食品安全投诉加关键词」**（B6 第二步）——那是业务策略，会抬高整条链路的风险等级。
5. **不许动 `memory_snapshot` 结构、不许动 `/retrieval/search` 与 `/retrieval/search-demo` 的字段**——本次范围只有 `full_trace` 的 metadata 和意图识别逻辑。
6. **不许为了让测试变绿而回退改动**。
7. **B5/B6/B7 要改 `intent_service.py`**，这会改变线上识别结果。**改前先跟老霸确认**，改后必须跑 5.2 的脚本并附实测输出。

---

## 七、优先级建议

| 优先级 | 项 | 理由 |
|---|---|---|
| P0 | **B5** | 上下文被 `del` 掉是明确的逻辑缺陷，原料（`facts.last_primary_intent`）现成，改动小、收益最直观 |
| P0 | **B1 / B2 / B3** | 纯补 metadata，不改任何判定逻辑，**零回归风险**，前端立刻见效 |
| P1 | **B6 第一步**（归一化） | 低风险，解决多意图漏识别 |
| P1 | **B4** | 改输出格式，注意别破坏现有测试断言 |
| P2 | **B7** | 需要前端同步补词表，两端一起做 |
| P2 | **B6 第二步**（加关键词） | **先要业务确认**，不是技术问题 |
