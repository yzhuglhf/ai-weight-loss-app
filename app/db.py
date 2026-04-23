import sqlite3
import os
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
            CREATE TABLE IF NOT EXISTS weight_logs (
                id    INTEGER PRIMARY KEY AUTOINCREMENT,
                date  TEXT    NOT NULL,
                weight_lbs REAL NOT NULL,
                notes TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS calorie_logs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT    NOT NULL,
                meal_name  TEXT    NOT NULL,
                calories   INTEGER NOT NULL,
                notes      TEXT
            )
        """)
        conn.commit()
