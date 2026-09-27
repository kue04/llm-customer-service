# 两个 P0 的修复与验证

日期：2026-09-27。范围：系统评审中的聊天历史越权、订单状态越权与归属伪造，以及直接绕过这些边界的聊天调用路径。

## 行为变化

- `/chat/history` 的 `user_id` 参数保留兼容但不再参与身份判定；按 JWT 用户、租户以及会话/订单过滤。不存在和越权统一 404。
- `/chat/prompt` 忽略请求体中的用户归属；恢复会话前检查 JWT 用户与租户。已有会话不可换绑，竞争创建只有一个归属能够成功。
- `/chat/review-action` 先检查目标 turn 所属会话，再执行复核、返回原回复或创建转人工信息；跨用户、跨租户和未知 turn 统一 404。
- 长期记忆按 `(tenant_id, user_id, key)` 保存、读取和更新，防止不同租户相同用户 ID 串读串写。
- 订单 HTTP 接口及聊天订单/退款工具均强制用户、租户绑定；写入忽略请求体归属，禁止接管其他主体的订单。未提供租户的内部工具调用不返回订单数据，移除无归属演示数据回退。
- 订单支持 `Idempotency-Key`、已有配送阶段的倒退保护，以及与状态更新同事务保存的脱敏审计前后快照。详细规则见 `ORDER_STATE_SECURITY.md`。

## 旧数据与兼容影响

首次打开 SQLite 存储时，在写锁事务内补充会话和订单的 `tenant_id`，并迁移长期记忆的复合主键。原值保留；无法从原数据确认的租户置为空字符串。

这些旧记录不能通过带有效 JWT 的在线接口读取或认领。需要依据可信的业务映射，在维护流程中补充用户和租户；不能把所有历史记录默认归给当前用户或某个租户。用户可以创建新会话继续使用。

新会话和订单仍沿用全局唯一的 `session_id`、`order_id`；本次没有改成租户内唯一。无历史记录时 `/chat/history` 从空列表 200 改为 404。客服角色本身不构成访问他人会话或订单的授权。

## 验证

修复前实际运行 JWT API 负向用例，确认历史和订单读取、复核越权均返回了 200；修复后相关联合回归 **140 passed**，仅有依赖弃用警告。

```powershell
venv/Scripts/python.exe -m pytest tests/test_chat_ownership.py tests/test_memory_tenant_isolation.py tests/test_conversation_service.py tests/test_chat_api.py tests/test_chat_service_degrade.py tests/test_chat_retrieval_track.py tests/test_read_auth_coverage.py tests/test_auth_context.py tests/test_order_authorization.py tests/test_order_state_store.py tests/test_audit_and_release.py -q --basetemp .tmp-p0-chat -p no:cacheprovider --tb=short
```

新增回归覆盖伪造用户、跨用户/租户读取和修改、会话接管、聊天到订单工具的身份传递、长期记忆隔离、旧库迁移、并发抢占、幂等冲突和审计失败回滚。本次执行的是相关测试集，未将结果描述为全项目测试通过，也未执行生产部署或历史租户归属回填。
