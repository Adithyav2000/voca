<p align="center">
  <strong style="font-size: 2em;">V.O.C.A.</strong><br>
  <em>Voice-Orchestrated Concierge for Appointments</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-blue?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/React_18-TypeScript-61DAFB?logo=react&logoColor=black" alt="React 18">
  <img src="https://img.shields.io/badge/OpenAI-Agentic_Voice-412991?logo=openai&logoColor=white" alt="OpenAI">
  <img src="https://img.shields.io/badge/PostgreSQL_15-asyncpg-4169E1?logo=postgresql&logoColor=white" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/Redis_7-Distributed_Locks-DC382D?logo=redis&logoColor=white" alt="Redis">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker">
</p>

---

## What is this?

VOCA is a **multi-agent voice AI system** that autonomously books real-world appointments by making actual phone calls. Give it a natural-language request like *"Find me a dentist near downtown, Tuesday afternoon"* and it will:

1. **Extract structured intent** using GPT-4o (`response_format=json_object`) with a regex fallback pipeline
2. **Query Google Places API (New)** for up to 15 matching providers with ratings, photos, and geolocation
3. **Spawn 15 concurrent voice agents** via `asyncio.create_task()` — each independently calls a provider through Twilio
4. Each agent **negotiates slots in natural conversation** using OpenAI's tool-calling protocol (5 server-side tools)
5. **Acquire distributed soft-locks** (Redis `SET NX EX 180`) to prevent double-booking across concurrent agents
6. **Score and rank** all returned offers using a weighted multi-objective function `(0.5×Recency + 0.3×Rating + 0.2×Proximity)`
7. **Stream results** to the browser in real-time via Server-Sent Events with snapshot deduplication
8. On user confirmation: **atomically book** (exclusive Redis lock → PostgreSQL insert → Google Calendar sync → Pub/Sub kill signal to terminate remaining agents)

The entire pipeline — from natural language input to confirmed appointment with calendar event — runs end-to-end in under 90 seconds.

---

## Table of Contents

- [System Architecture](#system-architecture)
- [Technical Deep-Dive](#technical-deep-dive)
  - [Concurrency Model](#concurrency-model)
  - [Finite State Machine](#finite-state-machine)
  - [Agentic Tool Protocol](#agentic-tool-protocol)
  - [Distributed Locking & Conflict Resolution](#distributed-locking--conflict-resolution)
  - [Match Quality Scoring Algorithm](#match-quality-scoring-algorithm)
  - [Real-Time Event Streaming](#real-time-event-streaming)
  - [Intent Extraction Pipeline](#intent-extraction-pipeline)
- [Data Model](#data-model)
- [API Surface](#api-surface)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [License](#license)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENT LAYER                                   │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │  React 18 + TypeScript SPA (Vite, Tailwind, Framer Motion)          │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌───────────────┐   │   │
│  │  │ Dashboard │  │ Squad Monitor│  │ Booking  │  │ Appointments  │   │   │
│  │  │ (create)  │  │ (SSE stream) │  │ Confirm  │  │ (calendar)    │   │   │
│  │  └──────────┘  └──────────────┘  └──────────┘  └───────────────┘   │   │
│  └───────────────────────────┬──────────────────────────────────────────┘   │
└──────────────────────────────┼──────────────────────────────────────────────┘
                               │ HTTP / SSE (httpOnly cookie auth)
┌──────────────────────────────┼──────────────────────────────────────────────┐
│                          ORCHESTRATION LAYER                                │
│  ┌───────────────────────────┴──────────────────────────────────────────┐   │
│  │  FastAPI (async uvicorn, lifespan-managed)                          │   │
│  │  ┌────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────┐  │   │
│  │  │ Auth       │  │ Orchestrator │  │ Tool        │  │ Calendar  │  │   │
│  │  │ (OAuth 2.0)│  │ (FSM engine) │  │ Dispatcher  │  │ Service   │  │   │
│  │  └────────────┘  └──────┬───────┘  └──────┬──────┘  └─────┬─────┘  │   │
│  └──────────────────────────┼────────────────┼────────────────┼────────┘   │
└─────────────────────────────┼────────────────┼────────────────┼────────────┘
                              │                │                │
          ┌───────────────────┘      ┌─────────┘                │
          │ asyncio.create_task()    │ webhooks                 │ OAuth tokens
          │ (×15 concurrent)         │                          │
┌─────────┼──────────────────────────┼──────────────────────────┼────────────┐
│         ▼        AI AGENT LAYER    │                          ▼            │
│  ┌─────────────┐            ┌──────┴───────┐        ┌──────────────────┐  │
│  │  Voice Agent│◄──────────►│  OpenAI      │        │  Google Calendar │  │
│  │  (×15 pool) │  tool call │  Realtime    │        │  API (conflict   │  │
│  │  via Twilio │  + response│  Voice API   │        │  detect + sync)  │  │
│  └─────────────┘            └──────────────┘        └──────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────┼──────────────────────────────────────────────┐
│                        DATA LAYER                                          │
│  ┌──────────────────┐  ┌───┴──────────────┐  ┌─────────────────────────┐  │
│  │  PostgreSQL 15   │  │  Redis 7         │  │  Google Places API      │  │
│  │  ─────────────── │  │  ─────────────── │  │  (New)                  │  │
│  │  users           │  │  Soft holds      │  │  ──────────────────     │  │
│  │  sessions (FSM)  │  │  (SET NX EX)     │  │  Provider discovery     │  │
│  │  call_tasks      │  │  Booking locks   │  │  Ratings + reviews      │  │
│  │  appointments    │  │  Kill signals    │  │  Photos + geolocation   │  │
│  │  (CHECK + UQ)    │  │  (Pub/Sub)       │  │  Distance matrix        │  │
│  └──────────────────┘  └──────────────────┘  └─────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Technical Deep-Dive

### Concurrency Model

The system uses **cooperative async/await concurrency** on Python's `asyncio` event loop — no thread pools, no GIL contention. When a user creates a session, the orchestrator spawns up to **15 named async tasks** (`call_agent_{uuid}`) using `asyncio.create_task()`. Each agent runs independently with its own Twilio call lifecycle and OpenAI tool loop. The HTTP response returns immediately with the squad execution plan while all agents negotiate concurrently in the background.

```
User Request ──► Orchestrator ──┬──► asyncio.Task(agent_01) ──► Twilio + OpenAI
                                ├──► asyncio.Task(agent_02) ──► Twilio + OpenAI
                                ├──► asyncio.Task(agent_03) ──► Twilio + OpenAI
                                │         ... (up to 15)
                                └──► return SquadPlan to client (non-blocking)
```

**Design decisions:**
- **Non-blocking fan-out** — all 15 calls initiate within milliseconds of each other; the client receives a response before any call connects
- **Independent failure isolation** — one agent timing out or encountering an error doesn't affect the other 14; each task has its own exception boundary
- **Graceful lifecycle management** — a background stale-session monitor (`asyncio.create_task` on app lifespan startup) sweeps every 60 seconds, terminating sessions idle for >5 minutes and cleaning up orphaned call tasks
- **Structured shutdown** — on app teardown, the lifespan context manager cancels the monitor task, drains the Redis connection pool, and disposes the async SQLAlchemy engine

### Finite State Machine

Session lifecycle is governed by a **6-state deterministic FSM** enforced at the database level via PostgreSQL `CHECK` constraints. State transitions use **conditional SQL updates** to prevent invalid transitions under concurrent writes — no application-level locks required.

```
    CREATED ──────► PROVIDER_LOOKUP ──────► DIALING ──────► NEGOTIATING
                     (GPT-4o intent         (spawn ×15       (agents in
                      extraction +           Twilio calls)    live conversation)
                      Places API query)

    NEGOTIATING ──────► RANKING ──────► CONFIRMED
                         (offers scored      (slot booked,
                          via multi-obj       calendar synced,
                          function)           kill signal sent)

    Any state ──────► FAILED | CANCELLED  (terminal)
```

**Atomic transition pattern** (prevents race conditions without explicit locks):
```sql
UPDATE sessions SET status = 'ranking'
WHERE id = :session_id AND status IN ('negotiating', 'dialing')
-- If WHERE matches 0 rows → transition rejected (no-op)
-- If WHERE matches 1 row  → transition applied atomically
```

### Agentic Tool Protocol

Each voice agent is equipped with **5 server-side tools** that OpenAI invokes via webhooks during live phone conversations. The backend exposes a single dispatcher endpoint that routes tool calls to the appropriate service function:

| Tool | Behavior | Side Effects | Timeout |
|------|----------|--------------|---------|
| `check_availability` | Checks user's Google Calendar for conflicts; acquires a 180s Redis soft-lock | Redis `SET NX`, Calendar API read | 10s |
| `report_slot_offer` | Persists the offered slot; computes match quality score; transitions session → `RANKING` | PostgreSQL write | 10s |
| `book_slot` | Acquires 60s exclusive booking lock; inserts appointment; syncs to Google Calendar; publishes kill signal | Redis `SET NX`, PG write, Calendar write, Pub/Sub publish | 10s |
| `get_distance` | Computes travel time via Google Distance Matrix API | External API read | 10s |
| `end_call` | Marks call task as terminal; checks if all tasks complete → session state advance | PostgreSQL write | 10s |

Tool responses are JSON-serialized and returned directly to the OpenAI agent, which uses them to continue the natural-language conversation with the provider (e.g., "That time works — let me confirm the booking").

### Distributed Locking & Conflict Resolution

The system implements a **three-tier distributed locking strategy** using Redis to handle concurrent negotiations across 15 simultaneous agents targeting the same user's calendar:

```
TIER 1 — Soft Hold (Optimistic Reservation)
┌──────────────────────────────────────────────────┐
│  Key:     hold:{user_id}:{date}:{time}           │
│  TTL:     180 seconds (auto-expire)              │
│  Acquire: SET key value NX EX 180                │
│  Purpose: Prevent two agents from holding the    │
│           same time slot for the same user        │
└──────────────────────────────────────────────────┘

TIER 2 — Booking Lock (Exactly-Once Confirmation)
┌──────────────────────────────────────────────────┐
│  Key:     lock:session:{session_id}:booked       │
│  TTL:     60 seconds                             │
│  Acquire: SET key value NX EX 60                 │
│  Purpose: Serialize the confirm operation;       │
│           guarantee exactly-once booking per      │
│           session even under concurrent clicks    │
└──────────────────────────────────────────────────┘

TIER 3 — Kill Signal (Pub/Sub Broadcast)
┌──────────────────────────────────────────────────┐
│  Channel: kill:{session_id}                      │
│  Method:  PUBLISH                                │
│  Purpose: Notify all remaining agents to         │
│           terminate after a slot is confirmed     │
└──────────────────────────────────────────────────┘
```

**End-to-end booking flow** (all three tiers):
1. Agent negotiates a slot → calls `check_availability` → Redis `SET NX` acquires soft hold (Tier 1)
2. User clicks "Confirm" in UI → backend acquires exclusive booking lock via `SET NX` (Tier 2)
3. Google Calendar API conflict check (defense-in-depth)
4. PostgreSQL `INSERT` into appointments with `UNIQUE(user_id, date, time)` constraint (final safety net)
5. Google Calendar event created (bi-directional sync)
6. `PUBLISH kill:{session_id}` terminates all remaining agents (Tier 3)
7. Batch-release all soft holds for this session

This layered approach provides **defense-in-depth**: Redis for speed (sub-millisecond), PostgreSQL constraints for durability, and Calendar API for external consistency.

### Match Quality Scoring Algorithm

Offers from providers are ranked using a **normalized weighted multi-objective scoring function**:

```
Score = 0.50 × Recency + 0.30 × Rating + 0.20 × Proximity
```

Each component is independently normalized to `[0, 1]`:

| Component | Formula | Rationale |
|-----------|---------|-----------|
| **Recency** | `1.0 - (hours_until_slot / 336)` | Capped at 14 days (336h). Rewards earliest available slots — empirically, users prefer sooner appointments. |
| **Rating** | `provider_rating / 5.0` | Normalized Google Places rating. Balances quality against pure availability. |
| **Proximity** | `1.0 - min(distance_km / 30.0, 1.0)` | Linear decay with 30km reference. Anything beyond 30km scores zero proximity. |

**Key properties:**
- Weights are stored **per-session** in PostgreSQL (`weight_time`, `weight_rating`, `weight_distance`), enabling user-configurable ranking strategies
- Scores are persisted as **4-decimal floats** on each `CallTask` row for auditability
- The scoring function is idempotent — re-scoring the same offer always produces the same result

### Real-Time Event Streaming

The frontend receives live updates through **Server-Sent Events (SSE)** with a custom deduplication layer to minimize unnecessary renders:

```
Client (EventSource)                Server (FastAPI StreamingResponse)
  │                                    │
  │  GET /sessions/{id}/stream         │
  │  (withCredentials: true)           │
  │───────────────────────────────────►│
  │                                    │  ┌─── Poll loop (2s interval) ────┐
  │  event: call_tasks                 │  │  SELECT call_tasks             │
  │  data: {tasks: [...], status: ..} ◄│──│  WHERE session_id = :id        │
  │                                    │  │  Serialize → hash snapshot     │
  │  event: session_status             │  │  If hash ≠ previous → yield    │
  │  data: {status: "ranking"}        ◄│──│  If hash = previous → skip     │
  │                                    │  │                                │
  │  :ping (comment, every 30s)       ◄│──│  Heartbeat keeps connection    │
  │                                    │  │  alive through proxies/LBs     │
  │                                    │  └────────────────────────────────┘
  │  (on terminal status) close()      │
  │───────────────────────────────────►│
```

**Design decisions:**
- **Snapshot deduplication**: hashing the serialized payload before yielding prevents redundant events when nothing has changed
- **Terminal state auto-close**: the client-side `useSessionStream` hook automatically closes the `EventSource` when status reaches `confirmed`, `failed`, or `cancelled`
- **30-second heartbeat**: `:ping` SSE comments prevent reverse proxies (nginx, cloudflare) from timing out idle connections
- **Cookie auth**: `withCredentials: true` sends the session cookie on the SSE connection, maintaining auth parity with REST endpoints

### Intent Extraction Pipeline

User input flows through a **two-stage extraction pipeline** with graceful degradation:

```
┌─────────────────────────────────────────────────────────┐
│  Stage 1: GPT-4o Structured Output (primary)            │
│                                                         │
│  Input:  "Indian restaurant near SoHo, Saturday evening"│
│  Model:  gpt-4o (response_format=json_object)           │
│  Output: {                                              │
│    "service_type": "restaurant",                        │
│    "qualifier": "Indian",                               │
│    "location": "SoHo",                                  │
│    "target_date": "2026-03-28",                         │
│    "target_time": "19:00"                               │
│  }                                                      │
└───────────────────────┬─────────────────────────────────┘
                        │ if API unavailable
                        ▼
┌─────────────────────────────────────────────────────────┐
│  Stage 2: Local Regex Extraction (fallback)             │
│                                                         │
│  • Service keywords: dentist, doctor, mechanic,         │
│    hairdresser, therapist, vet, restaurant               │
│    + cuisine qualifier capture (e.g., "Indian")         │
│  • Date: "tomorrow", "next Tuesday", ISO YYYY-MM-DD    │
│  • Time: morning→09:00, afternoon→14:00,               │
│          evening→19:00, night→20:00, explicit HH:MM    │
│  • Location: "near me" detection, named places          │
└─────────────────────────────────────────────────────────┘
```

The fallback ensures the system remains fully functional without an OpenAI API key — useful for local development, testing, and CI pipelines.

---

## Data Model

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────────┐
│    users     │       │     sessions     │       │   call_tasks     │
├──────────────┤       ├──────────────────┤       ├──────────────────┤
│ id (UUID PK) │◄──┐   │ id (PK)          │◄──┐   │ id (PK)          │
│ email (UQ)   │   └───│ user_id (FK)     │   └───│ session_id (FK)  │
│ name         │       │ status (ENUM)    │       │ status (ENUM)    │
│ picture_url  │       │ raw_query        │       │ provider_name    │
│ google_      │       │ service_type     │       │ provider_phone   │
│  refresh_    │       │ target_date      │       │ offered_date     │
│  token (AES) │       │ target_time      │       │ offered_time     │
│ created_at   │       │ location         │       │ score (FLOAT)    │
└──────────────┘       │ weight_time      │       │ hold_keys (JSONB)│
                       │ weight_rating    │       │ photo_url        │
                       │ weight_distance  │       │ rating (FLOAT)   │
                       │ created_at       │       │ distance_km      │
                       │ updated_at       │       │ created_at       │
                       └──────────────────┘       └────────┬─────────┘
                                                           │
                       ┌──────────────────┐                │
                       │  appointments    │                │
                       ├──────────────────┤                │
                       │ id (PK)          │                │
                       │ session_id (FK)  │────────────────┘
                       │ call_task_id(FK) │
                       │ user_id (FK)     │
                       │ provider_name    │
                       │ appointment_date │
                       │ appointment_time │
                       │ google_event_id  │
                       │ status (ENUM)    │
                       │ created_at       │
                       └──────────────────┘
                       UQ(user_id, date, time)
```

**Integrity guarantees:**
- **CHECK constraints** on all ENUM columns enforce valid state values at the database level
- **UNIQUE constraint** on `(user_id, appointment_date, appointment_time)` prevents double-booking as a last line of defense
- **Foreign keys** with cascading behavior maintain referential integrity across the session → call_task → appointment hierarchy
- **AES encryption** (Fernet) on OAuth refresh tokens before storage — tokens are never persisted in plaintext
- **JSONB columns** for hold keys and structured metadata enable flexible querying without schema migration

---

## API Surface

### Client-Facing Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/health` | — | Liveness probe (`{"status": "ok"}`) |
| `GET` | `/ready` | — | Readiness probe (DB + Redis ping; `503` on failure) |
| `GET` | `/api/auth/login` | — | Initiate Google OAuth 2.0 authorization flow |
| `GET` | `/api/auth/callback` | — | Handle OAuth callback → upsert user → set session cookie |
| `POST` | `/api/sessions` | Cookie | Create session + spawn agent squad → returns `SquadPlan` |
| `GET` | `/api/sessions/{id}` | Cookie | Session detail with current FSM state |
| `GET` | `/api/sessions/{id}/stream` | Cookie | SSE stream — real-time call task updates (2s poll, 30s heartbeat) |
| `GET` | `/api/sessions/{id}/results` | Cookie | Ranked provider offers with match quality scores |
| `POST` | `/api/sessions/{id}/confirm` | Cookie | Confirm slot → exclusive lock → book → calendar sync → kill signal |
| `POST` | `/api/sessions/{id}/cancel` | Cookie | Cancel session → release all holds → terminate agents |
| `GET` | `/api/appointments` | Cookie | List user's confirmed appointments |

### OpenAI Agent Webhook Endpoints

| Method | Endpoint | Tool | Side Effects |
|--------|----------|------|--------------|
| `POST` | `/api/check-availability` | `check_availability` | Calendar read + Redis soft-lock |
| `POST` | `/api/report-slot-offer` | `report_slot_offer` | PostgreSQL write + score computation |
| `POST` | `/api/book-slot` | `book_slot` | Redis lock + PG write + Calendar write + Pub/Sub |
| `POST` | `/api/end-call` | `end_call` | PostgreSQL write |
| `POST` | `/api/get-distance` | `get_distance` | Distance Matrix API read |

---

## Tech Stack

| Layer | Technology | Design Rationale |
|-------|-----------|------------------|
| **Backend** | Python 3.12, FastAPI, SQLAlchemy 2.0 (async), Pydantic v2 | Async-native framework; SQLAlchemy 2.0 for type-safe ORM with `asyncpg` driver; Pydantic v2 for zero-copy validation |
| **Frontend** | React 18, TypeScript, Vite, Tailwind CSS, Framer Motion | Native `EventSource` for SSE; Framer Motion for staggered 15-agent grid animations; Tailwind for theme system (CSS custom properties) |
| **Voice AI** | OpenAI Conversational AI (tool-calling), Twilio Programmable Voice | Agentic function protocol with structured JSON tool responses; Twilio for carrier-grade outbound telephony |
| **Database** | PostgreSQL 15 (`asyncpg`), Redis 7 | Async connection pooling (`pool_pre_ping`); Redis for sub-millisecond distributed locks and pub/sub kill signals |
| **Infra** | Docker Compose (4 services), ngrok/cloudflared | Single `docker compose up` deployment; tunnel profiles for OpenAI webhook reachability in development |
| **Security** | Google OAuth 2.0, Fernet AES-128, httpOnly cookies | Refresh tokens encrypted at rest; `SameSite=Lax` cookies; CORS restricted to explicit `FRONTEND_ORIGIN` |

---

## Project Structure

```
.
├── docker-compose.yml                 # 4-service stack: api, frontend, db, redis
├── voca-backend/
│   ├── Dockerfile
│   ├── pyproject.toml                 # Poetry dependency management
│   └── app/
│       ├── main.py                    # Lifespan (init/shutdown), middleware stack,
│       │                              # health/readiness probes
│       ├── config.py                  # Pydantic Settings (12-factor env config)
│       ├── api/
│       │   ├── auth.py                # Google OAuth 2.0 (login → callback → cookie)
│       │   └── routes.py             # REST + SSE endpoints, tool webhook dispatcher
│       ├── core/
│       │   ├── database.py            # SQLAlchemy 2.0 async models, CHECK constraints,
│       │   │                          # enum states, async engine factory
│       │   ├── redis.py               # Connection pool, pub/sub helpers
│       │   └── crypto.py             # Fernet AES encrypt/decrypt for refresh tokens
│       ├── models/
│       │   └── schemas.py            # Pydantic v2 request/response DTOs
│       ├── services/
│       │   ├── orchestrator.py        # FSM engine, concurrent agent spawner, scoring
│       │   │                          # algorithm, GPT-4o intent extraction + regex
│       │   │                          # fallback, stale session monitor
│       │   ├── tools.py              # 5 agentic tool implementations (10s timeout)
│       │   ├── calendar_service.py    # 3-tier Redis locking, Google Calendar sync,
│       │   │                          # hold/release/confirm lifecycle
│       │   ├── google_calendar.py     # Calendar API wrapper (user OAuth tokens)
│       │   └── provider_service.py   # Google Places API (New) search + photos
│       └── utils/
│           └── date_parse.py          # Relative date/time resolution
└── voca-frontend/
    ├── Dockerfile
    ├── package.json
    ├── vite.config.ts
    ├── tailwind.config.js             # Design tokens (amber palette, Plus Jakarta Sans)
    └── src/
        ├── App.tsx                    # Route definitions (React Router v6)
        ├── index.css                  # CSS custom properties (HSL color system,
        │                              # light/dark mode)
        ├── hooks/
        │   └── useSessionStream.ts   # SSE hook: EventSource + dedup + auto-reconnect
        ├── context/                   # Auth, Theme (dark/light), UserProfile, AuditTrail
        ├── components/                # VocaHeader, Layout, AuditTrailSidebar
        └── pages/
            ├── Dashboard.tsx          # Session creation (NL input → squad launch)
            ├── SessionDetail.tsx      # Live 15-agent grid + ranked offer panel
            ├── Appointments.tsx       # Confirmed bookings with calendar links
            └── Settings.tsx           # Preference weights, theme toggle
```

---

## Getting Started

### Prerequisites

- Docker & Docker Compose
- API keys: **OpenAI**, **Twilio** (Account SID + Auth Token + Phone Number)
- Optional: Google Cloud project with OAuth 2.0 client + Places API + Calendar API enabled

### Quick Start

```bash
git clone <repo-url> && cd voca
cp .env.example .env
# Fill in: OPENAI_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_PHONE_NUMBER
docker compose up --build
```

| Service | URL |
|---------|-----|
| Frontend | `http://localhost:5173` |
| API | `http://localhost:8000` |
| API Docs (Swagger) | `http://localhost:8000/docs` |
| Health Check | `http://localhost:8000/health` |
| Readiness Check | `http://localhost:8000/ready` |

### Expose for OpenAI Webhooks (dev)

```bash
docker compose --profile expose up
# Grab the HTTPS URL from ngrok/cloudflared logs
# Set it as the tool base URL in your OpenAI agent configuration
```

---

## License

[MIT](LICENSE)
