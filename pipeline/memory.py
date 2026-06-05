from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from .models import Event, MemoryUpdate, Person, PersonMatch, PhotoMemory

MEMORY_FILE = Path("data/photo_memory.json")

# E4B tends to overconfident — scale back before applying updates
_CONFIDENCE_CORRECTION = 0.8
_MIN_CONFIDENCE = 0.5


def load_memory(path: Path = MEMORY_FILE) -> PhotoMemory:
    if path.exists():
        logger.info(f"Loading memory from {path}")
        return PhotoMemory.model_validate_json(path.read_text())
    logger.info("No existing memory — starting fresh")
    return PhotoMemory(last_updated=datetime.utcnow())


def save_memory(memory: PhotoMemory, path: Path = MEMORY_FILE) -> None:
    memory.last_updated = datetime.utcnow()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(memory.model_dump_json(indent=2))
    logger.debug(f"Memory saved: {len(memory.people)} people, {len(memory.events)} events")


def get_relevant_slice(memory: PhotoMemory, max_people: int = 20, max_events: int = 10) -> dict:
    """Compact memory slice passed as LLM context — never send the full memory."""
    people_slice = {
        pid: {
            "display_name": p.display_name,
            "description": p.description,
            "role": p.role,
            "photo_count": len(p.appears_in),
            "outfits": p.outfits[:3],
        }
        for pid, p in list(memory.people.items())[:max_people]
    }
    events_slice = {
        eid: {
            "label": e.label,
            "label_confidence": round(e.label_confidence, 2),
            "date_range": e.date_range,
            "venue": e.venue,
            "photo_count": len(e.photo_ids),
            "evidence": e.evidence[:3],
        }
        for eid, e in list(memory.events.items())[:max_events]
    }
    return {
        "people": people_slice,
        "events": events_slice,
        "open_questions": memory.open_questions[-5:],
    }


def apply_memory_update(memory: PhotoMemory, update: MemoryUpdate, asset_id: str) -> PhotoMemory:
    for pm in update.person_matches:
        adjusted = pm.confidence * _CONFIDENCE_CORRECTION
        if adjusted < _MIN_CONFIDENCE:
            logger.debug(f"Dropping low-confidence person match {pm.person_id} ({adjusted:.2f})")
            continue

        if pm.is_new_person:
            if pm.person_id not in memory.people:
                # Extract name from evidence string if Immich provided one
                immich_name = None
                if pm.evidence.startswith("Immich face recognition: "):
                    immich_name = pm.evidence.removeprefix("Immich face recognition: ").strip() or None
                memory.people[pm.person_id] = Person(
                    person_id=pm.person_id,
                    display_name=immich_name,
                    description=pm.evidence,
                    appears_in=[asset_id],
                )
                logger.info(f"New person: {immich_name or pm.person_id}")
        elif pm.person_id in memory.people:
            p = memory.people[pm.person_id]
            if asset_id not in p.appears_in:
                p.appears_in.append(asset_id)
            # Update name if Immich has since named this face cluster
            if pm.evidence.startswith("Immich face recognition: ") and not p.display_name:
                name = pm.evidence.removeprefix("Immich face recognition: ").strip()
                if name:
                    p.display_name = name
                    logger.info(f"Updated name for {pm.person_id}: {name}")

    for eu in update.event_updates:
        adjusted = eu.label_confidence * _CONFIDENCE_CORRECTION
        if eu.is_new_event:
            if eu.event_id not in memory.events:
                memory.events[eu.event_id] = Event(
                    event_id=eu.event_id,
                    label=eu.label,
                    label_confidence=adjusted,
                    evidence=eu.evidence,
                    photo_ids=[asset_id],
                    key_people=eu.key_people_to_add,
                )
                logger.info(f"New event: {eu.event_id} ({eu.label})")
        elif eu.event_id in memory.events:
            ev = memory.events[eu.event_id]
            if asset_id not in ev.photo_ids:
                ev.photo_ids.append(asset_id)
            ev.evidence.extend(eu.evidence)
            for pid in eu.key_people_to_add:
                if pid not in ev.key_people:
                    ev.key_people.append(pid)

    # Resolve and accumulate open questions
    memory.open_questions = [
        q for q in memory.open_questions if q not in update.resolved_questions
    ]
    memory.open_questions.extend(update.new_open_questions)

    if asset_id not in memory.processed_photos:
        memory.processed_photos.append(asset_id)

    return memory
