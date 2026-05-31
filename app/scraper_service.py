"""
Scraper Service — standalone scraper that populates the database.
Run it via CLI:  python -m app.scraper_service
Or via API:     GET /api/refresh

This is the core of Phase 1 — it scrapes all sources, diffs against DB,
detects new IPOs and status changes, and saves everything.
"""
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional, Callable

import httpx

# Add parent directory to path for direct execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients import (
    BSEClient,
    BSESmeClient,
    NSEClient,
    SEBIClient,
    UpstoxClient,
    merge_bse_into_results,
    merge_bse_sme_docs,
    merge_nse_into_results,
    merge_upstox_into_results,
)
from app.schemas import IPORecord
from app.status import compute_status, compute_dates, compute_documents
from app.utils import normalize_company_name, parse_source_date, format_date
from app.db_service import DatabaseService
from app.notifications import notify

logger = logging.getLogger(__name__)


def _source_count(record: IPORecord) -> int:
    """Count how many sources contributed data to this record."""
    count = 0
    if record.document_urls and (record.document_urls.detail_page or record.document_urls.drhp_pdf or record.document_urls.rhp_pdf):
        count += 1
    if record.bse_data:
        count += 1
    if record.nse_data:
        count += 1
    if record.bse_sme_doc:
        count += 1
    if record.upstox_data:
        count += 1
    return count


def _record_to_ipo_data(record: IPORecord, sources_queried: list[str]) -> dict[str, Any]:
    """
    Convert an IPORecord (from the merge pipeline) into the flat format
    expected by DatabaseService.upsert_ipo().
    """
    docs = compute_documents(record)
    dates = compute_dates(record)
    status = compute_status(record)
    
    bse = record.bse_data
    nse = record.nse_data
    upstox = record.upstox_data
    
    # Price band from Upstox (min-max) or BSE
    price_band = None
    if upstox and upstox.minimum_price is not None and upstox.maximum_price is not None:
        price_band = f"{upstox.minimum_price}-{upstox.maximum_price}"
    elif bse and bse.price_band:
        price_band = bse.price_band
    
    # Platform from Upstox or BSE or NSE
    platform = None
    if upstox and upstox.issue_type:
        platform = "SME" if upstox.issue_type == "sme" else "MainBoard" if upstox.issue_type == "regular" else None
    elif bse and bse.platform:
        platform = bse.platform
    elif nse and nse.index:
        platform = "SME" if nse.index == "sme" else "MainBoard"
    
    return {
        "normalized_name": normalize_company_name(record.company_name),
        "company_name": record.company_name,
        "status": status,
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
        "data_confidence": min(1.0, _source_count(record) / 3.0),
        "source_count": _source_count(record),
        "sebi_data": record.document_urls.model_dump() if record.document_urls else None,
        "bse_data": bse.model_dump() if bse else None,
        "nse_data": nse.model_dump() if nse else None,
        "bse_sme_data": record.bse_sme_doc.model_dump() if record.bse_sme_doc else None,
        "upstox_data": upstox.model_dump() if upstox else None,
        "_source": "aggregator",
        "_triggered_by": "cron",
    }


class ScraperService:
    """
    Runs the full scrape pipeline and writes results to DB.
    Detects new IPOs and status changes.
    """

    def __init__(self, db: Optional[DatabaseService] = None):
        self.db = db or DatabaseService()
        self.logger = logger

    async def run_full_scrape(
        self,
        sources: str = "upstox",
        bse_sme: bool = True,
        include_pdf_urls: bool = True,
        year: Optional[int] = None,
        progress_callback: Optional[Callable] = None,
    ) -> dict[str, Any]:
        """
        Scrape IPO data from configured sources, diff against DB, return a report.

        Args:
            sources: "upstox" (default, fast) or "all" (all sources including legacy).
            bse_sme: Scrape BSE SME pages (only used when sources='all').
            include_pdf_urls: Fetch PDF URLs from SEBI detail pages (only when sources='all').
            year: Limit to a specific filing year.
            progress_callback: optional async callable(progress, label) for real-time updates.
        """
        from app.config import settings
        start_time = time.monotonic()
        started_at_utc = datetime.now(timezone.utc)
        self.logger.info("Starting scrape (sources=%s)...", sources)

        results: list[IPORecord] = []
        errors: list[dict[str, str]] = []
        new_count = 0
        change_count = 0

        # Get Upstox token from config
        upstox_token = settings.upstox_access_token.strip() if settings.upstox_access_token else ""

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            # ─── Upstox (always runs — primary source) ───────
            async def fetch_upstox() -> None:
                if not upstox_token:
                    errors.append({"source": "upstox", "error": "UPSTOX_ACCESS_TOKEN not set"})
                    return
                try:
                    upstox_client = UpstoxClient(client, token=upstox_token)
                    # Step 1: Get all slugs from list endpoint (all statuses, paginated)
                    self.logger.info("Upstox: fetching all slugs...")
                    slugs = await upstox_client.fetch_all_slugs()
                    self.logger.info("Upstox: found %d slugs", len(slugs))

                    if not slugs:
                        return

                    if progress_callback:
                        await progress_callback(0.2, f"Upstox: {len(slugs)} IPOs — fetching details...")

                    # Step 2: Fetch detail for each slug
                    all_slugs = [s["id"] for s in slugs if s.get("id")]
                    self.logger.info("Upstox: fetching details for %d IPOs...", len(all_slugs))
                    details = await upstox_client.fetch_details_batch(all_slugs)
                    self.logger.info("Upstox: got %d details", len(details))

                    # Step 3: Merge into results
                    merge_upstox_into_results(results, details)
                    self.logger.info("Upstox: merged %d records into results", len(details))
                except Exception as exc:
                    self.logger.error("Upstox fetch failed: %s", exc)
                    errors.append({"source": "upstox", "error": str(exc)})

            # ─── Legacy sources (only when sources="all") ───
            async def fetch_sebi() -> None:
                if sources != "all":
                    return
                sebi_client = SEBIClient(client)
                MAX_PAGES = int(os.environ.get("SEBI_MAX_PAGES", "10"))
                for doc_type in ("DRHP", "RHP"):
                    try:
                        for page in range(1, MAX_PAGES + 1):
                            listing = await sebi_client.fetch_filings(
                                page=page, document_type=doc_type,
                            )
                            records = listing.get("records") or []
                            if not records:
                                break
                            results.extend(records)
                            total_pages = listing.get("total_pages") or 0
                            if total_pages and page >= total_pages:
                                break
                    except Exception as exc:
                        errors.append({"source": f"sebi:{doc_type}", "error": str(exc)})

                if sources == "all" and include_pdf_urls and results:
                    try:
                        sebi_records = [r for r in results if r.source == "sebi"]
                        seen: set[tuple[str, str]] = set()
                        deduped: list[IPORecord] = []
                        for r in sebi_records:
                            key = (normalize_company_name(r.company_name), r.document_type or "")
                            if key not in seen:
                                seen.add(key)
                                deduped.append(r)
                        if deduped:
                            self.logger.info(
                                f"Fetching PDF URLs for {len(deduped)} SEBI records "
                                f"(deduped from {len(sebi_records)} raw)"
                            )
                            await sebi_client.attach_pdf_urls(deduped)
                    except Exception as exc:
                        errors.append({"source": "sebi:detail", "error": str(exc)})

            async def fetch_bse() -> None:
                if sources != "all":
                    return
                try:
                    bse_client = BSEClient(client)
                    bse_rows = await bse_client.fetch_ipos()
                    bse_rows = [r for r in bse_rows if r.issue_type in ("IPO", "FPO")]
                    merge_bse_into_results(results, bse_rows)
                except Exception as exc:
                    errors.append({"source": "bse", "error": str(exc)})

            async def fetch_bse_sme() -> None:
                if sources != "all" or not bse_sme:
                    return
                try:
                    sme_client = BSESmeClient(client)
                    drhp = await sme_client.fetch_drhp_list()
                    rhp = await sme_client.fetch_rhp_list()
                    merge_bse_sme_docs(results, drhp + rhp)
                except Exception as exc:
                    errors.append({"source": "bse:sme", "error": str(exc)})

            async def fetch_nse() -> None:
                if sources != "all":
                    return
                try:
                    nse_client = NSEClient(client)
                    nse_rows = await nse_client.fetch_all_docs()
                    merge_nse_into_results(results, nse_rows)
                except Exception as exc:
                    errors.append({"source": "nse", "error": str(exc)})

            # Run Upstox first (always), then legacy sources if requested
            await fetch_upstox()
            if sources == "all":
                await asyncio.gather(
                    fetch_sebi(),
                    fetch_bse(),
                    fetch_bse_sme(),
                    fetch_nse(),
                )

            if progress_callback:
                await progress_callback(0.3, f"Scraping done — {len(results)} raw records")

        # Report raw counts
        sebi_count = len([r for r in results if r.source == 'sebi'])
        bse_count = len([r for r in results if r.source == 'bse'])
        nse_count = len([r for r in results if r.source == 'nse'])
        bse_sme_count = len([r for r in results if r.source == 'bse_sme'])
        upstox_count = len([r for r in results if r.source == 'upstox'])
        self.logger.info(f"Raw records: SEBI={sebi_count}, BSE={bse_count}, NSE={nse_count}, "
                         f"BSE_SME={bse_sme_count}, Upstox={upstox_count}")
        if progress_callback:
            await progress_callback(0.4, f"Raw: {len(results)} records — merging & deduplicating...")

        # Deduplicate in-memory
        merged: dict[str, IPORecord] = {}
        for r in results:
            key = normalize_company_name(r.company_name)
            if key not in merged:
                merged[key] = r
            else:
                # If we already have a record, prefer the one with more data
                existing = merged[key]
                existing_sources = _source_count(existing)
                new_sources = _source_count(r)
                if new_sources > existing_sources:
                    merged[key] = r

        deduped = list(merged.values())
        self.logger.info(f"Scraped {len(results)} raw records, deduped to {len(deduped)} unique IPOs")
        if progress_callback:
            await progress_callback(0.5, f"{len(deduped)} unique IPOs — saving to DB...")

        # Pre-fetch the set of normalized_names that are already 'listed' in DB —
        # we skip these to save Firecrawl credits + scraper time. Once an IPO
        # lists, its DRHP/RHP content is frozen; new price/GMP data comes via
        # a separate webhook (not this scrape).
        from app.db_models import IPOMaster, get_session as _get_session
        with _get_session() as _s:
            listed_set: set[str] = {
                row[0] for row in _s.query(IPOMaster.normalized_name)
                .filter(IPOMaster.status == "listed").all()
            }
        if listed_set:
            self.logger.info("Skipping %d 'listed' IPOs (frozen post-listing).", len(listed_set))

        # Save to database
        saved = 0
        skipped_listed = 0
        for i, record in enumerate(deduped):
            try:
                ipo_data = _record_to_ipo_data(record, ["sebi", "bse", "nse", "bse_sme"])

                # Year filter: check after converting to ipo_data (reliable dates)
                if year:
                    drhp = ipo_data.get("drhp_filed_date", "")
                    rhp = ipo_data.get("rhp_filed_date", "")
                    if not ((drhp and str(drhp).startswith(str(year))) or
                            (rhp and str(rhp).startswith(str(year)))):
                        continue

                # Skip already-listed IPOs (frozen post-listing).
                if ipo_data.get("normalized_name") in listed_set:
                    skipped_listed += 1
                    continue

                _, is_new = self.db.upsert_ipo(ipo_data)
                if is_new:
                    new_count += 1
                    saved += 1
                    self.logger.info(f"  NEW IPO: {record.company_name} ({ipo_data['status']})")
                    notify(
                        f"📥 New IPO: <b>{record.company_name}</b> · {ipo_data['status']}",
                        level="info",
                        details={
                            "company": record.company_name,
                            "status": ipo_data.get("status"),
                            "platform": ipo_data.get("platform"),
                            "drhp_filed_date": ipo_data.get("drhp_filed_date"),
                        },
                    )
                else:
                    saved += 1
                    # Check if status changed (we don't have the old status here,
                    # but upsert_ipo handles that internally via DB diff)
                    pass
                # Update progress every 100 records
                if progress_callback and saved % 100 == 0:
                    pct = 0.5 + (saved / len(deduped)) * 0.4
                    await progress_callback(pct, f"Saving to DB: {saved}/{len(deduped)} IPOs")
            except Exception as exc:
                self.logger.error(f"Failed to save {record.company_name}: {exc}")
                errors.append({"source": "database", "error": f"{record.company_name}: {exc}"})

        scrape_duration_ms = int((time.monotonic() - start_time) * 1000)

        # Count *real* status changes from THIS scrape run only
        # (entries written after started_at). The previous implementation
        # counted every row in the last 100 changes, regardless of when.
        cutoff_iso = started_at_utc.isoformat()
        recent_changes = self.db.get_recent_status_changes(limit=500)
        change_count = sum(
            1 for c in recent_changes
            if (c.get("change_date") or "") >= cutoff_iso
        )

        status = "success" if not errors else ("partial_success" if new_count > 0 else "error")
        non_sebi_errors = sum(1 for e in errors if "sebi" not in e.get("source", ""))
        self.db.log_scrape(
            scraper_type="aggregator",
            action="full_scrape",
            status=status,
            message=(
                f"Scraped {len(deduped)} unique IPOs. New: {new_count}. "
                f"Non-SEBI errors: {non_sebi_errors}"
            ),
            error_details={"errors": errors, "total_raw": len(results), "deduped": len(deduped)} if errors else None,
            execution_time_ms=scrape_duration_ms,
            new_ipos_found=new_count,
            status_changes=change_count,
        )

        self.logger.info(f"Scrape complete in {scrape_duration_ms}ms. {new_count} new, {change_count} changes.")

        # Summary ping (only fires if anything noteworthy happened)
        if new_count or change_count or errors:
            notify(
                f"🔄 Scrape done · <b>{new_count}</b> new · <b>{change_count}</b> status changes · "
                f"<b>{len(errors)}</b> errors · {scrape_duration_ms/1000:.1f}s",
                level=("warn" if errors else "info"),
                details=({"errors": [e for e in errors[:5]]} if errors else None),
            )

        return {
            "status": status,
            "total_raw": len(results),
            "total_unique": len(deduped),
            "new_ipos_found": new_count,
            "status_changes_detected": change_count,
            "execution_time_ms": scrape_duration_ms,
            "errors": errors,
        }


def main():
    """CLI entry point: python -m app.scraper_service"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    import argparse
    parser = argparse.ArgumentParser(description="IPO Scraper Service")
    parser.add_argument("--sources", default="upstox", choices=["upstox", "all"],
                        help="Sources to scrape: 'upstox' (default, fast) or 'all' (Upstox + legacy)")
    parser.add_argument("--no-pdf-urls", action="store_true", help="Skip fetching PDF URLs from SEBI detail pages (only with --sources=all)")
    parser.add_argument("--no-bse-sme", action="store_true", help="Skip BSE SME scraping (only with --sources=all)")
    parser.add_argument("--year", type=int, default=None, help="Only scrape IPOs from a specific year (e.g. 2026)")
    args = parser.parse_args()

    service = ScraperService()
    report = asyncio.run(service.run_full_scrape(
        sources=args.sources,
        bse_sme=not args.no_bse_sme,
        include_pdf_urls=not args.no_pdf_urls,
        year=args.year,
    ))

    print(f"\n{'='*50}")
    print(f"Scrape Complete")
    print(f"{'='*50}")
    print(f"  Status:        {report['status']}")
    print(f"  Raw records:   {report['total_raw']}")
    print(f"  Unique IPOs:   {report['total_unique']}")
    print(f"  New IPOs:      {report['new_ipos_found']}")
    print(f"  Status changes: {report['status_changes_detected']}")
    print(f"  Duration:       {report['execution_time_ms']}ms")
    if report["errors"]:
        print(f"  Errors:        {len(report['errors'])}")
        for err in report["errors"][:5]:
            print(f"    - {err['source']}: {err['error'][:80]}")
    print(f"{'='*50}")

    return 0 if report["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
