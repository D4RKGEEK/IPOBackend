# IPO Aggregation API

FastAPI service for IPO DRHP/RHP data aggregation.

## What It Provides

- SEBI DRHP and RHP listing fetch from the AJAX HTML endpoint.
- SEBI filing detail PDF extraction from the detail page iframe.
- BSE current/upcoming IPO metadata from the public JSON API.
- NSE best-effort endpoint with `mode=browser` by default. It opens NSE in a Playwright browser context and runs the API fetch inside that same session. `mode=http` is also available as a lighter fallback. NSE is Akamai-protected, so either mode can still be blocked depending on IP/session.
- Unified `/api/ipos` response with company name, filing date, document links, and BSE metadata when names match.

## Run

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
.venv/bin/uvicorn app.main:app --reload --port 8001
```

Open:

- API docs: `http://127.0.0.1:8001/docs`
- Health: `http://127.0.0.1:8001/health`

Use port `8000` instead if it is free; this workspace currently had another service on `8000`.

## Main Endpoints

### `GET /api/ipos`

Unified aggregation endpoint.

Query params:

- `source`: `sebi`, `bse`, `nse`, `all`
- `document_type`: `DRHP`, `RHP`, `all`
- `status`: `upcoming`, `open`, `closed`, `other`, `all`
- `platform`: `mainboard`, `sme`, `all`
- `search`: company name filter
- `from_date`, `to_date`: accepted as `YYYY-MM-DD`, converted to SEBI's `DD-MM-YYYY`
- `page`, `per_page`
- `include_pdf_urls`: when true, fetches SEBI detail pages and adds actual DRHP/RHP PDFs
- `nse_mode`: `browser` or `http`; only used when `source=nse`

Example:

```bash
curl "http://127.0.0.1:8001/api/ipos?source=sebi&document_type=DRHP&page=1&include_pdf_urls=true"
```

### `GET /api/sebi/filings`

Direct SEBI listing parser for DRHP or RHP.

### `GET /api/sebi/detail`

Extracts the real PDF URL from a SEBI filing detail page.

### `GET /api/bse/ipos`

Direct BSE JSON API wrapper.

### `GET /api/nse/under-issue`

Best-effort NSE call. Default `mode=browser` opens a real browser context, visits NSE pages, then calls `/api/equity/under-issue` from inside that same page session. `mode=http` first visits `https://www.nseindia.com`, then reuses the same `httpx.AsyncClient` cookie jar for `/api/equity/under-issue`.

## Postman

Import [postman/IPO Aggregation API.postman_collection.json](postman/IPO%20Aggregation%20API.postman_collection.json).
