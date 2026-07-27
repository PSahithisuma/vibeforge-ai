# VibeForge AI

> **Spec is the product. Selection is the input. Code is a rendering.**

VibeForge is a tenant-isolated, AI-powered application generation platform. You answer structured questions about your application's domain, authentication strategy, API style, and data model — the platform freezes a machine-readable Spec IR, and deterministically generates a production-ready codebase from it.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│  Client (Browser / CLI)                                         │
│    ↓ JWT (Keycloak RS256)         ↑ SSE stream                  │
├─────────────────────────────────────────────────────────────────┤
│  FastAPI  (services/api/)                                       │
│    POST /api/v1/projects/{id}/generate → Arq enqueue            │
│    GET  /api/v1/jobs/{id}/stream       → SSE via Redis pub/sub  │
├─────────────────────────────────────────────────────────────────┤
│  Arq Worker  (services/worker/)                                 │
│    run_dummy_job → progress events → Postgres + Redis pub/sub   │
├─────────────────────────────────────────────────────────────────┤
│  Postgres (RLS enforced, Contract C9)                           │
│    SET LOCAL app.current_tenant_id = '<uuid>'  per transaction  │
│    14 tables: tenants · users · projects · application_specs    │
│               jobs · job_events · audit_log · …                 │
└─────────────────────────────────────────────────────────────────┘
```

### Eight Design Principles

| # | Principle |
|---|-----------|
| P1 | Spec is the product — requirements capture is the hard problem |
| P2 | Polyglot output — same spec → any target language |
| P3 | Semantic caching — `canonical_hash` prevents redundant generation |
| P4 | Objective review — gate reports use facts, not opinions |
| P5 | Redis is never the system of record — Postgres owns truth |
| P6 | Tenant isolation — RLS enforced at the DB layer, not just the app |
| P7 | Escalation memory — commercial-model answers become local knowledge |
| P8 | Auditability — every action in `audit_log`, every decision in `gate_decisions` |

---

## Phase 0 — Status: Complete ✅

Phase 0 proves the **async job pipeline end-to-end with zero AI involved**.

### What's verified

| Check | Result |
|---|---|
| `GET /health` | `postgres: ok`, `redis: ok` |
| `POST /api/v1/projects/` + JWT | 201 → `project_id` + `tenant_id` |
| `POST /api/v1/projects/{id}/generate` | 202 → `job_id` + SSE `stream_url` |
| Arq worker picks up job | `queued → running → completed` in ~10 s |
| Events in Postgres | 6 rows (5 × progress @ 20/40/60/80/100% + 1 complete) |
| RLS isolation | All rows scoped to tenant UUID |
| Idempotency | Same `idempotency_key` prevents double-dispatch |

---

## Infrastructure Stack

| Service | Port | Role |
|---|---|---|
| **PostgreSQL 16** | 5432 | System of record, RLS enforced |
| **Redis 7** | 6379 | Arq job broker + SSE pub/sub |
| **Qdrant v1.9** | 6333 | Vector store (Phase 1+) |
| **MinIO** | 9000 / 9001 | Object storage for build bundles |
| **Keycloak 24** | 8080 | OIDC provider, vibeforge realm |
| **Langfuse 2** | 3001 | LLM observability (Phase 1+) |
| **Prometheus** | 9090 | Metrics collection |
| **Grafana** | 3000 | Dashboards |
| **FastAPI** | 8000 | REST API + SSE |
| **Arq Worker** | — | Background job processor |

---

## Quick Start

### Prerequisites
- Docker Desktop
- Git

### 1. Clone and configure

```bash
git clone https://github.com/PSahithisuma/vibeforge-ai.git
cd vibeforge-ai
cp .env.example .env
# Edit .env if you want to change any secrets (dev defaults work out of the box)
```

### 2. Start everything

```bash
docker compose up -d --build
```

First run takes ~3-4 minutes (pulling images + building api/worker).

### 3. Verify services are healthy

```bash
docker compose ps
```

All services should show `healthy`.

### 4. Get a JWT and test the pipeline

```powershell
# PowerShell
$body = "client_id=vibeforge-api&client_secret=vibeforge-api-secret-change-me&username=admin@vibeforge.local&password=admin123&grant_type=password"
$token = (Invoke-RestMethod -Uri "http://localhost:8080/realms/vibeforge/protocol/openid-connect/token" -Method POST -Body $body -ContentType "application/x-www-form-urlencoded").access_token

# Create project
$proj = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/projects/" -Method POST -Headers @{Authorization="Bearer $token"} -Body '{"name":"My App"}' -ContentType "application/json"

# Enqueue generation
$gen = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/projects/$($proj.id)/generate" -Method POST -Headers @{Authorization="Bearer $token"}
Write-Host "Job ID: $($gen.job_id)"
Write-Host "Stream: $($gen.stream_url)"
```

```bash
# Linux / macOS
TOKEN=$(curl -s -X POST http://localhost:8080/realms/vibeforge/protocol/openid-connect/token \
  -d "client_id=vibeforge-api&client_secret=vibeforge-api-secret-change-me&username=admin@vibeforge.local&password=admin123&grant_type=password" \
  | jq -r .access_token)

PROJECT=$(curl -s -X POST http://localhost:8000/api/v1/projects/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"My App"}' | jq -r .id)

curl -s -X POST http://localhost:8000/api/v1/projects/$PROJECT/generate \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### 5. Open the API docs

[http://localhost:8000/docs](http://localhost:8000/docs)

---

## Project Structure

```
vibeforge/
├── docker-compose.yml           # Full 10-service stack
├── .env.example                 # All environment variables documented
├── Makefile                     # make up / down / logs / pg-tables / keycloak-token
│
├── services/
│   ├── api/                     # FastAPI application
│   │   ├── core/
│   │   │   ├── config.py        # pydantic-settings
│   │   │   ├── database.py      # SQLAlchemy async engine + RLS session
│   │   │   ├── redis_client.py  # Shared async Redis pool
│   │   │   └── auth.py          # JWKS cache, RS256 JWT validation, user upsert
│   │   ├── routers/
│   │   │   ├── health.py        # GET /health
│   │   │   ├── projects.py      # Projects CRUD + /generate
│   │   │   ├── jobs.py          # Job status + events
│   │   │   └── stream.py        # SSE via Redis pub/sub
│   │   └── main.py              # FastAPI app, lifespan, CORS
│   │
│   └── worker/                  # Arq worker
│       ├── tasks/
│       │   └── dummy.py         # Phase 0 dummy task (5 progress events × 2s)
│       └── worker.py            # WorkerSettings, asyncpg pool, startup/shutdown
│
└── infra/
    ├── postgres/init/
    │   ├── 00_create_dbs.sh     # Creates keycloak + langfuse sibling DBs
    │   ├── 01_schema.sql        # 14 tables with RLS-ready design
    │   └── 02_rls.sql           # FORCE ROW LEVEL SECURITY on all tables
    ├── keycloak/
    │   └── vibeforge-realm.json # Pre-imported realm (3 seed users, vibeforge-api client)
    ├── prometheus/prometheus.yml
    └── grafana/dashboards/
        └── vibeforge-overview.json
```

---

## Seed Users (dev only)

| Email | Password | Role |
|---|---|---|
| `admin@vibeforge.local` | `admin123` | admin |
| `dev@vibeforge.local` | `dev123` | developer |
| `viewer@vibeforge.local` | `viewer123` | viewer |

---

## Roadmap

- **Phase 0** ✅ — Async pipeline, RLS schema, Keycloak, SSE streaming
- **Phase 1** 🔜 — Spec IR (`AppSpec` Pydantic model + `canonical_hash`), Option Graph compiler, Prometheus metrics live
- **Phase 2** — LLM agents (code generation, QA gate, reviewer)
- **Phase 3** — Semantic caching, escalation memory, Metacognition Gate
- **Phase 4** — Multi-language output, MinIO bundle delivery, diff cards

---

## License

MIT
