from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import aiosqlite
from loguru import logger

from .models import RetroactiveUpdate

DB_PATH = Path("data/pipeline_state.db")


class PipelineDB:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS processed_photos (
                    asset_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL,
                    description_written INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS retry_queue (
                    asset_id TEXT PRIMARY KEY,
                    error TEXT,
                    retry_count INTEGER DEFAULT 0,
                    last_attempt TEXT
                );

                CREATE TABLE IF NOT EXISTS retroactive_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    photo_ids TEXT NOT NULL,
                    update_text TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    applied INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS photo_log (
                    asset_id TEXT PRIMARY KEY,
                    filename TEXT,
                    description TEXT,
                    setting TEXT,
                    attire_notes TEXT,
                    activity TEXT,
                    cultural_context TEXT,
                    person_matches TEXT,
                    event_updates TEXT,
                    processed_at TEXT NOT NULL
                );
            """)
            await db.commit()
        logger.info(f"Pipeline DB ready at {self.db_path}")

    async def is_processed(self, asset_id: str) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM processed_photos WHERE asset_id = ?", (asset_id,)
            ) as cursor:
                return await cursor.fetchone() is not None

    async def mark_processed(self, asset_id: str, description_written: bool = True) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO processed_photos (asset_id, processed_at, description_written) VALUES (?, ?, ?)",
                (asset_id, datetime.utcnow().isoformat(), int(description_written)),
            )
            await db.commit()

    async def add_to_retry(self, asset_id: str, error: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO retry_queue (asset_id, error, retry_count, last_attempt)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(asset_id) DO UPDATE SET
                    error = excluded.error,
                    retry_count = retry_count + 1,
                    last_attempt = excluded.last_attempt
                """,
                (asset_id, error, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def get_retry_queue(self, max_retries: int = 3) -> list[str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT asset_id FROM retry_queue WHERE retry_count < ?", (max_retries,)
            ) as cursor:
                return [row[0] for row in await cursor.fetchall()]

    async def queue_retroactive(self, update: RetroactiveUpdate) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO retroactive_queue (photo_ids, update_text, reason, created_at) VALUES (?, ?, ?, ?)",
                (
                    json.dumps(update.photo_ids),
                    update.add_to_description,
                    update.reason,
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def get_pending_retroactive(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT id, photo_ids, update_text, reason FROM retroactive_queue WHERE applied = 0"
            ) as cursor:
                return [
                    {
                        "id": row[0],
                        "photo_ids": json.loads(row[1]),
                        "update_text": row[2],
                        "reason": row[3],
                    }
                    for row in await cursor.fetchall()
                ]

    async def mark_retroactive_applied(self, retro_id: int) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE retroactive_queue SET applied = 1 WHERE id = ?", (retro_id,)
            )
            await db.commit()

    async def save_photo_log(
        self,
        asset_id: str,
        filename: str,
        vision: "VisionDescription",
        person_matches: list,
        event_updates: list,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO photo_log
                (asset_id, filename, description, setting, attire_notes, activity,
                 cultural_context, person_matches, event_updates, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    filename,
                    vision.description,
                    vision.setting,
                    vision.attire_notes,
                    vision.activity,
                    vision.cultural_context,
                    json.dumps([p.model_dump() for p in person_matches]),
                    json.dumps([e.model_dump() for e in event_updates]),
                    datetime.utcnow().isoformat(),
                ),
            )
            await db.commit()

    async def get_photo_logs(self, limit: int = 50, offset: int = 0) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT asset_id, filename, description, setting, attire_notes,
                       activity, cultural_context, person_matches, event_updates, processed_at
                FROM photo_log
                ORDER BY processed_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        return [
            {
                "asset_id": r["asset_id"],
                "filename": r["filename"],
                "description": r["description"],
                "setting": r["setting"],
                "attire_notes": r["attire_notes"],
                "activity": r["activity"],
                "cultural_context": r["cultural_context"],
                "person_matches": json.loads(r["person_matches"] or "[]"),
                "event_updates": json.loads(r["event_updates"] or "[]"),
                "processed_at": r["processed_at"],
            }
            for r in rows
        ]

    async def count_photo_logs(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM photo_log") as c:
                return (await c.fetchone())[0]

    async def get_stats(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM processed_photos") as c:
                processed = (await c.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM retry_queue WHERE retry_count < 3"
            ) as c:
                retries = (await c.fetchone())[0]
            async with db.execute(
                "SELECT COUNT(*) FROM retroactive_queue WHERE applied = 0"
            ) as c:
                retroactive = (await c.fetchone())[0]
        return {
            "processed": processed,
            "pending_retries": retries,
            "pending_retroactive": retroactive,
        }
