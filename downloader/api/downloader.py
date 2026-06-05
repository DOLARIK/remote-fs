"""Download engine — asyncio queue with N concurrent workers, SSD monitor, Redis pub/sub."""

import asyncio
import json
import os
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

import config as cfg
import ssd as ssd_module
import state
from models import DownloadStatus
from nextcloud import NextcloudClient

# ── runtime globals ────────────────────────────────────────────────────────────
_session_id: Optional[str] = None
_pause_event = asyncio.Event()
_pause_event.set()   # set = running; cleared = paused
_cancel_event = asyncio.Event()
_paused_reason: str = ""          # "manual" | "ssd_disconnected" | ""
_current_files: set[str] = set()  # remote paths currently in-flight
_recent_bytes: deque = deque(maxlen=20)   # (timestamp, bytes) for speed calc
_speed_bps: float = 0.0
_active_count: int = 0            # tracks in-flight downloads for dynamic concurrency
_concurrency_event = asyncio.Event()
_concurrency_event.set()

REDIS_URL = os.environ.get("REDIS_URL", "redis://downloader-redis:6379")


# ── public API ─────────────────────────────────────────────────────────────────

def current_session_id() -> Optional[str]:
    return _session_id


def is_paused() -> bool:
    return not _pause_event.is_set()


def paused_reason() -> str:
    return _paused_reason


def current_speed_bps() -> float:
    return _speed_bps


def current_files() -> list[str]:
    return list(_current_files)


def active_count() -> int:
    return _active_count


def eta_seconds(total_bytes: int, downloaded_bytes: int) -> Optional[int]:
    remaining = total_bytes - downloaded_bytes
    if _speed_bps <= 0 or remaining <= 0:
        return None
    return int(remaining / _speed_bps)


async def start(session_id: str, nc: NextcloudClient, ssd_path: str, nc_folder: str) -> None:
    global _session_id, _paused_reason, _speed_bps
    _session_id = session_id
    _pause_event.set()
    _cancel_event.clear()
    _paused_reason = ""
    _current_files.clear()
    _recent_bytes.clear()
    _speed_bps = 0.0

    asyncio.create_task(_run(session_id, nc, ssd_path, nc_folder))
    logger.info(f"Download task started for session {session_id}")


async def pause(reason: str = "manual") -> None:
    global _paused_reason
    _paused_reason = reason
    _pause_event.clear()
    if _session_id:
        await state.update_session_status(_session_id, DownloadStatus.PAUSED)
        await _publish({"type": "paused", "reason": reason})
    logger.info(f"Download paused: {reason}")


async def resume() -> None:
    global _paused_reason
    _paused_reason = ""
    _pause_event.set()
    if _session_id:
        await state.update_session_status(_session_id, DownloadStatus.RUNNING)
        await _publish({"type": "resumed"})
    logger.info("Download resumed")


async def cancel() -> None:
    _cancel_event.set()
    _pause_event.set()  # unblock any waiting workers so they can exit
    if _session_id:
        await state.update_session_status(_session_id, DownloadStatus.FAILED)
        await _publish({"type": "cancelled"})
    logger.info("Download cancelled")


# ── internals ──────────────────────────────────────────────────────────────────

async def _run(session_id: str, nc: NextcloudClient, ssd_path: str, nc_folder: str) -> None:
    try:
        # ── phase 1: scan ──────────────────────────────────────────────────
        logger.info(f"Scanning {nc_folder} …")
        await state.update_session_status(session_id, DownloadStatus.SCANNING)

        all_files = []
        scanned = 0
        async for item in nc.iter_all_files(nc_folder):
            rel = item.path[len(nc_folder):].lstrip("/")
            local = str(Path(ssd_path) / "nc-download" / rel)
            all_files.append({"remote_path": item.path, "local_path": local, "size": item.size})
            scanned += 1
            if scanned % 100 == 0:
                await _publish({"type": "scan_progress", "scanned": scanned})

        await state.insert_files(session_id, all_files)
        total_bytes = sum(f["size"] for f in all_files)
        await state.update_session_totals(session_id, len(all_files), total_bytes)
        await state.update_session_status(session_id, DownloadStatus.RUNNING)
        await _publish({"type": "scan_complete", "total_files": len(all_files), "total_bytes": total_bytes})
        logger.info(f"Scan complete: {len(all_files)} files, {total_bytes // 1_000_000} MB")

        # ── phase 2: download ─────────────────────────────────────────────
        asyncio.create_task(_ssd_monitor(session_id, ssd_path))
        asyncio.create_task(_progress_broadcaster(session_id))

        queue: asyncio.Queue = asyncio.Queue()
        pending = await state.get_pending_files(session_id)
        for f in pending:
            await queue.put(f)

        # Use more workers than max_concurrent so they're always ready when a slot opens.
        n_workers = max(cfg.get("max_concurrent_downloads"), 16)
        workers = [asyncio.create_task(_worker(session_id, nc, queue)) for _ in range(n_workers)]
        await asyncio.gather(*workers)

        if not _cancel_event.is_set():
            await state.update_session_status(session_id, DownloadStatus.COMPLETED)
            await _publish({"type": "completed"})
            logger.info("Download completed")

    except Exception as e:
        logger.exception(f"Download session failed: {e}")
        await state.update_session_status(session_id, DownloadStatus.FAILED)
        await _publish({"type": "error", "message": str(e)})


async def _worker(session_id: str, nc: NextcloudClient, queue: asyncio.Queue) -> None:
    global _active_count
    while True:
        try:
            file_row = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        if _cancel_event.is_set():
            queue.task_done()
            return

        remote_path = file_row["remote_path"]
        local_path = Path(file_row["local_path"])
        size = file_row["size"]

        # skip if already on disk with matching size
        if local_path.exists() and local_path.stat().st_size == size and size > 0:
            await state.mark_file_completed(session_id, remote_path, size, skipped=True)
            queue.task_done()
            continue

        # wait for a concurrency slot — checks live config so changes apply immediately
        while _active_count >= cfg.get("max_concurrent_downloads"):
            await asyncio.sleep(0.2)
            if _cancel_event.is_set():
                queue.task_done()
                return

        _active_count += 1
        _current_files.add(remote_path)
        await state.mark_file_downloading(session_id, remote_path)

        async def on_progress(downloaded_bytes: int) -> None:
            _track_speed(downloaded_bytes)

        try:
            await nc.download_file(remote_path, local_path, _pause_event, _cancel_event, on_progress)
            await state.mark_file_completed(session_id, remote_path, size)
        except Exception as e:
            logger.warning(f"Failed: {remote_path}: {e}")
            await state.mark_file_failed(session_id, remote_path, str(e))
        finally:
            _active_count -= 1
            _current_files.discard(remote_path)
            queue.task_done()


async def _ssd_monitor(session_id: str, ssd_path: str) -> None:
    global _paused_reason
    was_present = True
    while not _cancel_event.is_set():
        await asyncio.sleep(3)
        present = ssd_module.is_connected(ssd_path)
        if not present and was_present:
            logger.warning(f"SSD disconnected: {ssd_path}")
            await pause("ssd_disconnected")
            await _publish({"type": "ssd_event", "event": "disconnected"})
        elif present and not was_present:
            logger.info(f"SSD reconnected: {ssd_path}")
            if _paused_reason == "ssd_disconnected":
                await resume()
            await _publish({"type": "ssd_event", "event": "reconnected"})
        was_present = present


async def _progress_broadcaster(session_id: str) -> None:
    """Pushes a progress snapshot to Redis every second while session is active."""
    while not _cancel_event.is_set():
        await asyncio.sleep(1)
        session = await state.get_session(session_id)
        if not session:
            continue
        dl_bytes = session["downloaded_bytes"]
        total_bytes = session["total_bytes"]
        eta = eta_seconds(total_bytes, dl_bytes) if _speed_bps > 0 else None
        await _publish({
            "type": "progress",
            "status": session["status"],
            "total_files": session["total_files"],
            "downloaded_files": session["downloaded_files"],
            "failed_files": session["failed_files"],
            "skipped_files": session["skipped_files"],
            "total_bytes": total_bytes,
            "downloaded_bytes": dl_bytes,
            "speed_bps": round(_speed_bps),
            "eta_seconds": eta,
            "current_files": [Path(p).name for p in _current_files],
            "max_concurrent": cfg.get("max_concurrent_downloads"),
            "active_count": _active_count,
        })


def _track_speed(chunk_bytes: int) -> None:
    global _speed_bps
    now = time.monotonic()
    _recent_bytes.append((now, chunk_bytes))
    # compute speed over last N samples
    if len(_recent_bytes) >= 2:
        oldest_t, _ = _recent_bytes[0]
        total = sum(b for _, b in _recent_bytes)
        elapsed = now - oldest_t
        _speed_bps = total / elapsed if elapsed > 0 else 0


async def _publish(event: dict) -> None:
    if not _session_id:
        return
    try:
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.publish(f"session:{_session_id}", json.dumps(event))
        await r.aclose()
    except Exception as e:
        logger.warning(f"Redis publish failed: {e}")
