# AI Photo Intelligence Pipeline

## Goal
Build an autonomous photo intelligence system on top of the existing Immich library.
The system processes photos, builds an accumulative memory, and enables natural language
querying of the entire media library — with cultural and personal context awareness.

## The Core Problem Immich Alone Cannot Solve
- CLIP search doesn't understand culturally specific terms ("sagai", "mehndi", "haldi")
- No cross-photo reasoning — each photo treated as isolated object
- Cannot identify "bride's sagai photos" without date/album hints
- No understanding of who people are without manual labeling

## Solution Architecture

```
Immich (media + CLIP)
    ↓ asset IDs + thumbnails
Ingestion Pipeline (Python)
    ↓ image bytes
Gemma 4 E4B via Ollama (vision descriptions)
    ↓ photo description text
Memory Reasoner (Gemma 4 E4B via Ollama)
    ↓ structured Pydantic updates
Graphiti Knowledge Graph (FalkorDB backend)
    ↓ enriched descriptions
Immich (write back descriptions via API)
    ↓
Open WebUI + MCP (natural language queries)
```

## Key Design Decisions

### Accumulative Memory (not batch)
NOT: send 200 photos at once to LLM
YES: maintain a growing JSON memory state, each photo reads+updates it

Each photo call:
1. Read relevant slice of current memory
2. Gemma looks at photo + memory context
3. Returns: description + person matches + event updates + retroactive flags
4. Memory updates, Immich description updated
5. If retroactive update flagged → batch update previous photos

### Two-tier Memory
```
Working memory (hot):   current cluster context, last 50 photos, ~small
Long-term memory (cold): confirmed people/events, compressed summaries
```
Gemma never reads full memory — only relevant slice per call.

### Chunked Prompts (not monolithic)
Each photo triggers 4 focused micro-calls to Gemma, not one complex call:
1. Vision pass: pure description, no memory
2. Person matching: description vs memory.people
3. Event update: description vs memory.events  
4. Retroactive check: only if confidence > 0.85

### Confidence Gating
- Auto-propagate: confidence > 0.85
- Flag for human review: confidence 0.5–0.85
- Discard: confidence < 0.5
- E4B tends to overestimate — apply 0.8x correction factor

## Data Models (Pydantic v2)

### Core Memory State
```python
class Person(BaseModel):
    person_id: str
    display_name: Optional[str]        # set once human confirms
    description: str
    outfits: list[str]
    role: Optional[str]                # "bride", "groom", "family"
    role_confidence: float
    appears_in: list[int]              # Immich asset IDs
    confirmed_by_human: bool = False

class Event(BaseModel):
    event_id: str
    label: Optional[str]               # "sagai", "mehndi", "haldi" etc
    label_confidence: float
    date_range: Optional[str]
    venue: Optional[str]
    key_people: list[str]              # person_ids
    photo_ids: list[int]               # Immich asset IDs
    evidence: list[str]
    confirmed_by_human: bool = False

class PhotoMemory(BaseModel):
    people: dict[str, Person] = {}
    events: dict[str, Event] = {}
    open_questions: list[str] = []
    processed_photos: list[int] = []   # Immich asset IDs
    last_updated: datetime
```

### Per-photo Update (instructor enforces this)
```python
class PersonMatch(BaseModel):
    person_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str                      # "same pink lehenga as photo 3"
    is_new_person: bool

class RetroactiveUpdate(BaseModel):
    photo_ids: list[int]
    add_to_description: str
    reason: str

class MemoryUpdate(BaseModel):
    photo_description: str
    person_matches: list[PersonMatch]
    event_updates: list[EventUpdate]
    retroactive_updates: list[RetroactiveUpdate]
    new_open_questions: list[str]
    resolved_questions: list[str]
```

## Knowledge Graph: Graphiti + FalkorDB

### Why Graphiti over Neo4j
- Temporal facts native: "Palak wears pink lehenga [valid: sagai event only]"
- Contradiction handling built in
- Right scale for personal library (~50 people, ~10 events, ~8000 photos)
- Neo4j is overkill and RAM-heavy for this use case

### Graph Node Types
- Person nodes: identity, role, confirmed status
- Event nodes: label, date, venue, confidence
- Photo nodes: Immich asset ID, event, people present
- Outfit nodes: description, worn_by, seen_at_events

### Key Relationships
```
(Person)-[:APPEARS_IN]->(Photo)
(Photo)-[:PART_OF]->(Event)
(Person)-[:WORE]->(Outfit)-[:AT]->(Event)
(Person)-[:RELATED_TO]->(Person)
```

## LLM Setup

### Ollama (native Mac, not Docker)
- Endpoint: `http://host.docker.internal:11434` (from Docker containers)
- Endpoint: `http://localhost:11434` (from host/pipeline scripts)
- Model: `gemma4:e4b`
- Vision: native in Gemma 4 E4B

### Instructor for structured outputs
```python
import instructor
from ollama import AsyncClient

client = instructor.from_provider("ollama/gemma4:e4b")
result = client.chat.completions.create(
    response_model=MemoryUpdate,
    messages=[...]
)
```

### Prompt pattern for vision pass
```
You are indexing a personal Indian family photo library.
The photo owner is Divyanshu, recently married to Palak (April 19 2026).
Wedding events: Mehndi, Haldi, Sangeet, Ring Ceremony (Sagai), Pheras.
All events at Serene Springs Farm, Hillsborough Township, NJ.

Current memory context:
{relevant_memory_slice}

Describe this photo:
- Who is present (match to known people if possible)
- Setting and attire
- What activity/ritual is happening
- Cultural context if visible
- Any text/signage visible
```

## Immich API Integration

### Base URL
- From host: `http://localhost:2283`
- From Docker containers: `http://immich_server:3001`

### Key endpoints used
```
GET  /api/asset                    # list all assets
GET  /api/asset/{id}/thumbnail     # fetch thumbnail for vision
PUT  /api/asset/{id}               # update description
GET  /api/person                   # list face clusters
GET  /api/search/smart             # CLIP semantic search
POST /api/search/metadata          # metadata search
```

### Auth
API key stored in `.env` as `IMMICH_API_KEY`
Header: `x-api-key: ${IMMICH_API_KEY}`

## Pipeline State (SQLite)
```
pipeline_state.db
├── processed_photos (asset_id, processed_at, description_written)
├── retry_queue (asset_id, error, retry_count)
└── retroactive_queue (asset_ids, update_text, reason)
```
Resumable: on crash, reload memory from `photo_memory.json`, skip processed_photos.

## Human Review UI
Simple FastAPI + htmx web interface at `http://localhost:8888`

Three views:
1. **Cluster Review** — confirm/correct AI event labels, sample thumbnails shown
2. **People Review** — confirm face cluster identities
3. **Free-form Insights** — plain text box, Gemma parses into graph nodes

## Docker Services for AI Pipeline
Separate `docker-compose.ai.yml` (or extend main compose):
- `falkordb` — graph database (Graphiti backend)
- `redis-ai` — Graphiti cache (separate from Nextcloud redis)
- `graphiti-mcp` — MCP server exposing graph tools to Gemma
- `pipeline-api` — FastAPI ingestion + review UI service
- `open-webui` — chat interface connecting to Ollama + MCP

Ollama runs NATIVELY on Mac (not in Docker) for Metal GPU acceleration.
All Docker services connect to Ollama via `host.docker.internal:11434`.

## Library Context (seed knowledge)
Divyanshu and Palak's wedding library:
- ~7,022 photos, ~748 videos
- 5 events: Mehndi (April 18 evening), Haldi (April 19 morning),
  Sangeet (April 19 afternoon), Ring Ceremony/Sagai (April 19 evening),
  Pheras (April 19 night)
- Venue: Serene Springs Farm, Hillsborough Township, NJ
- Photographer: Premal Patel / The Capture Image
- ~80 guests

## File Structure (planned)
```
remote-fss/
├── docker-compose.yml          # existing media stack
├── docker-compose.ai.yml       # AI pipeline services
├── CLAUDE.md
├── CLAUDE.local.md             # gitignored
├── .env
├── .claude/
│   └── ai-pipeline.md          # this file
├── pipeline/
│   ├── main.py                 # entry point
│   ├── models.py               # all Pydantic models
│   ├── immich_client.py        # Immich API wrapper
│   ├── ollama_client.py        # Gemma vision + reasoning calls
│   ├── memory.py               # PhotoMemory load/save/update
│   ├── graphiti_client.py      # knowledge graph read/write
│   ├── ingestion.py            # main photo processing loop
│   └── review_ui/
│       ├── app.py              # FastAPI app
│       └── templates/          # htmx templates
└── photo_memory.json           # persistent memory state (gitignored)
```
