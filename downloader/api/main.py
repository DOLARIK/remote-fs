import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

import config as cfg
import downloader
import ssd as ssd_module
import state
from models import DownloadStatus, NCItem, SessionInfo, SSDInfo, StartRequest
from nextcloud import NextcloudClient

NEXTCLOUD_URL = os.environ["NEXTCLOUD_URL"]
NEXTCLOUD_USER = os.environ["NEXTCLOUD_USERNAME"]
NEXTCLOUD_PASS = os.environ["NEXTCLOUD_PASSWORD"]
REDIS_URL = os.environ.get("REDIS_URL", "redis://downloader-redis:6379")

_nc = NextcloudClient(NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.init_db()
    logger.info("API ready")
    yield


app = FastAPI(title="NC Downloader", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Config ────────────────────────────────────────────────────────────────────

@app.get("/api/config")
async def get_config():
    return cfg.get_all()


@app.patch("/api/config")
async def patch_config(body: dict):
    allowed = {"max_concurrent_downloads"}
    for key, value in body.items():
        if key not in allowed:
            raise HTTPException(400, f"Unknown config key: {key}")
        if key == "max_concurrent_downloads":
            v = int(value)
            if not (1 <= v <= 16):
                raise HTTPException(400, "max_concurrent_downloads must be between 1 and 16")
            cfg.set(key, v)
    return cfg.get_all()


# ── SSDs ───────────────────────────────────────────────────────────────────────

@app.get("/api/ssds", response_model=list[SSDInfo])
async def list_ssds():
    return ssd_module.list_ssds()


# ── Nextcloud ──────────────────────────────────────────────────────────────────

@app.get("/api/nc/status")
async def nc_status():
    ok = await _nc.test_connection()
    return {"connected": ok, "url": NEXTCLOUD_URL, "username": NEXTCLOUD_USER}


@app.get("/api/nc/browse", response_model=list[NCItem])
async def nc_browse(path: str = ""):
    try:
        return await _nc.list_dir(path)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Session ────────────────────────────────────────────────────────────────────

@app.get("/api/session")
async def get_session():
    sid = downloader.current_session_id()
    if sid:
        s = await state.get_session(sid)
        if s:
            return _session_response(s)
    # fall back to latest persisted session
    s = await state.get_latest_session()
    return _session_response(s) if s else None


@app.post("/api/session/start")
async def start_session(req: StartRequest):
    # validate SSD
    ssd_info = ssd_module.get_ssd(req.ssd_path)
    if ssd_info is None:
        raise HTTPException(400, "SSD not found or not accessible")

    # wrong-SSD guard
    if ssd_info.has_session and not req.force:
        existing_session = await state.get_session(ssd_info.session_id or "")
        if existing_session:
            diff_folder = existing_session.get("nextcloud_folder") != req.nextcloud_folder
            diff_uuid = ssd_info.session_ssd_uuid and ssd_info.session_ssd_uuid != existing_session.get("ssd_uuid")
            if diff_folder or diff_uuid:
                return {
                    "warning": "wrong_ssd",
                    "message": f"This drive was previously used to download '{existing_session.get('nextcloud_folder')}'. Continuing will restart from scratch.",
                    "existing_folder": existing_session.get("nextcloud_folder"),
                }
        # same session & folder → resume
        sid = ssd_info.session_id
        if downloader.current_session_id() == sid and downloader.is_paused():
            await downloader.resume()
            return {"resumed": True, "session_id": sid}

        s = await state.get_session(sid)
        if s and s["status"] not in (DownloadStatus.COMPLETED, DownloadStatus.FAILED):
            await downloader.start(sid, _nc, req.ssd_path, req.nextcloud_folder)
            return {"session_id": sid, "resuming": True}

    # new session
    sid = str(uuid.uuid4())
    ssd_uuid = ssd_info.session_ssd_uuid or ssd_module.new_uuid()
    await state.create_session(sid, req.ssd_path, ssd_uuid, req.nextcloud_folder)
    ssd_module.write_session(req.ssd_path, sid, ssd_uuid, req.nextcloud_folder, NEXTCLOUD_URL)
    await downloader.start(sid, _nc, req.ssd_path, req.nextcloud_folder)
    return {"session_id": sid, "resuming": False}


@app.post("/api/session/pause")
async def pause_session():
    await downloader.pause("manual")
    return {"ok": True}


@app.post("/api/session/resume")
async def resume_session():
    await downloader.resume()
    return {"ok": True}


@app.post("/api/session/cancel")
async def cancel_session():
    await downloader.cancel()
    return {"ok": True}


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    sid = downloader.current_session_id()

    # send current state on connect
    if sid:
        s = await state.get_session(sid)
        if s:
            await ws.send_text(json.dumps({"type": "session_update", **_session_response(s)}))

    r = aioredis.from_url(REDIS_URL, decode_responses=True)
    pubsub = r.pubsub()

    channel = f"session:{sid}" if sid else "__placeholder__"
    await pubsub.subscribe(channel)

    # also listen for SSD events which can come before a session is started
    await pubsub.subscribe("ssd_events")

    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            try:
                await ws.send_text(msg["data"])
            except WebSocketDisconnect:
                break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"WebSocket error: {e}")
    finally:
        await pubsub.unsubscribe()
        await r.aclose()


# ── helpers ────────────────────────────────────────────────────────────────────

def _session_response(s: dict) -> dict:
    sid = s["id"]
    return {
        "id": sid,
        "status": s["status"],
        "ssd_path": s["ssd_path"],
        "nextcloud_folder": s["nextcloud_folder"],
        "total_files": s["total_files"],
        "total_bytes": s["total_bytes"],
        "downloaded_files": s["downloaded_files"],
        "downloaded_bytes": s["downloaded_bytes"],
        "failed_files": s["failed_files"],
        "skipped_files": s["skipped_files"],
        "speed_bps": downloader.current_speed_bps() if downloader.current_session_id() == sid else 0,
        "eta_seconds": downloader.eta_seconds(s["total_bytes"], s["downloaded_bytes"]),
        "current_files": downloader.current_files() if downloader.current_session_id() == sid else [],
        "created_at": s["created_at"],
        "updated_at": s["updated_at"],
    }
