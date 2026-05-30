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
    """R2 upload only runs when the bucket env var is set."""
    return bool(os.environ.get("R2_BUCKET"))

KNOWN_SECTIONS = [
    "GENERAL INFORMATION", "CAPITAL STRUCTURE",
    "OBJECTS OF THE OFFER", "OBJECTS OF THE ISSUE",
    "BASIS FOR OFFER PRICE", "BASIS FOR ISSUE PRICE",
    "RISK FACTORS", "OUR MANAGEMENT",
    "OUR PROMOTERS AND PROMOTER GROUP", "OUR PROMOTERS & PROMOTER GROUP",
    "OUR PROMOTER & PROMOTER GROUP",
    "DIVIDEND POLICY", "INDUSTRY OVERVIEW", "OUR BUSINESS", "BUSINESS OVERVIEW",
    "STATEMENT OF SPECIAL TAX BENEFITS", "STATEMENT OF POSSIBLE SPECIAL TAX BENEFITS",
    "RESTATED FINANCIAL STATEMENTS", "RESTATED FINANCIAL STATEMENT",
    "RESTATED FINANCIAL INFORMATION", "RESTATED CONSOLIDATED FINANCIAL STATEMENTS",
    "OTHER FINANCIAL INFORMATION", "STATEMENT OF FINANCIAL INDEBTEDNESS",
    "CAPITALISATION STATEMENT", "OUTSTANDING LITIGATION",
    "ISSUE PROCEDURE", "ISSUE STRUCTURE", "TERMS OF THE ISSUE", "TERMS OF THE OFFER",
    "OUR GROUP COMPANIES", "OUR GROUP COMPANY",
    "KEY REGULATIONS AND POLICIES", "HISTORY AND CERTAIN CORPORATE MATTERS",
    "ABOUT THE COMPANY",
]

MAX_DOWNLOAD_SIZE = 50 * 1024 * 1024


def _section_key(name: str) -> str:
    key = name.strip().upper().replace(" ", "_").replace("&", "AND").replace(".", "")
    ALIASES = {
        "RESTATED_FINANCIAL_STATEMENT": "RESTATED_FINANCIAL_STATEMENTS",
        "RESTATED_FINANCIAL_INFORMATION": "RESTATED_FINANCIAL_STATEMENTS",
        "RESTATED_CONSOLIDATED_FINANCIAL_STATEMENTS": "RESTATED_FINANCIAL_STATEMENTS",
        "STATEMENT_OF_POSSIBLE_SPECIAL_TAX_BENEFITS": "STATEMENT_OF_SPECIAL_TAX_BENEFITS",
        "OUR_PROMOTER_AND_PROMOTER_GROUP": "OUR_PROMOTERS_AND_PROMOTER_GROUP",
        "OUR_PROMOTERS_&_PROMOTER_GROUP": "OUR_PROMOTERS_AND_PROMOTER_GROUP",
        "OUR_PROMOTER_&_PROMOTER_GROUP": "OUR_PROMOTERS_AND_PROMOTER_GROUP",
        "CAPITALISATION_STATEMENT": "CAPITAL_STRUCTURE",
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
        pdf_doc = pymupdf.open(tmp_path)
        for sec in sections:
            section_name = sec["section_name"]
            ps, pe = sec["page_start"], sec["page_end"]
            if ps and ps <= total_pages:
                section_text = ""
                for pn in range(ps - 1, min(pe, total_pages)):
                    try:
                        section_text += f"\n\n--- Page {pn + 1} ---\n\n{pdf_doc[pn].get_text('text')}"
                    except: pass
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
        pdf_doc.close()
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
