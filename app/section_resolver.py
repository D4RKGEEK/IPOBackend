"""
Section-based PDF resolver for IPO documents.

Flow:
  1. Download PDF (or extract from ZIP) → temp file
  2. Scan all pages for known section headers → page ranges
  3. Extract each section's text → save to DB
"""
import io, logging, os, re, tempfile, zipfile
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Import the R2 module at startup. Even if we never call into it, importing it
# runs its module-level .env loader so R2_BUCKET becomes visible to _r2_enabled().
try:
    from app.storage import r2 as _r2_module  # noqa: F401
except Exception as _e:
    _r2_module = None
    logger.debug("R2 module unavailable: %s", _e)


def _r2_enabled() -> bool:
    """R2 upload only runs when R2 is configured."""
    from app.config import settings
    return settings.r2_enabled

KNOWN_SECTIONS = [
    "GENERAL INFORMATION", "CAPITAL STRUCTURE",
    "CAPITAL STRUCTURE OF THE COMPANY",
    "OBJECTS OF THE OFFER", "OBJECTS OF THE ISSUE",
    "OBJECT OF THE ISSUE",
    "BASIS FOR OFFER PRICE", "BASIS FOR ISSUE PRICE",
    "BASIS FOR THE ISSUE PRICE",
    "RISK FACTORS", "OUR MANAGEMENT",
    "OUR PROMOTERS AND PROMOTER GROUP", "OUR PROMOTERS & PROMOTER GROUP",
    "OUR PROMOTER & PROMOTER GROUP",
    "DIVIDEND POLICY", "INDUSTRY OVERVIEW", "OUR BUSINESS", "BUSINESS OVERVIEW",
    "STATEMENT OF SPECIAL TAX BENEFITS", "STATEMENT OF POSSIBLE SPECIAL TAX BENEFITS",
    "STATEMENT OF TAX BENEFITS",
    "RESTATED FINANCIAL STATEMENTS", "RESTATED FINANCIAL STATEMENT",
    "RESTATED FINANCIAL INFORMATION", "RESTATED CONSOLIDATED FINANCIAL STATEMENTS",
    "RESTATED CONSOLIDATED FINANCIAL INFORMATION",
    "FINANCIAL INFORMATION", "FINANCIAL INFORMATION OF THE COMPANY",
    "OTHER FINANCIAL INFORMATION", "STATEMENT OF FINANCIAL INDEBTEDNESS",
    "FINANCIAL INDEBTEDNESS",
    "CAPITALISATION STATEMENT", "OUTSTANDING LITIGATION",
    "OUTSTANDING LITIGATION AND MATERIAL DEVELOPMENTS",
    "OUTSTANDING LITIGATIONS AND MATERIAL DEVELOPMENTS",
    "OUTSTANDING LITIGATION AND OTHER MATERIAL DEVELOPMENTS",
    "ISSUE PROCEDURE", "ISSUE STRUCTURE", "TERMS OF THE ISSUE", "TERMS OF THE OFFER",
    "OUR GROUP COMPANIES", "OUR GROUP COMPANY",
    "KEY REGULATIONS AND POLICIES", "KEY INDUSTRY REGULATIONS AND POLICIES",
    "KEY INDUSTRY REGULATIONS",
    "GOVERNMENT AND OTHER APPROVALS", "GOVERNMENT AND OTHER STATUTORY APPROVALS",
    "HISTORY AND CERTAIN CORPORATE MATTERS",
    "HISTORY AND CORPORATE STRUCTURE",
    "OUR HISTORY AND CORPORATE STRUCTURE",
    "OUR HISTORY AND CERTAIN CORPORATE MATTERS",
    "ABOUT THE COMPANY", "ABOUT OUR COMPANY", "ABOUT COMPANY",
]

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024


def _section_key(name: str) -> str:
    key = name.strip().upper().replace(" ", "_").replace("&", "AND").replace(".", "")
    ALIASES = {
        "RESTATED_FINANCIAL_STATEMENT": "RESTATED_FINANCIAL_STATEMENTS",
        "RESTATED_FINANCIAL_INFORMATION": "RESTATED_FINANCIAL_STATEMENTS",
        "RESTATED_CONSOLIDATED_FINANCIAL_STATEMENTS": "RESTATED_FINANCIAL_STATEMENTS",
        "RESTATED_CONSOLIDATED_FINANCIAL_INFORMATION": "RESTATED_FINANCIAL_STATEMENTS",
        "FINANCIAL_INFORMATION": "RESTATED_FINANCIAL_STATEMENTS",
        "FINANCIAL_INFORMATION_OF_THE_COMPANY": "RESTATED_FINANCIAL_STATEMENTS",
        "FINANCIAL_INDEBTEDNESS": "STATEMENT_OF_FINANCIAL_INDEBTEDNESS",
        "STATEMENT_OF_POSSIBLE_SPECIAL_TAX_BENEFITS": "STATEMENT_OF_SPECIAL_TAX_BENEFITS",
        "STATEMENT_OF_TAX_BENEFITS": "STATEMENT_OF_SPECIAL_TAX_BENEFITS",
        "OUR_PROMOTER_AND_PROMOTER_GROUP": "OUR_PROMOTERS_AND_PROMOTER_GROUP",
        "OUR_PROMOTERS_&_PROMOTER_GROUP": "OUR_PROMOTERS_AND_PROMOTER_GROUP",
        "OUR_PROMOTER_&_PROMOTER_GROUP": "OUR_PROMOTERS_AND_PROMOTER_GROUP",
        "CAPITALISATION_STATEMENT": "CAPITAL_STRUCTURE",
        "CAPITAL_STRUCTURE_OF_THE_COMPANY": "CAPITAL_STRUCTURE",
        "OUTSTANDING_LITIGATION_AND_MATERIAL_DEVELOPMENTS": "OUTSTANDING_LITIGATION",
        "OUTSTANDING_LITIGATIONS_AND_MATERIAL_DEVELOPMENTS": "OUTSTANDING_LITIGATION",
        "OUTSTANDING_LITIGATION_AND_OTHER_MATERIAL_DEVELOPMENTS": "OUTSTANDING_LITIGATION",
        "KEY_INDUSTRY_REGULATIONS": "KEY_REGULATIONS_AND_POLICIES",
        "KEY_INDUSTRY_REGULATIONS_AND_POLICIES": "KEY_REGULATIONS_AND_POLICIES",
        "GOVERNMENT_AND_OTHER_APPROVALS": "KEY_REGULATIONS_AND_POLICIES",
        "GOVERNMENT_AND_OTHER_STATUTORY_APPROVALS": "KEY_REGULATIONS_AND_POLICIES",
        "HISTORY_AND_CORPORATE_STRUCTURE": "HISTORY_AND_CERTAIN_CORPORATE_MATTERS",
        "OUR_HISTORY_AND_CORPORATE_STRUCTURE": "HISTORY_AND_CERTAIN_CORPORATE_MATTERS",
        "OUR_HISTORY_AND_CERTAIN_CORPORATE_MATTERS": "HISTORY_AND_CERTAIN_CORPORATE_MATTERS",
        "ABOUT_OUR_COMPANY": "ABOUT_THE_COMPANY",
        "ABOUT_COMPANY": "ABOUT_THE_COMPANY",
        "OBJECT_OF_THE_ISSUE": "OBJECTS_OF_THE_ISSUE",
        "BASIS_FOR_THE_ISSUE_PRICE": "BASIS_FOR_ISSUE_PRICE",
    }
    return ALIASES.get(key, key)


async def _download_pdf(url: str, client: httpx.AsyncClient) -> Optional[bytes]:
    """Download a PDF/ZIP. Retries transient network errors with backoff.

    Returns None on permanent failure (4xx, size cap, malformed URL) — callers
    surface this as a doc-level skip, not a job-level abort.
    """
    from app.retry import async_retry

    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    if "bseindia.com" in url.lower(): headers["Referer"] = "https://www.bseindia.com/"
    if "bsesme.com" in url.lower(): headers["Referer"] = "https://www.bsesme.com/"
    if "nsearchives.nseindia.com" in url.lower() or "nseindia.com" in url.lower():
        headers["Referer"] = "https://www.nseindia.com/"

    # Network-level errors that are worth retrying. NOT 4xx (would be permanent).
    transient = (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.ReadError,
        httpx.RemoteProtocolError,
        httpx.PoolTimeout,
    )

    @async_retry(attempts=3, base_delay=2.0, max_delay=15.0, retry_on=transient,
                 label=f"download_pdf({url[:60]}...)")
    async def _do_get() -> httpx.Response:
        return await client.get(url, headers=headers, timeout=120, follow_redirects=True)

    try:
        resp = await _do_get()
        if resp.status_code >= 500:
            logger.warning("PDF %s returned %d (no retry — final attempt)", url[:80], resp.status_code)
            return None
        resp.raise_for_status()
        content = resp.content
        if len(content) > MAX_DOWNLOAD_SIZE:
            logger.warning("Document too large (%d bytes): %s", len(content), url[:80])
            return None
        return content
    except httpx.HTTPStatusError as e:
        logger.warning("PDF %s returned HTTP %d", url[:80], e.response.status_code)
        return None
    except Exception as e:
        logger.warning("Failed to download %s: %s", url[:80], e)
        return None


def _extract_pdf_from_zip(zip_bytes: bytes) -> Optional[bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            pdf_files = [n for n in zf.namelist() if n.lower().endswith('.pdf')]
            if not pdf_files: return None
            pdf_files.sort(key=lambda n: (
                0 if 'drhp' in n.lower() or 'draft' in n.lower() else
                1 if 'rhp' in n.lower() or 'prospectus' in n.lower() else 2
            ))
            return zf.read(pdf_files[0])
    except Exception as e:
        logger.warning(f"ZIP extraction failed: {e}")
        return None


def _find_sections_in_doc(pdf_path: str, total_pages: int) -> list[dict]:
    import pymupdf
    doc = pymupdf.open(pdf_path)
    found = []
    for page_num in range(1, total_pages + 1):
        page = doc[page_num - 1]
        text = page.get_text("text")
        for known in KNOWN_SECTIONS:
            # Strategy A: standalone header on its own line
            for m in re.finditer(r'(?:^|\n)\s*' + re.escape(known) + r'\s*(?:\n|$)', text, re.IGNORECASE | re.MULTILINE):
                key = _section_key(known)
                if any(e["section_name"] == key and e["page"] == page_num for e in found): continue
                found.append({"section_name": key, "display_name": known, "page": page_num})
                break
            else:
                # Strategy B: "SECTION X – NAME" pattern
                for m in re.finditer(
                    r'SECTION\s+(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII|XIII|XIV|XV|XVI|XVII|XVIII|XIX|XX)\s*[–\-:.]*\s*'
                    + re.escape(known) + r'\s*(?:\n|$)',
                    text, re.IGNORECASE | re.MULTILINE,
                ):
                    key = _section_key(known)
                    if any(e["section_name"] == key and e["page"] == page_num for e in found): continue
                    found.append({"section_name": key, "display_name": known, "page": page_num})
                    break
    doc.close()
    # Dedup: keep LAST occurrence (body, not ToC)
    seen = {}
    for entry in found:
        seen[entry["section_name"]] = entry
    unique = list(seen.values())
    unique.sort(key=lambda e: e["page"])
    return unique


def _page_range_entries(sections: list[dict], total_pages: int) -> list[dict]:
    result = []
    for i, entry in enumerate(sections):
        page_start = entry["page"]
        page_end = sections[i + 1]["page"] - 1 if i + 1 < len(sections) else total_pages
        if page_end < page_start: page_end = page_start
        result.append({"section_name": entry["section_name"], "display_name": entry["display_name"],
                        "page_start": page_start, "page_end": page_end})
    return result


def _extract_page_with_tables(pdf_path: str, page_num: int, pymupdf_fallback: bool = True) -> str:
    """Extract a PDF page as markdown text + structured markdown tables.

    Standalone version (opens pdfplumber per call). Use _extract_plumber_page
    instead when you already have a pdfplumber instance open.
    """
    try:
        import pdfplumber
        from tabulate import tabulate

        with pdfplumber.open(pdf_path) as plumber:
            return _render_plumber_page(plumber.pages[page_num], tabulate)
    except Exception:
        if pymupdf_fallback:
            import pymupdf
            with pymupdf.open(pdf_path) as doc:
                return doc[page_num].get_text("text").strip()
        return ""


def _render_plumber_page(page, tabulate_module) -> str:
    """Extract text + tables from an already-open pdfplumber page. Returns markdown."""
    page_text, _ = _extract_page_structured(page, tabulate_module)
    return page_text


def _clean_headers_rows(headers: list[str], rows: list[list[str]]) -> Optional[dict]:
    """Remove empty columns from headers+rows, merging shifted column pairs.

    pdfplumber often splits merged/spanning cells into separate columns,
    creating a 1-column offset between header text and data text.
    This function detects and merges those pairs.

    Returns {"headers": [str], "rows": [list[str]]} or None if no data rows remain.
    """
    if not headers and not rows:
        return None

    all_rows = [headers] + (rows or [])
    num_cols = max(len(r) for r in all_rows)
    col_has_data = [False] * num_cols
    for row in all_rows:
        for ci in range(min(len(row), num_cols)):
            if row[ci].strip():
                col_has_data[ci] = True

    keep_idx = [ci for ci, has in enumerate(col_has_data) if has]
    if not keep_idx:
        return None

    def _keep(row):
        return [row[ci] for ci in keep_idx if ci < len(row)]

    clean_headers = _keep(headers)
    clean_rows = [_keep(r) for r in (rows or [])]

    # Helper: check if two columns never have data in the same data row
    def _mutually_exclusive(ca: int, cb: int) -> bool:
        if not clean_rows:
            return False
        for r in clean_rows:
            a = r[ca].strip() if ca < len(r) else False
            b = r[cb].strip() if cb < len(r) else False
            if a and b:
                return False  # same row has data in both columns → not exclusive
        return True

    # Detect and merge shifted column pairs.
    # Pattern: col A has data but no header, col B (adjacent) has header but no data,
    # and A and B are mutually exclusive across all data rows (no row has both).
    merged_idx = []
    i = 0
    while i < len(clean_headers):
        h_here = bool(clean_headers[i].strip())
        data_here = any(r[i].strip() for r in clean_rows if i < len(r)) if clean_rows else False

        # Skip header-only column whose pair was already merged
        if h_here and not data_here and i > 0:
            prev_h = bool(clean_headers[i - 1].strip())
            prev_data = any(r[i - 1].strip() for r in clean_rows if i - 1 < len(r)) if clean_rows else False
            if not prev_h and prev_data and _mutually_exclusive(i - 1, i):
                i += 1
                continue

        # Detect pair: col i has data but no header, col i+1 has header
        # and NO data in any data row, AND they are mutually exclusive per-row.
        # If col i+1 has data in some rows (alternating pattern, like BRLM index table),
        # don't merge — both columns carry independent data.
        if not h_here and data_here and i + 1 < len(clean_headers):
            nxt_h = bool(clean_headers[i + 1].strip())
            nxt_data = any(r[i + 1].strip() for r in clean_rows if i + 1 < len(r)) if clean_rows else False
            if nxt_h and not nxt_data and _mutually_exclusive(i, i + 1):
                clean_headers[i] = clean_headers[i + 1]
                merged_idx.append(i)
                i += 2
                continue

        merged_idx.append(i)
        i += 1

    final_headers = [clean_headers[i] for i in merged_idx]
    final_rows = []
    for row in clean_rows:
        final_rows.append([row[i] if i < len(row) else "" for i in merged_idx])

    final_rows = [r for r in final_rows if any(c.strip() for c in r)]
    if not final_rows:
        return None

    return {"headers": final_headers, "rows": final_rows}


def _extract_page_structured(page, tabulate_module) -> tuple[str, list[dict]]:
    """Extract text + structured tables from a pdfplumber page.

    Returns (markdown_text, list_of_table_dicts).
    Each table dict: {"page_num": int, "table_index": int,
                      "headers": [str], "rows": [[str]]}
    """
    page_text = (page.extract_text() or "").strip()
    structured_tables: list[dict] = []

    try:
        found = page.find_tables()
        tables_md_parts = []
        for ti, t in enumerate(found):
            tbl = t.extract()
            clean = []
            for row in tbl:
                cleaned: list[str] = []
                for c in row:
                    cleaned.append((c or "").strip().replace("\n", " "))
                if any(c for c in cleaned):
                    clean.append(cleaned)
            if clean:
                tables_md_parts.append(tabulate_module(clean, tablefmt="github"))
                structured_tables.append({
                    "page_num": page.page_number if hasattr(page, 'page_number') else 0,
                    "table_index": ti,
                    "headers": clean[0] if clean else [],
                    "rows": clean[1:] if len(clean) > 1 else [],
                })
        if tables_md_parts:
            return page_text + "\n\n" + "\n\n".join(tables_md_parts), structured_tables
    except Exception:
        pass

    return page_text, structured_tables


def _extract_page_pymupdf(pdf_path: str, page_num: int) -> str:
    """Lightweight text extraction using pymupdf (fast, low RAM)."""
    import pymupdf
    with pymupdf.open(pdf_path) as doc:
        return doc[page_num].get_text("text").strip()


async def resolve_document(ipo_id: int, doc_type: str, url: str, db_service, client: httpx.AsyncClient) -> dict:
    from app.notifications import notify
    import pymupdf
    raw = await _download_pdf(url, client)
    if raw is None:
        return {"status": "error", "doc_type": doc_type, "error": "download_failed"}
    is_zip = url.lower().endswith(".zip") or raw[:4] == b'PK\x03\x04'
    if is_zip:
        pdf_bytes = _extract_pdf_from_zip(raw)
        if pdf_bytes is None:
            return {"status": "error", "doc_type": doc_type, "error": "no_pdf_in_zip"}
    else:
        pdf_bytes = raw
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    try:
        tmp_path = tmp.name; tmp.close()
        with open(tmp_path, 'wb') as f: f.write(pdf_bytes)
        pdf_doc = pymupdf.open(tmp_path)
        total_pages = pdf_doc.page_count
        pdf_doc.close()
        page_entries = _find_sections_in_doc(tmp_path, total_pages)
        sections = _page_range_entries(page_entries, total_pages)
        db_service.delete_sections(ipo_id, doc_type)
        saved_sections = []

        # Open pdfplumber ONCE for all section extractions (but use pymupdf for text
        # during resolve to keep RAM low — pdfplumber can OOM on 500MB containers)
        import pymupdf as _pm

        try:
            for sec in sections:
                section_name = sec["section_name"]
                ps, pe = sec["page_start"], sec["page_end"]
                if ps and ps <= total_pages:
                    section_text = ""
                    for pn in range(ps - 1, min(pe, total_pages)):
                        try:
                            ft = _extract_page_pymupdf(tmp_path, pn)
                            if ft:
                                section_text += f"\n\n--- Page {pn + 1} ---\n\n{ft}"
                        except Exception:
                            pass
                    db_service.upsert_section(ipo_id, doc_type, section_name, page_start=ps, page_end=pe, raw_md=section_text)

                    r2_url = None
                    if _r2_module is not None and _r2_enabled() and section_text.strip():
                        try:
                            r2_url = _r2_module.upload_section(ipo_id, doc_type, section_name, section_text)
                        except Exception as e:
                            logger.warning(f"R2 upload failed for {ipo_id}/{doc_type}/{section_name}: {e}")

                    saved_sections.append({"section_name": section_name, "page_start": ps, "page_end": pe, "r2_url": r2_url})
                else:
                    db_service.upsert_section(ipo_id, doc_type, section_name)
        finally:
            pass  # no pdfplumber to close — using pymupdf for lightweight resolve

        if len(saved_sections) == 0:
            notify(
                f"⚠️ Resolve found 0 sections · ipo={ipo_id} · {doc_type.upper()}",
                level="warn",
                details={"ipo_id": ipo_id, "doc_type": doc_type, "url": url[:200], "total_pages": total_pages},
            )
        return {"status": "ok", "doc_type": doc_type, "total_pages": total_pages,
                "sections_found": len(saved_sections), "sections": saved_sections}
    except Exception as e:
        logger.error(f"Resolve failed for {ipo_id}/{doc_type}: {e}")
        notify(
            f"🚨 Resolve failed · ipo={ipo_id} · {doc_type.upper()}",
            level="error",
            details={"ipo_id": ipo_id, "doc_type": doc_type, "url": url[:200], "error": str(e)[:300]},
        )
        return {"status": "error", "doc_type": doc_type, "error": str(e)[:200]}
    finally:
        try: os.unlink(tmp_path)
        except: pass
