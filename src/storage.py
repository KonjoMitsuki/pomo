from __future__ import annotations

import aiosqlite
from pathlib import Path


DEFAULT_DB_FILE = str(Path(__file__).resolve().parent.parent / "assets" / "pomo.db")


class StatsRepository:
    def __init__(self, db_file: str = DEFAULT_DB_FILE):
        self.db_file = db_file

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS stats (
                    user_id INTEGER PRIMARY KEY,
                    total_minutes INTEGER DEFAULT 0,
                    sessions INTEGER DEFAULT 0
                )
                """
            )
            await db.commit()

    async def add_work_minutes(self, user_ids: list[int], minutes: int) -> None:
        if not user_ids or minutes <= 0:
            return
        async with aiosqlite.connect(self.db_file) as db:
            await db.executemany(
                """
                INSERT INTO stats (user_id, total_minutes, sessions)
                VALUES (?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                total_minutes = total_minutes + ?
                """,
                [(uid, minutes, minutes) for uid in user_ids],
            )
            await db.commit()

    async def add_completed_session(self, user_ids: list[int]) -> None:
        if not user_ids:
            return
        async with aiosqlite.connect(self.db_file) as db:
            await db.executemany(
                """
                INSERT INTO stats (user_id, total_minutes, sessions)
                VALUES (?, 0, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                sessions = sessions + 1
                """,
                [(uid,) for uid in user_ids],
            )
            await db.commit()

    async def get_stats(self, user_id: int) -> tuple[int, int] | None:
        async with aiosqlite.connect(self.db_file) as db:
            async with db.execute(
                "SELECT total_minutes, sessions FROM stats WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
        if row:
            return row[0], row[1]
        return None

    async def reset_stats(self, user_id: int) -> tuple[int, int] | None:
        before = await self.get_stats(user_id)
        if before is None:
            return None
        async with aiosqlite.connect(self.db_file) as db:
            await db.execute("DELETE FROM stats WHERE user_id = ?", (user_id,))
            await db.commit()
        return before
