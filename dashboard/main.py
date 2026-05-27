"""
Dashboard — FastAPI + Jinja2 admin panel for IPO management.
Shares the same DB as the Phase 1 API.
Run: uvicorn dashboard.main:app --port 8002
"""
import sys, os
os.environ.setdefault("SSL_CERT_FILE", "/opt/homebrew/etc/openssl@3/cert.pem")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/opt/homebrew/etc/openssl@3/cert.pem")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Form, Query, Path, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from app.db_service import DatabaseService
from app.scraper_service import ScraperService
from app.db_models import get_session, IPOMaster

logger = logging.getLogger(__name__)

app = FastAPI(title="IPO Dashboard", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
from jinja2 import Environment, FileSystemLoader
jinja_env = Environment(loader=FileSystemLoader(template_dir), auto_reload=False)

def render(name: str, context: dict) -> HTMLResponse:
    template = jinja_env.get_template(name)
    content = template.render(context)
    return HTMLResponse(content)
db = DatabaseService()
scraper = ScraperService(db=db)


def get_stats():
    """Common stats for sidebar."""
    return db.get_dashboard_stats()


@app.get("/dashboard/", response_class=HTMLResponse)
async def index(request: Request):
    stats = get_stats()
    changes = db.get_recent_status_changes(limit=20)
    return render("index.html", {
        "request": request, "stats": stats, "recent_changes": changes,
    })


@app.get("/dashboard/ipos", response_class=HTMLResponse)
async def ipo_list(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
    status: str = Query("all"),
    phase: str = Query("all"),
):
    ipos_list, total = db.get_all_ipos(
        status=status, search=search, page=page, per_page=25,
    )
    # If phase filter is set, filter in-memory (phase is new, not in get_all_ipos)
    if phase != "all":
        ipos_list = [i for i in ipos_list if i.get("phase") == phase]
        total = len(ipos_list)
    
    per_page = 25
    total_pages = max(1, (total + per_page - 1) // per_page)
    
    return render("ipos.html", {
        "request": request, "stats": get_stats(),
        "ipos": ipos_list, "total": total,
        "page": page, "total_pages": total_pages,
        "search": search, "status": status, "phase": phase,
    })


@app.get("/dashboard/ipos/add", response_class=HTMLResponse)
async def ipo_add_form(request: Request):
    return render("ipo_form.html", {
        "request": request, "stats": get_stats(), "ipo": None,
    })


@app.post("/dashboard/ipos/add")
async def ipo_add_submit(
    request: Request,
    company_name: str = Form(...),
    status: str = Form("drhp_filed"),
    cin: str = Form(""),
    platform: str = Form(""),
    price_band: str = Form(""),
    website: str = Form(""),
    drhp_url: str = Form(""),
    rhp_url: str = Form(""),
    drhp_filed_date: str = Form(""),
    close_date: str = Form(""),
    face_value: float = Form(0.0),
):
    from app.utils import normalize_company_name
    
    ipo_data = {
        "normalized_name": normalize_company_name(company_name),
        "company_name": company_name,
        "status": status,
        "price_band": price_band or None,
        "platform": platform or None,
        "drhp_url": drhp_url or None,
        "rhp_url": rhp_url or None,
        "drhp_filed_date": drhp_filed_date or None,
        "close_date": close_date or None,
        "data_confidence": 0.5,
        "source_count": 1,
        "phase": "discovered",
        "_source": "manual",
        "_triggered_by": "manual",
    }
    
    # Store CIN and website via direct DB update
    record, is_new = db.upsert_ipo(ipo_data)
    if cin:
        with get_session() as s:
            ipo = s.query(IPOMaster).filter(IPOMaster.id == record.id).first()
            if ipo:
                ipo.cin_field = cin
                s.commit()
    
    return RedirectResponse(f"/dashboard/ipos/{record.id}", status_code=303)


@app.get("/dashboard/ipos/{ipo_id}", response_class=HTMLResponse)
async def ipo_detail(request: Request, ipo_id: int = Path(...)):
    ipo = db.get_ipo_by_id(ipo_id)
    if not ipo:
        return HTMLResponse("IPO not found", status_code=404)
    
    history = db.get_status_history(ipo_id, limit=50)
    stats = get_stats()
    d = ipo.to_dict()
    d["cin"] = getattr(ipo, "cin_field", "")  # Handle CIN
    d["website"] = getattr(ipo, "cin_field", "")  # Will be in to_dict
    
    return render("ipo_detail.html", {
        "request": request, "stats": stats,
        "ipo": d, "status_history": history,
    })


@app.get("/dashboard/ipos/{ipo_id}/edit", response_class=HTMLResponse)
async def ipo_edit_form(request: Request, ipo_id: int = Path(...)):
    ipo = db.get_ipo_by_id(ipo_id)
    if not ipo:
        return HTMLResponse("IPO not found", status_code=404)
    return render("ipo_form.html", {
        "request": request, "stats": get_stats(),
        "ipo": ipo.to_dict(),
    })


@app.post("/dashboard/ipos/{ipo_id}/edit")
async def ipo_edit_submit(
    request: Request,
    ipo_id: int = Path(...),
    company_name: str = Form(...),
    status: str = Form(...),
    cin: str = Form(""),
    platform: str = Form(""),
    price_band: str = Form(""),
    website: str = Form(""),
    drhp_url: str = Form(""),
    rhp_url: str = Form(""),
    drhp_filed_date: str = Form(""),
    close_date: str = Form(""),
):
    from app.utils import normalize_company_name
    
    ipo_data = {
        "normalized_name": normalize_company_name(company_name),
        "company_name": company_name,
        "status": status,
        "price_band": price_band or None,
        "platform": platform or None,
        "drhp_url": drhp_url or None,
        "rhp_url": rhp_url or None,
        "drhp_filed_date": drhp_filed_date or None,
        "close_date": close_date or None,
        "_source": "manual",
        "_triggered_by": "manual",
    }
    db.upsert_ipo(ipo_data)
    return RedirectResponse(f"/dashboard/ipos/{ipo_id}", status_code=303)


@app.get("/dashboard/ipos/{ipo_id}/delete")
async def ipo_delete(request: Request, ipo_id: int = Path(...)):
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if ipo:
            s.delete(ipo)
            s.commit()
    return RedirectResponse("/dashboard/ipos", status_code=303)


# ─── Phase Transitions ──────────────────────────────────────

@app.post("/dashboard/ipos/{ipo_id}/phase/download")
async def phase_download(request: Request, ipo_id: int = Path(...)):
    """Move IPO to 'downloaded' phase (resolve documents)."""
    ipo = db.get_ipo_by_id(ipo_id)
    if not ipo:
        return HTMLResponse("IPO not found", status_code=404)
    
    # Trigger resolve via API (async in background)
    import httpx
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"http://127.0.0.1:8001/api/ipos/{ipo_id}/resolve")
        except:
            pass
    
    # Update phase
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if ipo:
            ipo.phase = "downloaded"
            s.commit()
    
    return RedirectResponse(f"/dashboard/ipos/{ipo_id}", status_code=303)


@app.post("/dashboard/ipos/{ipo_id}/phase/parse")
async def phase_parse(request: Request, ipo_id: int = Path(...)):
    """Move IPO to 'parsed' phase."""
    # Trigger parse via API
    import httpx
    async with httpx.AsyncClient() as client:
        try:
            await client.post(f"http://127.0.0.1:8001/api/ipos/{ipo_id}/parse")
        except:
            pass
    
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if ipo:
            ipo.phase = "parsed"
            s.commit()
    
    return RedirectResponse(f"/dashboard/ipos/{ipo_id}", status_code=303)


@app.post("/dashboard/ipos/{ipo_id}/phase/publish")
async def phase_publish(request: Request, ipo_id: int = Path(...)):
    """Move IPO to 'published' phase (ready for frontend)."""
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if ipo:
            ipo.phase = "published"
            ipo.published_at = datetime.now(timezone.utc)
            s.commit()
    
    return RedirectResponse(f"/dashboard/ipos/{ipo_id}", status_code=303)


# ─── Scrape ─────────────────────────────────────────────────

@app.get("/dashboard/scrape", response_class=HTMLResponse)
async def scrape_page(request: Request):
    logs = db.get_recent_logs(limit=20)
    return render("scrape.html", {
        "request": request, "stats": get_stats(),
        "recent_logs": logs, "scrape_result": None,
    })


@app.post("/dashboard/scrape/run")
async def scrape_run(request: Request, resolve_docs: bool = Query(False), year: Optional[int] = Query(None)):
    report = await scraper.run_full_scrape(
        bse_sme=True,
        include_pdf_urls=True,
        resolve_docs=resolve_docs,
        year=year,
    )
    logs = db.get_recent_logs(limit=20)
    return render("scrape.html", {
        "request": request, "stats": get_stats(),
        "recent_logs": logs, "scrape_result": report,
    })


@app.get("/dashboard/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    logs = db.get_recent_logs(limit=100)
    return render("logs.html", {
        "request": request, "stats": get_stats(), "logs": logs,
    })
