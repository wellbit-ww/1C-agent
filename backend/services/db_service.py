"""SQLite-персистентность: реестр файлов и история чата.

Переживает рестарт backend: in-memory реестр storage_service при промахе
смотрит сюда, история чата читается для контекста follow-up вопросов.
"""
import json
import logging
import sqlite3
import time

from config import DB_PATH

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    original_name TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    charts_json TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_file ON chat_messages(file_id, id);
CREATE TABLE IF NOT EXISTS dashboard_specs (
    file_id TEXT PRIMARY KEY,
    spec_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS file_contexts (
    file_id TEXT PRIMARY KEY,
    data_hash TEXT NOT NULL,
    context_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    try:
        with _connect() as conn:
            conn.executescript(_SCHEMA)
    except sqlite3.Error as exc:
        logger.error("Не удалось инициализировать БД %s: %s", DB_PATH, exc)
        raise


init_db()


# --- Файлы -----------------------------------------------------------------

def save_file_record(file_id: str, original_name: str, path: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO files (file_id, original_name, path, created_at)"
            " VALUES (?, ?, ?, ?)",
            (file_id, original_name, path, time.time()),
        )


def get_file_record(file_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT file_id, original_name, path, created_at FROM files"
            " WHERE file_id = ?",
            (file_id,),
        ).fetchone()
    return dict(row) if row else None


# --- История чата -----------------------------------------------------------

def add_chat_message(
    file_id: str,
    role: str,
    content: str,
    charts: list[dict] | None = None,
) -> None:
    charts_json = json.dumps(charts, ensure_ascii=False) if charts else None
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages (file_id, role, content, charts_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (file_id, role, content, charts_json, time.time()),
        )


def get_chat_history(file_id: str, limit: int = 50) -> list[dict]:
    """Хронологический порядок, не более limit последних сообщений."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT role, content, charts_json FROM chat_messages"
            " WHERE file_id = ? ORDER BY id DESC LIMIT ?",
            (file_id, limit),
        ).fetchall()

    messages = []
    for row in reversed(rows):
        messages.append(
            {
                "role": row["role"],
                "content": row["content"],
                "charts": json.loads(row["charts_json"]) if row["charts_json"] else [],
            }
        )
    return messages


# --- Спеки дашбордов --------------------------------------------------------

def save_dashboard_spec(file_id: str, spec_json: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO dashboard_specs (file_id, spec_json, updated_at)"
            " VALUES (?, ?, ?)",
            (file_id, spec_json, time.time()),
        )


def get_dashboard_spec(file_id: str) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT spec_json FROM dashboard_specs WHERE file_id = ?",
            (file_id,),
        ).fetchone()
    return row["spec_json"] if row else None


def delete_dashboard_spec(file_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM dashboard_specs WHERE file_id = ?", (file_id,))


# --- Карточка понимания файла ----------------------------------------------

def save_file_context(file_id: str, data_hash: str, context_json: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO file_contexts"
            " (file_id, data_hash, context_json, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (file_id, data_hash, context_json, time.time()),
        )


def get_file_context(file_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT file_id, data_hash, context_json, updated_at"
            " FROM file_contexts WHERE file_id = ?",
            (file_id,),
        ).fetchone()
    return dict(row) if row else None
