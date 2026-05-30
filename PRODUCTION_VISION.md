# Production Vision — IPOScraper

> The goal: a system you can deploy on Friday and trust by Monday.
> Cron runs in background, your Next.js site gets webhooks, you see notifications when anything changes, and the data stays clean even as IPOs evolve from DRHP → RHP → Listed.

---

## 1. The target architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ TRIGGER                                                               │
│  • Fly.io / Railway cron, every 6h:  POST /internal/run-cycle        │
│  • Manual: same endpoint                                              │
└────────────────────────────┬─────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 1 — SCRAPE                                                      │
│   SEBI + BSE + NSE + SME → diff against DB                           │
│                                                                       │
│   For each IPO:                                                       │
│     ─ if NEW            → enqueue resolve+parse                       │
│     ─ if NEW DOC (RHP)  → enqueue resolve+parse                       │
│     ─ if STATUS CHANGED → record + enqueue webhook                    │
│     ─ if UNCHANGED      → skip (cheap)                                │
└────────────────────────────┬─────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 2 — RESOLVE (only triggered IPOs)                               │
│   Download PDF (HEAD first; skip if same ETag as last time)          │
│   Split into sections → upload to R2                                  │
│   Diff section content hashes vs previous run                         │
│     ─ unchanged sections → skip parse                                 │
│     ─ changed sections   → enqueue parse                              │
└────────────────────────────┬─────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 3 — PARSE (only changed sections)                               │
│   Group sections by domain (3 calls, not 7)                          │
│   Firecrawl extract per group with combined schema                    │
│   Merge into ipo_master.unified_data (jsonb)                          │
│   Bump unified_version, record provenance                             │
│   Compute diff vs previous unified_data                               │
└────────────────────────────┬─────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STAGE 4 — FAN-OUT                                                     │
│                                                                       │
│   ┌─── WEBHOOK to Next.js ──► HMAC-signed POST with diff + unified   │
│   │     /api/internal/ipo-event                                       │
│   │     retries on 4xx/5xx with exponential backoff                  │
│   │                                                                   │
│   ├─── NOTIFICATION ────────► Discord/Slack webhook                  │
│   │     "New RHP filed for Acme Ltd ✅ parsed, 3 fields changed"     │
│   │                                                                   │
│   └─── EVENT LOG ───────────► event_log table (for replay/debug)     │
└──────────────────────────────────────────────────────────────────────┘
```

This is one cohesive loop. Every stage has a clear input, output, and idempotency rule.

---

## 2. Addressing each concern

### 2.1 Firecrawl cost: 35 credits → ~10 credits per IPO

**Problem**: 7 sections × 5 credits = 35 credits per fresh IPO.

**Three stacked optimizations** (cumulative):

1. **Group sections by domain → 3-4 calls instead of 7.** Sections that share fields are merged. Example:

   ```python
   SECTION_GROUPS = {
       "company": ["GENERAL_INFORMATION", "OUR_PROMOTERS_AND_PROMOTER_GROUP"],
       "structure": ["CAPITAL_STRUCTURE", "OBJECTS_OF_THE_OFFER"],
       "financial": ["RESTATED_FINANCIAL_STATEMENTS", "BASIS_FOR_OFFER_PRICE"],
       "issue": ["ISSUE_PROCEDURE"],
   }
   ```

   Each group → one Firecrawl call with a combined schema. Each section's R2 markdown is concatenated. **4 calls × 5 credits = 20 credits.**

2. **Content-hash gating** — never re-parse a section whose markdown hasn't changed:

   ```
   document_sections.raw_md_sha256  (new column)
   document_sections.parsed_md_sha256 (records hash at parse time)
   ```

   Before calling Firecrawl, compare `current_hash == parsed_md_sha256`. If equal, skip. **Re-runs cost ~0 credits when nothing changed.**

3. **Skip empty sections** — sections with `char_count < 500` rarely yield data anyway. Drop them before calling.

**End result**: First parse ~20 credits, all subsequent runs ~0–5 credits unless content actually changed.

### 2.2 DRHP → RHP update flow (the systematic JSON question)

This is the most important architectural fix.

**New table** (not a schema "redesign" — additive):

```sql
ALTER TABLE ipo_master ADD COLUMN unified_data JSON;
ALTER TABLE ipo_master ADD COLUMN unified_version INTEGER DEFAULT 0;
ALTER TABLE ipo_master ADD COLUMN unified_updated_at DATETIME;
ALTER TABLE ipo_master ADD COLUMN unified_provenance JSON;  -- per-field source map
```

**`unified_data` is the contract you ship to Next.js.** Always the same shape:

```json
{
  "cin": "U74999KA2009PLC048905",
  "company_name": "Spectraa Technology Solutions Limited",
  "registered_address": "17/7, Ali Asker Road, ...",
  "capital_structure": { "authorized_shares": "1,50,00,000", ... },
  "financials": { "financial_years": [...], "total_revenue": "..." },
  "issue": { "bid_open_date": "...", "bid_close_date": "...", ... }
}
```

**`unified_provenance` says where each field came from.** Crucial for "DRHP says X, RHP says Y" cases:

```json
{
  "cin": { "doc_type": "drhp", "parsed_at": "2026-05-30T...", "schema_version": 1 },
  "bid_open_date": { "doc_type": "rhp", "parsed_at": "2026-05-31T...", "schema_version": 1 }
}
```

**Update rule** (deterministic, no surprises):
- For each field: take the value from the **most recent document type** that has a non-empty value. Preference order: `FP > RHP > DRHP`.
- If RHP arrives and updates `bid_open_date`, `unified_data.bid_open_date` updates; `provenance.bid_open_date.doc_type` becomes `"rhp"`.
- Diff between old and new `unified_data` → that's the webhook payload.

**Why this works for Next.js**:
- Your Next.js calls `GET /api/ipos/{id}/unified` → always gets the same JSON shape.
- Webhook payload includes `{ before, after, changed_fields }` so the frontend knows what to invalidate.

### 2.3 Schema evolution — adding a new section "xyz" later

**The flow**:

1. You add `"xyz": {...schema...}` to `app/parsers/section_schemas.py`. Bump `SCHEMA_VERSION = 2`.
2. The next scrape sees IPOs whose `unified_data` was built under schema_version=1 → flags them for re-parse.
3. Re-parse only the `xyz` section for those IPOs (sections already at v2 are skipped).
4. Webhook fires with `changed_fields: ["xyz_field_1", "xyz_field_2"]`.

**No data loss, no manual migration.** Add `schema_version` next to each section's parsed_data:

```python
document_sections.parsed_data = {
    "<extracted fields>": "...",
    "_schema_version": 2,
    "_provider": "firecrawl",
    "_parsed_at": "...",
}
```

When `SCHEMA_VERSION` > `parsed_data._schema_version`, re-parse.

### 2.4 DB size — keep only "live" data + R2 as source of truth

**Today**: `document_sections.raw_md` stores the full section text in SQLite — same text that's also in R2.

**Tomorrow**:

```
SQLite (lean, ~50MB max):
  ipo_master              (1,300 rows × ~5KB each = 6MB)
  ipo_status_history      (audit trail, prune > 1 year)
  document_sections       (metadata only — drop raw_md, keep page_start/end + parsed_data)
  background_tasks        (auto-pruned to last 100)
  event_log               (for webhook replay, keep 90 days)

R2 (heavy, cheap, no row limit):
  sections/{ipo_id}/{doc}/{section}.md   ← single source of truth for raw text
  (we already do this)
```

**Retention rule**: don't delete IPOs (small + valuable for context). Do:
- Drop `document_sections.raw_md` column entirely → fetch from R2 when needed
- Prune `ipo_status_history` rows older than 1 year
- Prune `background_tasks` to last 100 (already done)
- For `IPOMaster` rows that are `listed` for > 6 months: skip in scrape (no further updates expected) — saves API calls AND DB writes

**Expected size after cleanup: < 50 MB.** Fits in Railway's free tier or Fly's smallest volume.

### 2.5 Deployment — the actual answer

Given your stack (FastAPI + SQLite + R2 + cron):

**Recommended: Fly.io.**

| Why | Detail |
|---|---|
| Persistent volume | SQLite lives on a 1GB volume ($0.15/mo). |
| Built-in cron | `[[machines.crons]]` runs your scrape every 6h. |
| WebSocket-friendly | If you ever add live progress. |
| One config file | `fly.toml` — no Kubernetes nonsense. |
| Cheap | ~$5/mo for a 1-CPU shared instance + tiny DB volume. |
| Auto-restart on crash | Same supervisor that Heroku has. |

**Alternative if you prefer managed DB**: Railway + Supabase Postgres free tier (500MB). Cleaner DB management, but DB lives elsewhere from app → small latency cost.

**Not recommended**: bare VPS (DigitalOcean/Hetzner) — too much sysadmin work for one app.

**Skip Docker initially.** Fly's `flyctl deploy` does the right thing from a `Dockerfile` it generates for you. We'll add a 12-line `Dockerfile` when we deploy.

### 2.6 Webhooks — the missing piece

**New tables**:

```sql
CREATE TABLE webhook_subscriptions (
    id              INTEGER PRIMARY KEY,
    url             TEXT NOT NULL,
    secret          TEXT NOT NULL,           -- HMAC signing key
    events          TEXT NOT NULL,           -- JSON array
    active          INTEGER DEFAULT 1,
    created_at      DATETIME,
    last_delivery_at DATETIME,
    last_delivery_status TEXT
);

CREATE TABLE webhook_deliveries (
    id              INTEGER PRIMARY KEY,
    subscription_id INTEGER REFERENCES webhook_subscriptions(id),
    event_type      TEXT NOT NULL,
    payload         JSON NOT NULL,
    status          TEXT,                    -- pending|success|failed
    attempts        INTEGER DEFAULT 0,
    next_retry_at   DATETIME,
    response_code   INTEGER,
    response_body   TEXT,                    -- first 500 chars
    created_at      DATETIME,
    completed_at    DATETIME
);
```

**Event types**:

| Event | Fired when |
|---|---|
| `ipo.created` | First-time scrape inserts a new IPO |
| `ipo.status_changed` | Lifecycle stage advances (drhp_filed → sebi_approved, etc.) |
| `ipo.document_added` | New URL appears (e.g. RHP appears on an existing DRHP record) |
| `ipo.parsed` | unified_data updated (with diff) |
| `ipo.parse_failed` | Parse threw — for ops, not Next.js |

**Payload** (HMAC-signed in `X-Signature` header):

```json
{
  "event": "ipo.parsed",
  "ipo_id": 88,
  "delivered_at": "2026-05-30T11:00:00Z",
  "data": {
    "company_name": "Acme Ltd",
    "unified_version": 5,
    "unified": { ...full snapshot... },
    "changed_fields": ["bid_open_date", "bid_close_date", "post_issue_shares"]
  }
}
```

**Delivery worker**: a background loop pulls from `webhook_deliveries WHERE status='pending'`, POSTs, retries with exponential backoff (1m, 5m, 30m, 2h, 12h), gives up after 5 attempts.

**Next.js side**: one `POST /api/internal/ipo-event` handler that verifies HMAC, then upserts to its own DB (or revalidates ISR cache).

### 2.7 Notifications — Discord/Slack so you can sleep

A **third** webhook channel, but for humans:

```python
# .env
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/.../...
# or
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
```

Fired on:

- New IPO detected → `📥 New IPO: Acme Ltd (drhp_filed)` with link
- Parse complete → `✅ Parsed Acme Ltd · ₹0.32 · 3 fields changed`
- Parse failed → `🚨 Parse failed Acme Ltd: section detection found 0 sections`
- Daily summary at 9am → `Daily: 3 new IPOs, 12 status changes, 1 error`

One small module `app/notifications.py` with `notify_discord(message, level)` — fire-and-forget HTTP POST, never blocks.

### 2.8 Code quality cleanup

**Current API surface** (from your earlier scan): 20 routes. Several do near-identical things.

**Consolidation plan**:

| Today | Tomorrow |
|---|---|
| `/api/ipos/{id}/parse-sections` + `/parse-firecrawl` | `/api/ipos/{id}/parse?provider=firecrawl\|deepseek` |
| `/api/dashboard/stats` + `/api/dashboard/logs` | dashboard reads DB directly (already does), drop these |
| `/api/sebi/*` + `/api/bse/*` + `/api/nse/*` debug | move to `/internal/debug/*` (require API key) |
| `/api/ipos/{id}/parsed-all` | `/api/ipos/{id}/unified` (renamed, fed from `unified_data` column directly — no merge at read-time) |

**End state**: ~12 endpoints instead of 20. Cleaner mental model.

**Env vars consolidation**: settle on **10 vars max** in `.env`. Group: app (1), DeepSeek (1), Firecrawl (1), R2 (5), webhook (2 — secret + Next.js URL), notifications (1 — Discord URL).

---

## 3. The proposed roadmap

| Phase | Output | Effort |
|---|---|---|
| **A. Confidence layer (build before anything else)** | event_log table, every state change recorded; replay endpoint | 1 day |
| **B. unified_data + provenance** | Single source of truth on `ipo_master`; `/api/ipos/{id}/unified` reads it directly | 1.5 days |
| **C. Webhooks** | `webhook_subscriptions` + `webhook_deliveries` + delivery worker; `POST /api/webhooks` to register | 1 day |
| **D. Discord/Slack notifications** | `app/notifications.py` + hooks at scrape/resolve/parse | 0.5 day |
| **E. Cost optimization** | Section groups (4 calls), content-hash gating, skip-empty | 0.5 day |
| **F. DB diet** | Drop `raw_md` column, rely on R2; prune status_history > 1y | 0.5 day |
| **G. API consolidation** | Merge parse endpoints, move debug under /internal | 0.5 day |
| **H. Deploy to Fly.io** | `Dockerfile`, `fly.toml`, persistent volume, cron schedule, secrets | 1 day |
| **I. Next.js consumer** | Sample `/api/internal/ipo-event` handler that verifies HMAC and writes to Supabase/Postgres | 0.5 day |

**Total: ~7 days.** Build A→D first (confidence), then E→F (efficiency), then G→I (deploy).

---

## 4. What changes in your day-to-day after this is done

**Today**:
- You manually trigger `/api/refresh` and stare at logs.
- New IPOs appear in DB but nothing tells you.
- DRHP→RHP updates require manual re-parse.
- You have no idea how to deploy this.
- DB grows forever.

**After phases A–I**:
- Fly cron runs every 6h. You forget it exists.
- Discord pings: "📥 New IPO: Acme Ltd · ₹0.30 to parse?" with a button (eventually).
- Your Next.js auto-updates within 30s of any parse.
- DB stays under 50MB indefinitely.
- Every state change is in `event_log` — full audit, replayable.
- One API key to revoke if someone leaks it.

---

## 5. Open questions for you

1. **Deployment target** — Fly.io (my recommendation) or Railway (your earlier mention)?
2. **DB choice** — keep SQLite + R2 (simplest) or migrate to Supabase Postgres for a managed-DB feel?
3. **Notifications channel** — Discord, Slack, both?
4. **Next.js DB** — does Next.js have its own DB already (Supabase/Postgres)? That affects webhook handler design.
5. **IPOs to monitor** — all 1,311 forever, or just "active" (`status != listed` OR `last_updated < 6 months ago`)?
6. **What scares you most right now** — schema evolution, cost, deploy, or webhooks? Pick one and we start there.

---

## 6. The honest gap assessment

| Area | State | Risk |
|---|---|---|
| Backend logic | ✅ solid after prod-readiness pass | low |
| Data model | ⚠️ works but DRHP→RHP merging is implicit | medium — webhook quality depends on this |
| Deployment | ❌ never done | high — you can't ship without |
| Webhooks | ❌ doesn't exist | high — your Next.js can't react |
| Notifications | ❌ doesn't exist | medium — you'll fly blind |
| Cost | ⚠️ ~₹3/IPO is fine but optimizable | low |
| Observability | ⚠️ structured logs but no metrics | medium |
| Schema evolution | ⚠️ no version tracking | medium — bites you in 2 months |

The "huge gap" you're feeling is real, but it's **6 specific gaps**, not "everything's broken." Tackle them one phase at a time and you'll feel the system tighten with each.
