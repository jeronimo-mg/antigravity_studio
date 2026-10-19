import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.environ.get("ANTIGRAVITY_DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "antigravity_studio.db"))


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'agent',
                agent_name TEXT DEFAULT 'antigravity-preview-09-2026',
                model_name TEXT DEFAULT 'gemini-3.8-flash',
                environment_id TEXT DEFAULT '',
                last_interaction_id TEXT DEFAULT '',
                max_total_tokens INTEGER DEFAULT 0,
                system_instruction TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS turns (
                interaction_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                previous_interaction_id TEXT DEFAULT '',
                environment_id TEXT DEFAULT '',
                user_input_text TEXT DEFAULT '',
                output_text TEXT DEFAULT '',
                status TEXT DEFAULT 'completed',
                steps_json TEXT DEFAULT '[]',
                usage_json TEXT DEFAULT '{}',
                raw_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                PRIMARY KEY (session_id, interaction_id),
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
    conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    conn.close()


def create_session(
    title: str = "Nova Conversa Antigravity",
    mode: str = "agent",
    agent_name: str = "antigravity-preview-09-2026",
    model_name: str = "gemini-3.8-flash",
    environment_id: str = "",
    last_interaction_id: str = "",
    max_total_tokens: int = 0,
    system_instruction: str = "",
) -> Dict[str, Any]:
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    ts = now_iso()
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO sessions (
                id, title, mode, agent_name, model_name,
                environment_id, last_interaction_id, max_total_tokens,
                system_instruction, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                title,
                mode,
                agent_name,
                model_name,
                environment_id,
                last_interaction_id,
                max_total_tokens,
                system_instruction,
                ts,
                ts,
            ),
        )
    conn.close()
    return get_session(session_id)  # type: ignore


def update_session(session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    allowed = {
        "title",
        "mode",
        "agent_name",
        "model_name",
        "environment_id",
        "last_interaction_id",
        "max_total_tokens",
        "system_instruction",
    }
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return get_session(session_id)

    fields["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [session_id]

    conn = get_connection()
    with conn:
        conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
    conn.close()
    return get_session(session_id)


def list_sessions() -> List[Dict[str, Any]]:
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT s.*,
               (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turn_count
        FROM sessions s
        ORDER BY s.updated_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        conn.close()
        return None
    session_dict = dict(row)
    turns_rows = conn.execute(
        "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index ASC",
        (session_id,),
    ).fetchall()
    conn.close()

    turns = []
    for t in turns_rows:
        td = dict(t)
        td["steps"] = json.loads(td.pop("steps_json") or "[]")
        td["usage"] = json.loads(td.pop("usage_json") or "{}")
        td["raw"] = json.loads(td.pop("raw_json") or "{}")
        turns.append(td)
    session_dict["turns"] = turns
    return session_dict


def delete_session(session_id: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.close()


def upsert_turn(
    session_id: str,
    interaction_id: str,
    turn_index: int,
    previous_interaction_id: str,
    environment_id: str,
    user_input_text: str,
    output_text: str,
    status: str,
    steps: List[Dict[str, Any]],
    usage: Dict[str, Any],
    raw: Dict[str, Any],
    created_at: Optional[str] = None,
) -> None:
    ts = created_at or now_iso()
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO turns (
                interaction_id, session_id, turn_index, previous_interaction_id,
                environment_id, user_input_text, output_text, status,
                steps_json, usage_json, raw_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, interaction_id) DO UPDATE SET
                turn_index = excluded.turn_index,
                previous_interaction_id = excluded.previous_interaction_id,
                environment_id = excluded.environment_id,
                user_input_text = CASE WHEN excluded.user_input_text != '' THEN excluded.user_input_text ELSE turns.user_input_text END,
                output_text = excluded.output_text,
                status = excluded.status,
                steps_json = excluded.steps_json,
                usage_json = excluded.usage_json,
                raw_json = excluded.raw_json
            """,
            (
                interaction_id,
                session_id,
                turn_index,
                previous_interaction_id or "",
                environment_id or "",
                user_input_text or "",
                output_text or "",
                status or "completed",
                json.dumps(steps, ensure_ascii=False),
                json.dumps(usage, ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False),
                ts,
            ),
        )
    conn.close()
