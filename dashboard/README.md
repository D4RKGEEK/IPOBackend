# IPO Dashboard

## Quick Start

The dashboard is a single HTML file that talks to the main IPO API.

### Prerequisites
- Main API running on port 8001 (or Railway URL)
- Python 3 (no extra packages needed)

### Run locally

```bash
cd /Users/vaibhav/Documents/IPOScraper/dashboard
python3 -m http.server 3000
```

Then open http://127.0.0.1:3000/dashboard.html

### Run alongside main API

Start the main API:
```bash
cd /Users/vaibhav/Documents/IPOScraper
.venv/bin/python -m uvicorn app.main:app --port 8001
```

Start the dashboard (separate terminal):
```bash
cd /Users/vaibhav/Documents/IPOScraper/dashboard
python3 -m http.server 3000
```

The dashboard fetches all data from `http://127.0.0.1:8001` (configurable in the dashboard's API base setting).

---

## What's here

| File | Purpose |
|------|---------|
| `dashboard.html` | Full dashboard — CDN Tailwind, Groww theme, dark mode, 7 tabs |
| `server.py` | Companion FastAPI server *(optional — adds admin endpoints)* |
| `main.py` | Legacy Jinja2 dashboard *(deprecated)* |
| `templates/` | Old Jinja2 templates *(deprecated)* |

---

## Features (100 total)

### Overview Tab
- Total IPOs, by-status counts, avg confidence
- Pipeline bar chart (FILED → DRHP → RHP → UPCOMING → OPEN → CLOSED → LISTED)
- Recent IPOs list

### IPOs Tab
- Full searchable, filterable, sortable table
- Filters: status, platform, year, search
- Document presence indicators (DRHP / RHP / FP)
- Batch select + actions (resolve, parse, publish, delete)
- Slide-in detail panel with documents, dates, quick actions

### Review Queue Tab
- IPOs needing human review (needs_review / rejected / pending)
- Confidence scores, validation issues
- Quick-approve / reject / re-parse

### Activity Tab
- Status changes feed (who moved where)
- Scraper logs per source
- Background task progress with cancel

### Admin Tab
- Re-scrape (all sources or by year)
- Quick IPO editor (change name, status, price band)
- Test notifications
- Clear database (with confirmation)

### System Tab
- RAM, CPU load, disk usage, uptime
- Database size + table row counts
- R2 storage usage (objects, size)
- Firecrawl credits remaining/used
- Service health (DB / R2 / DeepSeek / Firecrawl)
- Configuration display

### Compare Tab
- Side-by-side comparison of up to 3 IPOs

---

## Dark Mode
Toggle with the moon icon in the header. Persisted in localStorage.

## Keyboard Shortcuts
- `1`-`7` — switch tabs
- `R` — refresh all data

## Adding Admin Endpoints (optional)

For full backend control from the dashboard, the main API needs these additional endpoints:

| Endpoint | Purpose |
|----------|---------|
| `PATCH /api/ipos/{id}/publish-status` | Approve/reject unified data |
| `PATCH /api/ipos/{id}/status` | Manually advance lifecycle |
| `PATCH /api/ipos/{id}` | Edit company fields |
| `POST /api/ipos/{id}/documents` | Add document URL |
| `DELETE /api/ipos/{id}` | Remove IPO |
| `GET /api/system/usage` | RAM/CPU/DB/R2/Firecrawl metrics |
| `POST /api/tasks/{id}/cancel` | Cancel background task |
| `GET /api/ipos/search-parsed?q=...` | Full-text search across parsed data |

The dashboard gracefully degrades if these endpoints aren't available.
