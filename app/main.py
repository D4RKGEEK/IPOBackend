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

app = FastAPI(title="IPO Aggregation API", version="3.0.0", description=DESCRIPTION)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ─── Services ─────────────────────────────────────────────────
db_service = DatabaseService()


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
        from app.db.operations import DatabaseService
        db = DatabaseService()
        ipo = db.get_ipo_by_id(ipo_id)
        if not ipo:
            return {"error": "IPO not found"}
        rhp_url = getattr(ipo, 'rhp_url', None)
        if not rhp_url:
            return {"error": "no RHP URL available"}

        import httpx, pdfplumber, tempfile, os, time as _time
        from tabulate import tabulate
        from app.section_resolver import _extract_page_structured, _clean_headers_rows

        t0 = _time.time()
        resp = httpx.get(rhp_url, timeout=120, follow_redirects=True)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}"}

        tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        tmp_path = tmp.name
        tmp.close()
        with open(tmp_path, 'wb') as f:
            f.write(resp.content)

        plumber = pdfplumber.open(tmp_path)
        total_pages = len(plumber.pages)
        results = []

        if effective_section and not page_num:
            # Multiple pages from a section (~0.5s per page)
            for pn in pages_to_extract:
                idx = pn - 1
                if idx < 0 or idx >= len(plumber.pages):
                    continue
                page = plumber.pages[idx]
                _, tables = _extract_page_structured(page, tabulate)
                for t in tables:
                    t["page_num"] = pn
                    t["doc_type"] = doc_type or "rhp"
                    t["section_name"] = effective_section
                    results.append(t)
            elapsed = _time.time() - t0
        elif page_num:
            # Single page (~0.5s)
            idx = page_num - 1
            if idx < 0 or idx >= len(plumber.pages):
                plumber.close()
                os.unlink(tmp_path)
                return {"error": f"page_num {page_num} out of range (1-{len(plumber.pages)})"}
            page = plumber.pages[idx]
            _, tables = _extract_page_structured(page, tabulate)
            for t in tables:
                t["page_num"] = page_num
                t["doc_type"] = doc_type or "rhp"
                t["section_name"] = effective_section or "all"
                results.append(t)
            elapsed = _time.time() - t0
        else:
            # ALL pages
            for pn in range(len(plumber.pages)):
                page = plumber.pages[pn]
                try:
                    _, tables = _extract_page_structured(page, tabulate)
                    for t in tables:
                        t["page_num"] = pn + 1
                        t["doc_type"] = doc_type or "rhp"
                        t["section_name"] = effective_section or "all"
                        results.append(t)
                except MemoryError:
                    results.append({"error": f"OOM on page {pn+1}"})
                    break
            elapsed = _time.time() - t0

        plumber.close()
        os.unlink(tmp_path)

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
            asyncio.run(run_scrape(
                year=year or 2026, sources=sources, progress_callback=on_progress,
            ))
            mgr.update(tid, 1.0, "Complete")
            return {"status": "ok", "message": "Scrape completed"}
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

@app.post("/api/clear-db", tags=["System"],
    summary="WARNING: Clears all parsed data and IPOs from the database")
async def clear_database(
    confirm: str = Query(..., description="Pass ?confirm=yes to actually clear"),
    internal_key: str = Query(None, description="Internal API key for auth"),
):
    if confirm != "yes":
        return {"error": "pass ?confirm=yes"}
    from app.config import settings
    if internal_key != settings.internal_api_key:
        return {"error": "invalid internal key"}
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
    return db_service.get_recent_logs(limit=limit)


# ─── Helpers ──────────────────────────────────────────────────
def _format_ipo(ipo) -> IPOSummary:
    """Format IPO for API response. Accepts IPOMaster ORM object or dict."""
    if hasattr(ipo, 'to_dict'):
        d = ipo.to_dict()
        docs = d.get("documents", {})
        upstox_raw = d.get("upstox_data")
        return IPOSummary(
            id=d.get("id", 0), company_name=d.get("company_name", ""),
            status=d.get("status", "unknown"),
            dates={"drhp_filed": d.get("drhp_filed_date"), "rhp_filed": d.get("rhp_filed_date"),
                   "fp_filed": d.get("fp_filed_date"), "open": d.get("open_date"), "close": d.get("close_date")},
            documents={"drhp": docs.get("drhp"), "rhp": docs.get("rhp"), "final_prospectus": docs.get("final_prospectus")},
            price_band=d.get("price_band"), platform=d.get("platform"),
            issue_type=d.get("issue_type"), upstox_data=upstox_raw,
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
