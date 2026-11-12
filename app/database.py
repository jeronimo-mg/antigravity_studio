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


def _safe_json_loads(raw: Optional[str], fallback: Any) -> Any:
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
        return parsed if type(parsed) is type(fallback) else fallback
    except Exception:
        return fallback


def get_connection() -> sqlite3.Connection:
    db_dir = os.path.dirname(os.path.abspath(DB_PATH))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    title_locked_by_user INTEGER DEFAULT 0,
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
                conn.execute("ALTER TABLE sessions ADD COLUMN title_locked_by_user INTEGER DEFAULT 0")
            except Exception:
                pass
            try:
                conn.execute(
                    """
                    UPDATE sessions
                    SET model_name = 'gemini-4-argon'
                    WHERE model_name IS NULL
                       OR model_name = ''
                       OR model_name NOT IN (
                           'gemini-4-argon',
                           'gemini-4-carbon',
                           'gemini-4-hydrogen',
                           'gemini-3.8-flash',
                           'gemini-3.6-flash',
                           'gemini-3.5-flash-lite'
                       )
                    """
                )
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
    finally:
        conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_setting(key: str, default: str = "") -> str:
    conn = get_connection()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    finally:
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
    title_locked_by_user: bool = False,
) -> Dict[str, Any]:
    effective_id = (session_id or "").strip() or f"sess_{uuid.uuid4().hex[:12]}"
    ts = now_iso()
    eff_updated = (updated_at or "").strip() or ts
    sources_data = json.dumps(sources or [])
    uploaded_files_data = json.dumps(uploaded_files or [])
    eff_exec_mode = (execution_mode or "remote").strip().lower()
    if eff_exec_mode not in ("remote", "local"):
        eff_exec_mode = "remote"
    locked_int = 1 if title_locked_by_user else 0
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO sessions (
                    id, title, title_locked_by_user, mode, execution_mode, local_workdir,
                    agent_name, model_name, environment_id, last_interaction_id,
                    max_total_tokens, system_instruction, sources_json,
                    uploaded_files_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effective_id,
                    title,
                    locked_int,
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
    finally:
        conn.close()
    return get_session(effective_id)  # type: ignore


def update_session(session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    allowed = {
        "title",
        "title_locked_by_user",
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
    if "title_locked_by_user" in kwargs and kwargs["title_locked_by_user"] is not None:
        kwargs["title_locked_by_user"] = 1 if kwargs["title_locked_by_user"] else 0

    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return get_session(session_id)

    fields["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in fields.keys())
    values = list(fields.values()) + [session_id]

    conn = get_connection()
    try:
        with conn:
            conn.execute(f"UPDATE sessions SET {set_clause} WHERE id = ?", values)
    finally:
        conn.close()
    return get_session(session_id)


def list_sessions() -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT s.*,
                   (SELECT COUNT(*) FROM turns t WHERE t.session_id = s.id) AS turn_count,
                   (SELECT MAX(t.created_at) FROM turns t WHERE t.session_id = s.id) AS last_prompt_at
            FROM sessions s
            ORDER BY COALESCE((SELECT MAX(t.created_at) FROM turns t WHERE t.session_id = s.id), s.updated_at) DESC
            """
        ).fetchall()
    finally:
        conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d.setdefault("execution_mode", "remote")
        d.setdefault("local_workdir", "")
        d["title_locked_by_user"] = bool(d.get("title_locked_by_user"))
        d["last_prompt_at"] = d.get("last_prompt_at") or d.get("updated_at") or d.get("created_at") or ""
        d["sources"] = _safe_json_loads(d.pop("sources_json", None), [])
        d["uploaded_files"] = _safe_json_loads(d.pop("uploaded_files_json", None), [])
        results.append(d)
    return results


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return None
        session_dict = dict(row)
        session_dict.setdefault("execution_mode", "remote")
        session_dict.setdefault("local_workdir", "")
        session_dict["title_locked_by_user"] = bool(session_dict.get("title_locked_by_user"))
        session_dict["sources"] = _safe_json_loads(session_dict.pop("sources_json", None), [])
        session_dict["uploaded_files"] = _safe_json_loads(session_dict.pop("uploaded_files_json", None), [])
        turns_rows = conn.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY turn_index ASC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    turns = []
    for t in turns_rows:
        td = dict(t)
        td["steps"] = _safe_json_loads(td.pop("steps_json", None), [])
        td["usage"] = _safe_json_loads(td.pop("usage_json", None), {})
        td["raw"] = _safe_json_loads(td.pop("raw_json", None), {})
        turns.append(td)
    session_dict["turns"] = turns
    session_dict["last_prompt_at"] = (
        turns[-1].get("created_at") if turns else (session_dict.get("updated_at") or session_dict.get("created_at") or "")
    )
    return session_dict


def delete_session(session_id: str) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    finally:
        conn.close()


def delete_turn(session_id: str, interaction_id: str) -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                "DELETE FROM turns WHERE session_id = ? AND interaction_id = ?",
                (session_id, interaction_id),
            )
    finally:
        conn.close()


def find_turn_by_interaction_id(interaction_id: str) -> Optional[Dict[str, Any]]:
    clean_id = (interaction_id or "").strip()
    if not clean_id:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT * FROM turns
            WHERE interaction_id = ?
            ORDER BY CASE WHEN user_input_text != '' THEN 0 ELSE 1 END, created_at DESC
            LIMIT 1
            """,
            (clean_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    td = dict(row)
    td["steps"] = _safe_json_loads(td.pop("steps_json", None), [])
    td["usage"] = _safe_json_loads(td.pop("usage_json", None), {})
    td["raw"] = _safe_json_loads(td.pop("raw_json", None), {})
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
    try:
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
    finally:
        conn.close()

