"""
IPO Aggregation API v3.0 — DB-Backed
"""
import os

import asyncio
import logging
from datetime import datetime, timezone
from math import ceil
from typing import Literal, Optional, Any

import httpx
# Fix SSL cert path (AFTER httpx import to avoid import hang)
os.environ.setdefault("SSL_CERT_FILE", "/opt/homebrew/etc/openssl@3/cert.pem")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/opt/homebrew/etc/openssl@3/cert.pem")

from fastapi import FastAPI, HTTPException, Query, Path

from .clients import (BSEClient, BSESmeClient, NSEClient, SEBIClient,
    merge_bse_into_results, merge_bse_sme_docs, merge_nse_into_results)
from .schemas import (IPOResponse, IPOSummary, Meta, Pagination,
    StatusChangeItem, ScraperLogItem, RefreshResult, IPODetail, StatusHistoryEntry)
from .status import compute_status, compute_dates, compute_documents
from .scraper_service import ScraperService, _record_to_ipo_data, _source_count
from .db_service import DatabaseService
from .parsers.pipeline import parse_all_available, parse_document
from .section_resolver import resolve_document
from .section_parser import parse_all_sections
from .task_manager import get_manager, run_in_background

logger = logging.getLogger(__name__)

# ─── App Setup ───────────────────────────────────────────────

DESCRIPTION = """# IPO Aggregation API v3

**DB-backed — always fast, always consistent.**

## Quick Start
```
GET  /api/ipos                          → List IPOs (instant, DB-backed)
GET  /api/ipos?documents=drhp,rhp       → Filter by documents filed
POST /api/ipos/{id}/resolve             → Download PDFs → extract sections (background)
POST /api/ipos/{id}/parse-sections      → 1 DeepSeek call for ALL fields (background)
GET  /api/ipos/{id}/parsed-all          → Unified JSON with ALL extracted fields
GET  /api/tasks/{task_id}               → Poll background task progress
POST /api/refresh                       → Re-scrape (background)
```
"""

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="IPO Aggregation API", version="3.0.0", description=DESCRIPTION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Services ─────────────────────────────────────────────────
db_service = DatabaseService()
scraper_service = ScraperService(db=db_service)


# ─── Health ───────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health() -> dict[str, Any]:
    try:
        stats = db_service.get_dashboard_stats()
        return {"status": "ok", "database": "connected", "total_ipos": stats.get("total_ipos", 0),
                "avg_confidence": stats.get("avg_confidence", 0.0), "last_scrape": stats.get("latest_scrape")}
    except Exception as e:
        return {"status": "error", "database": "error", "error": str(e)}


# ─── Task Management ──────────────────────────────────────
@app.get("/api/tasks", tags=["System"])
async def list_tasks(limit: int = Query(20, ge=1, le=100)):
    return {"tasks": get_manager().list_recent(limit=limit)}

@app.get("/api/tasks/{task_id}", tags=["System"])
async def get_task(task_id: str = Path(...)):
    task = get_manager().get(task_id)
    if not task: raise HTTPException(status_code=404, detail="Task not found")
    return task


# ─── Main API — IPO Listing ─────────────────────────────────
@app.get("/api/ipos", response_model=IPOResponse, tags=["Aggregation"],
    summary="List all IPOs — clean, DB-backed, always fast",
    description="Filters: documents (drhp,rhp,fp,any,comma-sep), status, search, year, page")
async def get_ipos(
    documents: str = Query("all", description="documents filter: drhp, rhp, fp, any, or comma-sep like drhp,rhp"),
    status: str = Query("all"), platform: str = Query("all"),
    search: str = Query(""), year: Optional[int] = Query(None),
    page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=100),
):
    ipos, total = db_service.get_all_ipos(status=status, platform=platform, search=search,
                                          year=year, documents=documents, page=page, per_page=per_page)
    return IPOResponse(data=[_format_ipo(ipo) for ipo in ipos],
        pagination=Pagination(total_records=total, current_page=page, per_page=per_page,
                              total_pages=max(1, ceil(total / per_page)) if total else 1),
        meta=Meta(sources_queried=["database"], errors=[], notes=[]))


# ─── IPO Detail ─────────────────────────────────────────────
@app.get("/api/ipos/{ipo_id}", tags=["Aggregation"])
async def get_ipo_by_id(ipo_id: int = Path(...)):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(status_code=404, detail="IPO not found")
    d = ipo.to_dict()
    history = db_service.get_status_history(ipo_id, limit=50)
    return {"id": ipo.id, "company_name": d["company_name"], "status": d.get("status"), "dates": {
        "drhp_filed": d.get("drhp_filed_date"), "rhp_filed": d.get("rhp_filed_date"),
        "fp_filed": d.get("fp_filed_date"), "open": d.get("open_date"), "close": d.get("close_date"),
    }, "documents": d.get("documents", {}), "platform": d.get("platform"),
       "status_history": history}


# ─── Section-based Document Viewing ─────────────────────────
@app.get("/api/ipos/{ipo_id}/documents", tags=["Sections"])
async def get_documents_overview(ipo_id: int = Path(...)):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(status_code=404, detail="IPO not found")
    d = ipo.to_dict()
    docs = d.get("documents", {})
    result = {"ipo_id": ipo_id, "company_name": ipo.company_name, "documents": {}}
    for dt in ("drhp", "rhp", "final_prospectus"):
        key = "fp" if dt == "final_prospectus" else dt
        sections = db_service.get_sections(ipo_id, key)
        result["documents"][dt] = {"url": docs.get(dt), "section_count": len(sections), "sections": sections}
    return result

@app.get("/api/ipos/{ipo_id}/documents/{doc_type}/sections", tags=["Sections"])
async def get_sections(ipo_id: int = Path(...), doc_type: str = Path(...)):
    if doc_type not in ("drhp", "rhp", "fp"): raise HTTPException(400, "doc_type must be drhp, rhp, or fp")
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    sections = db_service.get_sections(ipo_id, doc_type)
    return {"ipo_id": ipo_id, "company_name": ipo.company_name, "doc_type": doc_type,
            "section_count": len(sections), "sections": sections}

@app.get("/api/ipos/{ipo_id}/documents/{doc_type}/sections/{section_name}", tags=["Sections"])
async def get_section_raw(ipo_id: int = Path(...), doc_type: str = Path(...),
                          section_name: str = Path(...), raw: bool = Query(False)):
    if doc_type not in ("drhp", "rhp", "fp"): raise HTTPException(400, "Invalid doc_type")
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    sn = section_name.upper().replace(" ", "_").replace("&", "AND")
    md = db_service.get_section_raw_md(ipo_id, doc_type, sn)
    if not md: raise HTTPException(404, f"Section '{sn}' not found")
    if raw:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(md)
    return {"ipo_id": ipo_id, "company_name": ipo.company_name, "doc_type": doc_type,
            "section_name": sn, "char_count": len(md), "markdown": md[:500]}

@app.get("/api/ipos/{ipo_id}/documents/{doc_type}/sections/{section_name}/parsed", tags=["Sections"])
async def get_section_parsed(ipo_id: int = Path(...), doc_type: str = Path(...), section_name: str = Path(...)):
    if doc_type not in ("drhp", "rhp", "fp"): raise HTTPException(400, "Invalid doc_type")
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    sn = section_name.upper().replace(" ", "_").replace("&", "AND")
    parsed = db_service.get_section_parsed(ipo_id, doc_type, sn)
    if not parsed: raise HTTPException(404, "Not parsed yet. Run parse-sections first.")
    return {"ipo_id": ipo_id, "company_name": ipo.company_name, "section_name": sn,
            "doc_type": doc_type, "data": parsed.get("data"), "parsed_at": parsed.get("parsed_at")}

@app.get("/api/ipos/{ipo_id}/parsed-all", tags=["Sections"],
    summary="Get ALL parsed data for an IPO in one unified JSON")
async def get_ipo_parsed_all(ipo_id: int = Path(...)):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    for dt in ("drhp", "rhp", "fp"):
        for sec in db_service.get_sections(ipo_id, dt):
            parsed = db_service.get_section_parsed(ipo_id, dt, sec["section_name"])
            if parsed and parsed.get("data"):
                return {"ipo_id": ipo_id, "company_name": ipo.company_name,
                        "data": parsed["data"], "parsed_at": parsed.get("parsed_at")}
    raise HTTPException(404, "No parsed data. Run resolve then parse-sections.")


# ─── Resolve (background) ──────────────────────────────────
@app.post("/api/ipos/{ipo_id}/resolve", tags=["Aggregation"])
async def resolve_ipo_documents(ipo_id: int = Path(...)):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    task_id = get_manager().create("resolve", f"Resolve {ipo.company_name}")

    def _run(tid, mgr):
        import asyncio
        try:
            d = ipo.to_dict()
            docs = d.get("documents", {})
            results, errors = {}, []
            mgr.update(tid, 0.1, "Starting...")
            async def _resolve():
                async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
                    types = [("drhp","drhp"),("rhp","rhp"),("final_prospectus","fp")]
                    for i,(dt,dk) in enumerate(types):
                        url = docs.get(dt) or d.get(f"{dt}_url")
                        if not url: results[dt] = {"status":"skipped","reason":"no URL"}; continue
                        mgr.update(tid, 0.1+(i/3)*0.8, f"Resolving {dt.upper()}...")
                        db_service.upsert_document(ipo_id, dt, url)
                        r = await resolve_document(ipo_id, dk, url, db_service, client)
                        results[dt] = r
                        if r.get("status") == "error": errors.append({"doc_type":dt,"error":r.get("error")})
            asyncio.run(_resolve())
            mgr.update(tid, 1.0, "Complete")
            return {"ipo_id":ipo_id,"company_name":ipo.company_name,"status":"completed",
                    "results":results,"errors":errors}
        except Exception as e: mgr.fail(tid, str(e)); raise

    await run_in_background(task_id, _run)
    return {"task_id":task_id,"status":"started","message":f"Resolving in background. Poll GET /api/tasks/{task_id}"}


# ─── Parse Sections (background) ───────────────────────────
@app.post("/api/ipos/{ipo_id}/parse-sections", tags=["Sections"],
    summary="Parse ALL sections in 1 DeepSeek call",
    description="1 merged call for all sections. Returns unified JSON with all 60+ fields.")
async def parse_ipo_sections(ipo_id: int = Path(...), force: bool = Query(False)):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    task_id = get_manager().create("parse", f"Parse {ipo.company_name}")

    def _run(tid, mgr):
        try:
            mgr.update(tid, 0.1, "Parsing sections...")
            result = parse_all_sections(ipo_id, company_name=ipo.company_name, force=force)
            mgr.update(tid, 1.0, "Complete")
            return result
        except Exception as e: mgr.fail(tid, str(e)); raise

    await run_in_background(task_id, _run)
    return {"task_id":task_id,"status":"started","message":f"Parsing in background. Poll GET /api/tasks/{task_id}"}


# ─── Refresh (background) ──────────────────────────────────
@app.post("/api/refresh", tags=["Aggregation"],
    summary="Trigger a full re-scrape in the background")
async def refresh(resolve_docs: bool = Query(False), resolve_limit: int = Query(50, ge=1, le=500),
                  year: Optional[int] = Query(None)):
    task_id = get_manager().create("scrape", f"Scrape IPOs (year={year or 'all'})")

    def _run(tid, mgr):
        import asyncio
        try:
            mgr.update(tid, 0.1, "Scraping SEBI...")
            asyncio.run(scraper_service.run_full_scrape(bse_sme=True, include_pdf_urls=True,
                resolve_docs=resolve_docs, resolve_doc_limit=resolve_limit, year=year))
            mgr.update(tid, 1.0, "Complete")
            return {"status":"ok","message":"Scrape completed"}
        except Exception as e: mgr.fail(tid, str(e)); raise

    await run_in_background(task_id, _run)
    return {"task_id":task_id,"status":"started","message":f"Scrape started in background. Poll GET /api/tasks/{task_id}"}


# ─── Status Changes ────────────────────────────────────────
@app.get("/api/status-changes", response_model=list[StatusChangeItem], tags=["Aggregation"])
async def get_status_changes(limit: int = Query(50, ge=1, le=200)):
    return db_service.get_recent_status_changes(limit=limit)


# ─── Dashboard ─────────────────────────────────────────────
@app.get("/api/dashboard/stats", tags=["Dashboard"])
async def dashboard_stats():
    stats = db_service.get_dashboard_stats()
    stats["api_version"] = "3.0.0"
    return stats

@app.get("/api/dashboard/logs", response_model=list[ScraperLogItem], tags=["Dashboard"])
async def dashboard_logs(limit: int = Query(50, ge=1, le=200)):
    return db_service.get_recent_logs(limit=limit)


# ─── Helpers ──────────────────────────────────────────────────
def _format_ipo(ipo: dict[str, Any]) -> IPOSummary:
    docs = ipo.get("documents", {})
    if isinstance(docs, dict):
        clean_docs = {"drhp": docs.get("drhp"), "rhp": docs.get("rhp"), "final_prospectus": docs.get("final_prospectus")}
    else:
        clean_docs = {"drhp": ipo.get("drhp_url"), "rhp": ipo.get("rhp_url"), "final_prospectus": ipo.get("final_prospectus_url")}
    return IPOSummary(id=ipo.get("id", 0), company_name=ipo["company_name"], status=ipo.get("status", "unknown"),
        dates={"drhp_filed":ipo.get("drhp_filed_date"),"rhp_filed":ipo.get("rhp_filed_date"),
               "fp_filed":ipo.get("fp_filed_date"),"open":ipo.get("open_date"),"close":ipo.get("close_date")},
        documents=clean_docs, price_band=ipo.get("price_band"), platform=ipo.get("platform"),
        issue_type=ipo.get("issue_type"))
