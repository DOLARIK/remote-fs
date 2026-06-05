from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Person(BaseModel):
    person_id: str                       # Immich's person UUID
    display_name: Optional[str] = None  # Immich's name for this face cluster
    description: str = ""
    outfits: list[str] = []
    role: Optional[str] = None          # "bride", "groom", "family", "guest"
    role_confidence: float = 0.0
    appears_in: list[str] = []          # Immich asset IDs
    confirmed_by_human: bool = False


class Event(BaseModel):
    event_id: str
    label: Optional[str] = None         # "mehndi", "haldi", "sangeet", "sagai", "pheras"
    label_confidence: float = 0.0
    date_range: Optional[str] = None
    venue: Optional[str] = None
    key_people: list[str] = []          # person_ids
    photo_ids: list[str] = []           # Immich asset IDs
    evidence: list[str] = []
    confirmed_by_human: bool = False


class PhotoMemory(BaseModel):
    people: dict[str, Person] = {}
    events: dict[str, Event] = {}
    open_questions: list[str] = []
    processed_photos: list[str] = []    # Immich asset IDs
    last_updated: datetime = Field(default_factory=datetime.utcnow)


# ── Per-photo structured output models ──────────────────────────────────────


class VisionDescription(BaseModel):
    description: str
    setting: str
    attire_notes: str
    activity: str
    cultural_context: str
    visible_text: Optional[str] = None


class PersonMatch(BaseModel):
    person_id: str
    display_name: Optional[str] = None  # human-readable name (from Immich)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str
    is_new_person: bool


class EventUpdate(BaseModel):
    event_id: str
    is_new_event: bool
    label: Optional[str] = None
    label_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = []
    key_people_to_add: list[str] = []


class RetroactiveUpdate(BaseModel):
    photo_ids: list[str]
    add_to_description: str
    reason: str


class MemoryUpdate(BaseModel):
    photo_description: str
    person_matches: list[PersonMatch] = []
    event_updates: list[EventUpdate] = []
    retroactive_updates: list[RetroactiveUpdate] = []
    new_open_questions: list[str] = []
    resolved_questions: list[str] = []
