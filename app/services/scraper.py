"""Scraper service — simple waterfall + upsert to DB.

Run: python -m app.services.scraper --year 2026
Or via API: POST /api/refresh
"""
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import httpx

# Add parent to path for direct execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients.upstox import UpstoxClient
from app.clients.nse import NSEClient
from app.clients.bse import BSEClient, BSESmeClient
from app.clients.sebi import SEBIClient
from app.clients.merger import (
    merge_upstox_into_results, merge_nse_into_results,
    merge_bse_into_results, merge_bse_sme_docs,
)
from app.clients.chittorgarh import fetch_report, verify_rhp_url
from app.core.identity import match_ipo, get_existing_identifiers
from app.core.orchestrator import run_waterfall
from app.db.operations import upsert_ipo, log_scrape, get_recent_status_changes
from app.schemas import IPORecord
from app.utils import normalize_company_name, format_date

logger = logging.getLogger(__name__)


def _record_in_year(record: IPORecord, year: int) -> bool:
    """Return True if any date field on the record falls in the given year."""
    y = str(year)

    upstox = record.upstox_data
    if upstox:
        if upstox.status in ("upcoming", "open", "closed"):
            return True
        for val in [upstox.bidding_start_date, upstox.bidding_end_date]:
            if val and y in str(val):
                return True

    nse = record.nse_data
    if nse:
        for val in [nse.issue_open_date, nse.issue_close_date,
                    nse.drhp_date, nse.rhp_date, nse.fp_date]:
            if val and y in str(val):
                return True

    bse = record.bse_data
    if bse:
        for val in [bse.start_date, bse.end_date]:
            if val and y in str(val):
                return True

    sme = record.bse_sme_doc
    if sme and sme.date and y in str(sme.date):
        return True

    if record.filing_date and y in str(record.filing_date):
        return True

    return False


async def run_scrape(year: int = 2026, sources: str = "upstox",
                     progress_callback=None) -> dict:
    """Main scrape function. Fetches from sources, deduplicates, saves to DB.

    Args:
        year: Only process IPOs from this year
        sources: "upstox" (fast) or "all" (all sources)
        progress_callback: async callable(progress, label)
    """
    from app.config import settings
    start_time = time.monotonic()
    started_at = datetime.now(timezone.utc)

    results: list[IPORecord] = []
    errors: list[dict] = []
    new_count = 0

    # Load existing identifiers for matching
    existing_names, existing_symbols, existing_isins = get_existing_identifiers()

    async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
        upstox_token = settings.upstox_access_token.strip() if settings.upstox_access_token else ""

        # ─── Upstox (always runs) ─────────────────────
        if upstox_token:
            try:
                upstox = UpstoxClient(client, token=upstox_token)
                slugs = await upstox.fetch_all_slugs()
                logger.info(f"Upstox: found {len(slugs)} IPOs")
                if progress_callback:
                    await progress_callback(0.2, f"Upstox: {len(slugs)} IPOs")

                slugs_2026 = []
                for s in slugs:
                    name = s.get("name", "")
                    # Simple heuristic: upcoming/open/closed IPOs are likely current year
                    if s.get("status") in ("upcoming", "open", "closed"):
                        slugs_2026.append(s)
                    elif year and str(year) in name:
                        slugs_2026.append(s)

                if slugs_2026:
                    detail_ids = [s["id"] for s in slugs_2026]
                    details = await upstox.fetch_details_batch(detail_ids)
                    merge_upstox_into_results(results, details)
                    logger.info(f"Upstox: merged {len(details)} IPOs (year={year})")
            except Exception as exc:
                logger.error(f"Upstox failed: {exc}")
                errors.append({"source": "upstox", "error": str(exc)})
        else:
            errors.append({"source": "upstox", "error": "No UPSTOX_ACCESS_TOKEN"})

        # ─── Legacy sources (only when sources="all") ──
        if sources == "all":
            if progress_callback:
                await progress_callback(0.3, "Fetching NSE/BSE/SEBI...")

            # NSE — pass year-aware date range so we don't pull all-time history
            try:
                nse = NSEClient(client)
                if year:
                    # Start from Jan of prior year to catch DRHP-stage IPOs whose
                    # offer dates fall in the target year
                    from_d = f"01-01-{year - 1}"
                    to_d = f"31-12-{year}"
                else:
                    from_d = to_d = ""
                nse_rows = await nse.fetch_all_docs(from_date=from_d, to_date=to_d)
                merge_nse_into_results(results, nse_rows)
                logger.info(f"NSE: merged {len(nse_rows)} records (year={year})")
            except Exception as exc:
                errors.append({"source": "nse", "error": str(exc)})

            # BSE — no date params on the API; filter rows after fetching
            try:
                bse = BSEClient(client)
                bse_rows = await bse.fetch_ipos()
                bse_rows = [
                    r for r in bse_rows
                    if r.issue_type in ("IPO", "FPO")
                    and (not year or any(str(year) in str(d) for d in [r.start_date, r.end_date] if d))
                ]
                merge_bse_into_results(results, bse_rows)
                logger.info(f"BSE: merged {len(bse_rows)} records (year={year})")
            except Exception as exc:
                errors.append({"source": "bse", "error": str(exc)})

            # BSE SME — filter by document date
            try:
                sme = BSESmeClient(client)
                drhp = await sme.fetch_drhp_list()
                rhp = await sme.fetch_rhp_list()
                if year:
                    drhp = [r for r in drhp if not r.date or str(year) in str(r.date)]
                    rhp = [r for r in rhp if not r.date or str(year) in str(r.date)]
                merge_bse_sme_docs(results, drhp + rhp)
                logger.info(f"BSE SME: merged {len(drhp)+len(rhp)} records (year={year})")
            except Exception as exc:
                errors.append({"source": "bse_sme", "error": str(exc)})

        if progress_callback:
            await progress_callback(0.5, f"Raw: {len(results)} records — deduplicating...")

        # ─── Deduplicate ───────────────────────────────
        merged: dict[str, IPORecord] = {}
        for r in results:
            key = normalize_company_name(r.company_name)
            if key not in merged:
                merged[key] = r
            else:
                existing = merged[key]
                # Prefer record with more source data
                existing_count = sum(1 for x in [
                    existing.upstox_data, existing.bse_data,
                    existing.nse_data, existing.bse_sme_doc,
                ] if x is not None)
                new_count_val = sum(1 for x in [
                    r.upstox_data, r.bse_data,
                    r.nse_data, r.bse_sme_doc,
                ] if x is not None)
                if new_count_val > existing_count:
                    merged[key] = r

        deduped = list(merged.values())
        if year:
            before = len(deduped)
            deduped = [r for r in deduped if _record_in_year(r, year)]
            logger.info(f"Year filter {year}: {before} → {len(deduped)} records")
        logger.info(f"Raw: {len(results)}, unique after year filter: {len(deduped)}")

        if progress_callback:
            await progress_callback(0.6, f"{len(deduped)} unique — saving to DB...")

        # ─── Save to DB ────────────────────────────────
        for i, record in enumerate(deduped):
            try:
                ipo_data = _record_to_ipo_data(record)
                if not ipo_data:
                    continue

                ipo, is_new = upsert_ipo(ipo_data)
                if is_new:
                    new_count += 1
                    logger.info(f"NEW IPO: {record.company_name}")

                if progress_callback and i % 50 == 0:
                    pct = 0.6 + (i / len(deduped)) * 0.3
                    await progress_callback(pct, f"Saving: {i}/{len(deduped)}")
            except Exception as exc:
                logger.error(f"Failed to save {record.company_name}: {exc}")
                errors.append({"source": "database", "error": f"{record.company_name}: {exc}"})

    # ─── Summary ───────────────────────────────────────
    duration = int((time.monotonic() - start_time) * 1000)
    log_scrape("waterfall", "run_scrape",
               status="success" if not errors else "partial_success",
               message=f"Scraped {len(deduped)} IPOs, {new_count} new",
               error_details={"errors": errors} if errors else None,
               execution_time_ms=duration,
               new_ipos_found=new_count)

    return {
        "status": "success" if not errors else "partial_success",
        "total_raw": len(results),
        "total_unique": len(deduped),
        "new_ipos_found": new_count,
        "execution_time_ms": duration,
        "errors": errors,
    }


def _record_to_ipo_data(record: IPORecord) -> Optional[dict]:
    """Convert IPORecord to flat dict for DB upsert."""
    from app.status import compute_status, compute_dates, compute_documents

    docs = compute_documents(record)
    dates = compute_dates(record)
    status = compute_status(record)

    bse = record.bse_data
    nse = record.nse_data
    upstox = record.upstox_data

    # Price band: Upstox > BSE
    price_band = None
    if upstox and upstox.minimum_price is not None and upstox.maximum_price is not None:
        price_band = f"{upstox.minimum_price}-{upstox.maximum_price}"
    elif bse and bse.price_band:
        price_band = bse.price_band

    # Platform
    platform = None
    if upstox and upstox.issue_type:
        platform = "SME" if upstox.issue_type == "sme" else "MainBoard"
    elif bse and bse.platform:
        platform = bse.platform

    return {
        "normalized_name": normalize_company_name(record.company_name),
        "company_name": record.company_name,
        "status": status,
        "data_confidence": 0.0,
        "source_count": sum(1 for x in [record.upstox_data, record.bse_data,
                                         record.nse_data, record.bse_sme_doc] if x is not None),
        "phase": "scraped",
        "drhp_filed_date": format_date(dates.get("drhp_filed")),
        "rhp_filed_date": format_date(dates.get("rhp_filed")),
        "fp_filed_date": format_date(dates.get("fp_filed")),
        "open_date": format_date(dates.get("open")),
        "close_date": format_date(dates.get("close")),
        "price_band": price_band,
        "platform": platform,
        "issue_type": upstox.issue_type if upstox else (bse.issue_type if bse else None),
        "drhp_url": docs.get("drhp"),
        "rhp_url": docs.get("rhp"),
        "final_prospectus_url": docs.get("final_prospectus"),
        "abridged_prospectus_url": docs.get("abridged_prospectus"),
        "sebi_data": record.document_urls.model_dump() if record.document_urls else None,
        "bse_data": bse.model_dump() if bse else None,
        "nse_data": nse.model_dump() if nse else None,
        "bse_sme_data": record.bse_sme_doc.model_dump() if record.bse_sme_doc else None,
        "upstox_data": upstox.model_dump() if upstox else None,
    }


def main():
    """CLI entry: python -m app.services.scraper --year 2026"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    import argparse
    parser = argparse.ArgumentParser(description="IPO Scraper")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--sources", default="upstox", choices=["upstox", "all"])
    args = parser.parse_args()

    report = asyncio.run(run_scrape(year=args.year, sources=args.sources))
    print(f"\n{'='*50}")
    print(f"Scrape Complete")
    print(f"{'='*50}")
    print(f"  Status:  {report['status']}")
    print(f"  Raw:     {report['total_raw']}")
    print(f"  Unique:  {report['total_unique']}")
    print(f"  New:     {report['new_ipos_found']}")
    print(f"  Time:    {report['execution_time_ms']}ms")
    if report["errors"]:
        print(f"  Errors:  {len(report['errors'])}")
    print(f"{'='*50}")


if __name__ == "__main__":
    sys.exit(main())
