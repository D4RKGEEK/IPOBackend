# IPO Aggregation API

FastAPI service that aggregates IPO DRHP/RHP documents from **SEBI**, **BSE**, and **NSE** — merging records by company name so each IPO appears once with data from all available sources.

## What's New in v2.0

- **NSE offer-docs endpoint** — replaced the broken Akamai-protected `under-issue` endpoint with `/api/corporates/offerdocs` which works directly (no browser/session needed)
- **Full cross-referencing** — `source=all` queries all 3 sources concurrently and merges by normalized company name
- **NSE URL cleaning** — trailing `\r`, whitespace, and control characters are stripped from all URLs
- **ZIP handling** — NSE DRHP attachments that are ZIP files are detected (flagged as `is_zip=true`), and can be auto-extracted with `resolve_zips=true`
- **Rich NSE data** — DRHP/RHP/FP dates, statuses, file sizes, and download URLs all parsed into structured models

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8001
```

Open:

- API docs: `http://127.0.0.1:8001/docs`
- Health: `http://127.0.0.1:8001/health`

## Main Endpoints

### `GET /api/ipos` (Unified Aggregation)

Queries SEBI, BSE, and/or NSE and merges results by company name.

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `source` | `sebi`, `bse`, `nse`, `all` | `all` | Sources to query |
| `document_type` | `DRHP`, `RHP`, `all` | `all` | Filter by document type |
| `platform` | `mainboard`, `sme`, `all` | `all` | Filter by exchange platform |
| `status` | `upcoming`, `open`, `closed`, `other`, `all` | `all` | IPO status filter |
| `search` | string | `""` | Company name search |
| `from_date`, `to_date` | `YYYY-MM-DD` | `""` | Date range (auto-converted per source) |
| `include_pdf_urls` | bool | false | Fetch actual DRHP/RHP PDFs from SEBI detail pages |
| `resolve_zips` | bool | false | Extract PDFs from NSE ZIP attachments |
| `page`, `per_page` | int | 1, 25 | Pagination |

```
curl "http://127.0.0.1:8001/api/ipos?source=all&search=Rentomojo&include_pdf_urls=true&resolve_zips=true"
```

### Source-specific Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/sebi/filings` | Direct SEBI filing listing (DRHP/RHP) |
| `GET /api/sebi/detail` | Extract real PDF URL from SEBI detail page |
| `GET /api/bse/ipos` | Direct BSE IPO metadata |
| `GET /api/nse/offer-docs` | Direct NSE offer documents (equities or SME) |
| `GET /api/nse/under-issue` | **Deprecated** — redirects to `/api/nse/offer-docs` |

## Response Format

A merged IPO might look like:

```json
{
  "company_name": "Rentomojo Limited",
  "filing_date": "2026-04-06",
  "source": "sebi",
  "document_type": "DRHP",
  "document_urls": {
    "detail_page": "https://www.sebi.gov.in/.../rentomojo-limited-drhp_100746.html",
    "drhp_pdf": "https://www.sebi.gov.in/sebi_data/attachdocs/apr-2026/1775525404083_1204.pdf",
    "abridged_prospectus_pdf": "https://.../Rentomojo%20Limited-Draft%20Abridged%20Prospectus_p.pdf"
  },
  "bse_data": {
    "scrip_cd": 4560,
    "price_band": "42.00 - 45.00",
    "platform": "MainBoard",
    "status": "upcoming",
    ...
  },
  "nse_data": {
    "drhp": "Draft Prospectus/Draft Red Herring Prospectus",
    "drhp_date": "24-Apr-2026",
    "drhp_status": "Under Process",
    "drhp_attach": {
      "url": "https://nsearchives.nseindia.com/corporate/Rentomojo.zip",
      "is_zip": true,
      "file_size": "9.07 MB"
    },
    "fp_attach": {
      "url": "https://nsearchives.nseindia.com/corporate/FP_Rentomojo.pdf",
      "is_zip": false
    },
    ...
  }
}
```

## Data Coverage

| IPO Type | SEBI | BSE | NSE |
|----------|------|-----|-----|
| MainBoard DRHP/RHP | ✅ Yes | ❌ No docs (metadata only) | ✅ Yes |
| MainBoard metadata | ❌ No | ✅ Yes (dates, price) | ✅ Yes (dates, status) |
| SME DRHP/RHP | ❌ No | ❌ No docs | ✅ Yes |
| SME metadata | ❌ No | ✅ Yes | ✅ Yes |

- **SEBI**: All MainBoard DRHP/RHP filings. No SME IPOs. HTML response, requires parsing.
- **BSE**: IPO metadata (dates, price bands, platform) via JSON API. **No document PDFs** — cross-reference with SEBI/NSE.
- **NSE**: Rich document data via `/api/corporates/offerdocs`. Direct JSON API, no session needed.
  - Some DRHP attachments are ZIP files (~50%). Use the `/api/nse/offer-docs?resolve_zips=true` endpoint or the `resolve_zips` flag on `/api/ipos` to auto-extract PDFs.
  - All URLs are cleaned of trailing whitespace/`\r` characters.

## Postman

Import `postman/IPO Aggregation API.postman_collection.json`.
