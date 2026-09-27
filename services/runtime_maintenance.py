"""Offline import and retention tools; never called from an HTTP request."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from sqlalchemy import and_, select, text

from services.ingestion.db import get_engine
from services.runtime_schema import metadata


def _key(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def import_legacy_sqlite(source: Path, mappings: list[dict], *, apply: bool = False) -> dict:
    """Import one immutable SQLite file with a reviewed identity for every row.

    Mapping: {table, key: {legacy_primary_key: value}, tenant_id, created_by}.
    Unknown ownership, conflicting tenant claims, duplicate mappings and target
    conflicts abort the operation. Defaults to a read-only planning pass.
    """
    source = Path(source).resolve(strict=True)
    mapping_index = {}
    for item in mappings:
        table = str(item["table"])
        if "runtime_" + table not in metadata.tables:
            raise ValueError(f"unsupported legacy table: {table}")
        if not item.get("tenant_id") or not item.get("created_by"):
            raise ValueError("every mapping needs a trusted tenant_id and created_by")
        key = (table, _key(item["key"]))
        if key in mapping_index:
            raise ValueError(f"duplicate mapping for {table}")
        mapping_index[key] = item
    rows_by_table = {}
    unmapped = []
    used = set()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as legacy:
        legacy.row_factory = sqlite3.Row
        present = {row[0] for row in legacy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in metadata.sorted_tables:
            name = table.name.removeprefix("runtime_")
            if name not in present:
                continue
            # Identifiers come only from the fixed application metadata allowlist.
            columns = list(legacy.execute(f'PRAGMA table_info("{name}")'))
            primary = [row["name"] for row in sorted(columns, key=lambda row: row["pk"]) if row["pk"]]
            if not primary:
                raise ValueError(f"legacy table has no auditable primary key: {name}")
            converted = []
            for source_row in legacy.execute(f'SELECT * FROM "{name}"'):
                row = dict(source_row)
                row_key = {column: row[column] for column in primary}
                mapping_key = (name, _key(row_key))
                mapping = mapping_index.get(mapping_key)
                if mapping is None:
                    unmapped.append({"table": name, "key": row_key})
                    continue
                used.add(mapping_key)
                tenant = mapping["tenant_id"]
                creator = mapping["created_by"]
                if row.get("tenant_id") and row["tenant_id"] != tenant:
                    raise ValueError(f"trusted mapping conflicts with existing tenant: {name}")
                if (
                    name
                    in {
                        "conversations",
                        "conversation_turns",
                        "order_states",
                        "user_memory",
                        "order_state_requests",
                        "users",
                    }
                    and row.get("user_id") != creator
                ):
                    raise ValueError(f"trusted creator must match recorded owner: {name}")
                row = {key: value for key, value in row.items() if key in table.c}
                row.update(tenant_id=tenant, created_by=creator)
                row.setdefault("created_at", row.get("updated_at") or now)
                row.setdefault("updated_at", row["created_at"])
                row.setdefault("deleted_at", None)
                converted.append(row)
            rows_by_table[table.name] = converted
    unused = sorted(set(mapping_index) - used)
    report = {
        "source_sha256": digest,
        "planned": {name: len(rows) for name, rows in rows_by_table.items()},
        "unmapped": unmapped,
        "unused_mapping_count": len(unused),
        "applied": False,
    }
    if not apply:
        return report
    if unmapped or unused:
        raise ValueError("import requires an exact reviewed mapping for every legacy row; run dry-run first")
    # A single transaction covers all tables. No upsert, overwrite or automatic
    # inference: collisions fail and roll back, leaving the source untouched.
    with get_engine().begin() as connection:
        for table in metadata.sorted_tables:
            for row in rows_by_table.get(table.name, []):
                connection.execute(table.insert().values(**row))
        if connection.dialect.name == "postgresql":
            for table in metadata.sorted_tables:
                if "id" in table.c and rows_by_table.get(table.name):
                    # Supplied legacy ids must not leave an identity sequence behind.
                    connection.execute(
                        text(
                            "SELECT setval(pg_get_serial_sequence(:table_name, 'id'), "
                            f"GREATEST((SELECT COALESCE(MAX(id), 1) FROM {table.name}), 1), true)"
                        ),
                        {"table_name": table.name},
                    )
    report["applied"] = True
    return report


def archive_runtime_data(tenant_id: str, cutoff: str, output: Path, *, apply: bool = False) -> dict:
    """Archive a tenant's complete expired conversations plus independent records.

    A reviewable JSONL archive is fully written before soft deletion commits.
    Never archive active prompt/knowledge rows or active orders. Caller selects
    the cutoff per retention policy; original timestamps and ownership survive.
    """
    if not tenant_id:
        raise ValueError("tenant_id is required")
    parsed = datetime.fromisoformat(cutoff)
    if parsed.tzinfo is None:
        raise ValueError("cutoff must contain a timezone")
    cutoff = parsed.astimezone(timezone.utc).isoformat()
    output = Path(output)
    if output.exists():
        raise FileExistsError("archive target already exists")
    now = datetime.now(timezone.utc).isoformat()
    selected = {}
    independent = {"runtime_feedback", "runtime_audit_logs", "runtime_chat_sessions", "runtime_order_state_requests"}
    with get_engine().begin() as connection:
        # Cooperate with online tenant transactions for a consistent archive.
        if connection.dialect.name == "postgresql":
            key = int.from_bytes(hashlib.sha256(tenant_id.encode()).digest()[:8], "big", signed=True)
            connection.execute(text("SET LOCAL lock_timeout = '5s'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        elif connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        sessions = metadata.tables["runtime_conversations"]
        expired_ids = select(sessions.c.session_id).where(
            sessions.c.tenant_id == tenant_id,
            sessions.c.updated_at < cutoff,
            sessions.c.deleted_at.is_(None),
        )
        for table in metadata.sorted_tables:
            conditions = [table.c.tenant_id == tenant_id, table.c.deleted_at.is_(None)]
            if table.name == "runtime_conversations":
                conditions.append(table.c.updated_at < cutoff)
            elif table.name.startswith("runtime_conversation_"):
                conditions.append(table.c.session_id.in_(expired_ids))
            elif table.name in independent:
                conditions.append(table.c.updated_at < cutoff)
            elif table.name == "runtime_order_states":
                conditions.extend([table.c.updated_at < cutoff, table.c.status == "delivered"])
            elif table.name == "runtime_knowledge_items":
                conditions.extend(
                    [table.c.updated_at < cutoff, table.c.status.in_(["archived", "rollback", "rejected"])]
                )
            elif table.name == "runtime_prompt_versions":
                conditions.extend([table.c.updated_at < cutoff, table.c.status == "rollback"])
            else:
                continue
            predicate = and_(*conditions)
            rows = [dict(row) for row in connection.execute(select(table).where(predicate)).mappings()]
            selected[table.name] = (table, rows)
        summary = {name: len(rows) for name, (_, rows) in selected.items()}
        if not apply:
            return {"applied": False, "counts": summary}
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as target:
            for name, (_, rows) in selected.items():
                for row in rows:
                    target.write(json.dumps({"table": name, "row": row}, ensure_ascii=False) + "\n")
            target.flush()
            import os

            os.fsync(target.fileno())
        # Target exact archived primary keys; later writes cannot broaden this set.
        for _, (table, rows) in selected.items():
            for row in rows:
                predicate = and_(
                    *(table.c[col.name] == row[col.name] for col in table.primary_key),
                    table.c.updated_at == row["updated_at"],
                )
                result = connection.execute(table.update().where(predicate).values(deleted_at=now))
                if result.rowcount != 1:
                    raise RuntimeError("record changed while archiving; transaction rolled back")
    return {"applied": True, "counts": summary, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
