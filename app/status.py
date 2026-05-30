"""IPO lifecycle status computation.

Maps raw data from SEBI, BSE, and NSE into a single unified status.

Lifecycle stages (earliest -> latest):
  drhp_filed        → Company filed draft documents (early signal)
  sebi_approved     → SEBI approved the DRHP
  rhp_filed         → Final offer document filed with ROC (IPO confirmed)
  upcoming          → Dates announced but not yet open
  open              → Currently accepting bids
  closed            → Bidding over, awaiting listing
  listed            → Trading on exchange
"""

from typing import Optional

from .schemas import IPORecord


def compute_status(record: IPORecord) -> str:
    """Derive a single lifecycle status from all available data.

    Status priority (highest wins):
      1. If bse status is 'open'   → 'open'
      2. If nse has fp (final prospectus) → 'listed'
      3. If bse has end_date in past → 'closed' (not yet listed)
      4. If bse status is 'upcoming' → 'upcoming'
      5. If nse has rhp     → 'rhp_filed'
      6. If nse drhp_status is 'Approved' → 'sebi_approved'
      7. If nse has drhp    → 'drhp_filed'
      8. If sebi has RHP    → 'rhp_filed'
      9. If sebi has DRHP   → 'drhp_filed'
      10. If bse exists     → 'announced'
      11. Fallback          → 'unknown'
    """
    from datetime import datetime, timezone

    bse = record.bse_data
    nse = record.nse_data

    # 1. Live/open
    if bse and bse.status == 'open':
        return 'open'

    # 2. Listed (has final prospectus on NSE, or already past close)
    if nse and nse.fp_attach and nse.fp_attach.url:
        return 'listed'

    # 3. Closed (bidding done, awaiting listing)
    if bse and bse.end_date:
        try:
            end = datetime.strptime(bse.end_date[:10], '%Y-%m-%d').replace(tzinfo=timezone.utc)
            if end < datetime.now(timezone.utc):
                return 'closed'
        except (ValueError, IndexError):
            pass

    # 4. Upcoming
    if bse and bse.status == 'upcoming':
        return 'upcoming'

    # 5. RHP filed (confirmed IPO coming)
    if nse and nse.rhp_attach and nse.rhp_attach.url:
        return 'rhp_filed'

    # 6. SEBI approved
    if nse and nse.drhp_status and nse.drhp_status.lower() == 'approved':
        return 'sebi_approved'

    # 7. DRHP filed (NSE)
    if nse and nse.drhp_attach and nse.drhp_attach.url:
        return 'drhp_filed'

    # 8. RHP filed (SEBI only)
    if record.source == 'sebi' and record.document_type == 'RHP':
        return 'rhp_filed'

    # 9. BSE SME document
    if record.bse_sme_doc:
        if record.bse_sme_doc.document_type == 'DRHP':
            return 'drhp_filed'
        return 'rhp_filed' if record.bse_sme_doc.document_type in ('RHP', 'Prospectus') else 'drhp_filed'

    # 10. DRHP filed (SEBI only)
    if record.source == 'sebi' and record.document_type == 'DRHP':
        return 'drhp_filed'

    # 11. BSE metadata only
    if record.source == 'bse' and bse:
        return 'announced'

    return 'unknown'


def compute_dates(record: IPORecord) -> dict[str, Optional[str]]:
    """Extract key dates from all sources into a unified object."""
    nse = record.nse_data
    bse = record.bse_data
    sme = record.bse_sme_doc

    return {
        'drhp_filed': (
            nse.drhp_date
            if nse and nse.drhp_date
            else record.filing_date
            if record.document_type == 'DRHP' and record.filing_date
            else sme.date
            if sme and sme.document_type == 'DRHP'
            else None
        ),
        'rhp_filed': (
            nse.rhp_date
            if nse and nse.rhp_date
            else record.filing_date
            if record.document_type == 'RHP' and record.filing_date
            else sme.date
            if sme and sme.document_type in ('RHP', 'Prospectus')
            else None
        ),
        'fp_filed': nse.fp_date if nse and nse.fp_date else None,
        'open': bse.start_date if bse and bse.start_date else (
            nse.issue_open_date if nse and nse.issue_open_date else None
        ),
        'close': bse.end_date if bse and bse.end_date else (
            nse.issue_close_date if nse and nse.issue_close_date else None
        ),
    }


def compute_documents(record: IPORecord) -> dict[str, Optional[str]]:
    """Collect document PDF/URLs from all sources."""
    sebi_urls = record.document_urls
    nse = record.nse_data
    sme = record.bse_sme_doc

    doc: dict[str, Optional[str]] = {
        'drhp': None,
        'rhp': None,
        'final_prospectus': None,
        'abridged_prospectus': None,
    }

    # SEBI documents
    if sebi_urls:
        doc['drhp'] = sebi_urls.drhp_pdf or doc['drhp']
        doc['rhp'] = sebi_urls.rhp_pdf or doc['rhp']
        doc['abridged_prospectus'] = sebi_urls.abridged_prospectus_pdf or doc['abridged_prospectus']

        # If SEBI has detail_page but no PDF URLs yet, store it as the document source
        if sebi_urls.detail_page and not doc['drhp'] and not doc['rhp']:
            if record.document_type == 'DRHP':
                doc['drhp'] = sebi_urls.detail_page
            elif record.document_type == 'RHP':
                doc['rhp'] = sebi_urls.detail_page

    # NSE documents (prefer direct download URLs)
    if nse:
        if nse.drhp_attach and nse.drhp_attach.url:
            doc['drhp'] = nse.drhp_attach.url
        if nse.rhp_attach and nse.rhp_attach.url:
            doc['rhp'] = nse.rhp_attach.url
        if nse.fp_attach and nse.fp_attach.url:
            doc['final_prospectus'] = nse.fp_attach.url

    # BSE SME docs (for SMEs not on SEBI/NSE)
    if sme:
        if sme.document_type == 'DRHP' and not doc['drhp']:
            doc['drhp'] = sme.document_url
        elif sme.document_type in ('RHP', 'Prospectus') and not doc['rhp']:
            doc['rhp'] = sme.document_url

    return doc


def compute_summary(record: IPORecord) -> dict:
    """Build the clean unified summary with all computed fields."""
    bse = record.bse_data
    nse = record.nse_data

    return {
        'company_name': record.company_name,
        'status': compute_status(record),
        'dates': compute_dates(record),
        'documents': compute_documents(record),
        'price_band': bse.price_band if bse else None,
        'platform': bse.platform if bse else (nse.index if nse else None),
        'issue_type': bse.issue_type if bse else None,
    }