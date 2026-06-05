"""Merge functions — attach source data to IPORecord results list."""
from typing import Optional

from app.schemas import (
    BSEData, BSESMEDocument, DocumentUrls, IPORecord, NSEData, UpstoxData,
)
from app.utils import normalize_company_name


def _source_count(record: IPORecord) -> int:
    """Count sources contributing data to this record."""
    return sum(1 for x in [
        record.document_urls, record.bse_data,
        record.nse_data, record.bse_sme_doc, record.upstox_data,
    ] if x is not None and (
        x.model_dump() if hasattr(x, 'model_dump') else str(x)
    ) not in ('{}', 'None'))


def merge_upstox_into_results(results: list[IPORecord], upstox_rows: list[UpstoxData]) -> None:
    index = {normalize_company_name(r.company_name): r for r in results}
    for row in upstox_rows:
        key = normalize_company_name(row.name)
        existing = index.get(key)
        if existing:
            existing.upstox_data = row
        else:
            record = IPORecord(company_name=row.name, source="upstox", upstox_data=row)
            results.append(record)
            index[key] = record


def merge_bse_into_results(results: list[IPORecord], bse_rows: list[BSEData]) -> None:
    index = {normalize_company_name(r.company_name): r for r in results}
    for row in bse_rows:
        key = normalize_company_name(row.company_name)
        existing = index.get(key)
        if existing:
            existing.bse_data = row
        else:
            record = IPORecord(company_name=row.company_name, source="bse", bse_data=row)
            results.append(record)
            index[key] = record


def merge_bse_sme_docs(results: list[IPORecord], sme_docs: list[BSESMEDocument]) -> None:
    index = {normalize_company_name(r.company_name): r for r in results}
    for doc in sme_docs:
        key = normalize_company_name(doc.company_name)
        existing = index.get(key)
        if existing:
            existing.bse_sme_doc = doc
        else:
            record = IPORecord(
                company_name=doc.company_name, source="bse",
                document_type="DRHP" if doc.document_type == "DRHP" else "RHP",
                bse_sme_doc=doc,
            )
            results.append(record)
            index[key] = record


def merge_sebi_into_results(results: list[IPORecord], sebi_records: list[IPORecord]) -> None:
    """Attach SEBI filing data (company existence + filing date + doc type) to existing records."""
    index = {normalize_company_name(r.company_name): r for r in results}
    for record in sebi_records:
        key = normalize_company_name(record.company_name)
        existing = index.get(key)
        if existing:
            # Only fill gaps — NSE/Upstox take priority
            if existing.document_urls is None and record.document_urls:
                existing.document_urls = record.document_urls
            if existing.filing_date is None and record.filing_date:
                existing.filing_date = record.filing_date
            if existing.document_type is None and record.document_type:
                existing.document_type = record.document_type
        else:
            results.append(record)
            index[key] = record


def merge_nse_into_results(results: list[IPORecord], nse_rows: list[NSEData]) -> None:
    index = {normalize_company_name(r.company_name): r for r in results}
    for nse_row in nse_rows:
        key = normalize_company_name(nse_row.company_name)
        existing = index.get(key)
        if existing:
            existing.nse_data = nse_row
        else:
            doc_type = None
            urls = None
            if nse_row.drhp:
                doc_type = "DRHP"
                urls = DocumentUrls(drhp_pdf=nse_row.drhp_attach.url if nse_row.drhp_attach else None)
            elif nse_row.rhp:
                doc_type = "RHP"
                urls = DocumentUrls(rhp_pdf=nse_row.rhp_attach.url if nse_row.rhp_attach else None)
            record = IPORecord(
                company_name=nse_row.company_name, source="nse",
                document_type=doc_type, document_urls=urls, nse_data=nse_row,
            )
            results.append(record)
            index[key] = record
