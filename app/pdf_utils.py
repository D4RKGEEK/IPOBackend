"""
PDF text extraction utilities.

Downloads PDFs (or ZIPs containing PDFs), extracts text, and stores it.
No file storage — text goes directly to DB.
"""
import io
import logging
import re
import zipfile
from typing import Optional

import httpx
import fitz  # PyMuPDF

logger = logging.getLogger(__name__)

# Max download size for documents (50 MB)
MAX_DOC_SIZE = 50 * 1024 * 1024


async def download_document(url: str, client: httpx.AsyncClient) -> Optional[bytes]:
    """Download a document from URL. Returns raw bytes or None."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    # BSE downloads need Referer
    if "bseindia.com" in url.lower():
        headers["Referer"] = "https://www.bseindia.com/"
        headers["Accept"] = "application/pdf,application/zip,application/octet-stream,*/*"
    # NSE downloads need Referer
    if "nsearchives.nseindia.com" in url.lower() or "nseindia.com" in url.lower():
        headers["Referer"] = "https://www.nseindia.com/"
        headers["Accept"] = "application/pdf,application/zip,*/*"
    try:
        resp = await client.get(url, headers=headers, timeout=60, follow_redirects=True)
        resp.raise_for_status()
        content = resp.content
        if len(content) > MAX_DOC_SIZE:
            logger.warning(f"Document too large ({len(content)} bytes): {url[:80]}")
            return None
        return content
    except Exception as e:
        logger.warning(f"Failed to download {url[:80]}: {e}")
        return None


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> Optional[str]:
    """Extract text from raw PDF bytes using PyMuPDF."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text_parts = []
        for page in doc:
            text = page.get_text()
            if text and text.strip():
                text_parts.append(text)
        doc.close()
        full_text = "\n\n".join(text_parts)
        return full_text if full_text.strip() else None
    except Exception as e:
        logger.warning(f"Failed to extract text from PDF: {e}")
        return None


def extract_pdf_from_zip_bytes(zip_bytes: bytes) -> Optional[bytes]:
    """Find and return the first PDF binary from a ZIP archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            pdf_files = [
                name for name in zf.namelist()
                if name.lower().endswith(".pdf")
            ]
            if not pdf_files:
                return None

            # Prefer DRHP/named docs over "abridged"
            def sort_key(name):
                lower = name.lower()
                if "drhp" in lower:
                    return 0
                if "draft" in lower and "prospectus" in lower:
                    return 1
                return 2

            pdf_files.sort(key=sort_key)
            best = pdf_files[0]
            logger.info(f"  Extracting PDF from ZIP: '{best}'")
            return zf.read(best)
    except Exception as e:
        logger.warning(f"Failed to extract PDF from ZIP: {e}")
        return None


async def download_and_extract_text(
    url: str,
    client: httpx.AsyncClient,
) -> Optional[str]:
    """
    Download a document URL (PDF or ZIP), extract text.
    
    Returns extracted text string, or None on failure.
    Handles both .pdf and .zip URLs transparently.
    """
    raw = await download_document(url, client)
    if raw is None:
        return None
    
    is_zip = url.lower().endswith(".zip") or _looks_like_zip(raw)

    if is_zip:
        pdf_bytes = extract_pdf_from_zip_bytes(raw)
        if pdf_bytes is None:
            logger.warning(f"  No PDF found in ZIP: {url[:80]}")
            return None
        text = extract_text_from_pdf_bytes(pdf_bytes)
    else:
        text = extract_text_from_pdf_bytes(raw)
    
    if text:
        # Truncate to reasonable size (500KB max to avoid DB bloat)
        max_chars = 500 * 1024
        if len(text) > max_chars:
            text = text[:max_chars]
            logger.info(f"  Truncated text to {max_chars} chars")
        
        logger.info(f"  Extracted {len(text):,} chars from {url[:60]}")
    else:
        logger.warning(f"  No text extracted from {url[:60]}")
    
    return text


def _looks_like_zip(data: bytes) -> bool:
    """Check if bytes look like a ZIP archive (PK header)."""
    return len(data) > 4 and data[:4] == b'PK\x03\x04'
