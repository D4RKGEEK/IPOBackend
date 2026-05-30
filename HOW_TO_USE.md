# How to Use — IPO Scraper v3

> Single source of truth for **what this system does**, **how to run it**, and **how to operate it day-to-day**.

---

## Table of contents

1. [What this is](#1-what-this-is)
2. [Architecture in one picture](#2-architecture-in-one-picture)
3. [First-time local setup](#3-first-time-local-setup)
4. [Environment variables — every one explained](#4-environment-variables--every-one-explained)
5. [The data lifecycle](#5-the-data-lifecycle)
6. [API reference (every endpoint, with curl)](#6-api-reference-every-endpoint-with-curl)
7. [Background tasks (how to poll)](#7-background-tasks-how-to-poll)
8. [Validation layer & confidence scoring](#8-validation-layer--confidence-scoring)
9. [The unified contract (what Next.js consumes)](#9-the-unified-contract-what-nextjs-consumes)
10. [Notifications (Telegram + Gmail)](#10-notifications-telegram--gmail)
11. [Cloudflare R2 (section storage)](#11-cloudflare-r2-section-storage)
12. [Firecrawl + DeepSeek (the parsers)](#12-firecrawl--deepseek-the-parsers)
13. [Cost guide](#13-cost-guide)
14. [Database (SQLite ↔ Supabase Postgres)](#14-database-sqlite--supabase-postgres)
15. [Schema versioning (adding a new section later)](#15-schema-versioning-adding-a-new-section-later)
16. [Deployment](#16-deployment)
17. [Common operations cheat sheet](#17-common-operations-cheat-sheet)
18. [Troubleshooting](#18-troubleshooting)

---

## 1. What this is

A backend service that:

- **Scrapes** IPO filings from SEBI, BSE, NSE, and BSE-SME every 6 hours.
- **Downloads** each IPO's DRHP / RHP / Final Prospectus PDF.
- **Splits** each PDF into named sections (CAPITAL_STRUCTURE, GENERAL_INFORMATION, RESTATED_FINANCIAL_STATEMENTS, etc.) using the Table of Contents.
- **Hosts** each section's markdown on Cloudflare R2 at a deterministic URL.
- **Extracts** structured fields (CIN, BRLM, financials, dates) by sending those R2 URLs to **Firecrawl** with per-section JSON schemas.
- **Validates** each extraction (regex formats + cross-source checks vs BSE/NSE) and assigns a `confidence_score` + `publish_status`.
- **Unifies** all sections into a single `unified_data` JSON per IPO — the contract your Next.js site consumes.
- **Notifies** you (Telegram + Gmail) on every important event.

You run it on Fly.io or Railway. Cron (via GitHub Actions) hits `/api/refresh` every 6h. Your Next.js calls `GET /api/ipos/{id}/unified`.

---

## 2. Architecture in one picture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│  CRON (GH Actions, every 6h)                                             │
│      └─► POST /api/refresh                                               │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ 1. SCRAPE (background)                                             │  │
│  │    SEBI + BSE + NSE + SME → diff vs DB                            │  │
│  │    Skip status=listed IPOs (frozen)                                │  │
│  │    Writes: ipo_master, ipo_status_history                          │  │
│  │    Notifies: new IPO, status changes, scrape summary               │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                              │                                           │
│                              ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ 2. RESOLVE (per IPO, background)                                   │  │
│  │    Download PDF → parse ToC → split into sections                  │  │
│  │    Save raw_md to DB + upload to R2                                │  │
│  │    Writes: document_sections (with raw_md_sha256)                  │  │
│  │    R2:     sections/{ipo_id}/{doc_type}/{section_name}.md          │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                              │                                           │
│                              ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ 3. PARSE (per IPO, background)                                     │  │
│  │    Group sections into 4 calls. Hash-gate to skip unchanged.       │  │
│  │    Each call: R2 URL + tiny JSON schema → Firecrawl                │  │
│  │    Writes: document_sections.parsed_data (with parsed_md_sha256)   │  │
│  │    Cost: ~₹1.60 fresh, ₹0 cached re-runs                           │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                              │                                           │
│                              ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ 4. VALIDATE + UNIFY (synchronous tail of parse)                    │  │
│  │    Format checks (CIN regex, email, dates, face_value)             │  │
│  │    Cross-source (LLM company_name vs BSE/NSE; dates within 3d)     │  │
│  │    Confidence scoring per field                                    │  │
│  │    Merge per-section data → ipo_master.unified_data                │  │
│  │    Set publish_status: published / needs_review / rejected         │  │
│  │    Notify: ✅ Parsed / 👀 Needs review / ❌ Rejected                │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                              │                                           │
│                              ▼                                           │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ 5. CONSUME (Next.js calls)                                         │  │
│  │    GET /api/ipos/{id}/unified  ←  publish_status, confidence, data │  │
│  │    GET /api/ipos?status=listed&documents=fp  (list with filters)   │  │
│  │    GET /api/review-queue?publish_status=needs_review  (ops view)   │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 3. First-time local setup

```bash
# 1. clone
git clone https://github.com/D4RKGEEK/IPOBackend.git
cd IPOBackend

# 2. python venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. config
cp .env.example .env
# Edit .env — at minimum, set:
#   DEEPSEEK_API_KEY     (any working key)
#   FIRECRAWL_API_KEY    (any working key)
#   CF_ACCOUNT_ID / R2_*  (any working R2 bucket)
#
# Leave blank for now (optional, unlocks features when set):
#   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID  → notifications
#   GMAIL_USER / GMAIL_APP_PASSWORD        → email digests
#   DATABASE_URL                            → Supabase (else SQLite)
#   INTERNAL_API_KEY                        → cron authentication

# 4. DB
# If DATABASE_URL is blank, the app uses local SQLite (ipos.db).
# Either way, run migrations once:
.venv/bin/alembic upgrade head

# 5. boot. ulimit fix avoids macOS file-descriptor exhaustion on PyMuPDF import.
ulimit -n 65536
.venv/bin/python -m uvicorn app.main:app --port 8001 --reload

# 6. dashboard (optional, port 8002)
ulimit -n 65536
.venv/bin/python -m uvicorn dashboard.main:app --port 8002 --reload

# 7. verify
curl http://127.0.0.1:8001/health
curl 'http://127.0.0.1:8001/health?deep=true'
```

You should see:
- `/health` → 200, `total_ipos > 0`.
- `/health?deep=true` → 200, every configured service `ok: true`.

---

## 4. Environment variables — every one explained

All defined in [app/config.py](app/config.py) with pydantic-settings. Missing required vars fail boot with a clear error.

### Required for full functionality

| Variable | Purpose | Where to get it |
|---|---|---|
| `DEEPSEEK_API_KEY` | LLM extraction fallback (`/parse-sections`) | <https://platform.deepseek.com> |
| `FIRECRAWL_API_KEY` | Primary LLM extraction (`/parse-firecrawl`) | <https://firecrawl.dev> |
| `CF_ACCOUNT_ID` | Cloudflare R2 account ID | Cloudflare dashboard → top right |
| `R2_ACCESS_KEY_ID` | R2 access key | R2 → Manage API Tokens |
| `R2_SECRET_ACCESS_KEY` | R2 secret | shown once at token creation |
| `R2_BUCKET` | Bucket name | the bucket you created (lowercase, no underscores) |
| `R2_PUBLIC_BASE` | Public r2.dev URL | bucket → Settings → Public Access |

### Optional (graceful degradation when blank)

| Variable | When blank | When set |
|---|---|---|
| `DATABASE_URL` | uses local SQLite `ipos.db` | uses Supabase/any Postgres |
| `INTERNAL_API_KEY` | `/api/refresh` is open | requires `X-Internal-Key` header on write endpoints |
| `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | no Telegram pings | sends ping on new IPO / parse / error |
| `GMAIL_USER` + `GMAIL_APP_PASSWORD` + `NOTIFY_RECIPIENT_EMAIL` | no emails | sends digest emails on errors |
| `PARSER_PROVIDER` | defaults to `deepseek` | `firecrawl` switches default |
| `SEBI_MAX_PAGES` | 10 | how many SEBI listing pages to scrape per doc type |

---

## 5. The data lifecycle

```
SCRAPE       /api/refresh                       writes ipo_master
   ↓
RESOLVE      /api/ipos/{id}/resolve             writes document_sections.raw_md + R2 upload
   ↓
PARSE        /api/ipos/{id}/parse-firecrawl     writes document_sections.parsed_data
   ↓
VALIDATE     (automatic tail of parse)          writes ipo_master.validation_issues, publish_status, confidence_score
   ↓
UNIFY        (automatic tail of parse)          writes ipo_master.unified_data + provenance + version
   ↓
CONSUME      /api/ipos/{id}/unified             your Next.js reads this
```

Each step is **idempotent** — safe to re-run. Hash gating on parse means re-running on unchanged content costs zero credits.

---

## 6. API reference (every endpoint, with curl)

### Reads (no auth needed)

```bash
# Liveness
curl http://127.0.0.1:8001/health

# Deep health (probes R2 + Firecrawl + DeepSeek + DB)
curl 'http://127.0.0.1:8001/health?deep=true'

# List IPOs with filters
curl 'http://127.0.0.1:8001/api/ipos?per_page=25'
curl 'http://127.0.0.1:8001/api/ipos?documents=drhp,rhp&status=listed'
curl 'http://127.0.0.1:8001/api/ipos?search=rentomojo'
curl 'http://127.0.0.1:8001/api/ipos?platform=MainBoard&year=2026'

# Single IPO
curl http://127.0.0.1:8001/api/ipos/88

# The unified contract — this is what Next.js calls
curl http://127.0.0.1:8001/api/ipos/88/unified

# Documents overview (per-doc summary + R2 URLs per section)
curl http://127.0.0.1:8001/api/ipos/88/documents

# Section-level: list, raw markdown, parsed data
curl http://127.0.0.1:8001/api/ipos/88/documents/drhp/sections
curl 'http://127.0.0.1:8001/api/ipos/88/documents/drhp/sections/CAPITAL_STRUCTURE?raw=true'
curl http://127.0.0.1:8001/api/ipos/88/documents/drhp/sections/CAPITAL_STRUCTURE/parsed

# Review queue
curl 'http://127.0.0.1:8001/api/review-queue?publish_status=needs_review'
curl 'http://127.0.0.1:8001/api/review-queue?publish_status=published'

# Recent status changes (audit)
curl 'http://127.0.0.1:8001/api/status-changes?limit=20'

# Dashboard stats
curl http://127.0.0.1:8001/api/dashboard/stats
curl 'http://127.0.0.1:8001/api/dashboard/logs?limit=20'

# Background tasks
curl http://127.0.0.1:8001/api/tasks
curl http://127.0.0.1:8001/api/tasks/<task_id>
```

### Writes (require `X-Internal-Key` header in production)

```bash
KEY='your-internal-api-key'  # leave blank locally if INTERNAL_API_KEY is unset

# Trigger a scrape
curl -X POST -H "X-Internal-Key: $KEY" \
  'http://127.0.0.1:8001/api/refresh'

# Trigger a scrape for a specific year
curl -X POST -H "X-Internal-Key: $KEY" \
  'http://127.0.0.1:8001/api/refresh?year=2026'

# Resolve PDFs → sections → R2 for one IPO
curl -X POST -H "X-Internal-Key: $KEY" \
  http://127.0.0.1:8001/api/ipos/88/resolve

# Parse via Firecrawl (preferred — grouped + hash-gated)
curl -X POST -H "X-Internal-Key: $KEY" \
  http://127.0.0.1:8001/api/ipos/88/parse-firecrawl

# Force re-parse (bypass hash gate)
curl -X POST -H "X-Internal-Key: $KEY" \
  'http://127.0.0.1:8001/api/ipos/88/parse-firecrawl?force=true'

# Parse via DeepSeek (legacy fallback)
curl -X POST -H "X-Internal-Key: $KEY" \
  http://127.0.0.1:8001/api/ipos/88/parse-sections

# Test notification setup
curl -X POST http://127.0.0.1:8001/api/internal/notify/test
```

### Source-level debug (read-only, no auth)

```bash
curl 'http://127.0.0.1:8001/api/sebi/filings?document_type=DRHP'
curl http://127.0.0.1:8001/api/bse/ipos
curl http://127.0.0.1:8001/api/nse/offer-docs
curl http://127.0.0.1:8001/api/bse-sme/drhp
```

---

## 7. Background tasks (how to poll)

Long ops (scrape, resolve, parse) return `{task_id}` immediately. Poll until `status: completed` or `failed`:

```bash
# Start a parse
RESP=$(curl -s -X POST http://127.0.0.1:8001/api/ipos/88/parse-firecrawl)
TASK_ID=$(echo $RESP | jq -r .task_id)
echo "task: $TASK_ID"

# Poll
while true; do
  STATUS=$(curl -s http://127.0.0.1:8001/api/tasks/$TASK_ID | jq -r .status)
  echo "status: $STATUS"
  [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]] && break
  sleep 5
done

# Get the final result
curl -s http://127.0.0.1:8001/api/tasks/$TASK_ID | jq .result
```

**Task survival**: tasks are persisted to `background_tasks` (same DB as everything else), so polls keep working after API restarts, container redeploys, etc.

---

## 8. Validation layer & confidence scoring

After each parse, every extracted field runs through:

### Format checks
- `cin`: must match `[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}`
- `email`: RFC-shape regex
- `face_value`: must be in `{1, 2, 5, 10}`
- `price_band`: matches `X - Y` shape
- `bid_open_date` / `bid_close_date` etc: parseable as a date

### Cross-source checks
- `company_name`: fuzzy-match against `bse_data.company_name` and `nse_data.company_name` (token-overlap ≥ 85%)
- `bid_open_date` / `bid_close_date`: within 3 days of `bse_data.start_date` / `nse_data.issue_open_date`

### Per-field score
Each scored field starts at `1.0`. Subtracts:
- `0.5` if format check fails
- `0.4` if cross-source check fails
- Empty value (string `""` or `[]`) → fixed `0.2`

### Aggregate confidence → publish_status
- `>= 0.70` → `published` (Next.js can show it)
- `>= 0.40` → `needs_review` (do not publish)
- `<  0.40` → `rejected`

Thresholds are tunable in [app/validation.py](app/validation.py).

### Inspect the issues
```bash
curl http://127.0.0.1:8001/api/ipos/88/unified | jq '{publish_status, confidence_score, validation_issues}'
```

---

## 9. The unified contract (what Next.js consumes)

`GET /api/ipos/{id}/unified` is the **only endpoint Next.js needs** for IPO detail pages.

```json
{
  "ipo_id": 88,
  "company_name": "Acme Ltd",
  "status": "drhp_filed",
  "publish_status": "published",
  "confidence_score": 0.92,
  "unified_version": 4,
  "unified_updated_at": "2026-05-30T11:44:33Z",
  "validation_issues": [],
  "data": {
    "cin": "L74999KA2009PLC048905",
    "registered_address": "...",
    "email": "ir@acme.com",
    "brlm_name": "...",
    "authorized_shares": "1,50,00,000",
    "fresh_issue_shares": "27,84,000",
    "eps_basic": "30.68",
    "roe_percent": "31.70%",
    "bid_open_date": "...",
    "promoter_names": ["...", "..."]
  },
  "provenance": {
    "cin":              {"doc_type": "drhp", "parsed_at": "...", "section_name": "GENERAL_INFORMATION", "schema_version": 1},
    "bid_open_date":    {"doc_type": "rhp",  "parsed_at": "...", "section_name": "ISSUE_PROCEDURE",     "schema_version": 1}
  }
}
```

### Rules for Next.js
1. **Only render data when `publish_status == "published"`**. Otherwise show "Coming soon" or skip.
2. **Use `unified_version` for cache busting**. When it increments, revalidate.
3. **Provenance tells you which document was the source** — useful for "as of RHP filed on X" disclaimers.
4. **DRHP → RHP merge is automatic**. When a new RHP is filed and parsed, fields from RHP overwrite the corresponding DRHP fields. Provenance updates. `unified_version` bumps.

---

## 10. Notifications (Telegram + Gmail)

Module: [app/notifications.py](app/notifications.py). Graceful degradation: if env vars are blank, sends are silent no-ops.

### Setup Telegram (2 minutes)

```
1. Telegram → @BotFather → /newbot → copy the token
2. Send any message to your bot
3. Visit: https://api.telegram.org/bot<TOKEN>/getUpdates
4. Copy the "chat":{"id": ...} number
5. Put both in .env:
     TELEGRAM_BOT_TOKEN=...
     TELEGRAM_CHAT_ID=...
```

### Setup Gmail (3 minutes)

```
1. myaccount.google.com → Security → 2-Step Verification (must be ON)
2. App passwords → create one → copy the 16-char password
3. Put in .env:
     GMAIL_USER=you@gmail.com
     GMAIL_APP_PASSWORD=...
     NOTIFY_RECIPIENT_EMAIL=you@gmail.com
```

### Verify

```bash
curl -X POST http://127.0.0.1:8001/api/internal/notify/test
# Returns per-channel {enabled, ok, error}.
```

### What gets pinged

| Event | Channel | Sample |
|---|---|---|
| New IPO scraped | Telegram | `📥 New IPO: Acme Ltd · drhp_filed` |
| Status change | Telegram | `🔁 Acme Ltd: drhp_filed → sebi_approved` |
| Scrape complete (no errors) | Telegram | `🔄 Scrape done · 3 new · 12 changes · 0 errors · 87s` |
| Scrape errors | Telegram + Gmail | same as above + traceback in email |
| Resolve found 0 sections | Telegram | `⚠️ Resolve found 0 sections · ipo=88` |
| Resolve crashed | Telegram + Gmail | with full error |
| Parse success | Telegram | `✅ Parsed · Acme · ₹1.59 · conf=0.92 · v3` |
| Parse needs_review | Telegram | `👀 Needs review · Acme · conf=0.62 · 3 issues` |
| Parse rejected | Telegram | `❌ Rejected · Acme · conf=0.28` |
| Parse failed | Telegram + Gmail | with traceback |

---

## 11. Cloudflare R2 (section storage)

Module: [app/storage/r2.py](app/storage/r2.py).

### Naming convention (deterministic)

```
sections/{ipo_id}/{doc_type}/{SECTION_NAME}.md

→ https://pub-<bucket_id>.r2.dev/sections/88/drhp/CAPITAL_STRUCTURE.md
```

`doc_type` is one of: `drhp`, `rhp`, `fp`.

### Why R2?
- Free egress, cheap storage (~$0.015/GB/mo).
- Public URLs Firecrawl can fetch.
- Path-deterministic so the API can compute URLs without DB lookups.

### Useful operations

```bash
# Backfill all DB sections → R2 (one-shot, idempotent)
.venv/bin/python scripts/backfill_r2.py

# Just one IPO
.venv/bin/python scripts/backfill_r2.py --ipo-id 88

# Smoke test (upload → fetch → delete one test object)
.venv/bin/python scripts/test_r2.py
```

---

## 12. Firecrawl + DeepSeek (the parsers)

### Firecrawl (`/parse-firecrawl`) — recommended

- 4 calls per IPO (sections grouped into `company`, `structure`, `financial`, `issue`)
- Each call sends a hosted R2 URL + a per-group JSON schema
- Hash-gated: if `raw_md_sha256 == parsed_md_sha256`, skip the call
- Fresh parse: **~₹1.60 per IPO**
- Cached re-run: **₹0**

Module: [app/parsers/firecrawl_parser.py](app/parsers/firecrawl_parser.py). Schemas in [app/parsers/section_schemas.py](app/parsers/section_schemas.py).

### DeepSeek (`/parse-sections`) — legacy fallback

- 2 calls per IPO (one non-financial, one financial)
- Truncates input to 60K chars (lossy on long DRHPs)
- ~₹2.50 per IPO
- Kept for A/B comparison

Module: [app/section_parser.py](app/section_parser.py).

### Switching default

`PARSER_PROVIDER=firecrawl` in `.env` (already set). Doesn't affect endpoints — they're explicit.

---

## 13. Cost guide

### Per-IPO cost breakdown

| Activity | Cost |
|---|---|
| Scrape (HTTPS calls to SEBI/BSE/NSE/SME) | ₹0 |
| Resolve (PDF download + section split) | ₹0 |
| Parse via Firecrawl (fresh, 4 calls) | ~₹1.60 |
| Parse via Firecrawl (cached, hash-gated) | ₹0 |
| Parse via DeepSeek (legacy) | ~₹2.50 |
| R2 storage (~50 KB markdown × 7 sections = 350 KB / IPO) | ~₹0.0005/mo |

### Monthly infra cost (Fly.io)

| Item | Cost |
|---|---|
| Fly.io machine (1 CPU, 512 MB, always-on) | ~$3.89/mo |
| Supabase Postgres (free tier, 500 MB) | $0 |
| R2 (free tier, 10 GB + 10M ops) | $0 |
| **Total infra** | **~$4/mo** |

Per LLM call cost (Firecrawl/DeepSeek) is on top — depends on how many new IPOs hit each month. Indian markets typically see 5–20 new IPOs per month → **₹5–30/month of LLM spend**.

---

## 14. Database (SQLite ↔ Supabase Postgres)

### Selection rule

```
DATABASE_URL set?
    yes → uses Postgres (Supabase)
    no  → falls back to local ipos.db
```

The same SQLAlchemy ORM code works against both. Switch by changing one env var.

### Migrations

```bash
# After editing app/db_models.py:
.venv/bin/alembic revision --autogenerate -m "describe the change"

# Apply (against whichever DB DATABASE_URL points at)
.venv/bin/alembic upgrade head

# Roll back one step
.venv/bin/alembic downgrade -1
```

### Switching SQLite → Supabase (one-time)

```bash
# 1. Create Supabase project, copy Database URL (Settings → Database → Connection string)
# 2. Set DATABASE_URL in .env (URL-encode any `@` in the password as `%40`)

# 3. Create the schema
.venv/bin/alembic upgrade head

# 4. Copy data over (1,311 IPOs etc.)
.venv/bin/python scripts/copy_to_postgres.py

# 5. Restart. App now reads/writes Postgres.
ulimit -n 65536
.venv/bin/python -m uvicorn app.main:app --port 8001 --reload
```

### Rolling back to SQLite

Blank out `DATABASE_URL` in `.env` and restart. The app falls back to `ipos.db` (untouched).

---

## 15. Schema versioning (adding a new section later)

### Adding a new section

1. **Edit [app/parsers/section_schemas.py](app/parsers/section_schemas.py)**:
   ```python
   SECTION_SCHEMAS["XYZ_NEW_SECTION"] = {
       "type": "object",
       "properties": {
           "new_field_1": {"type": "string", "description": "..."},
           ...
       }
   }
   ```
2. **Decide which group it joins** (or add it to its own):
   ```python
   SECTION_GROUPS["company"].append("XYZ_NEW_SECTION")
   ```
3. **Bump `SCHEMA_VERSION` in the same file**:
   ```python
   SCHEMA_VERSION = 2
   ```
4. **Optionally add to `KNOWN_SECTIONS` in [app/section_resolver.py](app/section_resolver.py)** so the PDF resolver detects the section header.
5. **Re-run resolve + parse** for IPOs you care about:
   ```bash
   curl -X POST http://127.0.0.1:8001/api/ipos/88/resolve
   curl -X POST 'http://127.0.0.1:8001/api/ipos/88/parse-firecrawl?force=true'
   ```

The new fields show up in `unified_data` automatically. Existing fields stay untouched.

### Changing an existing section's schema

Same as above — bump `SCHEMA_VERSION`. Old parsed_data still works; the next parse overwrites with the new shape.

---

## 16. Deployment

### Fly.io (recommended, ~$4/mo)

```bash
# 1. install + login
brew install flyctl
fly auth login

# 2. create the app (no deploy yet)
fly launch --no-deploy --name ipo-scraper

# 3. set every secret from .env
fly secrets set \
  DATABASE_URL="postgresql://..." \
  DEEPSEEK_API_KEY="sk-..." \
  FIRECRAWL_API_KEY="fc-..." \
  CF_ACCOUNT_ID="..." \
  R2_ACCESS_KEY_ID="..." \
  R2_SECRET_ACCESS_KEY="..." \
  R2_BUCKET="ipo" \
  R2_PUBLIC_BASE="https://pub-...r2.dev" \
  TELEGRAM_BOT_TOKEN="..." \
  TELEGRAM_CHAT_ID="..." \
  INTERNAL_API_KEY="$(openssl rand -hex 32)"

# 4. deploy
fly deploy

# 5. verify
fly status
curl https://ipo-scraper.fly.dev/health
curl 'https://ipo-scraper.fly.dev/health?deep=true'
```

Config: [fly.toml](fly.toml). Image: [Dockerfile](Dockerfile) (570 MB, 80 MB RAM idle).

### Railway

```bash
railway login
railway init                       # link to GitHub
railway variables --set "DATABASE_URL=..." --set "DEEPSEEK_API_KEY=..." ...
railway up
```

Config: [railway.toml](railway.toml). Pricing: $5-10/mo (Hobby plan + usage).

### Cron via GitHub Actions

Set repo secrets in GitHub → Settings → Secrets and variables → Actions:
- `IPO_API_URL` = `https://ipo-scraper.fly.dev`
- `INTERNAL_API_KEY` = same value as the server's env var

The workflow at [.github/workflows/cron-scrape.yml](.github/workflows/cron-scrape.yml) runs every 6 hours. Manually trigger from the Actions tab.

---

## 17. Common operations cheat sheet

### "I just deployed — verify everything"
```bash
curl https://your-host/health?deep=true | jq
# All channels should show ok:true.
curl -X POST -H "X-Internal-Key: $KEY" https://your-host/api/internal/notify/test | jq
# Expect Telegram + Gmail pings.
```

### "Re-parse one IPO from scratch"
```bash
curl -X POST -H "X-Internal-Key: $KEY" \
  'https://your-host/api/ipos/88/parse-firecrawl?force=true'
```

### "Show me everything that needs review"
```bash
curl 'https://your-host/api/review-queue?publish_status=needs_review' | jq
```

### "Trigger a full scrape now (don't wait for cron)"
```bash
curl -X POST -H "X-Internal-Key: $KEY" https://your-host/api/refresh
```

Or in GitHub: Actions tab → "Cron scrape" → Run workflow.

### "I added a new field — re-parse a few IPOs to backfill"
```bash
# Bump SCHEMA_VERSION, restart, then:
for id in 88 1310 1311; do
  curl -X POST -H "X-Internal-Key: $KEY" \
    "https://your-host/api/ipos/$id/parse-firecrawl?force=true"
done
```

### "What's the latest parse cost?"
```bash
curl https://your-host/api/tasks?limit=20 | \
  jq '.tasks[] | select(.type=="parse_firecrawl") | {id, status, cost_inr: .result.cost_inr, conf: .result.confidence_score}'
```

### "Export all published IPOs as one JSON for Next.js seed"
```bash
curl 'https://your-host/api/review-queue?publish_status=published&limit=200' | \
  jq '.ipos[] | .ipo_id' | xargs -I{} curl -s "https://your-host/api/ipos/{}/unified" > all_published.jsonl
```

---

## 18. Troubleshooting

### "macOS: imports hang forever on startup"
You forgot `ulimit -n 65536` before uvicorn. PyMuPDF needs lots of file descriptors during import.

### "psycopg.errors.DuplicatePreparedStatement: _pg3_0 already exists"
You're on a Supabase pooler that strips prepared statements. Already handled — [app/db_models.py](app/db_models.py) sets `prepare_threshold=None` for Postgres connections. If you see this, you're on an old build.

### "Password authentication failed for user 'postgres'"
- Check the `@` in your DB password is URL-encoded as `%40`. e.g. `Shifts323212@@` → `Shifts323212%40%40`.
- Use the **Session pooler** URL (port 5432) for Alembic if `pgbouncer=true` flag causes issues.

### "/api/refresh returns 401"
`INTERNAL_API_KEY` is set on the server. Send `X-Internal-Key: <key>` header.

### "Firecrawl returns success but data is empty"
- Check the R2 URL is publicly accessible: `curl https://pub-xxx.r2.dev/sections/88/drhp/CAPITAL_STRUCTURE.md` — should return markdown, not 403.
- Verify the section had enough content: `curl 'https://your-host/api/ipos/88/documents/drhp/sections'` and check `char_count > 500`.

### "Container OOM-kills during resolve"
Bump Fly machine memory from 512 MB to 1024 MB. Edit [fly.toml](fly.toml) → `memory = "1024mb"` → `fly deploy`.

### "I want to see what was actually sent to Firecrawl"
Inspect `document_sections.parsed_data._source_url` — that's the R2 URL Firecrawl scraped. Fetch it directly to see the input.

### "Cron didn't fire"
- GH Actions: Actions tab → Cron scrape → check if it ran. Look at the workflow logs.
- Manual trigger: Run workflow button.
- Make sure `IPO_API_URL` and `INTERNAL_API_KEY` are set in repo secrets.

### "Memory keeps growing over time"
The task store keeps the last 200 tasks. If you suspect a leak, restart the machine (`fly machine restart`). Check [app/task_manager.py](app/task_manager.py) for the prune logic.

---

## Reading further

- [README.md](README.md) — short overview + endpoint matrix
- [PARSING_ARCHITECTURE.md](PARSING_ARCHITECTURE.md) — the original parsing-cost analysis & design rationale
- [PRODUCTION_VISION.md](PRODUCTION_VISION.md) — the long-term roadmap & "is this deployable" check
- [.env.example](.env.example) — every env var, documented

---

**One screen, no hidden config:** if you've read this file end-to-end, you can operate, debug, and deploy this system. Everything else lives in the code.
