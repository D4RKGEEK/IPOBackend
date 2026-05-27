"""
Migration: re-extract top 100 IPOs using pdfplumber.
Clears old raw_text data, processes newest IPOs first.
"""
import sys, os, asyncio, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from app.db_models import IPOParsedData, IPOMaster, get_session
from app.db_service import DatabaseService
from app.pdf_utils import extract_document
import httpx

db = DatabaseService()

async def main():
    # Step 1: Clear all existing raw_text entries
    print("Step 1: Clearing old raw_text data...")
    with get_session() as s:
        deleted = s.query(IPOParsedData).filter(
            IPOParsedData.data_type.like('raw_text_%')
        ).delete()
        s.commit()
        print(f"  Deleted {deleted} old entries")

    # Step 2: Get top 100 IPOs with document URLs (newest first)
    print("\nStep 2: Getting top 100 IPOs...")
    with get_session() as s:
        ipos = s.query(IPOMaster).filter(
            IPOMaster.drhp_url.isnot(None)
        ).order_by(
            IPOMaster.drhp_filed_date.desc().nullslast(),
            IPOMaster.id.desc()
        ).limit(100).all()
        print(f"  Found {len(ipos)} IPOs")

    # Step 3: Re-extract documents
    print("\nStep 3: Extracting documents...")
    processed = 0
    failed = 0
    start = time.monotonic()

    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
        for ipo in ipos:
            for doc_type in ("drhp", "rhp", "final_prospectus"):
                url = getattr(ipo, f"{doc_type}_url", None)
                if not url:
                    continue
                
                try:
                    result = await extract_document(
                        url, client,
                        ipo_id=ipo.id,
                        doc_type=doc_type,
                    )
                    
                    if result and result.get("text"):
                        # Save text to DB
                        db.save_document_text(
                            ipo_id=ipo.id,
                            document_type=doc_type,
                            text=result["text"],
                            source_url=url,
                        )
                        db.mark_document_processed(ipo.id, doc_type)
                        processed += 1
                        print(f"  ✓ #{ipo.id} {ipo.company_name[:35]:35s} {doc_type:15s} "
                              f"{len(result['text']):,} chars, "
                              f"{result['metadata']['total_tables']} tables")
                    else:
                        failed += 1
                        print(f"  ✗ #{ipo.id} {ipo.company_name[:35]:35s} {doc_type} - no text")
                
                except Exception as e:
                    failed += 1
                    print(f"  ✗ #{ipo.id} {ipo.company_name[:35]:35s} {doc_type} - {e}")
    
    elapsed = time.monotonic() - start
    print(f"\n{'='*60}")
    print(f"Complete: {processed} processed, {failed} failed")
    print(f"Duration: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Cache dir: {os.path.abspath('.doc_cache/')}")
    print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
