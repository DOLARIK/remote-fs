from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from loguru import logger

from ..db import PipelineDB
from ..graphiti_client import GraphitiClient
from ..immich_client import ImmichClient
from ..ingestion import IngestionStats, run_ingestion
from ..memory import load_memory, save_memory

IMMICH_BASE_URL = os.getenv("IMMICH_BASE_URL", "http://localhost:2283")
IMMICH_API_KEY = os.getenv("IMMICH_API_KEY", "")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))

_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

_immich: ImmichClient | None = None
_db: PipelineDB | None = None
_graphiti: GraphitiClient | None = None
_ingestion_task: asyncio.Task | None = None
_ingestion_stats: IngestionStats = IngestionStats()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _immich, _db, _graphiti
    _immich = ImmichClient(IMMICH_BASE_URL, IMMICH_API_KEY)
    _db = PipelineDB()
    await _db.init()
    _graphiti = GraphitiClient(
        falkordb_host=FALKORDB_HOST,
        falkordb_port=FALKORDB_PORT,
        ollama_base=OLLAMA_BASE_URL,
    )
    await _graphiti.init()
    logger.info("Review UI ready")
    yield
    if _graphiti:
        await _graphiti.close()


app = FastAPI(title="Photo Pipeline Review UI", lifespan=lifespan)


def _thumbnail_url(asset_id: str, size: str = "thumbnail") -> str:
    return f"/thumb/{asset_id}?size={size}"


@app.get("/thumb/{asset_id}")
async def proxy_thumbnail(asset_id: str, size: str = "thumbnail"):
    async with __import__("httpx").AsyncClient() as c:
        r = await c.get(
            f"{IMMICH_BASE_URL}/api/assets/{asset_id}/thumbnail",
            headers={"x-api-key": IMMICH_API_KEY},
            params={"size": size},
            timeout=15.0,
        )
    return Response(content=r.content, media_type=r.headers.get("content-type", "image/jpeg"))


# ── Dashboard ────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = await _db.get_stats() if _db else {}
    memory = load_memory()
    running = _ingestion_task is not None and not _ingestion_task.done()
    return templates.TemplateResponse(request, "base.html", {
        "stats": stats,
        "memory": memory,
        "ingestion_running": running,
        "ingestion_progress": {
            "processed": _ingestion_stats.processed,
            "total": _ingestion_stats.total,
        },
    })


# ── Ingestion control ────────────────────────────────────────────────────────


@app.post("/ingestion/start", response_class=HTMLResponse)
async def start_ingestion(request: Request, background_tasks: BackgroundTasks):
    global _ingestion_task, _ingestion_stats

    if _ingestion_task and not _ingestion_task.done():
        return HTMLResponse("<p>Ingestion already running.</p>")

    _ingestion_stats = IngestionStats()

    async def _run():
        try:
            await run_ingestion(
                immich=_immich,
                db=_db,
                graphiti=_graphiti,
                concurrency=1,
                stats=_ingestion_stats,
            )
        except Exception as exc:
            logger.error(f"Ingestion task failed: {exc}")
            raise

    _ingestion_task = asyncio.create_task(_run())
    logger.info("Ingestion task started from UI")
    return HTMLResponse('<p class="text-green-600">Ingestion started.</p>')


@app.get("/ingestion/status", response_class=HTMLResponse)
async def ingestion_status(request: Request):
    running = _ingestion_task is not None and not _ingestion_task.done()
    s = _ingestion_stats
    pct = int(s.processed / s.total * 100) if s.total else 0
    return templates.TemplateResponse(request, "partials/status.html", {
        "running": running,
        "processed": s.processed,
        "total": s.total,
        "failed": s.failed,
        "pct": pct,
    })


# ── Clusters (event review) ──────────────────────────────────────────────────


@app.get("/clusters", response_class=HTMLResponse)
async def clusters_page(request: Request):
    memory = load_memory()
    events_with_thumbs = []
    for ev in memory.events.values():
        sample_ids = ev.photo_ids[:8]
        events_with_thumbs.append({
            "event": ev,
            "thumb_urls": [_thumbnail_url(aid) for aid in sample_ids],
        })
    return templates.TemplateResponse(request, "clusters.html", {
        "events": events_with_thumbs,
    })


@app.post("/clusters/{event_id}/confirm", response_class=HTMLResponse)
async def confirm_event(request: Request, event_id: str, label: str = Form(...)):
    memory = load_memory()
    if event_id not in memory.events:
        return HTMLResponse(f'<p class="text-red-500">Event {event_id} not found.</p>')

    ev = memory.events[event_id]

    # Find an existing confirmed cluster with the same label to merge into
    target_id = next(
        (eid for eid, e in memory.events.items()
         if eid != event_id and e.confirmed_by_human and e.label == label),
        None,
    )

    if target_id:
        # Merge this cluster into the existing one and delete it
        target = memory.events[target_id]
        for pid in ev.photo_ids:
            if pid not in target.photo_ids:
                target.photo_ids.append(pid)
        for kp in ev.key_people:
            if kp not in target.key_people:
                target.key_people.append(kp)
        target.evidence.extend(ev.evidence)
        del memory.events[event_id]
        save_memory(memory)
        logger.info(f"Merged {event_id} into {target_id} (label={label})")
        n = len(memory.events[target_id].photo_ids)
        return HTMLResponse(
            f'<div id="card-{event_id}" class="text-sm text-gray-400 italic px-2 py-1">'
            f'Merged into {label} ({n} photos total). <a href="/clusters" class="text-indigo-500 underline">Refresh</a> to see updated count.'
            f'</div>'
        )
    else:
        ev.label = label
        ev.confirmed_by_human = True
        save_memory(memory)
        logger.info(f"Event {event_id} confirmed as '{label}'")
        sample_ids = ev.photo_ids[:8]
        thumb_urls = [_thumbnail_url(aid) for aid in sample_ids]
        return templates.TemplateResponse(request, "partials/cluster_card.html", {
            "item": {"event": ev, "thumb_urls": thumb_urls},
        })


# ── People (identity review) ─────────────────────────────────────────────────


@app.get("/people", response_class=HTMLResponse)
async def people_page(request: Request):
    memory = load_memory()
    people_with_thumbs = []
    for person in memory.people.values():
        sample_ids = person.appears_in[:8]
        people_with_thumbs.append({
            "person": person,
            "thumb_urls": [_thumbnail_url(aid) for aid in sample_ids],
        })
    try:
        face_clusters = await _immich.list_face_clusters() if _immich else []
    except Exception as exc:
        logger.warning(f"Could not fetch face clusters: {exc}")
        face_clusters = []
    return templates.TemplateResponse(request, "people.html", {
        "people": people_with_thumbs,
        "face_clusters": face_clusters[:20],
    })


@app.post("/people/sync-immich", response_class=HTMLResponse)
async def sync_people_from_immich():
    named = await _immich.get_named_people()   # {immich_uuid: name}
    memory = load_memory()
    updated = 0
    for person_id, person in memory.people.items():
        if person_id in named and not person.display_name:
            person.display_name = named[person_id]
            updated += 1
        elif person_id in named and person.display_name != named[person_id]:
            person.display_name = named[person_id]
            updated += 1
    if updated:
        save_memory(memory)
        logger.info(f"Synced {updated} names from Immich")
    return HTMLResponse(f'<span class="text-green-600">Synced {updated} name(s) from Immich.</span>')


@app.post("/people/{person_id}/name", response_class=HTMLResponse)
async def name_person(person_id: str, display_name: str = Form(...), role: str = Form("")):
    memory = load_memory()
    if person_id in memory.people:
        memory.people[person_id].display_name = display_name
        if role:
            memory.people[person_id].role = role
            memory.people[person_id].role_confidence = 1.0
        memory.people[person_id].confirmed_by_human = True
        save_memory(memory)
        logger.info(f"Person {person_id} named '{display_name}' (role={role})")
    return HTMLResponse(f'<span class="text-green-600">Saved: {display_name}</span>')


# ── Insights (free-form human input) ─────────────────────────────────────────


@app.get("/insights", response_class=HTMLResponse)
async def insights_page(request: Request):
    memory = load_memory()
    return templates.TemplateResponse(request, "insights.html", {
        "open_questions": memory.open_questions,
    })


@app.post("/insights/submit", response_class=HTMLResponse)
async def submit_insight(request: Request, text: str = Form(...)):
    if not _graphiti:
        return HTMLResponse("<p>Graph not initialized.</p>")
    from graphiti_core.nodes import EpisodeType

    await _graphiti._graphiti.add_episode(
        name="human_insight",
        episode_body=text,
        source=EpisodeType.text,
        source_description="Human review annotation",
    )
    logger.info(f"Human insight added to graph: {text[:80]}...")
    return HTMLResponse('<p class="text-green-600">Insight saved to knowledge graph.</p>')


# ── Photo log (per-photo LLM output browser) ─────────────────────────────────


@app.get("/photos", response_class=HTMLResponse)
async def photos_page(request: Request, page: int = 1):
    limit = 20
    offset = (page - 1) * limit
    logs = await _db.get_photo_logs(limit=limit, offset=offset)
    total = await _db.count_photo_logs()
    for log in logs:
        log["thumb_url"] = _thumbnail_url(log["asset_id"])
    return templates.TemplateResponse(request, "photos.html", {
        "logs": logs,
        "page": page,
        "total": total,
        "limit": limit,
        "total_pages": max(1, -(-total // limit)),
    })


# ── Knowledge graph browser ──────────────────────────────────────────────────


@app.get("/graph", response_class=HTMLResponse)
async def graph_page(request: Request):
    stats = {}
    if _graphiti:
        try:
            stats = await _graphiti.get_graph_stats()
        except Exception as exc:
            logger.warning(f"Graph stats failed: {exc}")
    return templates.TemplateResponse(request, "graph.html", {"stats": stats})


# ── Graph search ─────────────────────────────────────────────────────────────


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = ""):
    results: list[dict] = []
    if q and _graphiti:
        results = await _graphiti.search(q, limit=10)
    return templates.TemplateResponse(request, "search.html", {
        "query": q,
        "results": results,
    })
