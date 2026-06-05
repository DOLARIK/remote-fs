from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Callable

from loguru import logger

from .db import PipelineDB
from .graphiti_client import GraphitiClient
from .immich_client import ImmichClient
from .memory import apply_memory_update, get_relevant_slice, load_memory, save_memory
from .models import MemoryUpdate, PersonMatch, PhotoMemory
from .ollama_client import (
    event_update_pass,
    retroactive_check_pass,
    vision_pass,
)


@dataclass
class IngestionStats:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    retroactive_queued: int = 0
    progress_callbacks: list[Callable] = field(default_factory=list)

    def on_progress(self, cb: Callable) -> None:
        self.progress_callbacks.append(cb)

    def _notify(self) -> None:
        for cb in self.progress_callbacks:
            cb(self)


async def process_single_photo(
    asset: dict,
    memory: PhotoMemory,
    immich: ImmichClient,
    db: PipelineDB,
    graphiti: GraphitiClient,
) -> MemoryUpdate | None:
    asset_id: str = asset["id"]

    if await db.is_processed(asset_id):
        logger.debug(f"Skipping already-processed {asset_id}")
        return None

    logger.info(f"Processing {asset_id} ({asset.get('originalFileName', '')})")

    try:
        image_b64 = await immich.get_thumbnail_b64(asset_id)

        # Pass 1: pure vision description (no memory context)
        vision = await vision_pass(image_b64)

        # Reload from disk so human confirmations made via UI are visible to the LLM
        memory = load_memory()
        memory_slice = get_relevant_slice(memory)

        # Use Immich's ground-truth face recognition — keyed by Immich person UUID
        immich_people = await immich.get_asset_people(asset_id)
        person_matches = [
            PersonMatch(
                person_id=p["id"],
                display_name=p["name"] or None,
                confidence=1.0,
                evidence="Immich face recognition" + (f": {p['name']}" if p["name"] else " (unnamed)"),
                is_new_person=p["id"] not in memory.people,
            )
            for p in immich_people
        ]
        named = [p["name"] for p in immich_people if p["name"]]
        unnamed = sum(1 for p in immich_people if not p["name"])
        logger.info(f"Immich faces: named={named}, unnamed_clusters={unnamed}")

        # Pass 3: classify into event cluster
        event_updates = await event_update_pass(vision.description, memory_slice["events"])

        # Pass 4: retroactive only when high-confidence new identification
        retroactive = await retroactive_check_pass(
            vision.description, person_matches, memory.processed_photos
        )

        update = MemoryUpdate(
            photo_description=vision.description,
            person_matches=person_matches,
            event_updates=event_updates,
            retroactive_updates=retroactive,
        )

        apply_memory_update(memory, update, asset_id)
        save_memory(memory)

        await db.save_photo_log(
            asset_id,
            asset.get("originalFileName", ""),
            vision,
            person_matches,
            event_updates,
        )

        await immich.update_description(asset_id, vision.description)
        await graphiti.add_photo_episode(asset_id, vision.description, person_matches, event_updates)

        for retro in retroactive:
            await db.queue_retroactive(retro)
            logger.info(f"Retroactive update queued for {len(retro.photo_ids)} photos: {retro.reason}")

        await db.mark_processed(asset_id, description_written=True)
        logger.success(f"Done: {asset_id}")
        return update

    except Exception as exc:
        logger.error(f"Failed {asset_id}: {exc}")
        await db.add_to_retry(asset_id, str(exc))
        return None


async def apply_retroactive_updates(
    db: PipelineDB, immich: ImmichClient, memory: PhotoMemory
) -> int:
    pending = await db.get_pending_retroactive()
    applied = 0
    for item in pending:
        for photo_id in item["photo_ids"]:
            try:
                asset = await immich.get_asset(photo_id)
                existing = asset.get("exifInfo", {}).get("description", "") or ""
                updated = (existing + " " + item["update_text"]).strip()
                await immich.update_description(photo_id, updated)
                applied += 1
            except Exception as exc:
                logger.warning(f"Retroactive update failed for {photo_id}: {exc}")
        await db.mark_retroactive_applied(item["id"])
    if applied:
        logger.info(f"Applied {applied} retroactive description updates")
    return applied


def _stratified_random_order(assets: list[dict], neighborhood: int) -> list[dict]:
    """Return assets in stratified-random order.

    Picks random anchors spread across the full corpus and emits each anchor
    plus its nearest neighbors before moving to the next anchor. This ensures
    early ingestion sees diverse events rather than one long sequential session.
    """
    n = len(assets)
    if n == 0:
        return []
    seen: set[int] = set()
    result: list[dict] = []
    indices = list(range(n))
    random.shuffle(indices)
    for anchor in indices:
        if anchor in seen:
            continue
        # Emit anchor + neighborhood in original temporal order
        lo = max(0, anchor - neighborhood)
        hi = min(n, anchor + neighborhood + 1)
        for i in range(lo, hi):
            if i not in seen:
                result.append(assets[i])
                seen.add(i)
    return result


async def run_ingestion(
    immich: ImmichClient,
    db: PipelineDB,
    graphiti: GraphitiClient,
    *,
    concurrency: int = 1,
    stats: IngestionStats | None = None,
) -> IngestionStats:
    if stats is None:
        stats = IngestionStats()

    memory = load_memory()
    semaphore = asyncio.Semaphore(concurrency)

    _RAW_EXTENSIONS = {".ARW", ".CR2", ".CR3", ".NEF", ".RAF", ".DNG"}

    all_assets: list[dict] = []
    async for asset in immich.iter_all_assets():
        fname = asset.get("originalFileName", "")
        is_raw = any(fname.upper().endswith(ext) for ext in _RAW_EXTENSIONS)
        if asset.get("type") == "IMAGE" and not is_raw:
            all_assets.append(asset)

    stats.total = len(all_assets)
    logger.info(f"Found {stats.total} images to process")

    # Stratified random order: pick random anchors across the corpus and include
    # their neighbors so the LLM builds diverse event knowledge early rather than
    # seeing hundreds of photos from the same session first.
    _NEIGHBORHOOD = 2  # photos either side of each anchor to include together
    ingestion_order = _stratified_random_order(all_assets, _NEIGHBORHOOD)

    async def bounded_process(asset: dict) -> None:
        async with semaphore:
            result = await process_single_photo(asset, memory, immich, db, graphiti)
            if result is None:
                stats.skipped += 1
            else:
                stats.processed += 1
                stats.retroactive_queued += len(result.retroactive_updates)
            stats._notify()

    await asyncio.gather(*[bounded_process(a) for a in ingestion_order])

    # Flush retroactive queue after main pass
    await apply_retroactive_updates(db, immich, memory)

    # Retry failed assets once
    retry_ids = await db.get_retry_queue()
    if retry_ids:
        logger.info(f"Retrying {len(retry_ids)} failed assets")
        for asset_id in retry_ids:
            try:
                asset = await immich.get_asset(asset_id)
                async with semaphore:
                    result = await process_single_photo(asset, memory, immich, db, graphiti)
                    if result:
                        stats.processed += 1
                        stats.failed = max(0, stats.failed - 1)
            except Exception as exc:
                logger.error(f"Retry failed for {asset_id}: {exc}")
                stats.failed += 1

    logger.success(
        f"Ingestion complete: {stats.processed} processed, "
        f"{stats.skipped} skipped, {stats.failed} failed"
    )
    return stats
