"""
IPO Admin Dashboard (Jinja2 + Tailwind).

Hybrid arch:
  - Reads (list, detail) hit DatabaseService directly for speed.
  - Writes (resolve, parse, scrape) call the FastAPI API on port 8001 — that
    layer owns the background-task queue and business logic.

Run: uvicorn dashboard.main:app --port 8002 --reload
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional

# Ensure project root is on sys.path before app.* imports.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Auto-detect a usable SSL cert bundle (same logic as app.main).
for _cert_path in (
    os.environ.get("SSL_CERT_FILE"),
    "/opt/homebrew/etc/openssl@3/cert.pem",
    "/usr/local/etc/openssl@3/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
):
    if _cert_path and os.path.exists(_cert_path):
        os.environ.setdefault("SSL_CERT_FILE", _cert_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", _cert_path)
        break

from fastapi import FastAPI, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.db_models import IPOMaster, get_session
from app.db_service import DatabaseService

logger = logging.getLogger(__name__)

app = FastAPI(title="IPO Dashboard", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

_template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_jinja = Environment(
    loader=FileSystemLoader(_template_dir),
    autoescape=select_autoescape(["html", "xml"]),
    auto_reload=True,
)

db = DatabaseService()


def render(name: str, context: dict) -> HTMLResponse:
    return HTMLResponse(_jinja.get_template(name).render(context))


def _stats() -> dict:
    """Cached-ish stats payload used by the sidebar pipeline indicator."""
    try:
        return db.get_dashboard_stats()
    except Exception as e:
        logger.warning("get_dashboard_stats failed: %s", e)
        return {
            "total_ipos": 0, "total_with_drhp": 0, "total_with_rhp": 0,
            "avg_confidence": 0.0, "ipos_by_status": {}, "ipos_by_platform": {},
            "latest_scrape": None,
        }


# ─── Pages ──────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/dashboard/")


@app.get("/dashboard/", response_class=HTMLResponse)
async def overview(request: Request):
    return render("index.html", {
        "request": request,
        "stats": _stats(),
        "recent_changes": db.get_recent_status_changes(limit=20),
        "recent_logs": db.get_recent_logs(limit=15),
    })


@app.get("/dashboard/ipos", response_class=HTMLResponse)
async def ipo_list(
    request: Request,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=5, le=100),
    search: str = Query(""),
    status: str = Query("all"),
    platform: str = Query("all"),
    documents: str = Query("all"),
    year: Optional[int] = Query(None),
):
    ipos, total = db.get_all_ipos(
        status=status, platform=platform, search=search, year=year,
        documents=documents, page=page, per_page=per_page,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)
    return render("ipos.html", {
        "request": request, "stats": _stats(),
        "ipos": ipos, "total": total, "page": page,
        "total_pages": total_pages, "per_page": per_page,
        "filters": {
            "search": search, "status": status, "platform": platform,
            "documents": documents, "year": year,
        },
    })


@app.get("/dashboard/ipos/{ipo_id}", response_class=HTMLResponse)
async def ipo_detail(request: Request, ipo_id: int = Path(..., ge=1)):
    ipo = db.get_ipo_by_id(ipo_id)
    if not ipo:
        return HTMLResponse("IPO not found", status_code=404)
    return render("ipo_detail.html", {
        "request": request, "stats": _stats(),
        "ipo": ipo.to_dict(),
        "status_history": db.get_status_history(ipo_id, limit=50),
    })


@app.get("/dashboard/scrape", response_class=HTMLResponse)
async def scrape_page(request: Request):
    return render("scrape.html", {
        "request": request, "stats": _stats(),
        "recent_logs": db.get_recent_logs(limit=20),
        "scrape_result": None,
    })


@app.delete("/dashboard/api/ipos/{ipo_id}")
async def delete_ipo(ipo_id: int = Path(..., ge=1)):
    """Hard-delete an IPO (cascades to sections + status history via ORM)."""
    with get_session() as s:
        row = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if not row:
            return {"status": "not_found", "ipo_id": ipo_id}
        name = row.company_name
        s.delete(row)
        s.commit()
    return {"status": "deleted", "ipo_id": ipo_id, "company_name": name}
