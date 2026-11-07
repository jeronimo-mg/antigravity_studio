import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

if getattr(sys, "frozen", False):
    _EXEC_DIR = os.path.dirname(os.path.abspath(sys.executable))
    if _EXEC_DIR.startswith(("/opt", "/usr", "/bin", "/sbin")) or not os.access(_EXEC_DIR, os.W_OK):
        _xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        _DEFAULT_BASE_DIR = os.path.join(_xdg_data, "antigravity-studio")
        os.makedirs(_DEFAULT_BASE_DIR, exist_ok=True)
    else:
        _DEFAULT_BASE_DIR = _EXEC_DIR
else:
    _DEFAULT_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.environ.get("ANTIGRAVITY_DB_PATH", os.path.join(_DEFAULT_BASE_DIR, "antigravity_studio.db"))


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
                execution_mode TEXT NOT NULL DEFAULT 'remote',
                local_workdir TEXT DEFAULT '',
                agent_name TEXT DEFAULT 'antigravity-preview-09-2026',
                model_name TEXT DEFAULT 'gemini-4-argon',
                environment_id TEXT DEFAULT '',
                last_interaction_id TEXT DEFAULT '',
                max_total_tokens INTEGER DEFAULT 0,
                system_instruction TEXT DEFAULT '',
                sources_json TEXT DEFAULT '[]',
                uploaded_files_json TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN sources_json TEXT DEFAULT '[]'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN uploaded_files_json TEXT DEFAULT '[]'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'remote'")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN local_workdir TEXT DEFAULT ''")
        except Exception:
            pass
        try:
            conn.execute("UPDATE sessions SET model_name = 'gemini-4-argon' WHERE model_name != 'gemini-4-argon' OR model_name IS NULL OR model_name = ''")
        except Exception:
            pass
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
    model_name: str = "gemini-4-argon",
    environment_id: str = "",
    last_interaction_id: str = "",
    max_total_tokens: int = 0,
    system_instruction: str = "",
    sources: Optional[List[Dict[str, Any]]] = None,
    uploaded_files: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    updated_at: Optional[str] = None,
    execution_mode: str = "remote",
    local_workdir: str = "",
) -> Dict[str, Any]:
    effective_id = (session_id or "").strip() or f"sess_{uuid.uuid4().hex[:12]}"
    ts = now_iso()
    eff_updated = (updated_at or "").strip() or ts
    sources_data = json.dumps(sources or [])
    uploaded_files_data = json.dumps(uploaded_files or [])
    eff_exec_mode = (execution_mode or "remote").strip().lower()
    if eff_exec_mode not in ("remote", "local"):
        eff_exec_mode = "remote"
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO sessions (
                id, title, mode, execution_mode, local_workdir,
                agent_name, model_name, environment_id, last_interaction_id,
                max_total_tokens, system_instruction, sources_json,
                uploaded_files_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                effective_id,
                title,
                mode,
                eff_exec_mode,
                local_workdir or "",
                agent_name,
                model_name,
                environment_id,
                last_interaction_id,
                max_total_tokens,
                system_instruction,
                sources_data,
                uploaded_files_data,
                ts,
                eff_updated,
            ),
        )
    conn.close()
    return get_session(effective_id)  # type: ignore


def update_session(session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    allowed = {
        "title",
        "mode",
        "execution_mode",
        "local_workdir",
        "agent_name",
        "model_name",
        "environment_id",
        "last_interaction_id",
        "max_total_tokens",
        "system_instruction",
        "sources",
        "sources_json",
        "uploaded_files",
        "uploaded_files_json",
    }
    if "sources" in kwargs and kwargs["sources"] is not None:
        kwargs["sources_json"] = json.dumps(kwargs.pop("sources"))
    if "uploaded_files" in kwargs and kwargs["uploaded_files"] is not None:
        kwargs["uploaded_files_json"] = json.dumps(kwargs.pop("uploaded_files"))

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
    results = []
    for r in rows:
        d = dict(r)
        d.setdefault("execution_mode", "remote")
        d.setdefault("local_workdir", "")
        d["sources"] = json.loads(d.pop("sources_json", None) or "[]")
        d["uploaded_files"] = json.loads(d.pop("uploaded_files_json", None) or "[]")
        results.append(d)
    return results


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        conn.close()
        return None
    session_dict = dict(row)
    session_dict.setdefault("execution_mode", "remote")
    session_dict.setdefault("local_workdir", "")
    session_dict["sources"] = json.loads(session_dict.pop("sources_json", None) or "[]")
    session_dict["uploaded_files"] = json.loads(session_dict.pop("uploaded_files_json", None) or "[]")
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


def delete_turn(session_id: str, interaction_id: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            "DELETE FROM turns WHERE session_id = ? AND interaction_id = ?",
            (session_id, interaction_id),
        )
    conn.close()


def find_turn_by_interaction_id(interaction_id: str) -> Optional[Dict[str, Any]]:
    clean_id = (interaction_id or "").strip()
    if not clean_id:
        return None
    conn = get_connection()
    row = conn.execute(
        """
        SELECT * FROM turns
        WHERE interaction_id = ?
        ORDER BY CASE WHEN user_input_text != '' THEN 0 ELSE 1 END, created_at DESC
        LIMIT 1
        """,
        (clean_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    td = dict(row)
    td["steps"] = json.loads(td.pop("steps_json") or "[]")
    td["usage"] = json.loads(td.pop("usage_json") or "{}")
    td["raw"] = json.loads(td.pop("raw_json") or "{}")
    return td


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
    effective_user_text = (user_input_text or "").strip()
    if not effective_user_text:
        known = find_turn_by_interaction_id(interaction_id)
        if known and known.get("user_input_text"):
            effective_user_text = known["user_input_text"]

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
                effective_user_text,
                output_text or "",
                status or "completed",
                json.dumps(steps, ensure_ascii=False),
                json.dumps(usage, ensure_ascii=False),
                json.dumps(raw, ensure_ascii=False),
                ts,
            ),
        )
    conn.close()

