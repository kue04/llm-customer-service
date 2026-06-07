from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "knowledge_ops.db"
KNOWLEDGE_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "takeout_customer_service_seed.jsonl"
BACKUP_DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge_backups"
VALID_REVIEW_STATUSES = {"approved", "rejected"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            category TEXT NOT NULL,
            intent TEXT NOT NULL,
            status TEXT NOT NULL,
            review_note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reviewed_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_publish_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            publish_id TEXT NOT NULL,
            action TEXT NOT NULL,
            status TEXT NOT NULL,
            merged_count INTEGER NOT NULL DEFAULT 0,
            item_ids TEXT NOT NULL DEFAULT '[]',
            backup_path TEXT NOT NULL DEFAULT '',
            knowledge_path TEXT NOT NULL DEFAULT '',
            faiss_index_path TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )
    connection.commit()


def row_to_item(row: sqlite3.Row) -> dict:
    return dict(row)


def create_knowledge_item(payload: dict) -> dict:
    now = utc_now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            INSERT INTO knowledge_items
            (base_id, version, question, answer, category, intent, status, created_at, updated_at)
            VALUES (?, 1, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                f"kb_{uuid4().hex[:12]}",
                payload["question"],
                payload["answer"],
                payload["category"],
                payload["intent"],
                now,
                now,
            ),
        )
        connection.commit()
        return get_knowledge_item(int(cursor.lastrowid))
    finally:
        connection.close()


def get_knowledge_item(item_id: int) -> dict:
    connection = get_connection()
    try:
        row = connection.execute("SELECT * FROM knowledge_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"knowledge item not found: {item_id}")
        return row_to_item(row)
    finally:
        connection.close()


def update_knowledge_item(item_id: int, payload: dict) -> dict:
    original = get_knowledge_item(item_id)
    now = utc_now()
    connection = get_connection()
    try:
        latest_version = connection.execute(
            "SELECT MAX(version) AS version FROM knowledge_items WHERE base_id = ?",
            (original["base_id"],),
        ).fetchone()["version"]
        cursor = connection.execute(
            """
            INSERT INTO knowledge_items
            (base_id, version, question, answer, category, intent, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?)
            """,
            (
                original["base_id"],
                int(latest_version) + 1,
                payload["question"],
                payload["answer"],
                payload["category"],
                payload["intent"],
                now,
                now,
            ),
        )
        connection.commit()
        return get_knowledge_item(int(cursor.lastrowid))
    finally:
        connection.close()


def archive_knowledge_item(item_id: int) -> dict:
    now = utc_now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            "UPDATE knowledge_items SET status = 'archived', updated_at = ? WHERE id = ?",
            (now, item_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"knowledge item not found: {item_id}")
        connection.commit()
        return get_knowledge_item(item_id)
    finally:
        connection.close()


def review_knowledge_item(item_id: int, status: str, review_note: str = "") -> dict:
    if status not in VALID_REVIEW_STATUSES:
        raise ValueError("status must be approved or rejected")
    now = utc_now()
    connection = get_connection()
    try:
        cursor = connection.execute(
            """
            UPDATE knowledge_items
            SET status = ?, review_note = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, review_note, now, now, item_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"knowledge item not found: {item_id}")
        connection.commit()
        return get_knowledge_item(item_id)
    finally:
        connection.close()


def list_knowledge_items(
    limit: int = 20,
    offset: int = 0,
    category: str = "",
    intent: str = "",
    status: str = "",
    keyword: str = "",
) -> dict:
    clauses = []
    params: list[object] = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if intent:
        clauses.append("intent = ?")
        params.append(intent)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if keyword:
        clauses.append("(question LIKE ? OR answer LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    connection = get_connection()
    try:
        total = connection.execute(
            f"SELECT COUNT(*) AS total FROM knowledge_items {where_sql}",
            params,
        ).fetchone()["total"]
        rows = connection.execute(
            f"""
            SELECT * FROM knowledge_items
            {where_sql}
            ORDER BY updated_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [row_to_item(row) for row in rows],
        }
    finally:
        connection.close()


def export_approved_jsonl() -> dict:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM knowledge_items
            WHERE status = 'approved'
            ORDER BY base_id, version
            """
        ).fetchall()
    finally:
        connection.close()

    lines = []
    for row in rows:
        payload = {
            "id": f"{row['base_id']}_v{row['version']}",
            "source": "knowledge_ops",
            "dialogue_type": "single_turn",
            "quality": "reviewed",
            "question": row["question"],
            "answer": row["answer"],
            "category": row["category"],
            "intent": row["intent"],
            "sentiment": "neutral",
            "entities": {"risk": "待确认"},
        }
        lines.append(json.dumps(payload, ensure_ascii=False))
    return {"count": len(lines), "jsonl": "\n".join(lines)}


def build_jsonl_payload(row: sqlite3.Row) -> dict:
    return {
        "id": f"{row['base_id']}_v{row['version']}",
        "source": "knowledge_ops",
        "dialogue_type": "single_turn",
        "quality": "reviewed",
        "question": row["question"],
        "answer": row["answer"],
        "category": row["category"],
        "intent": row["intent"],
        "sentiment": "neutral",
        "entities": {"risk": "待确认"},
    }


def rebuild_vector_store() -> None:
    from utils.vector_retriever import reset_vector_store_cache, save_real_vector_store

    reset_vector_store_cache()
    save_real_vector_store()
    reset_vector_store_cache()


def reset_runtime_vector_cache() -> None:
    from utils.vector_retriever import reset_vector_store_cache

    reset_vector_store_cache()


def get_faiss_index_path() -> Path:
    from utils.vector_retriever import FAISS_INDEX_PATH

    return FAISS_INDEX_PATH


def create_backup(publish_id: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f"{publish_id}.jsonl"
    if KNOWLEDGE_DATA_PATH.exists():
        shutil.copy2(KNOWLEDGE_DATA_PATH, backup_path)
    else:
        backup_path.write_text("", encoding="utf-8")
    return backup_path


def insert_publish_history(
    connection: sqlite3.Connection,
    *,
    publish_id: str,
    action: str,
    status: str,
    merged_count: int = 0,
    item_ids: list[int] | None = None,
    backup_path: str = "",
    note: str = "",
) -> dict:
    now = utc_now()
    cursor = connection.execute(
        """
        INSERT INTO knowledge_publish_history
        (publish_id, action, status, merged_count, item_ids, backup_path, knowledge_path, faiss_index_path, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            publish_id,
            action,
            status,
            merged_count,
            json.dumps(item_ids or []),
            backup_path,
            str(KNOWLEDGE_DATA_PATH),
            str(get_faiss_index_path()),
            note,
            now,
        ),
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM knowledge_publish_history WHERE id = ?",
        (int(cursor.lastrowid),),
    ).fetchone()
    return row_to_publish_history(row)


def row_to_publish_history(row: sqlite3.Row) -> dict:
    item_ids = json.loads(row["item_ids"] or "[]")
    return {
        "id": int(row["id"]),
        "publish_id": row["publish_id"],
        "action": row["action"],
        "status": row["status"],
        "merged_count": int(row["merged_count"]),
        "item_ids": item_ids,
        "backup_path": row["backup_path"],
        "knowledge_path": row["knowledge_path"],
        "faiss_index_path": row["faiss_index_path"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


def publish_approved_knowledge() -> dict:
    connection = get_connection()
    publish_id = f"pub_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
    backup_path: Path | None = None
    try:
        rows = connection.execute(
            """
            SELECT * FROM knowledge_items
            WHERE status = 'approved'
            ORDER BY base_id, version
            """
        ).fetchall()
        if not rows:
            return insert_publish_history(
                connection,
                publish_id=publish_id,
                action="publish",
                status="skipped",
                note="no approved knowledge",
            )

        backup_path = create_backup(publish_id)
        KNOWLEDGE_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        needs_leading_newline = KNOWLEDGE_DATA_PATH.exists() and KNOWLEDGE_DATA_PATH.stat().st_size > 0
        if needs_leading_newline:
            tail = KNOWLEDGE_DATA_PATH.read_bytes()[-1:]
            needs_leading_newline = tail not in {b"\n", b"\r"}

        lines = [json.dumps(build_jsonl_payload(row), ensure_ascii=False) for row in rows]
        with KNOWLEDGE_DATA_PATH.open("a", encoding="utf-8") as file:
            if needs_leading_newline:
                file.write("\n")
            file.write("\n".join(lines))
            file.write("\n")

        rebuild_vector_store()

        item_ids = [int(row["id"]) for row in rows]
        now = utc_now()
        connection.executemany(
            "UPDATE knowledge_items SET status = 'published', updated_at = ? WHERE id = ?",
            [(now, item_id) for item_id in item_ids],
        )
        connection.commit()
        return insert_publish_history(
            connection,
            publish_id=publish_id,
            action="publish",
            status="succeeded",
            merged_count=len(rows),
            item_ids=item_ids,
            backup_path=str(backup_path),
        )
    except Exception as error:
        if backup_path is not None and backup_path.exists():
            shutil.copy2(backup_path, KNOWLEDGE_DATA_PATH)
            reset_runtime_vector_cache()
        insert_publish_history(
            connection,
            publish_id=publish_id,
            action="publish",
            status="failed",
            note=str(error),
        )
        raise
    finally:
        connection.close()


def rollback_latest_publish() -> dict:
    connection = get_connection()
    rollback_id = f"rollback_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
    try:
        row = connection.execute(
            """
            SELECT * FROM knowledge_publish_history
            WHERE action = 'publish' AND status = 'succeeded' AND backup_path != ''
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is None:
            raise ValueError("no succeeded publish record to rollback")

        backup_path = Path(row["backup_path"])
        if not backup_path.exists():
            raise FileNotFoundError(f"backup not found: {backup_path}")

        KNOWLEDGE_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, KNOWLEDGE_DATA_PATH)
        rebuild_vector_store()

        item_ids = json.loads(row["item_ids"] or "[]")
        if item_ids:
            now = utc_now()
            connection.executemany(
                "UPDATE knowledge_items SET status = 'approved', updated_at = ? WHERE id = ? AND status = 'published'",
                [(now, int(item_id)) for item_id in item_ids],
            )
            connection.commit()

        return insert_publish_history(
            connection,
            publish_id=rollback_id,
            action="rollback",
            status="succeeded",
            merged_count=int(row["merged_count"]),
            item_ids=[int(item_id) for item_id in item_ids],
            backup_path=str(backup_path),
            note=f"rollback {row['publish_id']}",
        )
    except Exception as error:
        insert_publish_history(
            connection,
            publish_id=rollback_id,
            action="rollback",
            status="failed",
            note=str(error),
        )
        raise
    finally:
        connection.close()


def list_publish_history(limit: int = 20) -> dict:
    connection = get_connection()
    try:
        rows = connection.execute(
            """
            SELECT * FROM knowledge_publish_history
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {"count": len(rows), "items": [row_to_publish_history(row) for row in rows]}
    finally:
        connection.close()
