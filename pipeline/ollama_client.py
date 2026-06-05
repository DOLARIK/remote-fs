from __future__ import annotations

import json
from typing import Any

import os

import instructor
from loguru import logger
from openai import AsyncOpenAI

from .models import EventUpdate, PersonMatch, RetroactiveUpdate, VisionDescription

OLLAMA_BASE = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434") + "/v1"
MODEL = "gemma4:e4b"
_RETROACTIVE_THRESHOLD = 0.85

_openai = AsyncOpenAI(base_url=OLLAMA_BASE, api_key="ollama")
_client = instructor.from_openai(_openai, mode=instructor.Mode.JSON)

_SYSTEM = (
    "You are indexing a personal Indian family photo library. "
    "The photo owner is Divyanshu, recently married to Palak (April 19 2026). "
    "Wedding events: Mehndi (April 18 evening), Haldi (April 19 morning), "
    "Sangeet (April 19 afternoon), Ring Ceremony/Sagai (April 19 evening), "
    "Pheras (April 19 night). All events at Serene Springs Farm, Hillsborough Township, NJ. "
    "~80 guests. Photographer: Premal Patel / The Capture Image."
)


async def vision_pass(image_b64: str) -> VisionDescription:
    logger.debug("Pass 1: vision description")
    prompt_text = (
        "Describe this photo: who is present, the setting, attire, "
        "what activity or ritual is happening, cultural context, "
        "and any visible text or signage."
    )
    logger.debug(f"[LLM IN] vision_pass: <image b64 omitted> | prompt: {prompt_text}")
    result = await _client.chat.completions.create(
        model=MODEL,
        response_model=VisionDescription,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                    },
                    {"type": "text", "text": prompt_text},
                ],
            },
        ],
    )
    logger.debug(f"[LLM OUT] vision_pass:\n{result.model_dump()}")
    return result


async def person_matching_pass(
    description: str, known_people: dict[str, Any]
) -> list[PersonMatch]:
    logger.debug("Pass 2: person matching")
    user_content = (
        f"Photo description:\n{description}\n\n"
        f"Known people in memory:\n{json.dumps(known_people, indent=2)}\n\n"
        "Match people visible in this photo to known people, or register new ones. "
        "Be conservative — only match if there is clear visual evidence. "
        "For new people, generate a descriptive person_id like 'person_woman_red_saree'."
    )
    logger.debug(f"[LLM IN] person_matching_pass:\n{user_content}")
    result = await _client.chat.completions.create(
        model=MODEL,
        response_model=list[PersonMatch],
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_content},
        ],
    )
    logger.debug(f"[LLM OUT] person_matching_pass:\n{[r.model_dump() for r in result]}")
    return result


async def event_update_pass(
    description: str, known_events: dict[str, Any]
) -> list[EventUpdate]:
    logger.debug("Pass 3: event classification")
    user_content = (
        f"Photo description:\n{description}\n\n"
        f"Known event clusters:\n{json.dumps(known_events, indent=2)}\n\n"
        "Which single wedding event does this photo belong to? "
        "Return EXACTLY ONE EventUpdate — the best matching cluster. "
        "If it fits an existing cluster, set is_new_event=false and use that event_id. "
        "Only create a new cluster if no existing one fits. "
        "Event labels: mehndi, haldi, sangeet, sagai, pheras, pre_wedding, candid."
    )
    logger.debug(f"[LLM IN] event_update_pass:\n{user_content}")
    result = await _client.chat.completions.create(
        model=MODEL,
        response_model=list[EventUpdate],
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_content},
        ],
    )
    logger.debug(f"[LLM OUT] event_update_pass:\n{[r.model_dump() for r in result]}")
    return result


async def retroactive_check_pass(
    description: str,
    person_matches: list[PersonMatch],
    recent_photo_ids: list[str],
) -> list[RetroactiveUpdate]:
    high_conf = [
        pm for pm in person_matches
        if pm.confidence >= _RETROACTIVE_THRESHOLD and not pm.is_new_person
    ]
    if not high_conf:
        return []

    logger.debug(f"Pass 4: retroactive check ({len(high_conf)} high-confidence matches)")
    return await _client.chat.completions.create(
        model=MODEL,
        response_model=list[RetroactiveUpdate],
        messages=[
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"New photo description:\n{description}\n\n"
                    f"High-confidence identifications:\n"
                    f"{json.dumps([pm.model_dump() for pm in high_conf], indent=2)}\n\n"
                    f"Recently processed asset IDs (last 20): {recent_photo_ids[-20:]}\n\n"
                    "Should any previously processed photos have their descriptions updated "
                    "with this new identity information? Only suggest retroactive updates "
                    "when you are very certain. Return empty list if unsure."
                ),
            },
        ],
    )
