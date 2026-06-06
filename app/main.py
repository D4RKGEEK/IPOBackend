"""
IPO Aggregation API v3.0 — DB-backed.

Boot-time concerns:
  - SSL cert: macOS-Homebrew openssl path is set automatically if present so
    httpx works on local dev. On Linux/CI the system trust store is used.
  - Config will move to app.config in a later refactor; for now env vars and
    the .env file are read where needed (db_service, storage, parsers).
"""
import logging
import os
from math import ceil
from pathlib import Path as FilePath
from typing import Any, Optional

# Auto-detect a usable SSL cert bundle without hardcoding macOS paths.
for _cert_path in (
    os.environ.get("SSL_CERT_FILE"),
    "/opt/homebrew/etc/openssl@3/cert.pem",   # macOS (Apple Silicon)
    "/usr/local/etc/openssl@3/cert.pem",      # macOS (Intel)
    "/etc/ssl/certs/ca-certificates.crt",     # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",       # RHEL/CentOS
):
    if _cert_path and os.path.exists(_cert_path):
        os.environ.setdefault("SSL_CERT_FILE", _cert_path)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", _cert_path)
        break

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware


def _require_internal_key(
    x_internal_key: str | None = Header(default=None, alias="X-Internal-Key"),
    authorization: str | None = Header(default=None),
) -> None:
    """Gate write/cron endpoints when INTERNAL_API_KEY is set.

    Accepts the key via either:
        X-Internal-Key: <key>
        Authorization: Bearer <key>

    When INTERNAL_API_KEY is blank (local dev), the gate is open.
    """
    from .config import settings
    expected = settings.internal_api_key.strip()
    if not expected:
        return  # auth disabled
    provided = x_internal_key
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization.split(None, 1)[1].strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid or missing internal API key")

from .config import settings
from .db.operations import (
    DatabaseService, get_recent_status_changes, list_scraper_logs,
    upsert_ipo, get_ipo, list_ipos, log_scrape,
)
from .logging_setup import configure_logging
from .schemas import (
    IPOResponse,
    IPOSummary,
    Meta,
    Pagination,
    ScraperLogItem,
    StatusChangeItem,
)
from .services.scraper import run_scrape
from .section_parser import parse_all_sections
from .section_resolver import resolve_document
from .task_manager import get_manager, run_in_background

try:
    from .storage.r2 import section_url as _r2_section_url
except Exception:
    _r2_section_url = None

configure_logging()
logger = logging.getLogger(__name__)


def _attach_r2_url(section: dict, ipo_id: int) -> dict:
    """Add r2_url to a section dict if R2 is configured. Deterministic — no I/O."""
    if _r2_section_url is None or not settings.r2_enabled:
        return section
    section = dict(section)
    section["r2_url"] = _r2_section_url(ipo_id, section["doc_type"], section["section_name"])
    return section

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
from fastapi.middleware.gzip import GZipMiddleware

app = FastAPI(title="IPO Aggregation API", version="3.0.0", description=DESCRIPTION)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Services ─────────────────────────────────────────────────
db_service = DatabaseService()


def _collect_candidate_urls(d: dict, doc_type: str) -> list:
    """Return all known URLs for a doc type across all stored sources, deduped, best-quality first.

    Priority: NSE archive (most stable) → SEBI extracted PDF → Upstox → flat computed → BSE SME.
    Chittorgarh is not included here — caller appends it as final fallback.
    """
    seen: set = set()
    urls: list = []

    def _add(url):
        if url and isinstance(url, str) and url.startswith("http") and url not in seen:
            seen.add(url)
            urls.append(url)

    # 1. NSE — direct archive downloads from nsarchives.nseindia.com (most stable)
    nse = d.get("nse_data") or {}
    attach_key = "rhp_attach" if doc_type == "rhp" else ("drhp_attach" if doc_type == "drhp" else "fp_attach")
    _add((nse.get(attach_key) or {}).get("url"))

    # 2. SEBI — extracted PDF URLs from detail page
    sebi = d.get("sebi_data") or {}
    _add(sebi.get(f"{doc_type}_pdf"))

    # 3. Flat computed URL (may be Upstox, NSE, or SEBI — whatever won compute_documents)
    docs = d.get("documents") or {}
    _add(docs.get(doc_type))
    _add(d.get(f"{doc_type}_url"))

    # 4. Upstox — explicit (in case flat URL is something else)
    upstox = d.get("upstox_data") or {}
    _add(upstox.get(f"{doc_type}_url"))

    # 5. BSE SME — only when doc type matches
    sme = d.get("bse_sme_data") or {}
    if sme.get("document_url"):
        sme_type = (sme.get("document_type") or "").upper()
        if (doc_type == "rhp" and sme_type in ("RHP", "PROSPECTUS")) or \
           (doc_type == "drhp" and sme_type == "DRHP") or \
           (doc_type == "final_prospectus" and sme_type in ("PROSPECTUS", "FINAL PROSPECTUS", "FP")):
            _add(sme["document_url"])

    return urls


# ─── Health ───────────────────────────────────────────────────
@app.get("/health", tags=["System"], summary="Liveness + dependency reachability check")
async def health(deep: bool = Query(False, description="Probe external services (R2, Firecrawl, DeepSeek)")) -> dict[str, Any]:
    """Returns 200 with per-component status. Set ?deep=true to actually probe upstreams.

    Default mode is fast (DB only) so this can be used as a liveness probe.
    Deep mode performs lightweight HEAD/list calls against R2/Firecrawl/DeepSeek.
    """
    checks: dict[str, dict[str, Any]] = {}
    overall_ok = True

    # DB (always checked, cheap)
    try:
        stats = db_service.get_dashboard_stats()
        checks["database"] = {"ok": True, "total_ipos": stats.get("total_ipos", 0)}
    except Exception as e:
        overall_ok = False
        checks["database"] = {"ok": False, "error": str(e)[:200]}

    # Config (does .env look complete?)
    checks["config"] = {
        "r2_configured": settings.r2_enabled,
        "deepseek_configured": bool(settings.deepseek_api_key),
        "firecrawl_configured": bool(settings.firecrawl_api_key),
        "parser_provider": settings.parser_provider,
    }

    if deep:
        # R2: list bucket (cheap, just checks creds + reachability)
        if settings.r2_enabled:
            try:
                from .storage.r2 import _client as _r2_client
                _r2_client().list_objects_v2(Bucket=settings.r2_bucket, MaxKeys=1)
                checks["r2"] = {"ok": True, "bucket": settings.r2_bucket}
            except Exception as e:
                overall_ok = False
                checks["r2"] = {"ok": False, "error": str(e)[:200]}
        else:
            checks["r2"] = {"ok": False, "skipped": "not configured"}

        # Firecrawl: HEAD /v1 (no separate health endpoint; we trust DNS+TLS)
        if settings.firecrawl_api_key:
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get("https://api.firecrawl.dev/v1/team", headers={
                        "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    })
                # Any 2xx/4xx means the API is reachable; only network-level failures count as down.
                checks["firecrawl"] = {"ok": r.status_code < 500, "status_code": r.status_code}
                if r.status_code >= 500:
                    overall_ok = False
            except Exception as e:
                overall_ok = False
                checks["firecrawl"] = {"ok": False, "error": str(e)[:200]}
        else:
            checks["firecrawl"] = {"ok": False, "skipped": "not configured"}

        # DeepSeek: cheap GET on the models endpoint
        if settings.deepseek_api_key:
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get("https://api.deepseek.com/v1/models", headers={
                        "Authorization": f"Bearer {settings.deepseek_api_key}",
                    })
                checks["deepseek"] = {"ok": r.status_code < 500, "status_code": r.status_code}
                if r.status_code >= 500:
                    overall_ok = False
            except Exception as e:
                overall_ok = False
                checks["deepseek"] = {"ok": False, "error": str(e)[:200]}
        else:
            checks["deepseek"] = {"ok": False, "skipped": "not configured"}

    return {
        "status": "ok" if overall_ok else "degraded",
        "version": settings.version,
        "deep": deep,
        "checks": checks,
    }


# ─── Notifications ────────────────────────────────────────
@app.post("/api/internal/notify/test", tags=["System"],
    summary="Send a test notification through every configured channel")
async def notify_test():
    """Synchronously hits Telegram + Gmail (whichever are configured) and
    reports per-channel success. Use this after setting env vars to confirm
    the wiring works before relying on it for production alerts."""
    from .notifications import test_channels
    result = test_channels()
    overall_ok = all(
        (ch.get("ok") is True) or (ch.get("enabled") is False)
        for ch in result.values()
    )
    return {"ok": overall_ok, "channels": result}


# ─── Task Management ──────────────────────────────────────
@app.get("/api/tasks", tags=["System"])
async def list_tasks(limit: int = Query(20, ge=1, le=100)):
    return {"tasks": get_manager().list_recent(limit=limit)}

@app.get("/api/tasks/{task_id}", tags=["System"])
async def get_task(task_id: str = Path(...)):
    task = get_manager().get(task_id)
    if not task: raise HTTPException(status_code=404, detail="Task not found")
    return task


import time as _time
_ipos_cache = {}

@app.get("/api/ipos", response_model=IPOResponse, tags=["Aggregation"],
    summary="List all IPOs — clean, DB-backed, always fast",
    description="Filters: documents (drhp,rhp,fp,any,comma-sep), status, search, year, page")
async def get_ipos(
    documents: str = Query("all", description="documents filter: drhp, rhp, fp, any, or comma-sep like drhp,rhp"),
    status: str = Query("all"), platform: str = Query("all"),
    search: str = Query(""), year: Optional[int] = Query(None),
    page: int = Query(1, ge=1), per_page: int = Query(25, ge=1, le=100),
):
    global _ipos_cache
    cache_key = f"{documents}_{status}_{platform}_{search}_{year}_{page}_{per_page}"
    now = _time.time()
    
    if cache_key in _ipos_cache and now < _ipos_cache[cache_key]["expires"]:
        ipos, total = _ipos_cache[cache_key]["data"]
    else:
        ipos, total = db_service.get_all_ipos(status=status, platform=platform, search=search,
                                              year=year, documents=documents, page=page, per_page=per_page)
        _ipos_cache[cache_key] = {"expires": now + 60, "data": (ipos, total)}

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
    from .schemas import UpstoxData
    upstox_raw = d.get("upstox_data")
    upstox_obj = UpstoxData(**upstox_raw) if isinstance(upstox_raw, dict) else None
    return {"id": ipo.id, "company_name": d["company_name"], "status": d.get("status"), "dates": {
        "drhp_filed": d.get("drhp_filed_date"), "rhp_filed": d.get("rhp_filed_date"),
        "fp_filed": d.get("fp_filed_date"), "open": d.get("open_date"), "close": d.get("close_date"),
    }, "documents": d.get("documents", {}), "platform": d.get("platform"),
       "upstox_data": upstox_obj.model_dump(exclude_none=True) if upstox_obj else None,
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
        sections = [_attach_r2_url(s, ipo_id) for s in db_service.get_sections(ipo_id, key)]
        result["documents"][dt] = {"url": docs.get(dt), "section_count": len(sections), "sections": sections}
    return result

@app.get("/api/ipos/{ipo_id}/documents/{doc_type}/sections", tags=["Sections"])
async def get_sections(ipo_id: int = Path(...), doc_type: str = Path(...)):
    if doc_type not in ("drhp", "rhp", "fp"): raise HTTPException(400, "doc_type must be drhp, rhp, or fp")
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    sections = [_attach_r2_url(s, ipo_id) for s in db_service.get_sections(ipo_id, doc_type)]
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

@app.get("/api/ipos/{ipo_id}/unified", tags=["Sections"],
    summary="The contract shipped to Next.js — unified extracted JSON for one IPO",
    description="Reads ipo_master.unified_data directly (no merge at read-time). "
                "Returns unified data + provenance (where each field came from) + "
                "publish_status + confidence_score. If publish_status='needs_review' "
                "or 'rejected', the data exists but should NOT be treated as canonical.")
async def get_ipo_unified(ipo_id: int = Path(..., ge=1)):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(404, "IPO not found")
    if not ipo.unified_data:
        raise HTTPException(404, "No unified data yet. Run /resolve then /parse-firecrawl.")
    return {
        "ipo_id": ipo_id,
        "company_name": ipo.company_name,
        "status": ipo.status,
        "publish_status": ipo.publish_status,
        "confidence_score": ipo.confidence_score,
        "unified_version": ipo.unified_version,
        "unified_updated_at": ipo.unified_updated_at.isoformat() if ipo.unified_updated_at else None,
        "validation_issues": ipo.validation_issues or [],
        "data": ipo.unified_data,
        "provenance": ipo.unified_provenance or {},
    }


@app.get("/api/review-queue", tags=["Sections"],
    summary="IPOs that need human review (low confidence or validation issues)")
async def get_review_queue(
    limit: int = Query(50, ge=1, le=200),
    publish_status: str = Query("needs_review", description="needs_review | rejected | pending"),
):
    from .db.models import IPOMaster
    from .db.engine import get_session
    with get_session() as s:
        rows = (
            s.query(IPOMaster)
            .filter(IPOMaster.publish_status == publish_status)
            .order_by(IPOMaster.unified_updated_at.desc().nullslast(), IPOMaster.id.desc())
            .limit(limit)
            .all()
        )
        return {
            "publish_status": publish_status,
            "count": len(rows),
            "ipos": [
                {
                    "ipo_id": r.id,
                    "company_name": r.company_name,
                    "status": r.status,
                    "confidence_score": r.confidence_score,
                    "validation_issues": r.validation_issues or [],
                    "unified_version": r.unified_version,
                    "unified_updated_at": r.unified_updated_at.isoformat() if r.unified_updated_at else None,
                }
                for r in rows
            ],
        }


@app.get("/api/ipos/{ipo_id}/parsed-all", tags=["Sections"],
    summary="Alias for /unified — kept for backwards compatibility",
    description="Same payload as /api/ipos/{id}/unified. The old per-section on-the-fly "
                "merge has been removed — it picked up stale data from sections that were "
                "parsed by the deprecated /parse-sections (DeepSeek) endpoint, which stuffed "
                "the full 60-field blob into every section. Use /unified for new clients.")
async def get_ipo_parsed_all(ipo_id: int = Path(...)):
    return await get_ipo_unified(ipo_id)


@app.get("/api/ipos/{ipo_id}/tables", tags=["Sections"],
    summary="Structured tables from PDF pages",
    description="Returns tables detected by pdfplumber. Filter by page_num (recommended!) "
                "to extract a single page — avoids OOM on low-RAM containers. "
                "Omitting page_num extracts ALL pages (may crash on 500MB RAM for 400+ pg PDFs).")
async def get_ipo_tables(
    ipo_id: int = Path(...),
    doc_type: Optional[str] = Query(None, description="Filter: drhp / rhp / fp"),
    section_name: Optional[str] = Query(None, description="Section name (e.g. CAPITAL_STRUCTURE, ISSUE_STRUCTURE). Auto-resolves page range from resolve step."),
    page_num: Optional[int] = Query(None, description="Extract tables from this page only (1-indexed). Safe on any RAM."),
):
    from app.db.operations import get_tables, save_tables
    from app.db.models import DocumentSection
    from app.db.engine import get_session

    # If section_name given, resolve page range from document_sections
    effective_page = page_num
    effective_section = section_name
    if section_name and not page_num:
        with get_session() as s:
            sec = s.query(DocumentSection).filter(
                DocumentSection.ipo_master_id == ipo_id,
                DocumentSection.section_name == section_name,
            ).first()
            if sec and sec.page_start and sec.page_end:
                effective_page = sec.page_start
                pages_to_extract = list(range(sec.page_start, sec.page_end + 1))
            elif sec and sec.page_start:
                effective_page = sec.page_start
                pages_to_extract = [sec.page_start]
            else:
                return {"error": f"section '{section_name}' has no page range"}

    cached = get_tables(ipo_id, doc_type=doc_type, section_name=effective_section)
    if cached and not page_num:
        # Clean cached data — may have empty columns from raw pdfplumber
        from app.section_resolver import _clean_headers_rows
        grouped: dict[str, Any] = {}
        for t in cached:
            sec = t.get("section_name", "unknown")
            if sec not in grouped:
                grouped[sec] = {
                    "section_name": sec,
                    "doc_type": t.get("doc_type", "rhp"),
                    "page_range": {
                        "start": min(r["page_num"] for r in cached if r.get("section_name") == sec),
                        "end": max(r["page_num"] for r in cached if r.get("section_name") == sec),
                    },
                    "tables": [],
                }
            grouped[sec]["tables"].append({
                "page": t.get("page_num"),
                "table_index": t.get("table_index", 0),
                "headers": t.get("data", {}).get("headers", []),
                "rows": t.get("data", {}).get("rows", []),
            })
        # Clean: remove empty columns, merge shifted pairs, drop header-only tables
        cleaned_sections = []
        for sec_data in grouped.values():
            clean_tables = []
            for tbl in sec_data["tables"]:
                cleaned = _clean_headers_rows(tbl["headers"], tbl["rows"])
                if cleaned:
                    clean_tables.append({
                        "page": tbl["page"],
                        "table_index": tbl["table_index"],
                        "headers": cleaned["headers"],
                        "rows": cleaned["rows"],
                    })
            if clean_tables:
                sec_data["tables"] = clean_tables
                cleaned_sections.append(sec_data)
        return {
            "ipo_id": ipo_id,
            "sections": cleaned_sections,
            "total_tables": sum(len(s["tables"]) for s in cleaned_sections),
            "source": "cache",
        }

    # No tables cached — extract on-demand
    try:
        import asyncio as _aio, tempfile, os, time as _time
        from app.db.operations import DatabaseService
        from app.section_resolver import _extract_tables_worker, _clean_headers_rows, _get_pdf_executor

        db = DatabaseService()
        ipo = db.get_ipo_by_id(ipo_id)
        if not ipo:
            return {"error": "IPO not found"}
        rhp_url = getattr(ipo, 'rhp_url', None)
        if not rhp_url:
            return {"error": "no RHP URL available"}

        t0 = _time.time()
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as _dl_client:
            resp = await _dl_client.get(rhp_url)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}

        tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        tmp_path = tmp.name
        tmp.close()
        try:
            with open(tmp_path, 'wb') as f:
                f.write(resp.content)
            del resp

            if effective_section and not page_num:
                worker_pages = pages_to_extract
            elif page_num:
                worker_pages = [page_num]
            else:
                worker_pages = None  # all pages

            loop = _aio.get_running_loop()
            raw_results = await loop.run_in_executor(
                _get_pdf_executor(), _extract_tables_worker, tmp_path, worker_pages
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        elapsed = _time.time() - t0
        eff_doc = doc_type or "rhp"
        eff_section_label = effective_section or "all"
        results = []
        for t in raw_results:
            t["doc_type"] = eff_doc
            t["section_name"] = eff_section_label
            results.append(t)

        if results and doc_type and effective_section and not page_num:
            save_tables(ipo_id, doc_type, effective_section, results)

        # Restructure into clean grouped response
        grouped: dict[str, Any] = {}
        for t in results:
            sec = t.get("section_name", "unknown")
            if sec not in grouped:
                grouped[sec] = {
                    "section_name": sec,
                    "doc_type": t.get("doc_type", "rhp"),
                    "page_range": {
                        "start": min(r["page_num"] for r in results if r.get("section_name") == sec),
                        "end": max(r["page_num"] for r in results if r.get("section_name") == sec),
                    },
                    "tables": [],
                }
            grouped[sec]["tables"].append({
                "page": t.get("page_num"),
                "table_index": t.get("table_index", 0),
                "headers": t.get("headers", []),
                "rows": t.get("rows", []),
            })

        # Safety clean on fresh extraction too (parity with cache path)
        clean_sections = []
        for sec_data in grouped.values():
            clean_tables = []
            for tbl in sec_data["tables"]:
                cleaned = _clean_headers_rows(tbl["headers"], tbl["rows"])
                if cleaned:
                    clean_tables.append({
                        "page": tbl["page"],
                        "table_index": tbl["table_index"],
                        "headers": cleaned["headers"],
                        "rows": cleaned["rows"],
                    })
            if clean_tables:
                sec_data["tables"] = clean_tables
                clean_sections.append(sec_data)
        
        import gc
        gc.collect()

        return {
            "ipo_id": ipo_id,
            "sections": clean_sections,
            "total_tables": sum(len(s["tables"]) for s in clean_sections),
            "extraction_ms": round(elapsed * 1000),
        }
    except MemoryError:
        return {"error": "Out of memory — pass ?section_name=CAPITAL_STRUCTURE to extract a specific section"}


# ─── Resolve (background) ──────────────────────────────────
@app.post("/api/ipos/{ipo_id}/resolve", tags=["Aggregation"])
async def resolve_ipo_documents(ipo_id: int = Path(...), stream: bool = Query(False)):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo: raise HTTPException(404, "IPO not found")
    task_id = get_manager().create("resolve", f"Resolve {ipo.company_name}")

    def _run(tid, mgr):
        import asyncio
        try:
            d = ipo.to_dict()
            # Augment with raw source blobs not in to_dict() — needed for multi-URL fallback
            for _f in ("nse_data", "sebi_data", "bse_sme_data"):
                if _f not in d:
                    d[_f] = getattr(ipo, _f, None)
            docs = d.get("documents", {})
            results, errors = {}, []
            mgr.update(tid, 0.1, "Starting...")
            async def _resolve():
                async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
                    # Pre-compute Chittorgarh slug for fallback
                    _chitto_slug = None
                    async def _chitto_fallback(dt: str, prefer_suffixes: Optional[list[str]] = None) -> Optional[str]:
                        nonlocal _chitto_slug
                        from app.clients.chittorgarh import find_document_url
                        if _chitto_slug is None:
                            import re
                            slug = d.get("company_name","").lower().replace(" ipo","").replace(" ","-")
                            slug = re.sub(r"[^a-z0-9-]","",slug).strip("-")
                            for _s in ["-ltd","-limited","-pvt-ltd","-private-limited"]:
                                slug = slug.replace(_s,"")
                            _chitto_slug = slug
                        return await find_document_url(_chitto_slug, prefer=prefer_suffixes)

                    # Try RHP first (from ALL sources), then DRHP. RHP from any source beats DRHP.
                    resolved_doc = None  # tracks which doc type succeeded
                    for dt, dk, prefer in [
                        ("rhp", "rhp", ["-rhp", "-prospectus", "-drhp"]),
                        ("drhp", "drhp", ["-drhp", "-prospectus"]),
                    ]:
                        # Collect every known URL for this doc type across all stored sources
                        candidate_urls = _collect_candidate_urls(d, dt)
                        # Only call Chittorgarh when we have no URLs or just one (as extra fallback).
                        # Avoids a network HEAD request on every resolve for well-covered IPOs.
                        if len(candidate_urls) < 2:
                            chitto_url = await _chitto_fallback(dt, prefer)
                            if chitto_url and chitto_url not in candidate_urls:
                                candidate_urls.append(chitto_url)

                        if not candidate_urls:
                            results[dt] = {"status": "skipped", "reason": "no URL from any source"}
                            continue

                        mgr.update(tid, 0.1 + (0.5 if dt == "rhp" else 0.9),
                                   f"Resolving {dt.upper()} — {len(candidate_urls)} source(s)...")
                        r = None
                        abridged_count = 0
                        for url in candidate_urls:
                            db_service.upsert_document(ipo_id, dt, url)
                            r = await resolve_document(ipo_id, dk, url, db_service, client, stream_download=stream)
                            if r.get("status") == "ok":
                                break
                            if r.get("status") in ("abridged_detected", "not_a_prospectus"):
                                abridged_count += 1
                                logger.info("resolve %s/%s %s at %s — trying next",
                                            ipo_id, dt, r.get("status"), url[:60])
                                continue
                            logger.warning("resolve %s/%s failed url=%s err=%s — trying next source",
                                           ipo_id, dt, url[:80], r.get("error", ""))

                        if r and r.get("status") == "ok":
                            results[dt] = r
                            resolved_doc = dt
                            if dt == "rhp":
                                results["drhp"] = {"status": "skipped", "reason": "rhp_resolved_first"}
                                break
                        elif abridged_count == len(candidate_urls):
                            # Every URL was an Abridged Prospectus — full doc not yet filed.
                            # Persist to DB so the audit cooldown (24h) kicks in and we don't
                            # re-download the ZIP every 15-min pipeline run.
                            results[dt] = {"status": "abridged_only",
                                           "reason": "only Abridged Prospectus available — full DRHP/RHP not yet public"}
                            from datetime import datetime as _dt, timezone as _tz
                            db_service.update_ipo_field(ipo_id, "publish_status", "abridged_only")
                            db_service.update_ipo_field(ipo_id, "unified_updated_at", _dt.now(_tz.utc))
                            logger.info("resolve %s/%s: all %d sources returned Abridged Prospectus",
                                        ipo_id, dt, abridged_count)
                        else:
                            errors.append({"doc_type": dt, "tried": len(candidate_urls),
                                           "error": r.get("error") if r else "no URL"})

                    # Last resort: if BOTH RHP and DRHP failed from every source, try Final Prospectus.
                    # NSE's fp_attach is equivalent in content to RHP — worth attempting.
                    if resolved_doc is None:
                        fp_candidates = _collect_candidate_urls(d, "final_prospectus")
                        chitto_fp = await _chitto_fallback("final_prospectus", ["-prospectus", "-rhp"])
                        if chitto_fp and chitto_fp not in fp_candidates:
                            fp_candidates.append(chitto_fp)

                        if fp_candidates:
                            mgr.update(tid, 0.95, f"RHP+DRHP all failed — trying Final Prospectus ({len(fp_candidates)} source(s))...")
                            r = None
                            for url in fp_candidates:
                                db_service.upsert_document(ipo_id, "fp", url)
                                r = await resolve_document(ipo_id, "fp", url, db_service, client, stream_download=stream)
                                if r.get("status") == "ok":
                                    results["fp"] = r
                                    resolved_doc = "fp"
                                    break
                                logger.warning("resolve %s/fp failed url=%s — trying next", ipo_id, url[:80])
                            if r and r.get("status") != "ok":
                                errors.append({"doc_type": "fp", "tried": len(fp_candidates),
                                               "error": r.get("error") if r else "no URL"})
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


# ─── Parse Sections via Firecrawl (background) ─────────────
@app.post("/api/ipos/{ipo_id}/parse-firecrawl", tags=["Sections"],
    summary="Parse sections one-at-a-time via Firecrawl (R2-hosted markdown)",
    description="Sends each target section's R2 URL + JSON schema to Firecrawl. "
                "Cheaper and more accurate than the merged DeepSeek call because each "
                "extraction is targeted to a small schema. Returns task_id; poll /api/tasks/{id}.")
async def parse_ipo_sections_firecrawl(ipo_id: int = Path(...), force: bool = Query(False)):
    from .parsers.firecrawl_parser import parse_all_sections_firecrawl
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(404, "IPO not found")
    task_id = get_manager().create("parse_firecrawl", f"Firecrawl {ipo.company_name}")

    def _run(tid, mgr):
        try:
            def _progress(pct: float, label: str):
                mgr.update(tid, pct, label)
            result = parse_all_sections_firecrawl(
                ipo_id, company_name=ipo.company_name, force=force, progress=_progress,
            )
            mgr.update(tid, 1.0, "Complete")
            return result
        except Exception as e:
            mgr.fail(tid, str(e))
            raise

    await run_in_background(task_id, _run)
    return {"task_id": task_id, "status": "started",
            "message": f"Firecrawl parse started. Poll GET /api/tasks/{task_id}"}


# ─── Refresh (background) ──────────────────────────────────
@app.post("/api/refresh", tags=["Aggregation"],
    summary="Trigger a full re-scrape in the background")
async def refresh(
    sources: str = Query("upstox", description="Sources: 'upstox' (default) or 'all' (Upstox + legacy SEBI/BSE/NSE)"),
    year: Optional[int] = Query(None, description="Limit to a specific filing year (e.g. 2026)."),
    _auth: None = Depends(_require_internal_key),
):
    task_id = get_manager().create("scrape", f"Scrape IPOs (sources={sources}, year={year or 'all'})")

    def _run(tid, mgr):
        import asyncio
        try:
            async def on_progress(pct, label):
                mgr.update(tid, pct, label)

            mgr.update(tid, 0.05, "Starting scrape...")
            scrape_result = asyncio.run(run_scrape(
                year=year or 2026, sources=sources, progress_callback=on_progress,
            ))
            mgr.update(tid, 1.0, f"Scraped {scrape_result.get('total_unique', 0)} IPOs, {scrape_result.get('new_ipos_found', 0)} new")
            return scrape_result
        except Exception as e: mgr.fail(tid, str(e)); raise

    await run_in_background(task_id, _run)
    return {"task_id":task_id,"status":"started","message":f"Scrape started in background. Poll GET /api/tasks/{task_id}"}


# ─── Master Pipeline (background) ──────────────────────────
@app.post("/api/pipeline/auto", tags=["Aggregation"],
    summary="Master Orchestrator: Scrapes, Resolves, and Parses sequentially")
async def pipeline_auto(
    year: Optional[int] = Query(2026, description="Limit to a specific filing year"),
    stream: bool = Query(False, description="Stream PDF downloads directly to disk"),
    _auth: None = Depends(_require_internal_key),
):
    task_id = get_manager().create("pipeline_auto", f"Auto Pipeline (year={year})")

    # ─── Resource-aware concurrency ─────────────
    def _get_resource_budget() -> dict:
        """Check free RAM + CPU load. Returns concurrency hints."""
        try:
            with open('/proc/meminfo') as f:
                mem = {}
                for line in f:
                    parts = line.split()
                    if parts[0].rstrip(':') in ('MemTotal', 'MemAvailable', 'MemFree'):
                        mem[parts[0].rstrip(':')] = int(parts[1])
            free_mb = mem.get('MemAvailable', 0) / 1024
            total_mb = mem.get('MemTotal', 0) / 1024
        except Exception:
            free_mb = total_mb = 0

        try:
            load_1m, load_5m, _ = os.getloadavg()
        except Exception:
            load_1m = load_5m = 0

        # RESOLVE: PDF processing takes 200-500MB per IPO (pymupdf + pdfplumber).
        # Leave 400MB safety margin for PM2 services (hermes, frontend, etc).
        resolve_margin = 400
        resolve_slots = max(1, int((free_mb - resolve_margin) / 350)) if free_mb > resolve_margin else 1
        resolve_slots = min(resolve_slots, 2)  # 2 vCPU, never exceed 2

        # PARSE: Firecrawl is network-only (~0MB local). CPU is the bottleneck.
        # 2 vCPU → 2 parallel parse calls is safe.
        if load_1m < 1.5:
            parse_slots = min(3, max(1, int(free_mb / 400)))
        elif load_1m < 2.5:
            parse_slots = 2
        else:
            parse_slots = 1

        return {
            "free_mb": round(free_mb, 1), "total_mb": round(total_mb, 1),
            "load_1m": round(load_1m, 2), "load_5m": round(load_5m, 2),
            "resolve_concurrency": resolve_slots,
            "parse_concurrency": parse_slots,
            "resolve_timeout": 600,   # 10 min per IPO resolve
            "parse_timeout": 600,     # 10 min per Firecrawl parse
        }

    def _run(tid, mgr):
        import asyncio
        import os
        import time
        try:
            budget = _get_resource_budget()
            stats = {
                "stage": "starting",
                "total_ipos_checked": 0,
                "skipped_count": 0,       # already fully processed
                "no_url_count": 0,        # no document URL at all
                "pending_resolve": 0,
                "resolved_success": 0,
                "resolved_failed": 0,
                "pending_parse": 0,
                "parsed_success": 0,
                "parsed_failed": 0,
                "current_ipo": None,
                "current_action": "Initializing...",
                "log": [],                # running human-readable log
                "resolve_details": [],    # [{name, status, doc_type, sections_found, error}]
                "parse_details": [],      # [{name, status, groups_parsed, groups_skipped, error}]
                "resources": budget,
            }

            def _log(msg: str):
                from datetime import datetime as _dt, timezone as _tz
                entry = f"[{_dt.now(_tz.utc).strftime('%H:%M:%S')}] {msg}"
                stats["log"].append(entry)
                logger.info("pipeline: %s", msg)

            def _update_stats(progress: float):
                mgr.update(tid, progress, stats["current_action"], "", result=stats)

            async def _run_async():
                _update_stats(0.01)

                # 1. Scrape
                stats["stage"] = "scraping"
                stats["current_action"] = f"Scraping {year or 'all'} IPOs..."
                _update_stats(0.05)

                async def on_progress(pct, label):
                    pass # Keep the master progress bar clean
                scrape_result = await run_scrape(year=year or 2026, sources="all", progress_callback=on_progress)
                stats["scrape_result"] = scrape_result
                new_found = scrape_result.get("new_ipos_found", 0)
                total_unique = scrape_result.get("total_unique", 0)
                _log(f"Scrape complete — {total_unique} total IPOs, {new_found} new this run")
                _update_stats(0.2)

                # 2. Audit
                stats["stage"] = "auditing"
                stats["current_action"] = "Auditing DB for unresolved/unparsed IPOs..."
                _update_stats(0.25)

                ipos, _ = db_service.get_all_ipos(year=year, per_page=1000)
                # Convert ORM objects to plain dicts so we can use .get() safely
                ipos = [ipo.to_dict() if hasattr(ipo, 'to_dict') else ipo for ipo in ipos]
                stats["total_ipos_checked"] = len(ipos)

                to_resolve = []
                to_parse = []
                skipped = []   # fully done — nothing to do
                no_url = []    # no document URL at all

                _RERESOLVE_COOLDOWN_HOURS = 24

                for ipo in ipos:
                    name = ipo.get("company_name", f"id={ipo.get('id')}")
                    docs = ipo.get("documents", {})
                    rhp_url = docs.get("rhp") if isinstance(docs, dict) else ipo.get("rhp_url")
                    fp_url = docs.get("final_prospectus") if isinstance(docs, dict) else ipo.get("final_prospectus_url")
                    drhp_url = docs.get("drhp") if isinstance(docs, dict) else ipo.get("drhp_url")
                    # rhp_processed covers both RHP and FP (they share the flag in db_service)
                    rhp_processed = ipo.get("rhp_processed", False)
                    drhp_processed = ipo.get("drhp_processed", False)
                    publish_status = ipo.get("publish_status", "pending")
                    unified_at = ipo.get("unified_updated_at")

                    # Gate 1: Fully done — skip until a new URL or status change arrives
                    # BUT: if this IPO got a NEW RHP/FP/DRHP URL since last publish → re-process
                    # Also: if RHP/FP URL exists but was never processed, re-process too
                    if publish_status in ("published", "needs_review") and unified_at:
                        # Check if any document URL appeared that we didn't have before (new since publish)
                        new_doc_url = False
                        if fp_url and not ipo.get("final_prospectus_url"):
                            new_doc_url = True
                        elif rhp_url and not ipo.get("rhp_url"):
                            new_doc_url = True
                        elif drhp_url and not ipo.get("drhp_url"):
                            new_doc_url = True

                        # Check if we have a primary doc (RHP/FP > DRHP) that's unprocessed
                        # This catches cases where URL existed at publish time but was never resolved
                        unproc_doc = False
                        primary_url = rhp_url or fp_url
                        if primary_url and not rhp_processed:
                            unproc_doc = True
                            resolve_reason = "rhp/fp not yet resolved (existing URL)"
                        elif not rhp_processed and not primary_url and drhp_url and not drhp_processed:
                            unproc_doc = True
                            resolve_reason = "drhp not yet resolved (existing URL)"

                        if not new_doc_url and not unproc_doc:
                            skipped.append(f"{name} [{publish_status}]")
                            continue

                    # Gate 2: No document URLs at all — nothing we can do
                    primary_url = rhp_url or fp_url  # RHP/FP share rhp_processed flag
                    if not primary_url and not drhp_url:
                        no_url.append(name)
                        continue

                    # Gate 3: 24-hour cooldown for persistently-failing IPOs
                    needs_reresolve = False
                    reresolve_reason = ""
                    if publish_status in ("rejected", "abridged_only"):
                        if not unified_at:
                            needs_reresolve = True
                            reresolve_reason = f"status={publish_status}, never attempted"
                        else:
                            try:
                                from datetime import datetime as _dt, timezone as _tz
                                _lu = _dt.fromisoformat(unified_at) if isinstance(unified_at, str) else unified_at
                                if _lu.tzinfo is None:
                                    _lu = _lu.replace(tzinfo=_tz.utc)
                                _hours_ago = (_dt.now(_tz.utc) - _lu).total_seconds() / 3600
                                needs_reresolve = _hours_ago >= _RERESOLVE_COOLDOWN_HOURS
                                if needs_reresolve:
                                    reresolve_reason = f"status={publish_status}, last attempt {_hours_ago:.0f}h ago"
                            except Exception:
                                needs_reresolve = True
                                reresolve_reason = f"status={publish_status}, timestamp parse error"

                    # Gate 4: RHP/FP takes priority over DRHP.
                    # If RHP/FP URL exists, we only care about rhp_processed — never re-queue DRHP.
                    # If no RHP/FP, fall back to DRHP.
                    needs_resolve = False
                    resolve_reason = ""
                    if primary_url:
                        if not rhp_processed or needs_reresolve:
                            needs_resolve = True
                            resolve_reason = reresolve_reason if needs_reresolve else "rhp/fp not yet resolved"
                        # else: primary doc done → DRHP flag doesn't matter, move on
                    elif drhp_url:
                        if not drhp_processed or needs_reresolve:
                            needs_resolve = True
                            resolve_reason = reresolve_reason if needs_reresolve else "drhp not yet resolved"

                    if needs_resolve:
                        to_resolve.append({**ipo, "_resolve_reason": resolve_reason})
                    elif rhp_processed or drhp_processed:
                        if not unified_at:
                            to_parse.append(ipo)
                        else:
                            # Resolved + unified but not published/needs_review — edge state
                            skipped.append(f"{name} [resolved+unified, status={publish_status}]")
                    # else: no URL resolved yet and status not rejected/abridged → genuinely new/pending

                stats["skipped_count"] = len(skipped)
                stats["no_url_count"] = len(no_url)
                stats["pending_resolve"] = len(to_resolve)
                stats["pending_parse"] = len(to_parse)

                _log(f"Audit — {len(ipos)} checked: "
                     f"{len(to_resolve)} to resolve, {len(to_parse)} to parse, "
                     f"{len(skipped)} skipped (done), {len(no_url)} no URL")
                if no_url:
                    _log(f"  No URL: {', '.join(no_url[:10])}" + (" …" if len(no_url) > 10 else ""))
                if skipped:
                    _log(f"  Skipped (fully done): {', '.join(skipped[:10])}" + (" …" if len(skipped) > 10 else ""))
                for ipo in to_resolve:
                    _log(f"  → Resolve queued: {ipo.get('company_name')} ({ipo.get('_resolve_reason', '')})")
                for ipo in to_parse:
                    _log(f"  → Parse queued:   {ipo.get('company_name')}")

                _update_stats(0.3)

                # 3. Resolve Loop (SEQ — PDF memory is too heavy for parallel)
                stats["stage"] = "resolving"
                b = budget
                _log(f"Resources: {b['free_mb']}MB free / {b['total_mb']}MB total, "
                     f"load={b['load_1m']}/1m, resolve_seq, parse={b['parse_concurrency']}x")
                for idx, ipo in enumerate(to_resolve):
                    name = ipo.get("company_name", str(ipo.get("id")))
                    stats["current_ipo"] = name
                    stats["current_action"] = f"Resolving {name} ({idx+1}/{len(to_resolve)})"
                    _update_stats(0.3 + (0.3 * (idx / max(1, len(to_resolve)))))

                    try:
                        resp = await resolve_ipo_documents(ipo_id=ipo["id"], stream=stream)
                        sub_tid = resp["task_id"]

                        # poll
                        while True:
                            await asyncio.sleep(2)
                            sub_task = mgr.get(sub_tid)
                            if not sub_task or sub_task["status"] in ("completed", "failed"):
                                if sub_task and sub_task["status"] == "completed":
                                    sub_result = sub_task.get("result") or {}
                                    sections = sub_result.get("sections_found", "?")
                                    doc_type = sub_result.get("doc_type", "?")
                                    stats["resolved_success"] += 1
                                    stats["resolve_details"].append({
                                        "name": name, "status": "ok",
                                        "doc_type": doc_type, "sections_found": sections,
                                    })
                                    _log(f"  Resolve OK: {name} — {doc_type.upper()}, {sections} sections")
                                    to_parse.append(ipo)
                                else:
                                    err = (sub_task or {}).get("error", "unknown error")
                                    stats["resolved_failed"] += 1
                                    stats["resolve_details"].append({
                                        "name": name, "status": "failed", "error": err,
                                    })
                                    _log(f"  Resolve FAILED: {name} — {err}")
                                break
                    except Exception as e:
                        logger.error(f"Auto pipeline resolve failed for {ipo.get('id')}: {e}")
                        stats["resolved_failed"] += 1
                        stats["resolve_details"].append({"name": name, "status": "error", "error": str(e)})
                        _log(f"  Resolve ERROR: {name} — {e}")

                # 4. Parse Loop (PARALLEL — Firecrawl is network-only)
                stats["stage"] = "parsing"
                stats["pending_parse"] = len(to_parse)
                b = _get_resource_budget()  # re-check before parse phase
                parse_sem = asyncio.Semaphore(b["parse_concurrency"])

                async def _parse_and_poll(ipo: dict) -> dict:
                    """Parse one IPO with concurrency limit. Returns log entry."""
                    name = ipo.get("company_name", str(ipo.get("id")))
                    async with parse_sem:
                        try:
                            resp = await parse_ipo_sections_firecrawl(ipo_id=ipo["id"], force=False)
                            sub_tid = resp["task_id"]
                            poll_start = time.monotonic()
                            while True:
                                if time.monotonic() - poll_start > b["parse_timeout"]:
                                    return {"name": name, "status": "timeout"}
                                await asyncio.sleep(2)
                                sub_task = mgr.get(sub_tid)
                                if not sub_task or sub_task["status"] in ("completed", "failed"):
                                    if sub_task and sub_task["status"] == "completed":
                                        sr = sub_task.get("result") or {}
                                        return {"name": name, "status": "ok",
                                                "groups_parsed": sr.get("groups_parsed", "?"),
                                                "groups_skipped": sr.get("groups_skipped", "?")}
                                    err = (sub_task or {}).get("error", "unknown")
                                    return {"name": name, "status": "failed", "error": err}
                        except Exception as e:
                            return {"name": name, "status": "error", "error": str(e)}

                    return {"name": name, "status": "error", "error": "unreachable"}

                parse_results = await asyncio.gather(*[_parse_and_poll(i) for i in to_parse])
                for r in parse_results:
                    if r["status"] == "ok":
                        stats["parsed_success"] += 1
                        stats["parse_details"].append(r)
                        _log(f"  Parse OK: {r['name']} — {r['groups_parsed']} groups parsed, {r['groups_skipped']} cached/skipped")
                    elif r["status"] == "timeout":
                        stats["parsed_failed"] += 1
                        _log(f"  Parse TIMEOUT: {r['name']}")
                    else:
                        stats["parsed_failed"] += 1
                        err = r.get("error", "unknown")
                        stats["parse_details"].append(r)
                        _log(f"  Parse FAILED: {r['name']} — {err}")

                _log(f"Pipeline complete — resolved {stats['resolved_success']}/{stats['pending_resolve']}, "
                     f"parsed {stats['parsed_success']}/{stats['pending_parse']}")

                # 5. Subscription Data — refresh for all open/closed IPOs
                stats["stage"] = "subscription"
                stats["current_action"] = "Fetching subscription data for open/closed IPOs..."
                _update_stats(0.85)
                try:
                    from app.subscription_data.service import fetch_all_open as fetch_subs
                    sub_result = await fetch_subs()
                    stats["subscription"] = sub_result
                    _log(f"Subscription — {sub_result.get('fetched',0)} fetched, "
                         f"{sub_result.get('skipped',0)} skipped, {sub_result.get('failed',0)} failed")
                except Exception as e:
                    logger.warning("pipeline: subscription failed: %s", e)
                    stats["subscription"] = {"error": str(e)}
                    _log(f"Subscription FAILED — {e}")

                # 6. Historical Price Data — refresh for all eligible IPOs
                stats["stage"] = "historical"
                stats["current_action"] = "Fetching historical candle data..."
                _update_stats(0.92)
                try:
                    from app.historical_data.service import fetch_all_open as fetch_hist
                    hist_result = await fetch_hist()
                    stats["historical"] = hist_result
                    _log(f"Historical — {hist_result.get('fetched',0)} fetched, "
                         f"{hist_result.get('skipped',0)} skipped, {hist_result.get('failed',0)} failed")
                except Exception as e:
                    logger.warning("pipeline: historical failed: %s", e)
                    stats["historical"] = {"error": str(e)}
                    _log(f"Historical FAILED — {e}")

                stats["stage"] = "completed"
                stats["current_action"] = "Pipeline complete"
                stats["current_ipo"] = None

                # Send pipeline report (email + Telegram)
                try:
                    from app.pipeline_report import send_pipeline_report
                    send_pipeline_report(stats)
                    _log("Pipeline report sent (email + Telegram)")
                except Exception as report_err:
                    logger.warning("Failed to send pipeline report: %s", report_err)

                mgr.complete(tid, result=stats)

            asyncio.run(_run_async())
        except Exception as e:
            mgr.fail(tid, str(e))
            raise

    await run_in_background(task_id, _run)
    return {"task_id": task_id, "status": "started", "message": "Pipeline started in background. Poll GET /api/tasks/{task_id}"}


# ─── Subscription Data ─────────────────────────────────────
@app.post("/api/ipos/{ipo_id}/subscription", tags=["Aggregation"],
    summary="Fetch subscription data for an IPO from NSE (primary) or BSE (fallback)",
    description="Fetches category-wise subscription breakdown. Stores in ipo_master.subscription_latest.")
async def fetch_subscription(ipo_id: int = Path(...), _auth: None = Depends(_require_internal_key)):
    from app.subscription_data.service import fetch_and_store
    result = await fetch_and_store(ipo_id)
    if not result:
        raise HTTPException(404, "No subscription data available (IPO must be open/closed with a valid symbol)")
    return {"ipo_id": ipo_id, "data": result}


@app.post("/api/subscription/refresh", tags=["Aggregation"],
    summary="Refresh subscription data for all open/closed IPOs",
    description="Fetches subscription for every eligible IPO in one batch.")
async def refresh_all_subscriptions(_auth: None = Depends(_require_internal_key)):
    from app.subscription_data.service import fetch_all_open
    result = await fetch_all_open()
    return {"status": "ok", **result}


# ─── Historical Price Data ───────────────────────────────
@app.post("/api/ipos/{ipo_id}/historical", tags=["Aggregation"],
    summary="Fetch historical candle data for an IPO via Upstox API",
    description="Fetches daily O/H/L/C/volume via ISIN. Upserts in ipo_historical_prices table.")
async def fetch_historical(ipo_id: int = Path(...), _auth: None = Depends(_require_internal_key)):
    from app.historical_data.service import fetch_and_store
    result = await fetch_and_store(ipo_id)
    if not result:
        raise HTTPException(404, "No historical data available (need ISIN + valid Upstox token)")
    return {"ipo_id": ipo_id, "data": result}


@app.post("/api/historical/refresh", tags=["Aggregation"],
    summary="Refresh historical prices for all open/closed/listed IPOs",
    description="Fetches candle data for every eligible IPO. Daily cron target.")
async def refresh_all_historical(_auth: None = Depends(_require_internal_key)):
    from app.historical_data.service import fetch_all_open
    result = await fetch_all_open()
    return {"status": "ok", **result}


@app.get("/api/ipos/{ipo_id}/historical", tags=["Aggregation"],
    summary="Get stored historical price data for an IPO",
    description="Returns the latest candle summary + full candle array for charting.")
async def get_historical(ipo_id: int = Path(...)):
    from app.db.engine import get_session
    from app.db.models import IPOHistoricalPrice
    with get_session() as s:
        rec = s.query(IPOHistoricalPrice).filter(
            IPOHistoricalPrice.ipo_master_id == ipo_id
        ).first()
    if not rec:
        raise HTTPException(404, "No historical data found. Run POST /api/ipos/{id}/historical first.")
    return {
        "ipo_id": rec.ipo_master_id,
        "isin": rec.isin,
        "exchange_type": rec.exchange_type,
        "fetch_date": rec.fetch_date,
        "last_updated": rec.last_updated.isoformat() if rec.last_updated else None,
        "summary": {
            "open": rec.open, "high": rec.high, "low": rec.low, "close": rec.close,
            "volume": rec.volume, "prev_close": rec.prev_close,
            "change_pct": rec.change_pct, "color": rec.color,
            "num_candles": rec.num_candles,
        },
        "candles": rec.candles or [],
    }


# ─── GMP Data ──────────────────────────────────────────────
@app.post("/api/gmp/refresh", tags=["Aggregation"],
    summary="Refresh GMP data from Chittorgarh/Investorgain",
    description="Fetches current GMP snapshot for all IPOs + daily history for active ones. Stores in ipo_master.gmp_latest.")
async def refresh_gmp(_auth: None = Depends(_require_internal_key)):
    from app.gmp_data.service import fetch_and_store_all
    result = await fetch_and_store_all()
    return {"status": "ok", **result}


@app.get("/api/ipos/{ipo_id}/gmp", tags=["Aggregation"],
    summary="Get stored GMP data for an IPO",
    description="Returns current GMP snapshot + daily history.")
async def get_gmp(ipo_id: int = Path(...)):
    from app.db.engine import get_session
    from app.db.models import IPOMaster
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
    if not ipo or not ipo.gmp_latest:
        raise HTTPException(404, "No GMP data found. Run POST /api/gmp/refresh first.")
    return {"ipo_id": ipo_id, "company_name": ipo.company_name, "data": ipo.gmp_latest}


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


@app.get("/dashboard", tags=["Dashboard"],
    summary="Serve the v1 dashboard SPA",
    description="Single-file HTML dashboard with Groww theme, CDN Tailwind, dark mode. "
                "Reads dashboard.html from disk and returns it as a web page.")
async def dashboard_page():
    """Serve the dashboard HTML directly from the API — no separate server needed."""
    dash_path = FilePath(__file__).resolve().parent.parent / "dashboard" / "dashboard.html"
    if not dash_path.exists():
        raise HTTPException(404, "dashboard.html not found — deploy the dashboard/ folder")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(dash_path.read_text(encoding="utf-8"))

@app.post("/api/clear-db", tags=["System"],
    summary="WARNING: Clears all parsed data and IPOs from the database")
async def clear_database(
    confirm: str = Query(..., description="Pass ?confirm=yes to actually clear"),
    _auth: None = Depends(_require_internal_key),
):
    if confirm != "yes":
        return {"error": "pass ?confirm=yes"}
    from sqlalchemy import text
    from .db.engine import get_session
    with get_session() as s:
        s.execute(text("DELETE FROM document_tables"))
        s.execute(text("UPDATE document_sections SET parsed=0, parsed_data=NULL, parsed_md_sha256=NULL, parsed_at=NULL"))
        s.execute(text("UPDATE ipo_master SET unified_data=NULL, unified_provenance=NULL, unified_version=0, unified_updated_at=NULL, confidence_score=0.0, publish_status='new', validation_issues=NULL"))
        s.execute(text("DELETE FROM ipo_documents"))
        s.execute(text("DELETE FROM ipo_master"))
        s.commit()
    return {"status": "ok", "message": "Database cleared"}


@app.get("/api/dashboard/logs", response_model=list[ScraperLogItem], tags=["Dashboard"])
async def dashboard_logs(limit: int = Query(50, ge=1, le=200)):
    rows = db_service.get_recent_logs(limit=limit)
    return [
        ScraperLogItem(
            id=r.id, scraper_type=r.scraper_type, action=r.action,
            status=r.status, company_name=r.company_name, message=r.message,
            error_details=r.error_details, execution_time_ms=r.execution_time_ms,
            new_ipos_found=r.new_ipos_found, status_changes=r.status_changes,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in rows
    ]


# ─── System Usage ──────────────────────────────────────────────────

import subprocess as _subprocess
import time as _time
from pathlib import Path as _FilePath


@app.get("/api/system/usage", tags=["System"],
    summary="All operational metrics: RAM, CPU, disk, DB, R2, Firecrawl credits, config")
async def system_usage():
    """Gather system-level operational metrics for the dashboard."""
    result: dict[str, Any] = {}

    # RAM
    try:
        mem = _FilePath("/proc/meminfo").read_text()
        total = avail = 0
        for line in mem.splitlines():
            if line.startswith("MemTotal:"): total = int(line.split()[1]) // 1024
            elif line.startswith("MemAvailable:"): avail = int(line.split()[1]) // 1024
        result["ram"] = {"total_mb": total, "available_mb": avail,
                         "used_mb": total - avail,
                         "used_pct": round((total - avail) / total * 100, 1) if total else 0}
    except Exception:
        result["ram"] = {"total_mb": 0, "available_mb": 0, "used_mb": 0, "used_pct": 0}

    # CPU load
    try:
        load = _FilePath("/proc/loadavg").read_text().split()
        result["cpu"] = {"load_1m": float(load[0]), "load_5m": float(load[1]), "load_15m": float(load[2])}
    except Exception:
        result["cpu"] = {"load_1m": 0, "load_5m": 0, "load_15m": 0}

    # Uptime
    try:
        uptime = _FilePath("/proc/uptime").read_text().split()
        result["uptime_seconds"] = float(uptime[0])
    except Exception:
        result["uptime_seconds"] = 0

    # Disk
    try:
        df = _subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        parts = df.stdout.splitlines()[1].split()
        result["disk"] = {"size": parts[1], "used": parts[2], "avail": parts[3], "used_pct": parts[4].rstrip("%")}
    except Exception:
        result["disk"] = {"size": "?", "used": "?", "avail": "?", "used_pct": "?"}

    # Database
    try:
        from .db.engine import get_session as _db_session
        from sqlalchemy import text as _sql
        with _db_session() as s:
            size = s.execute(_sql("SELECT pg_database_size(current_database())")).scalar()
            result["database"] = {"size_bytes": size, "size_mb": round(size / 1024 / 1024, 1) if size else 0}
            result["database"]["tables"] = {}
            for tbl in ("ipo_master", "document_sections", "document_tables",
                        "scraper_logs", "ipo_status_history", "documents"):
                try:
                    result["database"]["tables"][tbl] = s.execute(_sql(f"SELECT COUNT(*) FROM {tbl}")).scalar()
                except Exception:
                    pass
    except Exception as e:
        result["database"] = {"error": str(e)[:200]}

    # R2
    if settings.r2_enabled:
        try:
            import boto3
            from botocore.client import Config
            r2 = boto3.client("s3", endpoint_url=settings.r2_endpoint,
                              aws_access_key_id=settings.r2_access_key_id,
                              aws_secret_access_key=settings.r2_secret_access_key,
                              config=Config(signature_version="s3v4"), region_name="auto")
            total_size = total_objs = 0
            truncated, marker = True, ""
            while truncated:
                args: dict = {"Bucket": settings.r2_bucket, "MaxKeys": 1000}
                if marker: args["Marker"] = marker
                resp = r2.list_objects_v2(**args)
                contents = resp.get("Contents", [])
                total_objs += len(contents)
                total_size += sum(o.get("Size", 0) for o in contents)
                truncated = resp.get("IsTruncated", False)
                if truncated and contents: marker = contents[-1]["Key"]
                else: break
            result["r2"] = {"bucket": settings.r2_bucket, "object_count": total_objs,
                            "size_bytes": total_size, "size_mb": round(total_size / 1024 / 1024, 1)}
        except Exception as e:
            result["r2"] = {"error": str(e)[:200]}
    else:
        result["r2"] = {"error": "not configured"}

    # Firecrawl credits
    if settings.firecrawl_api_key:
        try:
            import httpx as _httpx2
            resp = _httpx2.get("https://api.firecrawl.dev/v1/team",
                               headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"}, timeout=10)
            if resp.status_code == 200:
                d = resp.json()
                result["firecrawl"] = {"credits_used": d.get("creditsUsed", 0),
                                        "credits_remaining": d.get("creditsRemaining", 0),
                                        "plan": d.get("plan", "unknown")}
            else:
                result["firecrawl"] = {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            result["firecrawl"] = {"error": str(e)[:200]}
    else:
        result["firecrawl"] = {"error": "not configured"}

    # Config
    result["config"] = {"parser_provider": settings.parser_provider,
                         "r2_configured": settings.r2_enabled,
                         "deepseek_configured": bool(settings.deepseek_api_key),
                         "firecrawl_configured": bool(settings.firecrawl_api_key),
                         "db_dialect": settings.db_dialect, "api_version": settings.version}

    return result


# ─── Helpers ──────────────────────────────────────────────────
def _format_ipo(ipo) -> IPOSummary:
    """Format IPO for API response. Accepts IPOMaster ORM object or dict."""
    if hasattr(ipo, 'to_dict'):
        d = ipo.to_dict()
        docs = d.get("documents", {})
        upstox_raw = d.get("upstox_data")
        symbol = (upstox_raw or {}).get("symbol") if isinstance(upstox_raw, dict) else None
        sub = d.get("subscription_latest")
        return IPOSummary(
            id=d.get("id", 0), company_name=d.get("company_name", ""),
            status=d.get("status", "unknown"),
            dates={"drhp_filed": d.get("drhp_filed_date"), "rhp_filed": d.get("rhp_filed_date"),
                   "fp_filed": d.get("fp_filed_date"), "open": d.get("open_date"), "close": d.get("close_date")},
            documents={"drhp": docs.get("drhp"), "rhp": docs.get("rhp"), "final_prospectus": docs.get("final_prospectus")},
            price_band=d.get("price_band"), platform=d.get("platform"),
            issue_type=d.get("issue_type"), upstox_data=upstox_raw,
            symbol=symbol, subscription_latest=sub,
        )
    # Legacy dict fallback
    docs = ipo.get("documents", {}) if isinstance(ipo, dict) else {}
    if isinstance(docs, dict):
        clean_docs = {"drhp": docs.get("drhp"), "rhp": docs.get("rhp"), "final_prospectus": docs.get("final_prospectus")}
    else:
        clean_docs = {"drhp": ipo.get("drhp_url"), "rhp": ipo.get("rhp_url"), "final_prospectus": ipo.get("final_prospectus_url")}
    from .schemas import UpstoxData
    upstox_raw = ipo.get("upstox_data") if isinstance(ipo, dict) else None
    upstox_obj = UpstoxData(**upstox_raw) if isinstance(upstox_raw, dict) else None
    return IPOSummary(id=ipo.get("id", 0), company_name=ipo["company_name"], status=ipo.get("status", "unknown"),
        dates={"drhp_filed": ipo.get("drhp_filed_date"), "rhp_filed": ipo.get("rhp_filed_date"),
               "fp_filed": ipo.get("fp_filed_date"), "open": ipo.get("open_date"), "close": ipo.get("close_date")},
        documents=clean_docs, price_band=ipo.get("price_band"), platform=ipo.get("platform"),
        issue_type=ipo.get("issue_type"), upstox_data=upstox_obj)
