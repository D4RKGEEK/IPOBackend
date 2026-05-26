from math import ceil
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .clients import BSEClient, NSEClient, SEBIClient, merge_bse_into_results
from .schemas import IPOResponse
from .utils import normalize_company_name


app = FastAPI(
    title="IPO Aggregation API",
    version="1.0.0",
    description="Aggregates IPO DRHP/RHP filings from SEBI with BSE IPO metadata and best-effort NSE session fetching.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ipos", response_model=IPOResponse)
async def get_ipos(
    source: Literal["sebi", "bse", "nse", "all"] = Query("all"),
    document_type: Literal["DRHP", "RHP", "all"] = Query("all"),
    platform: Literal["mainboard", "sme", "all"] = Query("all"),
    status: Literal["upcoming", "open", "closed", "other", "all"] = Query("all"),
    search: str = Query(""),
    from_date: str = Query(""),
    to_date: str = Query(""),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    include_pdf_urls: bool = Query(False),
    nse_mode: Literal["browser", "http"] = Query("browser"),
):
    results = []
    errors = []
    notes = []
    upstream_total_records = None

    async with httpx.AsyncClient(follow_redirects=True) as client:
        sebi_client = SEBIClient(client)
        bse_client = BSEClient(client)
        nse_client = NSEClient(client)

        if source in ("all", "sebi"):
            doc_types = ["DRHP", "RHP"] if document_type == "all" else [document_type]
            for doc_type in doc_types:
                try:
                    if source == "sebi" and document_type != "all":
                        listing = await fetch_sebi_window(
                            sebi_client=sebi_client,
                            document_type=doc_type,
                            page=page,
                            per_page=per_page,
                            from_date=from_date,
                            to_date=to_date,
                            search=search,
                        )
                    else:
                        listing = await sebi_client.fetch_filings(
                            page=page,
                            document_type=doc_type,
                            from_date=from_date,
                            to_date=to_date,
                            search=search,
                        )
                    upstream_total_records = (upstream_total_records or 0) + listing["total_records"]
                    results.extend(listing["records"])
                except Exception as exc:
                    errors.append({"source": f"sebi:{doc_type}", "error": str(exc)})

            if include_pdf_urls and results:
                try:
                    await sebi_client.attach_pdf_urls(
                        [record for record in results if record.source == "sebi"]
                    )
                except Exception as exc:
                    errors.append({"source": "sebi:detail", "error": str(exc)})

        if source in ("all", "bse"):
            try:
                bse_rows = await bse_client.fetch_ipos()
                bse_rows = [
                    row
                    for row in bse_rows
                    if (platform == "all" or (row.platform or "").lower() == platform)
                    and (status == "all" or row.status == status)
                    and (not search or search.lower() in row.company_name.lower())
                ]
                merge_bse_into_results(results, bse_rows)
            except Exception as exc:
                errors.append({"source": "bse", "error": str(exc)})

        if source == "nse":
            try:
                nse_rows = await nse_client.fetch_under_issue(mode=nse_mode)
                for nse_row in nse_rows:
                    if search and search.lower() not in nse_row.company_name.lower():
                        continue
                    results.append(
                        {
                            "company_name": nse_row.company_name,
                            "source": "nse",
                            "nse_data": nse_row,
                        }
                    )
            except Exception as exc:
                errors.append({"source": "nse", "error": str(exc)})
                notes.append(
                    "NSE is protected by Akamai. The default mode opens a real browser context, visits NSE, "
                    "then runs the API fetch inside that same session. If blocked, try nse_mode=http or use BSE fallback."
                )
        elif source == "all":
            notes.append("NSE is not queried in source=all because direct NSE API calls are commonly blocked.")

    if search and source not in ("bse", "nse"):
        results = [record for record in results if search.lower() in record.company_name.lower()]

    if document_type != "all":
        results = [
            record
            for record in results
            if record.document_type in (document_type, None) or record.source in ("bse", "nse")
        ]

    if source == "sebi" and document_type != "all":
        total = upstream_total_records if upstream_total_records is not None else len(results)
        page_results = results
    else:
        total = len(results)
        start = (page - 1) * per_page
        page_results = results[start : start + per_page]
    sources_queried = ["sebi", "bse"] if source == "all" else [source]

    return {
        "data": page_results,
        "pagination": {
            "total_records": total,
            "current_page": page,
            "per_page": per_page,
            "total_pages": max(1, ceil(total / per_page)) if total else 1,
        },
        "meta": {"sources_queried": sources_queried, "errors": errors, "notes": notes},
    }


async def fetch_sebi_window(
    sebi_client: SEBIClient,
    document_type: str,
    page: int,
    per_page: int,
    from_date: str,
    to_date: str,
    search: str,
):
    requested_start = (page - 1) * per_page
    first_listing = await sebi_client.fetch_filings(
        page=max(1, requested_start // 25 + 1),
        document_type=document_type,
        from_date=from_date,
        to_date=to_date,
        search=search,
    )
    source_per_page = first_listing.get("per_page") or len(first_listing["records"]) or 25
    first_source_page = requested_start // source_per_page + 1
    requested_end = requested_start + per_page
    last_source_page = max(first_source_page, (requested_end - 1) // source_per_page + 1)

    listings = [first_listing]
    actual_first_source_page = max(1, requested_start // 25 + 1)
    if actual_first_source_page != first_source_page:
        listings = [
            await sebi_client.fetch_filings(
                page=first_source_page,
                document_type=document_type,
                from_date=from_date,
                to_date=to_date,
                search=search,
            )
        ]

    for source_page in range(first_source_page + 1, last_source_page + 1):
        listings.append(
            await sebi_client.fetch_filings(
                page=source_page,
                document_type=document_type,
                from_date=from_date,
                to_date=to_date,
                search=search,
            )
        )

    records = [record for listing in listings for record in listing["records"]]
    offset = requested_start - ((first_source_page - 1) * source_per_page)
    return {
        "records": records[offset : offset + per_page],
        "total_pages": first_listing["total_pages"],
        "per_page": source_per_page,
        "total_records": first_listing["total_records"],
    }


@app.get("/api/sebi/filings")
async def get_sebi_filings(
    document_type: Literal["DRHP", "RHP"] = Query("DRHP"),
    page: int = Query(1, ge=1),
    search: str = Query(""),
    from_date: str = Query(""),
    to_date: str = Query(""),
):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return await SEBIClient(client).fetch_filings(
            page=page,
            document_type=document_type,
            from_date=from_date,
            to_date=to_date,
            search=search,
        )


@app.get("/api/sebi/detail")
async def get_sebi_detail(detail_url: str = Query(...)):
    async with httpx.AsyncClient(follow_redirects=True) as client:
        result = await SEBIClient(client).fetch_detail_page(detail_url)
    if not result.get("pdf_url"):
        raise HTTPException(status_code=404, detail="PDF URL not found")
    return {"detail_page_url": detail_url, **result}


@app.get("/api/bse/ipos")
async def get_bse_ipos():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        return await BSEClient(client).fetch_ipos()


@app.get("/api/nse/under-issue")
async def get_nse_under_issue(mode: Literal["browser", "http"] = Query("browser")):
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            return await NSEClient(client).fetch_under_issue(mode=mode)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "source": "nse",
                "mode": mode,
                "error": str(exc),
                "fallback": "Try /api/ipos?source=bse or /api/nse/under-issue?mode=http if browser mode is unavailable.",
            },
        ) from exc


@app.get("/api/normalize-company-name")
async def normalize_company(name: str = Query(...)) -> dict[str, str]:
    return {"input": name, "normalized": normalize_company_name(name)}
