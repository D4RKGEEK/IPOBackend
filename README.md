# IPO Aggregation Platform — v3.0

FastAPI service that scrapes IPO filings from **SEBI**, **BSE**, **NSE**, and **BSE-SME**, persists to SQLite, downloads and section-splits prospectus PDFs, and extracts structured fields via **DeepSeek** or **Firecrawl** (per-section JSON-schema LLM extraction).

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SEBI ┐                                                                  │
│  BSE  ├──► /api/refresh (bg) ─► ipo_master + ipo_status_history          │
│  NSE  │                                                                  │
│  SME  ┘                                                                  │
│                       │                                                  │
│                       ▼                                                  │
│   /api/ipos/{id}/resolve (bg) ─► download PDF → split sections          │
│                       │              ├─ document_sections.raw_md         │
│                       │              └─ Cloudflare R2 (.md per section)  │
│                       ▼                                                  │
│   /api/ipos/{id}/parse-sections     (DeepSeek, merged call)              │
│   /api/ipos/{id}/parse-firecrawl    (Firecrawl, per-section schema)     │
│                       │                                                  │
│                       ▼                                                  │
│   /api/ipos/{id}/parsed-all  →  unified structured JSON                 │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Run locally

```bash
# 1. Set up venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# edit .env — DEEPSEEK_API_KEY, R2_*, FIRECRAWL_API_KEY

# 3. Start (raise file descriptor limit; macOS imports otherwise hang)
ulimit -n 65536
.venv/bin/python -m uvicorn app.main:app --port 8001 --reload

# (Optional) Dashboard on :8002
.venv/bin/python -m uvicorn dashboard.main:app --port 8002 --reload
```

- API docs: <http://127.0.0.1:8001/docs>
- Liveness: <http://127.0.0.1:8001/health>
- Deep health: <http://127.0.0.1:8001/health?deep=true> (probes DB + R2 + Firecrawl + DeepSeek)
- Dashboard: <http://127.0.0.1:8002/dashboard/>

---

## Endpoints

### Read

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health[?deep=true]` | Liveness + dependency reachability |
| `GET` | `/api/ipos` | List with filters: `documents`, `status`, `platform`, `search`, `year`, `page` |
| `GET` | `/api/ipos/{id}` | Single IPO + status history |
| `GET` | `/api/ipos/{id}/documents` | Doc overview per IPO + section counts + R2 URLs |
| `GET` | `/api/ipos/{id}/documents/{doc}/sections` | All sections in a doc with R2 URLs |
| `GET` | `/api/ipos/{id}/documents/{doc}/sections/{name}` | One section's raw markdown |
| `GET` | `/api/ipos/{id}/documents/{doc}/sections/{name}/parsed` | DeepSeek/Firecrawl output for one section |
| `GET` | `/api/ipos/{id}/parsed-all` | Unified merged JSON across all parsed sections |
| `GET` | `/api/status-changes` | Recent lifecycle transitions |
| `GET` | `/api/dashboard/stats` | Aggregate counts |
| `GET` | `/api/dashboard/logs` | Scraper-run history |
| `GET` | `/api/tasks[?limit=N]` | Recent background tasks (SQLite-persisted) |
| `GET` | `/api/tasks/{task_id}` | Single task status + progress |

### Write (all background → return `{task_id}`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/refresh[?year=YYYY]` | Full scrape: SEBI + BSE + NSE + BSE-SME |
| `POST` | `/api/ipos/{id}/resolve` | Download PDFs → split sections → upload to R2 |
| `POST` | `/api/ipos/{id}/parse-sections` | DeepSeek extraction (merged call) |
| `POST` | `/api/ipos/{id}/parse-firecrawl` | Firecrawl extraction (per-section, targeted schemas) |

Poll any background task via `GET /api/tasks/{task_id}`. Tasks persist across API restarts.

---

## Data lifecycle

```
1. SCRAPE     POST /api/refresh             → ipo_master, ipo_status_history
2. RESOLVE    POST /api/ipos/{id}/resolve   → document_sections.raw_md + R2 upload
3. PARSE      POST /api/ipos/{id}/parse-firecrawl   → document_sections.parsed_data
4. CONSUME    GET  /api/ipos/{id}/parsed-all        → unified extraction across sections
```

Each section's markdown is hosted at:

```
https://<R2_PUBLIC_BASE>/sections/{ipo_id}/{doc_type}/{SECTION_NAME}.md
```

Deterministic, public, idempotent. Firecrawl scrapes these URLs directly.

---

## Architecture

```
app/
  config.py            Central pydantic-settings (env validation at boot)
  main.py              FastAPI routes
  task_manager.py      SQLite-backed background task queue
  scraper_service.py   SEBI/BSE/NSE/SME orchestrator
  section_resolver.py  PDF → ToC → sections → DB + R2
  section_parser.py    DeepSeek per-IPO parser (merged)
  retry.py             Exponential-backoff retry helpers
  logging_setup.py     Structured logging (one place)
  clients.py           Per-source HTTP clients
  status.py            Lifecycle-status computation
  db_models.py         SQLAlchemy schema
  db_service.py        DB CRUD facade
  parsers/
    firecrawl_client.py    Thin /scrape client
    firecrawl_parser.py    Per-section orchestrator
    section_schemas.py     JSON Schemas (one per target section)
  storage/
    r2.py              Cloudflare R2 boto3 wrapper

dashboard/
  main.py              Jinja2 admin (port 8002)
  templates/           base.html + 4 pages
```

---

## Configuration

All env vars are loaded via [app/config.py](app/config.py). Missing required values fail boot with a clear message. See [.env.example](.env.example) for the full list — the most important are:

```bash
# LLM providers
DEEPSEEK_API_KEY=sk-...
FIRECRAWL_API_KEY=fc-...
PARSER_PROVIDER=deepseek          # deepseek | firecrawl

# Cloudflare R2 (section markdown storage)
CF_ACCOUNT_ID=...
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_BUCKET=ipo
R2_PUBLIC_BASE=https://pub-xxxx.r2.dev

# Scrape tuning
SEBI_MAX_PAGES=10                 # per doc-type
```

---

## Background tasks

Long ops run in a daemon thread; state is persisted to a `background_tasks` table in `ipos.db` so polls survive `uvicorn --reload` and process crashes.

```bash
# kick off a scrape
curl -X POST http://127.0.0.1:8001/api/refresh
# → {"task_id":"a1b2c3","status":"started"}

# poll
curl http://127.0.0.1:8001/api/tasks/a1b2c3
# → {"id":"a1b2c3","status":"running","progress":0.45,"progress_label":"…"}
```

---

## Smoke tests

```bash
.venv/bin/python scripts/test_r2.py          # R2 upload/get/delete round-trip
.venv/bin/python scripts/test_firecrawl.py   # End-to-end Firecrawl on IPO #88
.venv/bin/python scripts/backfill_r2.py      # Push existing DB sections to R2
```

---

## Database — local SQLite by default, Postgres in production

The app auto-selects based on env:

| `DATABASE_URL` env | DB used | When |
|---|---|---|
| unset / blank | local `ipos.db` | dev |
| `postgresql://…` | Postgres (Supabase) | production |

### Switching to Supabase Postgres

```bash
# 1. Create a Supabase project. Grab the DATABASE_URL from
#    Settings → Database → Connection string (Session pooler, port 5432).

# 2. Add to .env (or production env):
DATABASE_URL=postgresql://postgres.xxxxx:PASSWORD@aws-0-XX.pooler.supabase.com:5432/postgres

# 3. Create the schema in Postgres
.venv/bin/alembic upgrade head

# 4. Copy existing SQLite data over (idempotent — uses ON CONFLICT DO UPDATE)
.venv/bin/python scripts/copy_to_postgres.py

# 5. Restart the API. It now reads/writes Postgres.
ulimit -n 65536
.venv/bin/python -m uvicorn app.main:app --port 8001 --reload
```

To roll back: unset `DATABASE_URL` → the app drops back to SQLite. The
local `ipos.db` is never touched by the copy script.

### Schema migrations

```bash
# After adding/changing an ORM column in app/db_models.py:
.venv/bin/alembic revision --autogenerate -m "add some column"
.venv/bin/alembic upgrade head
```

`alembic stamp head` was already run on the existing SQLite to mark it
as up-to-date with the baseline migration. New columns or tables flow
through alembic from here on.

---

## Postman

Import `postman/IPO Aggregation API.postman_collection.json`.
