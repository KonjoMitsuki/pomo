from __future__ import annotations

import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).resolve().parent / "assets" / "pomo.db"


CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    display_name TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


CREATE_TIMERS_TABLE = """
CREATE TABLE IF NOT EXISTS timers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(user_id),
    guild_id    INTEGER,
    name        TEXT    NOT NULL,
    work_min    INTEGER NOT NULL DEFAULT 25,
    short_brk   INTEGER NOT NULL DEFAULT 5,
    long_brk    INTEGER NOT NULL DEFAULT 15,
    interval    INTEGER NOT NULL DEFAULT 4,
    is_shared   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_id, guild_id, name)
)
"""


CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timer_id        INTEGER NOT NULL REFERENCES timers(id),
    guild_id        INTEGER,
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    ended_at        TEXT,
    completed_count INTEGER NOT NULL DEFAULT 0
)
"""


CREATE_SESSION_MEMBERS_TABLE = """
CREATE TABLE IF NOT EXISTS session_members (
    session_id          INTEGER NOT NULL REFERENCES sessions(id),
    user_id             INTEGER NOT NULL REFERENCES users(user_id),
    work_minutes        INTEGER NOT NULL DEFAULT 0,
    completed_sessions  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, user_id)
)
"""


CREATE_TIMERS_NEW_TABLE = """
CREATE TABLE timers_new (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id    INTEGER NOT NULL REFERENCES users(user_id),
    guild_id    INTEGER,
    name        TEXT    NOT NULL,
    work_min    INTEGER NOT NULL DEFAULT 25,
    short_brk   INTEGER NOT NULL DEFAULT 5,
    long_brk    INTEGER NOT NULL DEFAULT 15,
    interval    INTEGER NOT NULL DEFAULT 4,
    is_shared   INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(owner_id, guild_id, name)
)
"""


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def ensure_base_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_USERS_TABLE)
    conn.execute(CREATE_TIMERS_TABLE)
    conn.execute(CREATE_SESSIONS_TABLE)
    conn.execute(CREATE_SESSION_MEMBERS_TABLE)


def migrate_timers_table(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS timers_new")
    conn.execute(CREATE_TIMERS_NEW_TABLE)
    conn.execute("INSERT OR IGNORE INTO timers_new SELECT * FROM timers")
    conn.execute("DROP TABLE timers")
    conn.execute("ALTER TABLE timers_new RENAME TO timers")


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        ensure_base_schema(conn)

        if table_exists(conn, "timers"):
            migrate_timers_table(conn)

        conn.commit()

    print(f"Rebuilt database at {DB_PATH}")


if __name__ == "__main__":
    main()