import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Use /tmp on read-only cloud filesystems, project root locally
_base = Path(os.getenv("DB_DIR", str(Path(__file__).parent.parent)))
DB_PATH = _base / "data.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (date('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weight_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                date       TEXT    NOT NULL,
                weight_lbs REAL    NOT NULL,
                notes      TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calorie_logs (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   INTEGER NOT NULL REFERENCES users(id),
                date      TEXT    NOT NULL,
                meal_name TEXT    NOT NULL,
                calories  INTEGER NOT NULL,
                notes     TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id),
                expires_at TEXT    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_usage (
                user_id         INTEGER NOT NULL REFERENCES users(id),
                date            TEXT    NOT NULL,
                request_count   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)
        # Purge expired sessions on every startup
        conn.execute(
            "DELETE FROM sessions WHERE expires_at <= ?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        # Migrate existing tables that predate user accounts
        for sql in [
            "ALTER TABLE weight_logs ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE calorie_logs ADD COLUMN user_id INTEGER NOT NULL DEFAULT 0",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass  # column already exists
        conn.commit()


# ── Password helpers ──────────────────────────────────────────────────────────

def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return f"{salt}:{key.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, key_hex = stored.split(":", 1)
    except ValueError:
        return False
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
    return hmac.compare_digest(key.hex(), key_hex)


# ── User helpers ──────────────────────────────────────────────────────────────

def get_user_by_email(email: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()


def create_user(email: str, password: str) -> int:
    pw_hash = _hash_password(password)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (?, ?)",
            (email.lower().strip(), pw_hash),
        )
        conn.commit()
        return cur.lastrowid


def authenticate(email: str, password: str):
    """Return the user row if credentials are valid, else None."""
    user = get_user_by_email(email)
    if user and _verify_password(password, user["password_hash"]):
        return user
    return None


# ── Session helpers ───────────────────────────────────────────────────────────

def create_session(user_id: int, days: int = 30) -> str:
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires_at),
        )
        conn.commit()
    return token


def get_session_user(token: str):
    """Return the user row if the token exists and has not expired."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT u.* FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.expires_at > ?""",
            (token, datetime.now(timezone.utc).isoformat()),
        ).fetchone()


def delete_session(token: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


# ── Rate limiting ─────────────────────────────────────────────────────────────

def get_usage_today(user_id: int) -> int:
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT request_count FROM ai_usage WHERE user_id = ? AND date = ?",
            (user_id, today),
        ).fetchone()
    return row["request_count"] if row else 0


def increment_usage(user_id: int) -> int:
    """Increment today's AI request count and return the new total."""
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_usage (user_id, date, request_count) VALUES (?, ?, 1)
               ON CONFLICT (user_id, date) DO UPDATE SET request_count = request_count + 1""",
            (user_id, today),
        )
        conn.commit()
        return conn.execute(
            "SELECT request_count FROM ai_usage WHERE user_id = ? AND date = ?",
            (user_id, today),
        ).fetchone()["request_count"]
