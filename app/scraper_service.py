"""
Scraper Service — standalone scraper that populates the database.
Run it via CLI:  python -m app.scraper_service
Or via API:     GET /api/refresh

This is the core of Phase 1 — it scrapes all sources, diffs against DB,
detects new IPOs and status changes, and saves everything.
"""
import asyncio
import logging
import time
import sys
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

# Add parent directory to path for direct execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.clients import (
    BSEClient,
    BSESmeClient,
    NSEClient,
    SEBIClient,
    merge_bse_into_results,
    merge_bse_sme_docs,
    merge_nse_into_results,
)
from app.schemas import IPORecord
from app.status import compute_status, compute_dates, compute_documents
from app.utils import normalize_company_name, parse_source_date, format_date
from app.db_service import DatabaseService
from app.pdf_utils import extract_document

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
    
    return {
        "normalized_name": normalize_company_name(record.company_name),
        "company_name": record.company_name,
        "status": status,
        "drhp_filed_date": format_date(dates.get("drhp_filed")),
        "rhp_filed_date": format_date(dates.get("rhp_filed")),
        "fp_filed_date": format_date(dates.get("fp_filed")),
        "open_date": format_date(dates.get("open")),
        "close_date": format_date(dates.get("close")),
        "price_band": bse.price_band if bse else None,
        "platform": (
            bse.platform if bse else (
                "SME" if nse and nse.index == "sme" else "MainBoard" if nse else None
            )
        ),
        "issue_type": bse.issue_type if bse else None,
        "drhp_url": docs.get("drhp"),
        "rhp_url": docs.get("rhp"),
        "final_prospectus_url": docs.get("final_prospectus"),
        "abridged_prospectus_url": docs.get("abridged_prospectus"),
        "data_confidence": min(1.0, _source_count(record) / 3.0),  # 3+ sources = high confidence
        "source_count": _source_count(record),
        "sebi_data": record.document_urls.model_dump() if record.document_urls else None,
        "bse_data": bse.model_dump() if bse else None,
        "nse_data": nse.model_dump() if nse else None,
        "bse_sme_data": record.bse_sme_doc.model_dump() if record.bse_sme_doc else None,
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
        bse_sme: bool = True,
        include_pdf_urls: bool = True,
        resolve_docs: bool = False,
        resolve_doc_limit: int = 20,
        year: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Scrape all sources, diff against DB, return a report.

        Returns:
            dict with: status, total_found, new_ipos, status_changes, errors, notes, execution_time_ms
        """
        start_time = time.monotonic()
        self.logger.info("Starting full scrape of all sources...")

        results: list[IPORecord] = []
        errors: list[dict[str, str]] = []
        new_count = 0
        change_count = 0

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            sebi_client = SEBIClient(client)
            bse_client = BSEClient(client)
            nse_client = NSEClient(client)
            sme_client = BSESmeClient(client) if bse_sme else None

            async def fetch_sebi() -> None:
                for doc_type in ("DRHP", "RHP"):
                    try:
                        # Fetch first 5 pages of each type to get good coverage
                        for page in range(1, 4):
                            listing = await sebi_client.fetch_filings(
                                page=page, document_type=doc_type,
                            )
                            results.extend(listing["records"])
                    except Exception as exc:
                        errors.append({"source": f"sebi:{doc_type}", "error": str(exc)})

                if include_pdf_urls and results:
                    try:
                        sebi_records = [r for r in results if r.source == "sebi"]
                        if sebi_records:
                            self.logger.info(f"Fetching PDF URLs for {len(sebi_records)} SEBI records...")
                            await sebi_client.attach_pdf_urls(sebi_records)
                    except Exception as exc:
                        errors.append({"source": "sebi:detail", "error": str(exc)})

            async def fetch_bse() -> None:
                try:
                    bse_rows = await bse_client.fetch_ipos()
                    # Filter to only IPO/FPO
                    bse_rows = [r for r in bse_rows if r.issue_type in ("IPO", "FPO")]
                    merge_bse_into_results(results, bse_rows)
                except Exception as exc:
                    errors.append({"source": "bse", "error": str(exc)})

            async def fetch_bse_sme() -> None:
                if sme_client is None:
                    return
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

            await asyncio.gather(
                fetch_sebi(),
                fetch_bse(),
                fetch_bse_sme(),
                fetch_nse(),
            )

        # Deduplicate in-memory (keep the richest record per company)
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
        
        # Save to database
        for record in deduped:
            try:
                ipo_data = _record_to_ipo_data(record, ["sebi", "bse", "nse", "bse_sme"])
                
                # Year filter: check after converting to ipo_data (reliable dates)
                if year:
                    drhp = ipo_data.get("drhp_filed_date", "")
                    rhp = ipo_data.get("rhp_filed_date", "")
                    if not ((drhp and str(drhp).startswith(str(year))) or 
                            (rhp and str(rhp).startswith(str(year)))):
                        continue
                
                _, is_new = self.db.upsert_ipo(ipo_data)
                if is_new:
                    new_count += 1
                    self.logger.info(f"  NEW IPO: {record.company_name} ({ipo_data['status']})")
                else:
                    # Check if status changed (we don't have the old status here,
                    # but upsert_ipo handles that internally via DB diff)
                    pass
            except Exception as exc:
                self.logger.error(f"Failed to save {record.company_name}: {exc}")
                errors.append({"source": "database", "error": f"{record.company_name}: {exc}"})

        # Count actual status changes from this scrape
        recent_changes = self.db.get_recent_status_changes(limit=100)
        # Only count changes that happened within the last minute (this scrape run)
        now = time.monotonic()
        scrape_duration_ms = int((now - start_time) * 1000)
        change_count = len([
            c for c in recent_changes
            if c.get("change_date")
        ])

        # Log the overall scrape result
        status = "success" if not errors else ("partial_success" if new_count > 0 else "error")
        self.db.log_scrape(
            scraper_type="aggregator",
            action="full_scrape",
            status=status,
            message=f"Scraped {len(deduped)} unique IPOs. New: {new_count}. Sources: {len([e for e in errors if 'sebi' not in e.get('source','')]) > 0} errors",
            error_details={"errors": errors, "total_raw": len(results), "deduped": len(deduped)} if errors else None,
            execution_time_ms=scrape_duration_ms,
            new_ipos_found=new_count,
            status_changes=change_count,
        )

        self.logger.info(f"Scrape complete in {scrape_duration_ms}ms. {new_count} new, {change_count} changes.")

        # Optional: resolve ZIPs and extract text for unprocessed documents
        doc_result = {}
        if resolve_docs:
            self.logger.info(f"Resolving documents (limit={resolve_doc_limit})...")
            doc_result = await self.resolve_document_texts(limit=resolve_doc_limit)

        result = {
            "status": status,
            "total_raw": len(results),
            "total_unique": len(deduped),
            "new_ipos_found": new_count,
            "status_changes_detected": change_count,
            "execution_time_ms": scrape_duration_ms,
            "errors": errors,
        }
        if doc_result:
            result["documents_resolved"] = doc_result
        
        return result

    async def resolve_document_texts(
        self,
        limit: int = 20,  # Process at most 20 per run to keep total time reasonable
        max_workers: int = 3,  # Process up to 3 concurrently
    ) -> dict[str, Any]:
        """
        After a scrape, resolve ZIP URLs to PDF text for unprocessed documents.
        
        Downloads ZIPs, extracts PDFs, extracts text, stores in DB.
        Only processes documents not marked as processed yet.
        Skips heavy files (>50MB), reports failures without crashing.
        """
        unprocessed = self.db.get_unprocessed_documents(limit=limit)
        if not unprocessed:
            return {"processed": 0, "failed": 0, "skipped": 0, "total_chars": 0}

        self.logger.info(f"Resolving {len(unprocessed)} unprocessed documents...")
        
        processed = 0
        failed = 0
        skipped = 0
        total_chars = 0

        import httpx
        semaphore = asyncio.Semaphore(max_workers)

        async def resolve_one(ipo: dict) -> None:
            nonlocal processed, failed, skipped, total_chars
            ipo_id = ipo["id"]
            name = ipo["company_name"]
            docs = ipo.get("documents", {})
            if not docs:
                docs = {
                    "drhp": ipo.get("drhp_url"),
                    "rhp": ipo.get("rhp_url"),
                    "final_prospectus": ipo.get("final_prospectus_url"),
                }

            async with semaphore:
                for doc_type in ("drhp", "rhp", "final_prospectus"):
                    url = docs.get(doc_type)
                    if not url:
                        continue
                    
                    # Skip already-processed
                    processed_field = f"{doc_type}_processed"
                    if ipo.get(processed_field, 0):
                        continue

                    # Check if already have text in DB
                    existing = self.db.get_document_text(ipo_id, doc_type)
                    if existing:
                        self.logger.info(f"  [{name}] {doc_type} already has text ({len(existing):,} chars)")
                        self.db.mark_document_processed(ipo_id, doc_type)
                        processed += 1
                        continue

                    # Only extract if it's a ZIP or a PDF that hasn't been done
                    if not url.lower().endswith((".pdf", ".zip")):
                        skipped += 1
                        continue

                    try:
                        async with httpx.AsyncClient(
                            follow_redirects=True, timeout=60
                        ) as client:
                            result = await extract_document(url, client)
                            text = result["text"] if result else None
                        
                        if text:
                            self.db.save_document_text(ipo_id, doc_type, text, url)
                            self.db.mark_document_processed(ipo_id, doc_type)
                            processed += 1
                            total_chars += len(text)
                            self.logger.info(
                                f"  ✓ {name} - {doc_type}: {len(text):,} chars"
                            )
                        else:
                            self.logger.warning(f"  ✗ {name} - {doc_type}: no text extracted")
                            failed += 1
                    except Exception as e:
                        self.logger.error(f"  ✗ {name} - {doc_type}: {e}")
                        failed += 1

        await asyncio.gather(*[resolve_one(ipo) for ipo in unprocessed])
        
        self.logger.info(
            f"Document resolution: {processed} processed, {failed} failed, "
            f"{skipped} skipped, {total_chars:,} total chars"
        )
        
        return {
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "total_chars": total_chars,
        }


def main():
    """CLI entry point: python -m app.scraper_service"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    
    import argparse
    parser = argparse.ArgumentParser(description="IPO Scraper Service")
    parser.add_argument("--no-pdf-urls", action="store_true", help="Skip fetching PDF URLs from SEBI detail pages")
    parser.add_argument("--no-bse-sme", action="store_true", help="Skip BSE SME scraping")
    parser.add_argument("--resolve-docs", action="store_true", help="Resolve ZIP URLs and extract PDF text after scraping")
    parser.add_argument("--resolve-limit", type=int, default=50, help="Max documents to resolve (default: 50, max: 500)")
    parser.add_argument("--year", type=int, default=None, help="Only scrape IPOs from a specific year (e.g. 2026)")
    args = parser.parse_args()

    service = ScraperService()
    report = asyncio.run(service.run_full_scrape(
        bse_sme=not args.no_bse_sme,
        include_pdf_urls=not args.no_pdf_urls,
        resolve_docs=args.resolve_docs,
        resolve_doc_limit=args.resolve_limit,
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
