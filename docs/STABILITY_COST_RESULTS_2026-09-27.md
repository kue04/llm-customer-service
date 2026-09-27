# 5.4 稳定性与成本实施记录

日期：2026-09-27。范围是非 Docker、本地既有 Qwen/BGE 模型、PostgreSQL、Redis、独立 API/worker。没有更换模型、安装新依赖、修改生产服务、提交或部署。5.3 的独立真实问法标注和质量目标仍未完成，不属于本报告的通过结论。

**交付结论：5.4 主要实现已落地，已有一份可使用的本机 CPU 对照数据和恢复记录。按用户要求停止追加压测、切换 GPU 和重复回归。** 本机安装的是 PyTorch 2.11.0+cpu，以下数据没有使用用户的 5060，不能当作该显卡的性能数据。

## 一页结果

环境：6 核 / 12 线程，约 31.7 GiB 内存；既有 Qwen2.5-1.5B、BGE；1 API + 1 worker；CPU 线程配置 6；固定诊断语料。前后 seed Markdown SHA256 一致。

| 负载 | 并发 | 改动前成功请求 P95 | 改动后成功请求 P95 | 改动后结果 |
| --- | ---: | ---: | ---: | --- |
| 聊天 | 1 | 9.50 秒 | 11.52 秒 | 2/2 成功 |
| 聊天 | 4 | 48.27 秒 | 19.30 秒 | 2/8 成功，6/8 明确拒绝 |
| 入库 | 4 | 1.92 秒 | 2.34 秒 | 8/8 成功 |
| 聊天 + 入库 | 4 | 52.89 秒 | 23.97 秒 | 6/8 成功，2/8 明确拒绝 |

这不是“整体提速”：聊天并发 4 的成功吞吐仍约 0.104 次/秒，主要变化是停止无界排队并明确拒绝超额请求；串行聊天和入库有额外开销。表中延迟只针对成功请求，拒绝比例同时列出；全部尝试分位数和各级结果保存在 `reports/stability_cost/summary.json`，不把快速拒绝当作生成加速。

恢复演练通过：新数据库和新文件目录恢复了 **22 份文档、44 个对象目录文件、67 个索引目录文件**，各表数量和文件哈希一致；6 条引用逐条匹配源库已发布、同租户 chunk 的原文，主证据正确，跨租户隔离通过。**实测恢复耗时 22.47 秒；静默源库下观测数据损失 0。** 这不是生产 SLA/PITR 承诺。初次单文档引用断言不适用于 22 文档快照的失败报告保留，未修改检索质量规则或引用内容来制造通过。

资源口径修正：本轮 RSS/CPU 采样命中了 Windows Python 启动器，不能代表实际模型进程，汇总明确标为不可用；连接池真实事件文件仍在，汇总已按实际 PID 离线读取。已修复今后的采样代码，但遵照用户要求不再重跑。

## 执行顺序与证据

1. 先测原代码，再实现预算与队列机制。初测报告在 `reports/stability_cost/before/`：聊天并发 1/2 的 P95 为 9.312/18.219 秒，并发 4 为 36.312 秒且 8 次中有 1 次 ReadError。旧接口在 async 路由中直接执行同步检索/生成，出现明显串行排队。入库并发 1/2/4 均完成；混合并发 4 的 P95 为 48.782 秒，按预设 30 秒停止条件停止增加并发。
2. `reports/stability_cost/budget/20260927T075855Z-72bf92fe.json`：实际本地模型、1.5 秒 HTTP 总预算，超时 504、四请求突发为 1×504 + 3×429；断连后可再次接纳请求。HTTP 返回后本次实测线程名额额外回收时间约 4.0–5.5 秒，主要落在不可逐 token 中断的首轮计算内。**没有把 HTTP 返回等同于模型立即停止。** 首次固定 0.5 秒回收探针失败的报告同目录保留。
3. `reports/stability_cost/delivery/20260927T080318Z-02fae837.json`：真实进程 kill、长任务租约互斥、Redis pending 接管、两次依赖错误后第三次成功、ACK 失败后索引哈希不变、永久错误死信、授权/幂等重投、跨租户 404、requires_review 正常 ACK 且不能 redrive。
4. `reports/stability_cost/ledger/20260927T080822Z-7e11d135.json`：真实生成前后重启，单请求生成 token 与账本一致，明细/汇总一致，跨租户按 request_id 查询为空；本地金额 null。后续 0006 补齐运行时表的归属与生命周期字段，旧记录回填为 legacy-ledger，不伪造历史操作者。
5. 固定样本对照为 `reports/stability_cost/before_v3/` 与 `reports/stability_cost/after/`；恢复通过报告为 `reports/stability_cost/recovery/20260927_verified.json`，初次失败为同目录 `20260927.json`。统一汇总为 `reports/stability_cost/summary.json`。

## 实现与运行边界

### 请求总预算与背压

- ASGI 入口记录开始时间，授权后的聊天处理使用剩余总预算。同步业务放到有界线程池；保存 ContextVar 身份，不改变 JWT、租户、会话归属和订单幂等语义。
- 默认每 API 进程最多 4 个未退出的聊天工作，其中每租户最多 2 个；生成最多 1 个并发。等待模型也消耗总预算，超额入口直接 429 + Retry-After。实例数增加时总容量相应倍增，这不是跨实例集中式聊天限流。
- 本地生成每个 token 检查取消/期限，线上 socket timeout 不超过剩余预算；检索/工具前后及业务 SQL/提交检查预算，数据库锁/语句局部超时不得超过剩余预算。
- 取消或超时后仍在执行的同步调用继续占用名额，直到实际退出；不强杀 Python 线程，不重试订单写操作。原生模型单次计算和不协作的外部 I/O 只保证有界隔离，不能宣称立即终止。
- PostgreSQL 事务锁串行化入库容量检查与任务创建，默认全库 64、每租户 16 个 pending/running/retrying；在写对象前拒绝满容量请求。requires_review 和死信不占活动队列容量。
- 错误语义：429 容量拒绝可重试；504 request_timeout、499 request_cancelled 不自动重试整条聊天，因为先前可能已有业务写入。现有依赖故障的安全降级继续保留。

### Pending、执行租约与死信

- Redis XAUTOCLAIM 的 idle 只产生接管候选，执行时还须获得 PostgreSQL 会话级任务锁和索引目录锁。处理与所有流水线事务使用同一专属数据库连接；长任务用 XCLAIM JUSTID 更新心跳。仅超过 idle 不能并发执行同一任务。
- 同一索引目录内的 worker 发布串行化，防止接管与并发任务争用 manifest。这是发布正确性保护，不是细化已有业务租户锁的吞吐优化。
- delivery_attempts 在执行前落库，包含被杀死的尝试；明确的连接/超时异常有限指数退避，永久错误或次数耗尽为 failed + dead_letter_at，默认最多 3 次。next_retry_at 持久化；Redis idle 与扫描周期可能使实际重试晚于该时间。
- 只有持久化终态才 ACK；ACK 失败会再次接管，succeeded/requires_review/cancelled/死信直接收敛，不重复发布。DB 无法持久化失败时保留 pending，不静默丢任务。
- `POST /ingestion-jobs/{id}/redrive` 复用主管/管理员重处理权限、租户和知识库写权限、文档 ACL；源任务行锁 + replay_job_id 保证同一死信重复点击返回同一个新任务，留审计。未改变原有 reprocess 的“新建任务”接口。
- requires_review 不属于故障重试或死信，不能通过 redrive 放行。Redis 生产需支持 XAUTOCLAIM（6.2+）；进程内队列仍只是测试/开发降级，不提供崩溃恢复保证。

### 调用账本与指标

- 生成、embedding、reranking 使用同一 runtime_model_calls；记录 request_id/job_id/attempt、provider/model/kind、开始/结束/状态、token 来源、价格版本、耗时及调用线程 CPU 时间。线程 CPU 不包含 Torch 原生工作线程，不能当作完整机器能耗。
- 每次实际调用开始前先持久化 started；完成更新同一唯一 ID，不按重试重复插入同一次调用的费用。进程被杀或结束落库失败时留下 started/未知用量，不能自动推定成功、失败或免费。
- 不保存 prompt、模型正文、密钥或原始异常文本。`GET /ops/model-calls` 复用运营读权限、服务端 tenant 过滤和分页；request_id/job_id 只是租户内筛选条件。
- `/ops/metrics` 新增 model_calls 明细汇总，scope 明确是模型尝试，不与原有完成聊天轮次重复相加。embedding/reranking token 不冒充生成 token。没有已确认价格时 amount=null；汇总显式给出 unknown_cost_calls。
- 可选价格文件以 `provider/model` 为 key，包含 confirmed=true、version、currency、input_per_million、output_per_million。货币分别汇总，不混加不同币种；此轮没有配置未经确认的外部价格。
- `/ops/capacity` 返回当前 API 的工作名额/模型等待/连接池占用，以及当前租户持久化积压数量和最老等待时间；signals 区分 model_capacity_wait 与 ingestion_backlog_age。依赖故障继续由 `/health/dependencies` 表达。没有配置外部告警投递渠道。

## 迁移与配置

先执行 `venv/Scripts/python.exe -m alembic upgrade head`，再启动新代码。新增 0004_delivery_attempts、0005_model_call_ledger、0006_ledger_audit_fields；没有运行时建表。0005 使用冻结表定义，0006 兼容已生成的旧格式账本。

所有预算配置集中列于 `.env.example`。本机故障实验缩短的 lease=2 秒、retry base=0.5 秒、deadline=1.5 秒只用于专属子进程；不应误抄为默认生产配置。

## 可复跑入口

先配置真实 RAG_DATABASE_URL/RAG_REDIS_STREAM_URL、HF_HUB_OFFLINE=1、PYTHONUTF8=1、OMP_NUM_THREADS=6、MKL_NUM_THREADS=6。使用全新的目录、专用数据库和已有本地模型；不能把共享活动数据库作为恢复目标。

```powershell
# 容量：每次传不存在的目录。可用 --capacity-phases chat / ingestion mixed 分组。
venv/Scripts/python.exe scripts/accept_live_rag.py --run-root tmp/new-capacity-run --report-dir reports/stability_cost/rerun --capacity
# 短预算与断连（需为该子进程配置短 deadline）。
venv/Scripts/python.exe scripts/accept_live_rag.py --run-root tmp/new-budget-run --budget-checks
# 专属 worker 的 crash/ACK/重试/死信实验。
venv/Scripts/python.exe scripts/accept_live_rag.py --run-root tmp/new-delivery-run --worker-checks
# 正常生成、重启和明细汇总核对。
venv/Scripts/python.exe scripts/accept_live_rag.py --run-root tmp/new-ledger-run --ledger-checks
# 停止源 API/worker 后执行备份恢复，目标数据库自动新建，源库不覆盖。
venv/Scripts/python.exe scripts/restore_stability_snapshot.py --source-report reports/stability_cost/after/RUN.json --pg-bin PATH_TO_PG_BIN --run-root tmp/new-restore-run --report reports/stability_cost/recovery/RUN.json
```

## 验证口径与限制

- 完整测试曾先暴露两项账本 schema 约定错误，补齐 metadata 注册及运行时归属字段后，使用真实 PostgreSQL 测试环境的最终完整检查为 **1202 passed，7 subtests passed，0 skipped**。不能把中间失败报告改写成通过。
- 上述是最近一次已执行的完整回归，JUnit 将 7 个子测试计入 1209 个用例。随后补充的池观测、容量边界测试及少量诊断/过滤改动未再次运行完整回归；新补充测试不计作已通过。按用户要求停止追加验证，只保留已有真实负载/恢复证据和已执行的检查记录。
- 入库原始基线只有端到端耗时与部分阶段日志，没有完整逐阶段耗时序列；不得据此声称全部阶段的瓶颈均已测定。
- 容量是单机、单 API、单 worker、小型诊断语料的有界闭环测量，最多并发 4，每级 2×并发次。样本很小，P99 基本等于最大值；不能外推到历史 9,229 chunks 或线上 QPS/SLA。
- 保留每次尝试，包括失败、拒绝和超时；报告同时保留全部尝试分位数和成功吞吐。拒绝变快不能当作成功请求延迟改善。
- 初测对象内容含动态标识，且仅记录数据库连接数。最终固定样本 v2 并增加真正的 pool checkout/checkin 观测；用哈希验证的旧源码副本补测，初测仅作为诊断证据，不混作最终严格对照。
- 恢复演练针对已静默的专用源库，数据库、对象和索引整体备份；报告实际 RPO/RTO。它不等于带在线写入/WAL 的生产 PITR 能力，也不代表业务已确认 SLA。
