"""
IPO Aggregation API v3.0 — DB-Backed

Architecture:
  Scraper (background/cron) → writes to SQLite DB → API reads from DB → your frontend

The API always returns consistent data. Live-scraping is opt-in via ?live=true.
"""
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
)
from .status import compute_status, compute_dates, compute_documents
from .scraper_service import ScraperService, _record_to_ipo_data, _source_count
from .db_service import DatabaseService

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
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(25, ge=1, le=100, description="Records per page"),
    live: bool = Query(
        False, description="If true, do a live scrape instead of reading from DB (slower)"
    ),
    raw: bool = Query(
        False, description="Include full source-level data in response"
    ),
):
    if live:
        # Live scrape mode — slower, direct from sources
        return await _live_scrape(status=status, platform=platform, search=search, raw=raw)
    
    # DB-backed — fast, always works
    ipos, total = db_service.get_all_ipos(
        status=status,
        platform=platform,
        search=search,
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
    tags=["Aggregation"],
    summary="Get a single IPO by ID",
)
async def get_ipo_by_id(
    ipo_id: int = Path(..., description="IPO database ID"),
    raw: bool = Query(False, description="Include source-level data"),
):
    ipo = db_service.get_ipo_by_id(ipo_id)
    if not ipo:
        raise HTTPException(status_code=404, detail="IPO not found")
    
    result = _format_ipo(ipo.to_dict(), raw=raw)
    result["status_history"] = db_service.get_status_history(ipo_id, limit=50)
    return result


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
""",
)
async def refresh():
    report = await scraper_service.run_full_scrape(
        bse_sme=True,
        include_pdf_urls=True,
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
