"""
IPO Aggregation API v3.0 — DB-Backed

Architecture:
  Scraper (background/cron) → writes to SQLite DB → API reads from DB → your frontend

The API always returns consistent data. Live-scraping is opt-in via ?live=true.
"""
import os
# Fix SSL cert path for httpx on macOS
os.environ.setdefault("SSL_CERT_FILE", "/opt/homebrew/etc/openssl@3/cert.pem")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "/opt/homebrew/etc/openssl@3/cert.pem")

import asyncio
import logging
from datetime import datetime, timezone
from math import ceil
from typing import Literal, Optional, Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Path

from .clients import (
    BSEClient,
    BSESmeClient,
    NSEClient,
    SEBIClient,
    merge_bse_into_results,
    merge_bse_sme_docs,
    merge_nse_into_results,
)
from .schemas import (
    IPOResponse, IPOSummary, IPOSummarySource,
    Meta, Pagination,
    StatusChangeItem, ScraperLogItem, RefreshResult,
    IPODetail, DocumentTextInfo, StatusHistoryEntry,
)
from .status import compute_status, compute_dates, compute_documents
from .scraper_service import ScraperService, _record_to_ipo_data, _source_count
from .db_service import DatabaseService
from .parsers.pipeline import parse_all_available, parse_document

logger = logging.getLogger(__name__)

# ─── App Setup ───────────────────────────────────────────────

DESCRIPTION = """
# IPO Aggregation API v3

**DB-backed — always fast, always consistent.**

IPOs are scraped in the background and stored in a local database.
The API reads from the database, so it always returns results immediately
even if SEBI/BSE/NSE are down.

## Quick Start

```
# List all IPOs (reads from local DB — instant)
curl http://127.0.0.1:8001/api/ipos

# Trigger a full re-scrape of all sources
curl -X POST http://127.0.0.1:8001/api/refresh

# See what's changed recently
curl http://127.0.0.1:8001/api/status-changes
```

## Data lifecycle

| Step | What happens |
|------|-------------|
| Scrape | Runs via cron or `GET /api/refresh`. Scrapes SEBI, BSE, NSE concurrently. |
| Diff | Diffs scraped data against DB. Detects **new IPOs** and **status changes**. |
| Store | New IPOs inserted. Status changes logged in `status_history`. |
| Serve | `GET /api/ipos` reads from DB — instant, reliable, paginated. |
"""

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="IPO Aggregation API",
    version="3.0.0",
    description=DESCRIPTION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Services ─────────────────────────────────────────────────

db_service = DatabaseService()
scraper_service = ScraperService(db=db_service)


# ─── Health ───────────────────────────────────────────────────

@app.get("/health", tags=["System"])
async def health() -> dict[str, Any]:
    """Health check — database status and IPO count."""
    try:
        stats = db_service.get_dashboard_stats()
        return {
            "status": "ok",
            "database": "connected",
            "total_ipos": stats.get("total_ipos", 0),
            "avg_confidence": stats.get("avg_confidence", 0.0),
            "last_scrape": stats.get("latest_scrape"),
        }
    except Exception as e:
        return {"status": "error", "database": "error", "error": str(e)}


# ─── Main API — Read from DB ─────────────────────────────────

@app.get(
    "/api/ipos",
    response_model=IPOResponse,
    tags=["Aggregation"],
    summary="List all IPOs (DB-backed, fast)",
    description="""
Returns IPO data from the local database. Fast and reliable — no live scraping.

**Filters** are applied server-side in SQL for fast pagination.

**Use `?live=true`** to bypass the DB and do a live scrape instead (slower, hits SEBI/BSE/NSE).
""",
)
async def get_ipos(
    status: str = Query(
        "all", description="Filter by IPO lifecycle status: drhp_filed, sebi_approved, rhp_filed, upcoming, open, closed, listed, all"
    ),
    platform: str = Query(
        "all", description="Filter by platform: mainboard, sme, all"
    ),
    search: str = Query("", description="Search by company name (case-insensitive)"),
    year: Optional[int] = Query(None, description="Filter by filing year (e.g. 2026)"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(25, ge=1, le=100, description="Records per page"),
    live: bool = Query(False, description="[Deprecated] Use DB-backed endpoints instead"),
    raw: bool = Query(
        False, description="Include full source-level data in response"
    ),
):
    if live:
        raise HTTPException(status_code=400, detail="live=true is deprecated. Use default DB-backed endpoint.")
    
    # DB-backed — fast, always works
    ipos, total = db_service.get_all_ipos(
        status=status,
        platform=platform,
        search=search,
        year=year,
        page=page,
        per_page=per_page,
    )
    
    return IPOResponse(
        data=[_format_ipo(ipo, raw=raw) for ipo in ipos],
        pagination=Pagination(
            total_records=total,
            current_page=page,
            per_page=per_page,
            total_pages=max(1, ceil(total / per_page)) if total else 1,
        ),
        meta=Meta(
            sources_queried=["database"],
            errors=[],
            notes=["Data from local DB. Use ?live=true for live scrape."],
        ),
    )


@app.get(
    "/api/ipos/{ipo_id}",
    response_model=IPODetail,
    tags=["Aggregation"],
    summary="Get a single IPO by ID — includes status history and extracted text",
    description="""
Returns everything for one IPO:
- Company info, status, dates, documents
- Which documents have been processed (text extracted)
- Status history (every status change ever detected)
- Extracted document text (preview + metadata)
- Raw source data (with ?raw=true)
""",
)
async def get_ipo_by_id(
    ipo_id: int = Path(..., description="IPO database ID"),
    raw: bool = Query(False, description="Include source-level data"),
    text_preview: int = Query(
        500, ge=0, le=5000,
        description="Max chars of text preview per document (0 = no preview)"
    ),
):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(status_code=404, detail="IPO not found")
    
    d = ipo.to_dict()
    
    # Status history
    status_history = db_service.get_status_history(ipo_id, limit=50)
    
    # Documents are nested in to_dict() under "documents" key
    docs = d.get("documents", {})
    if not docs or not isinstance(docs, dict):
        docs = {
            "drhp": d.get("drhp_url"),
            "rhp": d.get("rhp_url"),
            "final_prospectus": d.get("final_prospectus_url"),
            "abridged_prospectus": d.get("abridged_prospectus_url"),
        }
    
    # Document texts
    doc_texts = {}
    for doc_type in ("drhp", "rhp", "final_prospectus"):
        url = docs.get(doc_type)
        processed = bool(d.get(f"{doc_type}_processed", 0))
        # Also check direct DB field as fallback
        if not url:
            url = d.get(f"{doc_type}_url")
        text = db_service.get_document_text(ipo_id, doc_type)
        doc_texts[doc_type] = DocumentTextInfo(
            processed=processed or bool(text),
            char_count=len(text) if text else 0,
            source_url=url,
            text_preview=text[:text_preview] if text and text_preview > 0 else None,
        )
    
    # Build the full detail response
    return IPODetail(
        id=ipo.id,
        company_name=d["company_name"],
        normalized_name=d.get("normalized_name", ""),
        status=d.get("status", "unknown"),
        dates={
            "drhp_filed": d.get("drhp_filed_date"),
            "rhp_filed": d.get("rhp_filed_date"),
            "fp_filed": d.get("fp_filed_date"),
            "open": d.get("open_date"),
            "close": d.get("close_date"),
        },
        documents=docs,
        documents_processed={
            "drhp": bool(d.get("drhp_processed", 0)),
            "rhp": bool(d.get("rhp_processed", 0)),
            "final_prospectus": bool(d.get("final_prospectus_processed", 0)),
        },
        price_band=d.get("price_band"),
        platform=d.get("platform"),
        issue_type=d.get("issue_type"),
        data_confidence=d.get("data_confidence", 0.0),
        source_count=d.get("source_count", 0),
        first_seen=d.get("first_seen"),
        last_updated=d.get("last_updated"),
        last_scraped=d.get("last_scraped"),
        # Source data: read directly from ORM object
        raw=IPOSummarySource(
            sebi=ipo.sebi_data,
            bse=ipo.bse_data,
            nse=ipo.nse_data,
            bse_sme=ipo.bse_sme_data,
        ) if raw else None,
        status_history=[
            StatusHistoryEntry(**h) for h in status_history
        ],
        document_texts=doc_texts,
    )


@app.post(
    "/api/ipos/{ipo_id}/resolve",
    tags=["Aggregation"],
    summary="Resolve documents and extract text for a single IPO",
    description="""
Downloads document URLs (ZIP or PDF) for this IPO, extracts text,
and stores it in the database.

Returns: which documents were processed, char counts, and any errors.
""",
)
async def resolve_ipo_documents(
    ipo_id: int = Path(..., description="IPO database ID"),
):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(status_code=404, detail="IPO not found")
    
    import httpx
    from app.pdf_utils import extract_document
    from app.db_models import get_session, IPODocument

    d = ipo.to_dict()
    docs = d.get("documents", {})
    
    results = {}
    errors = []
    
    try:
        # Ensure we have ipo_documents records for existing URLs
        for doc_type in ("drhp", "rhp", "final_prospectus"):
            url = docs.get(doc_type)
            if url:
                db_service.upsert_document(ipo_id, doc_type, url)
        
        async with httpx.AsyncClient(follow_redirects=True, timeout=180) as client:
            for doc_type in ("drhp", "rhp", "final_prospectus"):
                url = docs.get(doc_type)
                if not url:
                    results[doc_type] = {"status": "skipped", "reason": "no URL"}
                    continue
                
                existing = db_service.get_document_text(ipo_id, doc_type)
                if existing:
                    db_service.update_document_phase_by_ipo(ipo_id, doc_type, "downloaded")
                    results[doc_type] = {"status": "already_done", "chars": len(existing)}
                    continue

                # Quick connectivity check (skip for NSE servers — too slow)
                if "nsearchives" not in url and "nseindia" not in url:
                    try:
                        head_check = await client.head(url, timeout=5)
                        if head_check.status_code >= 400:
                            results[doc_type] = {"status": "skipped", "reason": f"HTTP {head_check.status_code}"}
                            errors.append({"doc_type": doc_type, "error": f"HTTP {head_check.status_code}"})
                            continue
                    except Exception:
                        results[doc_type] = {"status": "skipped", "reason": "server unreachable"}
                        errors.append({"doc_type": doc_type, "error": "connection timeout or DNS error"})
                        continue
                
                try:
                    result = await extract_document(url, client)
                    text = result["text"] if result else None
                    if text:
                        db_service.save_document_text(ipo_id, doc_type, text, url)
                        db_service.mark_document_processed(ipo_id, doc_type)
                        db_service.update_document_phase_by_ipo(ipo_id, doc_type, "downloaded")
                        results[doc_type] = {"status": "ok", "chars": len(text)}
                    else:
                        errors.append({"doc_type": doc_type, "error": "no text extracted"})
                        results[doc_type] = {"status": "failed", "reason": "no text extracted"}
                except Exception as e:
                    errors.append({"doc_type": doc_type, "error": str(e)})
                    results[doc_type] = {"status": "error", "reason": str(e)[:100]}
    except Exception as e:
        import traceback
        return {"ipo_id": ipo_id, "company_name": ipo.company_name, "status": "error", "error": str(e)[:200]}
    
    return {"ipo_id": ipo_id, "company_name": d["company_name"], "results": results, "errors": errors}


@app.get(
    "/api/ipos/{ipo_id}/text/{doc_type}",
    tags=["Aggregation"],
    summary="Get full extracted document text",
    description="""
Returns the complete extracted text for a specific document of an IPO.

doc_type: drhp, rhp, or final_prospectus

Returns text as plain text (not JSON) for easy reading and processing.
""",
)
async def get_ipo_document_text(
    ipo_id: int = Path(..., description="IPO database ID"),
    doc_type: str = Path(..., description="Document type: drhp, rhp, or final_prospectus"),
):
    if doc_type not in ("drhp", "rhp", "final_prospectus"):
        raise HTTPException(status_code=400, detail=f"Invalid doc_type: {doc_type}. Use drhp, rhp, or final_prospectus.")
    
    text = db_service.get_document_text(ipo_id, doc_type)
    if not text:
        # Check if the IPO exists at all
        ipo = db_service.get_ipo_by_id(ipo_id)
        if not ipo:
            raise HTTPException(status_code=404, detail="IPO not found")
        raise HTTPException(
            status_code=404,
            detail=f"No extracted text for {doc_type} document. "
                   f"Run POST /api/ipos/{ipo_id}/resolve first."
        )
    
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(text)


@app.post(
    "/api/ipos/{ipo_id}/parse",
    tags=["Parsing"],
    summary="Run Phase 2 parsing pipeline",
    description="""
Complete parsing pipeline:
1. Collects all documents with extracted text
2. Runs regex extractors on each
3. Merges results across document types
4. Auto-falls back to DeepSeek if API key configured
5. Saves structured data to DB

Returns issue details, company info, financials, KPIs, promoters, etc.
No null fields — defaults provided.
""",
)
async def parse_ipo_documents(
    ipo_id: int = Path(..., description="IPO database ID"),
    use_deepseek: bool = Query(True, description="Use DeepSeek as fallback for table-heavy sections"),
):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(status_code=404, detail="IPO not found")
    
    from app.parsers.pipeline_v2 import parse_ipo as run_pipeline
    
    try:
        result = run_pipeline(ipo_id, use_deepseek=use_deepseek)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    if result.get("status") == "no_text":
        raise HTTPException(
            status_code=400,
            detail=result["message"],
        )
    
    return result


@app.get(
    "/api/ipos/{ipo_id}/sections",
    tags=["Parsing"],
    summary="Get extracted sections for an IPO's documents",
    description="""
Returns the sections extracted from an IPO's documents
with character counts and field predictions per section.
""",
)
async def get_ipo_sections(
    ipo_id: int = Path(..., description="IPO database ID"),
):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(status_code=404, detail="IPO not found")
    
    from app.parsers.pipeline_v2 import extract_sections, SECTION_FIELDS
    
    result = {"ipo_id": ipo_id, "company_name": ipo.company_name, "documents": {}}
    
    for doc_type in ("drhp", "rhp", "final_prospectus"):
        text = db_service.get_document_text(ipo_id, doc_type)
        if not text:
            continue
        
        sections = {}
        for name, section_text in extract_sections(text).items():
            fields = SECTION_FIELDS.get(name, [])
            sections[name] = {
                "chars": len(section_text),
                "predicted_fields": fields,
            }
        
        result["documents"][doc_type] = {
            "total_chars": len(text),
            "section_count": len(sections),
            "sections": sections,
        }
    
    return result


@app.get(
    "/api/ipos/{ipo_id}/parsed-data",
    tags=["Parsing"],
    summary="Get parsed IPO data",
    description="""
Returns structured data previously extracted by POST /api/ipos/{id}/parse.

Includes: issue details, capital structure, financials, promoters, 
intermediaries, and more — all with defaults so no fields are null.
""",
)
async def get_parsed_ipo_data(
    ipo_id: int = Path(..., description="IPO database ID"),
):
    # Check if IPO exists
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(status_code=404, detail="IPO not found")
    
    # Get parsed data
    parsed = db_service.get_parsed_ipo_data(ipo_id, "merged")
    if parsed:
        return {
            "ipo_id": ipo_id,
            **parsed,
        }
    
    # Check if text exists but hasn't been parsed yet
    has_text = any([
        db_service.get_document_text(ipo_id, "drhp"),
        db_service.get_document_text(ipo_id, "rhp"),
        db_service.get_document_text(ipo_id, "final_prospectus"),
    ])
    
    if has_text:
        raise HTTPException(
            status_code=404,
            detail="Text exists but not yet parsed. Run POST /api/ipos/{id}/parse first."
        )
    
    raise HTTPException(
        status_code=404,
        detail="No extracted text or parsed data. Run POST /api/ipos/{id}/resolve then POST /api/ipos/{id}/parse."
    )


@app.get(
    "/api/ipos/{ipo_id}/parsed-history",
    tags=["Parsing"],
    summary="Get all parsed data versions for an IPO",
    description="Returns all parsing runs for this IPO, ordered newest first.",
)
async def get_parsed_data_history(
    ipo_id: int = Path(..., description="IPO database ID"),
):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(status_code=404, detail="IPO not found")
    
    history = db_service.get_parsed_data_history(ipo_id)
    return {"ipo_id": ipo_id, "company_name": ipo.company_name, "history": history}


# ─── Status Changes ──────────────────────────────────────────

@app.get(
    "/api/status-changes",
    response_model=list[StatusChangeItem],
    tags=["Aggregation"],
    summary="Recent IPO status changes",
    description="Returns a chronological list of every detected status change across all IPOs.",
)
async def get_status_changes(
    limit: int = Query(50, ge=1, le=200, description="Number of changes to return"),
):
    return db_service.get_recent_status_changes(limit=limit)


# ─── Refresh (Trigger Scrape) ───────────────────────────────

@app.post(
    "/api/refresh",
    response_model=RefreshResult,
    tags=["Aggregation"],
    summary="Trigger a full re-scrape of all sources",
    description="""
Scrapes SEBI, BSE, and NSE concurrently, diffs against the database,
and saves any new IPOs or status changes.

Returns a report with: total found, new IPOs, status changes, errors, timing.

**With `?resolve_docs=true`**, also downloads ZIPs and extracts PDF text for unprocessed documents.
""",
)
async def refresh(
    resolve_docs: bool = Query(
        False, description="Also resolve ZIP URLs and extract PDF text into DB"
    ),
    resolve_limit: int = Query(
        50, ge=1, le=500, description="Max documents to resolve if resolve_docs=true"
    ),
    year: Optional[int] = Query(
        None, description="Only scrape IPOs from a specific year (e.g. 2026)"
    ),
):
    report = await scraper_service.run_full_scrape(
        bse_sme=True,
        include_pdf_urls=True,
        resolve_docs=resolve_docs,
        resolve_doc_limit=resolve_limit,
        year=year,
    )
    return report


# ─── Dashboard ───────────────────────────────────────────────

@app.get(
    "/api/dashboard/stats",
    tags=["Dashboard"],
    summary="Dashboard statistics and health",
)
async def dashboard_stats():
    """Get comprehensive dashboard statistics."""
    stats = db_service.get_dashboard_stats()
    stats["api_version"] = "3.0.0"
    return stats


@app.get(
    "/api/dashboard/logs",
    response_model=list[ScraperLogItem],
    tags=["Dashboard"],
    summary="Recent scraper logs",
)
async def dashboard_logs(
    limit: int = Query(50, ge=1, le=200, description="Number of logs to return"),
):
    return db_service.get_recent_logs(limit=limit)


# ─── Source-specific Endpoints (for debugging) ─────────────

@app.get("/api/sebi/filings", tags=["SEBI (debug)"])
async def get_sebi_filings(
    document_type: Literal["DRHP", "RHP"] = Query("DRHP"),
    page: int = Query(1, ge=1),
    search: str = Query(""),
    from_date: str = Query(""),
    to_date: str = Query(""),
):
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        return await SEBIClient(client).fetch_filings(
            page=page, document_type=document_type,
            from_date=from_date, to_date=to_date, search=search,
        )


@app.get("/api/sebi/detail", tags=["SEBI (debug)"])
async def get_sebi_detail(detail_url: str = Query(...)):
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        result = await SEBIClient(client).fetch_detail_page(detail_url)
    return {"detail_page_url": detail_url, **result}


@app.get("/api/bse/ipos", tags=["BSE (debug)"])
async def get_bse_ipos():
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        return await BSEClient(client).fetch_ipos()


@app.get("/api/nse/offer-docs", tags=["NSE (debug)"])
async def get_nse_offer_docs(
    index: Literal["equities", "sme", "all"] = Query("all"),
    from_date: str = Query(""),
    to_date: str = Query(""),
):
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        nse_client = NSEClient(client)
        return await nse_client.fetch_all_docs(from_date=from_date, to_date=to_date)


@app.get("/api/bse-sme/drhp", tags=["BSE SME (debug)"])
async def get_bse_sme_drhp():
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        return await BSESmeClient(client).fetch_drhp_list()


@app.get("/api/bse-sme/rhp", tags=["BSE SME (debug)"])
async def get_bse_sme_rhp():
    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        return await BSESmeClient(client).fetch_rhp_list()


@app.get("/api/normalize-company-name", tags=["Utilities"])
async def normalize_company(name: str = Query(...)):
    from .utils import normalize_company_name
    return {"input": name, "normalized": normalize_company_name(name)}


# ─── Helpers ──────────────────────────────────────────────────

def _format_ipo(ipo: dict[str, Any], raw: bool = False) -> IPOSummary:
    """Convert a DB record dict into an IPOSummary response."""
    docs = ipo.get("documents", {})
    result = IPOSummary(
        id=ipo.get("id", 0),
        company_name=ipo["company_name"],
        status=ipo.get("status", "unknown"),
        dates={
            "drhp_filed": ipo.get("drhp_filed_date"),
            "rhp_filed": ipo.get("rhp_filed_date"),
            "fp_filed": ipo.get("fp_filed_date"),
            "open": ipo.get("open_date"),
            "close": ipo.get("close_date"),
        },
        documents=docs if isinstance(docs, dict) else {
            "drhp": ipo.get("drhp_url"),
            "rhp": ipo.get("rhp_url"),
            "final_prospectus": ipo.get("final_prospectus_url"),
            "abridged_prospectus": ipo.get("abridged_prospectus_url"),
        },
        documents_processed={
            "drhp": bool(ipo.get("drhp_processed", 0)),
            "rhp": bool(ipo.get("rhp_processed", 0)),
        },
        price_band=ipo.get("price_band"),
        platform=ipo.get("platform"),
        issue_type=ipo.get("issue_type"),
    )
    if raw:
        result.raw = {
            "sebi": ipo.get("sebi_data"),
            "bse": ipo.get("bse_data"),
            "nse": ipo.get("nse_data"),
            "bse_sme": ipo.get("bse_sme_data"),
        }
    return result


async def _live_scrape(
    status: str = "all",
    platform: str = "all",
    search: str = "",
    raw: bool = False,
) -> IPOResponse:
    """
    Fallback: do the old live scrape (for ?live=true).
    Preserves the original behavior for when you need true real-time data.
    """
    from .clients import BSEClient, BSESmeClient, NSEClient, SEBIClient
    from .schemas import IPORecord
    from .utils import normalize_company_name

    results: list[IPORecord] = []
    errors: list[dict[str, str]] = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        sebi_client = SEBIClient(client)
        bse_client = BSEClient(client)
        nse_client = NSEClient(client)
        sme_client = BSESmeClient(client)

        async def fetch_sebi() -> None:
            for dt in ("DRHP", "RHP",):
                try:
                    listing = await sebi_client.fetch_filings(page=1, document_type=dt)
                    results.extend(listing["records"])
                except Exception as exc:
                    errors.append({"source": f"sebi:{dt}", "error": str(exc)})
            try:
                await sebi_client.attach_pdf_urls([r for r in results if r.source == "sebi"])
            except Exception as exc:
                errors.append({"source": "sebi:detail", "error": str(exc)})

        async def fetch_bse() -> None:
            try:
                bse_rows = await bse_client.fetch_ipos()
                bse_rows = [r for r in bse_rows if r.issue_type in ("IPO", "FPO")]
                merge_bse_into_results(results, bse_rows)
            except Exception as exc:
                errors.append({"source": "bse", "error": str(exc)})

        async def fetch_bse_sme() -> None:
            try:
                drhp = await sme_client.fetch_drhp_list()
                rhp = await sme_client.fetch_rhp_list()
                merge_bse_sme_docs(results, drhp + rhp)
            except Exception as exc:
                errors.append({"source": "bse:sme", "error": str(exc)})

        async def fetch_nse() -> None:
            try:
                nse_rows = await nse_client.fetch_all_docs()
                merge_nse_into_results(results, nse_rows)
            except Exception as exc:
                errors.append({"source": "nse", "error": str(exc)})

        await asyncio.gather(fetch_sebi(), fetch_bse(), fetch_bse_sme(), fetch_nse())

    # Deduplicate
    merged: dict[str, IPORecord] = {}
    for r in results:
        key = normalize_company_name(r.company_name)
        if key not in merged:
            merged[key] = r
        else:
            if _source_count(r) > _source_count(merged[key]):
                merged[key] = r

    deduped = list(merged.values())

    # Apply filters
    if status != "all":
        deduped = [r for r in deduped if compute_status(r) == status]
    if platform != "all":
        deduped = [r for r in deduped if (
            r.bse_data and r.bse_data.platform and platform.lower() in r.bse_data.platform.lower()
        ) or (
            r.nse_data and r.nse_data.index == platform
        )]
    if search:
        deduped = [r for r in deduped if search.lower() in r.company_name.lower()]

    return IPOResponse(
        data=[_live_record_to_summary(r, raw=raw) for r in deduped],
        pagination=Pagination(
            total_records=len(deduped),
            current_page=1,
            per_page=len(deduped),
            total_pages=1,
        ),
        meta=Meta(
            sources_queried=["sebi", "bse", "nse", "bse_sme"],
            errors=errors,
            notes=["Live scrape — data is from this moment, not cached."],
        ),
    )


def _live_record_to_summary(record: Any, raw: bool = False) -> IPOSummary:
    """Convert live-scraped IPORecord to IPOSummary."""
    from .status import compute_status, compute_dates, compute_documents
    
    docs = compute_documents(record)
    dates = compute_dates(record)
    status = compute_status(record)
    bse = record.bse_data
    
    summary = IPOSummary(
        company_name=record.company_name,
        status=status,
        dates=dates,
        documents=docs,
        price_band=bse.price_band if bse else None,
        platform=(
            bse.platform if bse else (
                "SME" if record.nse_data and record.nse_data.index == "sme" else
                "MainBoard" if record.nse_data else None
            )
        ),
        issue_type=bse.issue_type if bse else None,
    )
    
    if raw:
        summary.raw = {
            "sebi": record.document_urls.model_dump() if record.document_urls else None,
            "bse": bse.model_dump() if bse else None,
            "nse": record.nse_data.model_dump() if record.nse_data else None,
            "bse_sme": record.bse_sme_doc.model_dump() if record.bse_sme_doc else None,
        }
    
    return summary
