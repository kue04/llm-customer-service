"""Runtime business schema; installed by Alembic, never by a request."""

from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_N_name)s",
        "pk": "pk_%(table_name)s",
    }
)

users = Table(
    "runtime_users",
    metadata,
    Column("user_id", Text, primary_key=True, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=True),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_users_tenant_updated", users.c.tenant_id, users.c.updated_at)
Index("ix_runtime_users_owner", users.c.tenant_id, users.c.user_id)

conversations = Table(
    "runtime_conversations",
    metadata,
    Column("session_id", Text, primary_key=True, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("order_id", Text, nullable=True),
    Column("status", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False),
    Column("created_by", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_conversations_tenant_updated", conversations.c.tenant_id, conversations.c.updated_at)
Index("ix_runtime_conversations_owner", conversations.c.tenant_id, conversations.c.user_id)

conversation_messages = Table(
    "runtime_conversation_messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=False),
    Column("session_id", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("content", Text, nullable=False),
    Column("intent_json", Text, nullable=False),
    Column("risk_level", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=False),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index(
    "ix_runtime_conversation_messages_tenant_updated",
    conversation_messages.c.tenant_id,
    conversation_messages.c.updated_at,
)
Index(
    "ix_runtime_conversation_messages_session",
    conversation_messages.c.tenant_id,
    conversation_messages.c.session_id,
    conversation_messages.c.created_at,
)

conversation_turns = Table(
    "runtime_conversation_turns",
    metadata,
    Column("request_id", Text, primary_key=True, nullable=False),
    Column("session_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("order_id", Text, nullable=True),
    Column("query", Text, nullable=False),
    Column("reply", Text, nullable=False),
    Column("response_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=True),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_conversation_turns_tenant_updated", conversation_turns.c.tenant_id, conversation_turns.c.updated_at)
Index("ix_runtime_conversation_turns_owner", conversation_turns.c.tenant_id, conversation_turns.c.user_id)
Index(
    "ix_runtime_conversation_turns_session",
    conversation_turns.c.tenant_id,
    conversation_turns.c.session_id,
    conversation_turns.c.created_at,
)

conversation_review_actions = Table(
    "runtime_conversation_review_actions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=False),
    Column("request_id", Text, nullable=False),
    Column("session_id", Text, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("order_id", Text, nullable=True),
    Column("action", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("original_reply", Text, nullable=False),
    Column("final_reply", Text, nullable=False),
    Column("reason", Text, nullable=False),
    Column("operator_id", Text, nullable=False),
    Column("operator_role", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=False),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index(
    "ix_runtime_conversation_review_actions_tenant_updated",
    conversation_review_actions.c.tenant_id,
    conversation_review_actions.c.updated_at,
)
Index(
    "ix_runtime_conversation_review_actions_owner",
    conversation_review_actions.c.tenant_id,
    conversation_review_actions.c.user_id,
)
Index(
    "ix_runtime_conversation_review_actions_session",
    conversation_review_actions.c.tenant_id,
    conversation_review_actions.c.session_id,
    conversation_review_actions.c.created_at,
)

conversation_facts = Table(
    "runtime_conversation_facts",
    metadata,
    Column("session_id", Text, primary_key=True, nullable=False),
    Column("key", Text, primary_key=True, nullable=False),
    Column("value", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=True),
    Column("created_by", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_conversation_facts_tenant_updated", conversation_facts.c.tenant_id, conversation_facts.c.updated_at)

user_memory = Table(
    "runtime_user_memory",
    metadata,
    Column("user_id", Text, primary_key=True, nullable=False),
    Column("key", Text, primary_key=True, nullable=False),
    Column("value", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=True),
    Column("created_by", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_user_memory_tenant_updated", user_memory.c.tenant_id, user_memory.c.updated_at)
Index("ix_runtime_user_memory_owner", user_memory.c.tenant_id, user_memory.c.user_id)

order_states = Table(
    "runtime_order_states",
    metadata,
    Column("order_id", Text, primary_key=True, nullable=False),
    Column("user_id", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("status_label", Text, nullable=False),
    Column("delivery_status", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("refund_status", Text, nullable=False),
    Column("store_name", Text, nullable=False),
    Column("items_json", Text, nullable=False),
    Column("total", Float, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False),
    Column("created_by", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_order_states_tenant_updated", order_states.c.tenant_id, order_states.c.updated_at)
Index("ix_runtime_order_states_owner", order_states.c.tenant_id, order_states.c.user_id)

order_state_requests = Table(
    "runtime_order_state_requests",
    metadata,
    Column("user_id", Text, primary_key=True, nullable=False),
    Column("idempotency_key", Text, primary_key=True, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("response_json", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=True),
    Column("created_by", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index(
    "ix_runtime_order_state_requests_tenant_updated",
    order_state_requests.c.tenant_id,
    order_state_requests.c.updated_at,
)
Index("ix_runtime_order_state_requests_owner", order_state_requests.c.tenant_id, order_state_requests.c.user_id)

chat_sessions = Table(
    "runtime_chat_sessions",
    metadata,
    Column("request_id", Text, primary_key=True, nullable=False),
    Column("query", Text, nullable=False),
    Column("reply", Text, nullable=False),
    Column("trace_json", Text, nullable=False),
    Column("top1_intent", Text, nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("answer_source", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("user_id", Text, nullable=False, server_default=text("''")),
    Column("session_id", Text, nullable=False, server_default=text("''")),
    Column("order_id", Text, nullable=True),
    Column("token_usage_json", Text, nullable=False, server_default=text("'{}'")),
    Column("prompt_tokens", Integer, nullable=False, server_default=text("0")),
    Column("completion_tokens", Integer, nullable=False, server_default=text("0")),
    Column("total_tokens", Integer, nullable=False, server_default=text("0")),
    Column("token_counting_method", Text, nullable=False, server_default=text("''")),
    Column("tenant_id", String(200), nullable=False, primary_key=True),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_chat_sessions_tenant_updated", chat_sessions.c.tenant_id, chat_sessions.c.updated_at)
Index("ix_runtime_chat_sessions_owner", chat_sessions.c.tenant_id, chat_sessions.c.user_id)
Index(
    "ix_runtime_chat_sessions_session",
    chat_sessions.c.tenant_id,
    chat_sessions.c.session_id,
    chat_sessions.c.created_at,
)

feedback = Table(
    "runtime_feedback",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=False),
    Column("request_id", Text, nullable=False),
    Column("query", Text, nullable=False),
    Column("reply", Text, nullable=False),
    Column("helpful", Integer, nullable=False),
    Column("reason", Text, nullable=False),
    Column("expected_reply", Text, nullable=False),
    Column("trace_json", Text, nullable=False),
    Column("top1_intent", Text, nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("answer_source", Text, nullable=False),
    Column("failure_stage", Text, nullable=False),
    Column("exported", Integer, nullable=False, server_default=text("0")),
    Column("created_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=False),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_feedback_tenant_updated", feedback.c.tenant_id, feedback.c.updated_at)

audit_logs = Table(
    "runtime_audit_logs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=False),
    Column("operator_id", Text, nullable=False),
    Column("operator_role", Text, nullable=False),
    Column("action_type", Text, nullable=False),
    Column("object_type", Text, nullable=False),
    Column("object_id", Text, nullable=False),
    Column("request_id", Text, nullable=False, server_default=text("''")),
    Column("before_summary", Text, nullable=False, server_default=text("''")),
    Column("after_summary", Text, nullable=False, server_default=text("''")),
    Column("ip", Text, nullable=False, server_default=text("''")),
    Column("device_info", Text, nullable=False, server_default=text("''")),
    Column("created_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=False),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index("ix_runtime_audit_logs_tenant_updated", audit_logs.c.tenant_id, audit_logs.c.updated_at)

prompt_versions = Table(
    "runtime_prompt_versions",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=False),
    Column("version", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("system_prompt", Text, nullable=False),
    Column("developer_prompt", Text, nullable=False, server_default=text("''")),
    Column("change_reason", Text, nullable=False, server_default=text("''")),
    Column("author", Text, nullable=False, server_default=text("''")),
    Column("evaluation_result", Text, nullable=False, server_default=text("''")),
    Column("effective_at", Text, nullable=False, server_default=text("''")),
    Column("created_at", Text, nullable=False),
    Column("activated_at", Text, nullable=False, server_default=text("''")),
    Column("rolled_back_from", Text, nullable=False, server_default=text("''")),
    Column("tenant_id", String(200), nullable=False, primary_key=False),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
    UniqueConstraint("tenant_id", "version", name="uq_runtime_prompt_version"),
)
Index("ix_runtime_prompt_versions_tenant_updated", prompt_versions.c.tenant_id, prompt_versions.c.updated_at)

knowledge_items = Table(
    "runtime_knowledge_items",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=False),
    Column("base_id", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("title", Text, nullable=False, server_default=text("''")),
    Column("question", Text, nullable=False),
    Column("answer", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("intent", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("owner", Text, nullable=False, server_default=text("'knowledge_ops'")),
    Column("source", Text, nullable=False, server_default=text("'knowledge_ops'")),
    Column("effective_at", Text, nullable=False, server_default=text("''")),
    Column("expired_at", Text, nullable=False, server_default=text("''")),
    Column("review_note", Text, nullable=False, server_default=text("''")),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("reviewed_at", Text, nullable=False, server_default=text("''")),
    Column("tenant_id", String(200), nullable=False, primary_key=False),
    Column("created_by", Text, nullable=False),
    Column("deleted_at", Text),
    UniqueConstraint("tenant_id", "base_id", "version", name="uq_runtime_knowledge_version"),
)
Index("ix_runtime_knowledge_items_tenant_updated", knowledge_items.c.tenant_id, knowledge_items.c.updated_at)

knowledge_publish_history = Table(
    "runtime_knowledge_publish_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True, nullable=False),
    Column("publish_id", Text, nullable=False),
    Column("action", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("merged_count", Integer, nullable=False, server_default=text("0")),
    Column("item_ids", Text, nullable=False, server_default=text("'[]'")),
    Column("backup_path", Text, nullable=False, server_default=text("''")),
    Column("knowledge_path", Text, nullable=False, server_default=text("''")),
    Column("faiss_index_path", Text, nullable=False, server_default=text("''")),
    Column("note", Text, nullable=False, server_default=text("''")),
    Column("created_at", Text, nullable=False),
    Column("tenant_id", String(200), nullable=False, primary_key=False),
    Column("created_by", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("deleted_at", Text),
)
Index(
    "ix_runtime_knowledge_publish_history_tenant_updated",
    knowledge_publish_history.c.tenant_id,
    knowledge_publish_history.c.updated_at,
)

conversations.append_constraint(
    UniqueConstraint("tenant_id", "session_id", "created_by", name="uq_runtime_session_owner")
)
for child in (conversation_messages, conversation_turns, conversation_review_actions, conversation_facts):
    child.append_constraint(
        ForeignKeyConstraint(
            ["tenant_id", "session_id", "created_by"],
            ["runtime_conversations.tenant_id", "runtime_conversations.session_id", "runtime_conversations.created_by"],
            ondelete="CASCADE",
            name=f"fk_{child.name}_session_owner",
        )
    )
Index("ix_runtime_prompt_active", prompt_versions.c.tenant_id, prompt_versions.c.status, prompt_versions.c.activated_at)
Index("ix_runtime_feedback_filters", feedback.c.tenant_id, feedback.c.helpful, feedback.c.created_at)
Index("ix_runtime_audit_object", audit_logs.c.tenant_id, audit_logs.c.object_type, audit_logs.c.object_id)
Index("ix_runtime_audit_request", audit_logs.c.tenant_id, audit_logs.c.request_id)
Index(
    "ix_runtime_knowledge_status", knowledge_items.c.tenant_id, knowledge_items.c.status, knowledge_items.c.updated_at
)
