# 订单状态安全边界

订单 HTTP 读写和聊天订单/退款工具统一按 JWT `user_id + tenant_id` 访问。请求体中的 `user_id` 仅兼容旧客户端，不能指定归属；HTTP 响应返回真实归属。未知订单、其他用户订单和其他租户订单的读取/修改均返回 404。角色写权限不代表可修改其他用户订单。

## 旧库迁移

首次连接在 SQLite 写锁中幂等新增 `order_states.tenant_id`。历史记录保留 `tenant_id=''`，不推断租户、不允许首次请求认领；因此旧订单会暂时不可访问。恢复历史订单前，需要通过可信订单来源核实 `order_id/user_id/tenant_id`，备份数据库，再由维护人员完成离线映射。本次未将任何历史记录分配给默认租户。既有全局 `order_id` 主键保留，其他租户不能用同一 ID 覆盖归属。

## 写入一致性

- 可选请求头 `Idempotency-Key` 按租户和用户隔离；同键同内容返回原始结果，同键不同内容返回 409。新状态写入后重放旧键不回滚订单。
- 未提供幂等键时，相同 PUT 内容保留更新时间，不重复写入审计事件。
- 仅对既有配送阶段 `created → merchant_preparing → rider_picked/delivering → delivered` 禁止倒退，允许快照跳过中间阶段。未知阶段可首次导入或原样刷新，其变化返回 409，需可信订单系统先提供迁移规则。没有推断取消/退款业务状态机。
- 所有权检查、状态更新、幂等结果与审计前后快照处于同一 SQLite 写事务；审计失败会回滚订单和幂等记录。审计快照含租户/用户，沿用既有敏感内容脱敏。
- 聊天工具缺少用户或租户时拒绝读取；不再返回没有归属信息的内置演示订单。

## 回归验证

`venv/Scripts/python.exe -m pytest tests/test_order_authorization.py tests/test_order_state_store.py tests/test_audit_and_release.py -q`

覆盖 JWT 归属、跨用户/租户读写、旧数据隔离、聊天工具、重复请求、状态倒退、审计失败回滚及并发抢占/幂等。
