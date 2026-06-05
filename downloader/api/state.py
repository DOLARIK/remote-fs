import aiosqlite
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from loguru import logger

from models import DownloadStatus, FileStatus

DB_PATH = Path("/app/data/downloader.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                ssd_path TEXT NOT NULL,
                ssd_uuid TEXT NOT NULL,
                nextcloud_folder TEXT NOT NULL,
                total_files INTEGER DEFAULT 0,
                total_bytes INTEGER DEFAULT 0,
                downloaded_files INTEGER DEFAULT 0,
                downloaded_bytes INTEGER DEFAULT 0,
                failed_files INTEGER DEFAULT 0,
                skipped_files INTEGER DEFAULT 0,
                status TEXT DEFAULT 'idle',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                remote_path TEXT NOT NULL,
                local_path TEXT NOT NULL,
                size INTEGER DEFAULT 0,
                status TEXT DEFAULT 'pending',
                downloaded_bytes INTEGER DEFAULT 0,
                error_msg TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, remote_path)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_files_session_status ON files(session_id, status)"
        )
        await db.commit()
    logger.info("Database ready")


async def create_session(session_id: str, ssd_path: str, ssd_uuid: str, nextcloud_folder: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        now = _now()
        await db.execute(
            "INSERT INTO sessions (id, ssd_path, ssd_uuid, nextcloud_folder, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (session_id, ssd_path, ssd_uuid, nextcloud_folder, DownloadStatus.SCANNING, now, now),
        )
        await db.commit()


async def get_session(session_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_latest_session() -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sessions ORDER BY created_at DESC LIMIT 1") as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_session_status(session_id: str, status: DownloadStatus) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (status, _now(), session_id),
        )
        await db.commit()


async def update_session_totals(session_id: str, total_files: int, total_bytes: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE sessions SET total_files = ?, total_bytes = ?, updated_at = ? WHERE id = ?",
            (total_files, total_bytes, _now(), session_id),
        )
        await db.commit()


async def insert_files(session_id: str, files: list[dict]) -> None:
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO files (session_id, remote_path, local_path, size, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            [(session_id, f["remote_path"], f["local_path"], f["size"], FileStatus.PENDING, now, now) for f in files],
        )
        await db.commit()


async def get_pending_files(session_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM files WHERE session_id = ? AND status = ?",
            (session_id, FileStatus.PENDING),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def mark_file_downloading(session_id: str, remote_path: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE session_id = ? AND remote_path = ?",
            (FileStatus.DOWNLOADING, _now(), session_id, remote_path),
        )
        await db.commit()


async def mark_file_completed(session_id: str, remote_path: str, size: int, skipped: bool = False) -> None:
    status = FileStatus.SKIPPED if skipped else FileStatus.COMPLETED
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE files SET status = ?, downloaded_bytes = ?, updated_at = ? WHERE session_id = ? AND remote_path = ?",
            (status, size, _now(), session_id, remote_path),
        )
        col = "skipped_files = skipped_files + 1, " if skipped else ""
        await db.execute(
            f"UPDATE sessions SET {col}downloaded_files = downloaded_files + 1, downloaded_bytes = downloaded_bytes + ?, updated_at = ? WHERE id = ?",
            (size, _now(), session_id),
        )
        await db.commit()


async def mark_file_failed(session_id: str, remote_path: str, error: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE files SET status = ?, error_msg = ?, updated_at = ? WHERE session_id = ? AND remote_path = ?",
            (FileStatus.FAILED, error, _now(), session_id, remote_path),
        )
        await db.execute(
            "UPDATE sessions SET failed_files = failed_files + 1, updated_at = ? WHERE id = ?",
            (_now(), session_id),
        )
        await db.commit()
