from __future__ import annotations

import aiosqlite
from pathlib import Path


DEFAULT_DB_FILE = str(Path(__file__).resolve().parent.parent / "assets" / "pomo.db")


class StatsRepository:
    def __init__(self, db_file: str = DEFAULT_DB_FILE):
        self.db_file = db_file

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("DROP TABLE IF EXISTS stats")
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id      INTEGER PRIMARY KEY,
                    display_name TEXT,
                    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            
            await db.execute("""
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
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timer_id        INTEGER NOT NULL REFERENCES timers(id),
                    guild_id        INTEGER,
                    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
                    ended_at        TEXT,
                    completed_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS session_members (
                    session_id          INTEGER NOT NULL REFERENCES sessions(id),
                    user_id             INTEGER NOT NULL REFERENCES users(user_id),
                    work_minutes        INTEGER NOT NULL DEFAULT 0,
                    completed_sessions  INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_id, user_id)
                )
            """)
            await db.commit()

    async def upsert_user(self, user_id: int, display_name: str) -> None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """
                INSERT INTO users (user_id, display_name)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                display_name = excluded.display_name
                """,
                (user_id, display_name),
            )
            await db.commit()

    async def create_timer(self, owner_id: int, name: str, guild_id: int | None, work_min: int, short_brk: int, long_brk: int, interval: int) -> int:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                """
                INSERT INTO timers (owner_id, name, guild_id, work_min, short_brk, long_brk, interval)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (owner_id, name, guild_id, work_min, short_brk, long_brk, interval),
            ) as cursor:
                timer_id = cursor.lastrowid
            await db.commit()
            return timer_id

    async def get_timer_by_name(self, owner_id: int, name: str, guild_id: int | None) -> dict | None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM timers
                WHERE owner_id = ?
                  AND (guild_id = ? OR (guild_id IS NULL AND ? IS NULL))
                  AND name = ?
                """,
                (owner_id, guild_id, guild_id, name),
            ) as cursor:
                row = await cursor.fetchone()
        return dict(row) if row else None

    async def list_timers(self, owner_id: int, guild_id: int | None = None) -> list[dict]:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            db.row_factory = aiosqlite.Row
            if guild_id is None:
                async with db.execute(
                    "SELECT * FROM timers WHERE owner_id = ?",
                    (owner_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
            else:
                async with db.execute(
                    "SELECT * FROM timers WHERE owner_id = ? AND (guild_id = ? OR (guild_id IS NULL AND ? IS NULL))",
                    (owner_id, guild_id, guild_id),
                ) as cursor:
                    rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def upsert_timer(
        self,
        owner_id: int,
        name: str,
        guild_id: int | None,
        work_min: int,
        short_brk: int,
        long_brk: int,
        interval: int,
    ) -> int:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """
                INSERT INTO timers (owner_id, name, guild_id, work_min, short_brk, long_brk, interval)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_id, guild_id, name) DO UPDATE SET
                    work_min = excluded.work_min,
                    short_brk = excluded.short_brk,
                    long_brk = excluded.long_brk,
                    interval = excluded.interval
                """,
                (owner_id, name, guild_id, work_min, short_brk, long_brk, interval),
            )
            await db.commit()
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id FROM timers WHERE owner_id = ? AND name = ? AND (guild_id = ? OR (guild_id IS NULL AND ? IS NULL))",
                (owner_id, name, guild_id, guild_id),
            ) as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def delete_timer(self, owner_id: int, name: str, guild_id: int | None) -> bool:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM timers WHERE owner_id = ? AND name = ? AND (guild_id = ? OR (guild_id IS NULL AND ? IS NULL))",
                (owner_id, name, guild_id, guild_id),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return False
            timer_id = row["id"]
            async with db.execute(
                "SELECT COUNT(*) FROM sessions WHERE timer_id = ?",
                (timer_id,),
            ) as cursor:
                c = await cursor.fetchone()
            if c and c[0] and c[0] > 0:
                return False
            await db.execute("DELETE FROM timers WHERE id = ?", (timer_id,))
            await db.commit()
            return True

    async def get_stats_per_timer(self, user_id: int) -> list[dict]:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT t.name AS timer_name, SUM(sm.work_minutes) AS total_minutes
                FROM session_members sm
                JOIN sessions s ON sm.session_id = s.id
                JOIN timers t ON s.timer_id = t.id
                WHERE sm.user_id = ?
                GROUP BY t.id, t.name
                ORDER BY total_minutes DESC
                """,
                (user_id,),
            ) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def start_session(self, timer_id: int, guild_id: int | None) -> int:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                """
                INSERT INTO sessions (timer_id, guild_id)
                VALUES (?, ?)
                """,
                (timer_id, guild_id),
            ) as cursor:
                session_id = cursor.lastrowid
            await db.commit()
            return session_id

    async def end_session(self, session_id: int, completed_count: int) -> None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """
                UPDATE sessions 
                SET ended_at = datetime('now'), completed_count = ?
                WHERE id = ?
                """,
                (completed_count, session_id),
            )
            await db.commit()

    async def add_work_minutes(self, session_id: int, user_ids: list[int], minutes: int) -> None:
        if not user_ids or minutes <= 0:
            return
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.executemany(
                """
                INSERT INTO session_members (session_id, user_id, work_minutes, completed_sessions)
                VALUES (?, ?, ?, 0)
                ON CONFLICT(session_id, user_id) DO UPDATE SET
                work_minutes = session_members.work_minutes + excluded.work_minutes
                """,
                [(session_id, uid, minutes) for uid in user_ids],
            )
            await db.commit()

    async def add_completed_session(self, session_id: int, user_ids: list[int]) -> None:
        if not user_ids:
            return
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.executemany(
                """
                INSERT INTO session_members (session_id, user_id, work_minutes, completed_sessions)
                VALUES (?, ?, 0, 1)
                ON CONFLICT(session_id, user_id) DO UPDATE SET
                completed_sessions = session_members.completed_sessions + excluded.completed_sessions
                """,
                [(session_id, uid) for uid in user_ids],
            )
            await db.commit()

    async def get_stats(self, user_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                """
                SELECT SUM(work_minutes) as total_minutes, SUM(completed_sessions) as total_sessions
                FROM session_members
                WHERE user_id = ?
                """,
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                
        if row and row[0] is not None:
            return {"total_minutes": row[0], "total_sessions": row[1]}
        return None

    async def get_stats_by_timer(self, user_id: int, timer_id: int) -> dict | None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            async with db.execute(
                """
                SELECT SUM(sm.work_minutes) as total_minutes, SUM(sm.completed_sessions) as total_sessions
                FROM session_members sm
                JOIN sessions s ON sm.session_id = s.id
                WHERE sm.user_id = ? AND s.timer_id = ?
                """,
                (user_id, timer_id),
            ) as cursor:
                row = await cursor.fetchone()
                
        if row and row[0] is not None:
            return {"total_minutes": row[0], "total_sessions": row[1]}
        return None

    async def reset_stats(self, user_id: int) -> dict | None:
        before = await self.get_stats(user_id)
        if before is None:
            return None
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute("DELETE FROM session_members WHERE user_id = ?", (user_id,))
            await db.commit()
        return before
