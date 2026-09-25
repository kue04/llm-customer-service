# 后端交付说明：/chat/prompt trace 内容层 + 意图识别（B1~B7）

> 产出日期：2026-09-23　｜　分支：`optimize/interview-ready`
> 对应前端提示词：`docs/BACKEND_TRACE_FIX_PROMPT_2026-09-23.md`
> **状态：后端 7 项全部改完，端到端 16 项断言全 PASS，全量门禁 927 / 0F / 0E。**
> **跨端界面复核未做 —— 按提示词 §5.4，看到界面截图才算完成。**

---

## 一、30 秒版

| 你要的 | 状态 | 备注 |
|---|---|---|
| B1 `memory_loaded` 补内容 | ✅ | `recent_preview` + `long_term_summary` |
| B2 `intent_detected` 补置信度/证据/次意图 | ✅ | `confidence` 取不到就**不放键** |
| B3 `risk_precheck` 补检测信息 | ✅ | `requires_safety_prefix` 是 **int 0/1** |
| B4 `order_tool_called` 带工具名 | ✅ | 重复摘要问题消失 |
| B5 上下文继承（指代消解） | ✅ | 带 `inherited_from_context`，置信度 **0.6** |
| B6 第一步 修饰词归一化 | ✅ | 「骑手一直联系不上」现在能命中 |
| B6 第二步 加「不新鲜」关键词 | ⛔ **未做** | 业务策略，等老霸拍板 |
| B7 低置信度 `clarify` | ✅ | **需要前端补 1 行词表** |

---

## 二、字段名清单（照契约表落盘，一个没改名）

| step | metadata 键 | 实测值示例 |
|---|---|---|
| `memory_loaded` | `session_id` | `"sess_trace_verify_1"` |
| | `recent_preview` | `[]`（首轮无历史）→ 有历史时形如 `["用户：会员退款多久到账"]` |
| | `long_term_summary` | `"last_service_summary=最近咨询：通用客服咨询；风险等级：low；common_issue_types=退款/售后；…"`（**无画像时不放这个键**） |
| `intent_detected` | `risk_level` | `"medium"` |
| | `primary_intent` | `"退款进度"` |
| | `confidence` | `0.86`（**number**；取不到时不放键） |
| | `evidence` | `["退款", "多久到账"]` |
| | `secondary_intents` | `[]` / `["配送异常追问"]` |
| | ⭐ `inherited_from_context` | `"退款进度"`（**契约表外的键**，见第四节） |
| `risk_precheck` | `routing` | `"rag"` / `"high_risk_rag"` / ⭐ `"clarify"` |
| | `risk_level` | `"medium"` |
| | `risk_source` | `"intent_detected"` |
| | `matched_high_risk_intents` | `[]` / `["食品安全投诉"]` |
| | `requires_safety_prefix` | `0` / `1`（**int，不是 boolean**） |
| `order_tool_called` | `tool_count` | `2` |
| | `tools` | `[{"tool_name":"query_order_status","status":"failed","latency_ms":1.09,"summary":"order_user_mismatch"}, …]` |

`order_tool_called` 的 `output_summary` 现在是
`query_order_status: order_user_mismatch；query_refund_status: order_user_mismatch`
—— 旧的 `order_user_mismatch；order_user_mismatch` 重复问题消失。

---

## 三、需要前端做的事（2 件）

### 1. 补 `clarify` 词条（1 行）

`src/lib/status.ts` 的 `ROUTING_TEXT` 现在只有 `rag` / `high_risk_rag`。
不补的话界面会显示「**clarify（未收录的路由值，词表待补）**」——不瞎猜，但体验差。

```ts
clarify: "澄清链路（置信度过低，应先向用户确认诉求）",
```

### 2. 跑界面复核并出截图

```bash
node docs/diag_panel_probe.cjs
```

**看到界面上的内容之前，这件事不算完成**（JSON 里有字段 ≠ 前端真的用上了）。

---

## 四、两处「超出提示词」的改动（都不是擅自加需求，逐条说明理由）

### 1. `intent_detected.metadata` 多了一个 `inherited_from_context`

契约表外的键，前端忽略未知键无害。加它的理由是提示词 §B5 自己的要求：
**必须让人分得清「猜出来的意图」和「真命中的意图」**。
继承场景下它等于被继承的意图名（如 `"退款进度"`），同时 `confidence` 恒为 **0.6**
（低于任何直接命中的规则置信度，最低一档是 0.76）。

### 2. B5 加了两条守卫（提示词没写，但不加会造新缺陷）

- `last_primary_intent` 必须在 `KNOWN_INTENT_NAMES`（**由 `INTENT_RULES` 推导**，非手写）内；
- 本句必须含指代词（`COREFERENCE_HINTS`）。

**不加第一条守卫的后果**：上一轮走兜底时 `facts.last_primary_intent` 就是 `"通用客服咨询"`，
照提示词原样实现会把「上一句没识别出来」**伪装成「继承成功」**，
还带 `inherited_from_context=True` —— 正是本项目明令禁止的事。
登记踩坑 `E5`。

---

## 五、⚠️ B7 的落差：补的是信号，不是链路

提示词 §B7 写「让链路可以做澄清而不是硬猜」，但**后端 `routing` 零分支消费**：

```
services/chat_service.py:638   build_decision_trace 里透传给前端（展示）
services/chat_service.py:925   risk_precheck 的 output_summary（展示）
```

链路怎么走（检索 → rerank → 安全校验 → 生成）**没有任何一处读 `routing`**。
所以加了 `clarify` 之后：

- ✅ 界面上能识别「这条我拿不准」；
- ❌ **链路仍然照常检索、照常生成**，不会真的去澄清。

要让链路真的澄清，需要新增分支代码 + 前后端契约再对齐一轮 —— 那是下一批的事，
本批严格停在提示词 §6.5 划的范围内（只改 `full_trace` metadata 和意图识别逻辑）。
登记踩坑 `B16`。

---

## 六、未做项（不要当成已完成）

| 项 | 为什么没做 |
|---|---|
| B6 第二步：往 `食品安全投诉` 加「不新鲜」 | 该意图 `risk_level="high"`，加词后用户整句话会被抬成 high risk 链路（安全前缀 / 可能转人工）。**属业务策略，不是技术修复**，提示词 §6.4 明令不许自己决定 —— 等老霸拍板 |
| 界面截图复核 | 需要前端跑 `diag_panel_probe.cjs`，提示词 §5.4 |

---

## 七、证据文件

| 文件 | 内容 |
|---|---|
| `reports/rag_ingestion_auth_review/trace_after_b1_b2_b3_b4_20260923_234234.json` | B1~B4 真实响应（四个 step 的 metadata 全在里面，**前端可直接拿它复核字段名**） |
| `reports/rag_ingestion_auth_review/trace_after_b5_inherited_20260923_234234.json` | B5 继承场景完整响应 |
| `reports/rag_ingestion_auth_review/trace_fix_junit.xml` | 全量测试 JUnit XML（927 / 0F / 0E） |

## 八、验收怎么复跑

```bash
cd D:/llm/llm-customer-service
export RAG_JWT_SECRET=dev-secret-please-change-me-32bytes+
./venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
./venv/Scripts/python.exe scripts/mint_dev_token.py --role admin --format token

export TRACE_FIX_TOKEN=<上面签出的令牌>
./venv/Scripts/python.exe scripts/verify_trace_fix.py
# 非默认端口：TRACE_FIX_BASE=http://127.0.0.1:8002 ...
```

> 令牌**走环境变量，不硬编码**：硬编码提交一次就等于把一把 admin 令牌永久留在 git 历史里
> （JWT 撤销不了）。缺了直接报错退出，**不退化成「跳过鉴权」**。登记踩坑 `C7`。
