import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("ACCESS_DB_PATH", "data/access.db")


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    columns = {
        row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT,
                last_seen TEXT,
                is_channel_member INTEGER DEFAULT 0,
                is_paid INTEGER DEFAULT 0,
                paid_until TEXT,
                free_used INTEGER DEFAULT 0
            )
            """
        )
        _ensure_column(conn, "users", "free_used", "INTEGER DEFAULT 0")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS responses (
                response_id TEXT PRIMARY KEY,
                user_id INTEGER,
                created_at TEXT,
                content TEXT
            )
            """
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def upsert_user(user_id: int, username: str | None, first_name: str | None, last_name: str | None) -> None:
    ts = now_iso()
    with _connect() as conn:
        row = conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            conn.execute(
                """
                UPDATE users
                SET username = ?, first_name = ?, last_name = ?, last_seen = ?
                WHERE user_id = ?
                """,
                (username, first_name, last_name, ts, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, username, first_name, last_name, ts, ts),
            )


def set_channel_member(user_id: int, is_member: bool) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET is_channel_member = ?, last_seen = ? WHERE user_id = ?",
            (1 if is_member else 0, now_iso(), user_id),
        )


def grant_paid(user_id: int, days: int) -> str:
    paid_until = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    with _connect() as conn:
        conn.execute(
            """
            UPDATE users
            SET is_paid = 1, paid_until = ?, last_seen = ?
            WHERE user_id = ?
            """,
            (paid_until, now_iso(), user_id),
        )
    return paid_until


def revoke_paid(user_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET is_paid = 0, paid_until = NULL, last_seen = ? WHERE user_id = ?",
            (now_iso(), user_id),
        )


def is_paid_active(user_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT is_paid, paid_until FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row or int(row["is_paid"] or 0) != 1:
        return False
    paid_until = row["paid_until"]
    if not paid_until:
        return True
    try:
        return datetime.fromisoformat(paid_until) > datetime.now(timezone.utc)
    except ValueError:
        return False


def get_free_used(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT free_used FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return 0
    return int(row["free_used"] or 0)


def increment_free_used(user_id: int) -> int:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET free_used = COALESCE(free_used, 0) + 1, last_seen = ? WHERE user_id = ?",
            (now_iso(), user_id),
        )
        row = conn.execute(
            "SELECT free_used FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["free_used"] or 0) if row else 0


def get_user(user_id: int):
    with _connect() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def stats() -> dict:
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        paid = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_paid = 1").fetchone()["c"]
        members = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_channel_member = 1").fetchone()["c"]
    return {"total": total, "paid": paid, "members": members}


def recent_users(limit: int = 20):
    with _connect() as conn:
        return conn.execute(
            "SELECT user_id, username, first_name, last_name, last_seen FROM users ORDER BY last_seen DESC LIMIT ?",
            (limit,),
        ).fetchall()


def save_response(user_id: int | None, content: str) -> str:
    response_id = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO responses (response_id, user_id, created_at, content)
            VALUES (?, ?, ?, ?)
            """,
            (response_id, user_id, now_iso(), content),
        )
    return response_id


def get_response(response_id: str):
    with _connect() as conn:
        return conn.execute(
            "SELECT response_id, user_id, created_at, content FROM responses WHERE response_id = ?",
            (response_id,),
        ).fetchone()
