# IPO Parsing Architecture — Audit & Improvement Plan

> Goal: extract structured fields (CIN, capital structure, financials, dates, …) from IPO prospectuses (DRHP / RHP / FP) **cheaply, reliably, and at scale**, then expose them via the API/dashboard.

---

## 1. TL;DR

| | Today | Proposed |
|---|---|---|
| **Sections detected** | Regex over every page → noisy, misses ~30% | PDF outline first → regex fallback |
| **Parse strategy** | 2 giant DeepSeek calls (60K chars each, truncated) | 1 call **per section** with tiny targeted schema |
| **Cost / IPO** | ~₹4 (full PDF) or ~₹2.7 (current 2-call) | **~₹0.20–0.50** (only sections that matter) |
| **Provider** | DeepSeek directly | **Firecrawl** `/scrape` with JSON schema (you already pay for it) |
| **Section content delivery** | In-memory text | **Public URL per section** so Firecrawl can fetch it |
| **Failure mode today** | "Section not fetched properly", truncated text, missing fields | Per-section call is small → no truncation; reliable |

The single biggest unlock: **stop merging sections, stop truncating, parse one section at a time against a tiny schema**. That alone fixes the reliability *and* the cost.

---

## 2. Current Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                          SCRAPE STAGE                            │
│                                                                  │
│   SEBI ┐                                                         │
│   BSE  ├──► scraper_service.run_full_scrape  ──► ipo_master      │
│   NSE  │   (POST /api/refresh, background)        + status_hist  │
│   SME  ┘                                                         │
└─────────────────────────────────────────────────────────────────┘
                          │
                          │  ipo_master has drhp_url / rhp_url / final_prospectus_url
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                         RESOLVE STAGE                            │
│                                                                  │
│   POST /api/ipos/{id}/resolve  (background)                      │
│            │                                                     │
│   section_resolver.resolve_document():                           │
│      1. download PDF (or unzip)                                  │
│      2. for every page: regex-scan for KNOWN_SECTIONS  ◄ NOISY  │
│      3. dedup by last occurrence                                 │
│      4. page_end = next_section.page - 1               ◄ NAIVE  │
│      5. pymupdf.get_text("text") per page              ◄ NO MD  │
│      6. save raw text → document_sections.raw_md                 │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                          PARSE STAGE                             │
│                                                                  │
│   POST /api/ipos/{id}/parse-sections  (background)               │
│            │                                                     │
│   section_parser.parse_all_sections():                           │
│      ─ collect ALL section raw_md from DB                        │
│      ─ bucket = non_financial / financial                        │
│      ─ merge all non-fin sections into ONE blob                  │
│      ─ TRUNCATE TO 60K CHARS  ◄ DATA LOSS                       │
│      ─ POST to DeepSeek with ALL_FIELDS schema                   │
│      ─ same again for financial bucket                           │
│      = 2 calls per IPO                                           │
│      ─ result saved to ALL sections' parsed_data (copies!)       │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼
   GET /api/ipos/{id}/parsed-all  ← returns FIRST parsed section, not unified
```

---

## 3. What's broken today (concrete)

| # | File / line | Issue | Impact |
|---|---|---|---|
| 1 | [section_resolver.py:90-118](app/section_resolver.py#L90-L118) | Regex scans every page; misses headers that share a line with page numbers or "Continued"; depends on `KNOWN_SECTIONS` list staying complete | ~30% of sections missing or misnamed |
| 2 | [section_resolver.py:121-129](app/section_resolver.py#L121-L129) | `page_end = next_section.page - 1` blindly; if section ordering doesn't match the PDF, ranges go wild | Wrong text saved as section content |
| 3 | [section_resolver.py:163](app/section_resolver.py#L163) | `pdf_doc[pn].get_text('text')` — plain text, no markdown, tables flattened | Numbers/tables hard to parse |
| 4 | [section_parser.py:80](app/section_parser.py#L80) | `text[:60000]` hard truncation | Anything past 60K chars is invisible to the LLM |
| 5 | [section_parser.py:167-186](app/section_parser.py#L167-L186) | 2 giant calls, all sections merged | One bad section pollutes the whole call; schema is huge so LLM hallucinates fewer fields correctly |
| 6 | [section_parser.py:200-208](app/section_parser.py#L200-L208) | Same `result_data` saved into every section row | Wastes DB; "this section's parsed_data" is misleading |
| 7 | [section_parser.py:83](app/section_parser.py#L83) | `model: "deepseek-v4-flash"` — confirm this is current; the older code used `"deepseek-chat"` | May silently fail / fallback |
| 8 | [main.py:165-176](app/main.py#L165-L176) | `/parsed-all` returns first non-empty parsed section, not all of them | Endpoint name lies |
| 9 | [main.py:152](app/main.py#L152) | `markdown: md[:500]` truncates to 500 chars in the API response unless `?raw=true` | Hard to verify resolve output |
| 10 | Task manager is in-memory ([task_manager.py:21](app/task_manager.py#L21)) | Restart API → all running task IDs disappear | Browser polls 404 after restart |

---

## 4. Cost math — why your ₹4 vs ₹0.20 intuition is right

### DeepSeek pricing
- Input  : $0.28 / 1M tokens
- Output : $1.10 / 1M tokens
- Exchange (rough): $1 ≈ ₹96

### Scenario A — current full-PDF parse
```
DRHP text size           ≈ 800,000 chars   (200K tokens)
Input cost               = 200,000 × 0.28/1M × ₹96 ≈ ₹5.4
Output (8K tokens)       = 8,000   × 1.10/1M × ₹96 ≈ ₹0.85
Per-IPO total           ≈ ₹6
```

### Scenario B — current 2-call parse (today's code)
```
2 calls × 60K chars truncated = 120K chars total (30K tokens input)
Input cost               = 30,000  × 0.28/1M × ₹96 ≈ ₹0.81
Output (16K total)       = 16,000  × 1.10/1M × ₹96 ≈ ₹1.7
Per-IPO total           ≈ ₹2.50
```
→ But: **60K char truncation is silently dropping data**. Cheap but lossy.

### Scenario C — per-section, targeted (proposed)
```
Only 5-7 sections matter for the fields you actually want:
 - GENERAL_INFORMATION       (~3K chars  →  ~750 tokens)
 - CAPITAL_STRUCTURE         (~5K chars  → ~1250 tokens)
 - OBJECTS_OF_THE_OFFER      (~4K chars  → ~1000 tokens)
 - BASIS_FOR_OFFER_PRICE     (~3K chars  →  ~750 tokens)
 - RESTATED_FINANCIALS       (~6K chars  → ~1500 tokens)
 - ISSUE_PROCEDURE           (~2K chars  →  ~500 tokens)
 - OUR_PROMOTERS             (~2K chars  →  ~500 tokens)

Sum input  ≈ 6,250 tokens
Each call returns 5-10 fields → ~200 tokens output × 7 calls = 1,400 tokens
Input cost  = 6,250  × 0.28/1M × ₹96 ≈ ₹0.17
Output cost = 1,400  × 1.10/1M × ₹96 ≈ ₹0.15
Per-IPO total                       ≈ ₹0.32
```
→ **~10x cheaper than full PDF, and the schema per call is small enough that the LLM is dramatically more accurate.**

---

## 5. Proposed Architecture v2

```
┌─────────────────────────────────────────────────────────────────┐
│                         SCRAPE STAGE                             │
│                  (unchanged — already works)                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  RESOLVE STAGE v2                                │
│                                                                  │
│  resolve_document():                                             │
│    1. download PDF                                               │
│    2. PRIMARY: read PDF outline (pymupdf doc.get_toc())          │
│       ─ SEBI/BSE filings almost always have a real outline      │
│       ─ gives section_name + page_start directly, no regex      │
│    3. FALLBACK: regex scan only if outline is empty              │
│    4. extract per section as markdown (pymupdf4llm.to_markdown)  │
│    5. SAVE to disk: dl/sections/{ipo}/{doc_type}/{name}.md       │
│    6. SAVE to DB:   document_sections.raw_md + .file_path        │
│    7. EXPOSE URL:   GET /public/sections/{ipo}/{doc_type}/{name} │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  PARSE STAGE v2  (per-section, targeted)         │
│                                                                  │
│   POST /api/ipos/{id}/parse-sections                             │
│            │                                                     │
│   parse_all_sections():                                          │
│      for each section that's interesting:                        │
│          ┌────────────────────────────────────────┐              │
│          │  section.url  +  per-section schema    │              │
│          │           │                            │              │
│          │           ▼                            │              │
│          │   Firecrawl  /scrape  (formats=json)   │              │
│          │           │                            │              │
│          │           ▼                            │              │
│          │   structured JSON (5-10 fields)        │              │
│          └────────────────────────────────────────┘              │
│      merge all into one unified IPO record                       │
│      save to document_sections.parsed_data (THIS section only)   │
│      ALSO save merged result to ipo_master.unified_data          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
   GET /api/ipos/{id}/unified  ← actually returns unified merged data
   GET /api/ipos/{id}/documents/{doc}/sections/{name}/parsed
```

### Per-section schemas (small + targeted)

```python
SECTION_SCHEMAS = {
    "GENERAL_INFORMATION": {
        "type": "object",
        "properties": {
            "cin": {"type": "string"},
            "registered_address": {"type": "string"},
            "email": {"type": "string"},
            "telephone": {"type": "string"},
            "website": {"type": "string"},
            "company_secretary_name": {"type": "string"},
            "cfo_name": {"type": "string"},
            "statutory_auditor": {"type": "string"},
            "legal_advisor": {"type": "string"},
            "registrar_name": {"type": "string"},
            "brlm_name": {"type": "string"},
        }
    },
    "CAPITAL_STRUCTURE": {
        "type": "object",
        "properties": {
            "authorized_shares": {"type": "string"},
            "authorized_amount": {"type": "string"},
            "paid_up_shares": {"type": "string"},
            "paid_up_amount": {"type": "string"},
            "face_value": {"type": "string"},
            "fresh_issue_shares": {"type": "string"},
            "offer_for_sale_shares": {"type": "string"},
            "pre_issue_shares": {"type": "string"},
            "post_issue_shares": {"type": "string"},
            "qib_shares": {"type": "string"},
            "nii_shares": {"type": "string"},
            "retail_shares": {"type": "string"},
        }
    },
    "RESTATED_FINANCIAL_STATEMENTS": {
        "type": "object",
        "properties": {
            "financial_years": {"type": "array", "items": {"type": "string"}},
            "total_revenue": {"type": "string"},
            "total_income": {"type": "string"},
            "profit_after_tax": {"type": "string"},
            "ebitda": {"type": "string"},
            "total_assets": {"type": "string"},
            "net_worth": {"type": "string"},
            "total_borrowings": {"type": "string"},
        }
    },
    # ... one per section we care about
}
```

Each call only knows about ~10 fields — the LLM doesn't get distracted by 60 unrelated fields, hallucination drops sharply.

---

## 6. Firecrawl integration

### Why Firecrawl?
1. You already pay for it (sunk cost)
2. `/scrape` with `formats: ["json"]` + a JSON schema does the LLM extraction for you, with retries, validation, and better PDF handling
3. Their LLM extraction has the schema baked in — no JSON-parsing failures like the current `json.loads(content)` does

### What Firecrawl needs
- A **public URL** that returns the content (markdown / HTML / PDF)
- A **JSON schema** describing the fields you want

### The integration

```python
import httpx

FIRECRAWL_BASE = "https://api.firecrawl.dev/v1"

async def firecrawl_extract(section_url: str, schema: dict, api_key: str) -> dict:
    """Send a section URL to Firecrawl, get structured JSON back."""
    payload = {
        "url": section_url,
        "formats": ["json"],
        "jsonOptions": {
            "schema": schema,
            "prompt": "Extract the fields from this IPO prospectus section. "
                     "Use empty string for missing values, never null."
        },
        "onlyMainContent": True,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(f"{FIRECRAWL_BASE}/scrape", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()["data"]["json"]
```

### Flow per IPO
```
for each section in sections_we_care_about:
    url    = f"https://your-host.com/public/sections/{ipo_id}/{doc_type}/{name}.md"
    schema = SECTION_SCHEMAS[section_name]
    fields = await firecrawl_extract(url, schema, FIRECRAWL_API_KEY)
    db.save_section_parsed(ipo_id, doc_type, name, fields)
unified = merge_all_sections(...)
db.save_unified_ipo_data(ipo_id, unified)
```

---

## 7. URL hosting — the missing piece

Firecrawl runs in their cloud. It can't reach `http://127.0.0.1:8001`. You need a publicly reachable URL per section.

### Option A — Cloudflare Tunnel (fastest, free, dev-friendly) ⭐ recommended for now
```bash
# one-time
brew install cloudflared
cloudflared tunnel login

# every dev session
cloudflared tunnel --url http://localhost:8001
# prints:  https://random-words-1234.trycloudflare.com
```
- Free, no signup needed for "quick tunnels"
- Restart-friendly (same URL with named tunnels)
- Add `PUBLIC_BASE_URL=https://...` to .env, parser reads it

### Option B — Cloudflare R2 / AWS S3 (cheapest at scale)
- Upload `.md` files at resolve time
- Public bucket → `https://your-bucket.r2.dev/sections/{ipo}/{doc}/{name}.md`
- Costs ~$0.015/GB/month storage, $0 egress on R2

### Option C — deploy the API publicly (Railway / Render / Fly.io)
- Move FastAPI to a real host
- Add `GET /public/sections/...` endpoint that streams from DB or disk
- Easiest for read-after-write consistency

### Option D — ngrok (dev only)
```bash
ngrok http 8001
```
- Works, but URL changes each restart unless you have a paid plan

### Comparison
| Option | Setup | Latency | Cost | Production-fit |
|---|---|---|---|---|
| **Cloudflare Tunnel** | 5 min | low | free | ✅ named tunnels work in prod |
| **R2 / S3 static** | 30 min | lowest | ~$1/mo | ✅ best at scale |
| **Railway / Render** | 1 hour | low | $5-10/mo | ✅ all-in-one |
| **ngrok** | 2 min | low | free dev, $/mo paid | ⚠️ dev only |

**Recommended path**: start with **Cloudflare Tunnel + the existing FastAPI** for the next 2 weeks, then move sections to **R2** once volume justifies it.

---

## 8. Phased migration plan

### Phase 1 — Stop the bleeding (1 day)
**Goal**: detect sections correctly. No Firecrawl yet.

- [ ] `section_resolver.py`: try `pymupdf.Document.get_toc()` first; fallback to current regex
- [ ] Use `pymupdf4llm.to_markdown(pdf, pages=[start..end])` instead of `page.get_text("text")` — preserves tables
- [ ] Remove `markdown: md[:500]` truncation in API response, or add `?full=true` flag
- [ ] Fix `/parsed-all` to actually merge all parsed sections

### Phase 2 — Per-section DeepSeek (2 days)
**Goal**: kill the truncation, drop cost ~5x. Still DeepSeek, not Firecrawl.

- [ ] Define `SECTION_SCHEMAS` dict (one entry per section we care about)
- [ ] Rewrite `parse_all_sections` to loop: one call per section with its specific schema
- [ ] Save per-section `parsed_data` (no more copying merged blob)
- [ ] New endpoint: `GET /api/ipos/{id}/unified` → merge all section data into one JSON

### Phase 3 — Hosted section URLs (1 day)
**Goal**: prepare for Firecrawl. Sections must be web-accessible.

- [ ] Save section .md to `dl/sections/{ipo_id}/{doc_type}/{section_name}.md` at resolve time
- [ ] Add `GET /public/sections/{ipo_id}/{doc_type}/{section_name}` returning `Content-Type: text/markdown`
- [ ] Set up Cloudflare Tunnel, add `PUBLIC_BASE_URL` to .env
- [ ] Verify Firecrawl can fetch a section URL from outside

### Phase 4 — Swap to Firecrawl (1 day)
**Goal**: replace DeepSeek calls with Firecrawl calls.

- [ ] Add `FIRECRAWL_API_KEY` to .env
- [ ] Build `firecrawl_extract(section_url, schema)` helper
- [ ] Add `provider` env switch: `PARSER_PROVIDER=firecrawl|deepseek` so you can A/B
- [ ] Run a 5-IPO sample, compare extraction quality vs DeepSeek baseline
- [ ] Flip default once accuracy ≥ DeepSeek

### Phase 5 — Polish (ongoing)
- [ ] Persist tasks to SQLite (so restart doesn't 404 polls)
- [ ] Dashboard: per-IPO cost dashboard, retry-failed-sections button
- [ ] Schema versioning: track which schema version produced each extracted blob

---

## 9. Open questions for you

1. **Field priority** — which 7-10 sections do you actually need data from? I assumed the 7 in the cost math, confirm or expand.
2. **Provider choice** — do we go Firecrawl-first, or DeepSeek per-section first (cheaper to test, no URL hosting needed)?
3. **Hosting** — Cloudflare Tunnel for now and S3 later, or do you want production hosting (Railway/Render) right away?
4. **Re-parse policy** — when an RHP is uploaded after a DRHP, re-parse from scratch or keep both and diff?
5. **DB redesign** — you mentioned the DB will be redesigned. Should the new parsing v2 write to the *current* schema or wait for the redesign?

---

## 10. ASCII summary of the new flow

```
                ┌────────────────┐
                │  ipo_master    │   ← scrape stage (unchanged)
                │  drhp_url      │
                │  rhp_url       │
                │  fp_url        │
                └────────┬───────┘
                         │
              resolve (per IPO, background)
                         │
                         ▼
   ┌────────────────────────────────────────────────────┐
   │ 1. download PDF                                    │
   │ 2. doc.get_toc()  ──► [(level, name, page), …]    │
   │ 3. for each section:                               │
   │      md = pymupdf4llm.to_markdown(pages=[s..e])    │
   │      save → dl/sections/{ipo}/{doc}/{name}.md      │
   │      save → document_sections.raw_md + file_path   │
   └────────────────────────────────────────────────────┘
                         │
              parse-sections (per IPO, background)
                         │
                         ▼
   ┌────────────────────────────────────────────────────┐
   │ for each section in INTERESTING:                   │
   │    url = PUBLIC_BASE_URL + section.file_path       │
   │    schema = SECTION_SCHEMAS[section.name]          │
   │    json = firecrawl.scrape(url, formats=['json'],  │
   │                            jsonOptions={schema})   │
   │    save → document_sections.parsed_data = json     │
   │ unified = merge_all_sections(ipo_id)               │
   │ save  → ipo_master.unified_data = unified          │
   └────────────────────────────────────────────────────┘
                         │
                         ▼
            GET /api/ipos/{id}/unified
            GET /public/sections/{id}/{doc}/{section}
```

---

**Bottom line**: the architecture's bones are right (scrape → resolve → parse). What needs fixing is *how* resolve detects sections and *how* parse calls the LLM. Per-section + targeted schemas + Firecrawl with a hosted URL gives you the cost, accuracy, and reliability you're asking for, in that order.
