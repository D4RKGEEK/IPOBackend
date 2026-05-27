"""
PDF text + table extraction using pdfplumber only (replaces PyMuPDF).
Extracts clean text AND structured tables as JSON.

Saves results to:
  1. DB (ipo_parsed_data table, raw_text_*)
  2. Local JSON cache (.doc_cache/{ipo_id}_{doc_type}.json)
"""
import io
import json
import logging
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Optional

import httpx
import pdfplumber

logger = logging.getLogger(__name__)

# Max download size (50 MB)
MAX_DOC_SIZE = 50 * 1024 * 1024
# Cache dir for full pdfplumber output
CACHE_DIR = Path(__file__).parent.parent / ".doc_cache"


def _get_cache_path(ipo_id: int, doc_type: str) -> Path:
    CACHE_DIR.mkdir(exist_ok=True)
    return CACHE_DIR / f"{ipo_id}_{doc_type}.json"


async def download_document(url: str, client: httpx.AsyncClient) -> Optional[bytes]:
    """Download a document from URL with proper headers."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    }
    if "bseindia.com" in url.lower():
        headers["Referer"] = "https://www.bseindia.com/"
    if "nsearchives.nseindia.com" in url.lower() or "nseindia.com" in url.lower():
        headers["Referer"] = "https://www.nseindia.com/"
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


def _extract_pdf_from_zip(zip_bytes: bytes) -> Optional[bytes]:
    """Find and extract the first/best PDF from a ZIP archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            pdf_files = [n for n in zf.namelist() if n.lower().endswith('.pdf')]
            if not pdf_files:
                return None
            pdf_files.sort(key=lambda n: (
                0 if 'drhp' in n.lower() else 1 if 'draft' in n.lower() else 2
            ))
            best = pdf_files[0]
            logger.info(f"  Extracting PDF from ZIP: '{best}'")
            return zf.read(best)
    except Exception as e:
        logger.warning(f"Failed to extract from ZIP: {e}")
        return None


def extract_text_and_tables(pdf_bytes: bytes) -> Optional[dict]:
    """
    Extract text and tables from PDF bytes using pdfplumber.
    
    pdfplumber requires a file path — writes to a temp file, then removes it.
    
    Returns:
        dict with:
            - text: clean full text (str)
            - tables: list of {page: N, headers: [...], rows: [[...], ...]}
            - metadata: {pages: N, has_tables: bool}
    """
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    try:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name
        tmp.close()
        
        with pdfplumber.open(tmp_path) as pdf:
            pages_text = []
            all_tables = []
            
            for page_num, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                pages_text.append(text)
                
                raw_tables = page.extract_tables()
                for table in raw_tables:
                    if not table or len(table) < 2:
                        continue
                    headers = [str(h).strip() if h else "" for h in table[0]]
                    rows = []
                    for row in table[1:]:
                        clean_row = [str(c).strip() if c else "" for c in row]
                        if any(c for c in clean_row if c):
                            rows.append(clean_row)
                    if rows:
                        all_tables.append({
                            "page": page_num,
                            "headers": headers,
                            "rows": rows,
                        })
            
            full_text = "\n\n".join(pages_text)
            
            result = {
                "text": full_text,
                "tables": all_tables,
                "metadata": {
                    "pages": len(pdf.pages),
                    "has_tables": len(all_tables) > 0,
                    "total_chars": len(full_text),
                    "total_tables": len(all_tables),
                },
            }
            
            # Truncate text to 500KB to avoid DB bloat
            max_chars = 500 * 1024
            if len(full_text) > max_chars:
                result["text"] = full_text[:max_chars]
                result["metadata"]["truncated"] = True
                result["metadata"]["original_chars"] = len(full_text)
            
            return result
    
    except Exception as e:
        logger.warning(f"pdfplumber extraction failed: {e}")
        return None
    
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp.name)
        except (NameError, OSError):
            pass


def save_cache(ipo_id: int, doc_type: str, data: dict) -> str:
    """Save full pdfplumber output (text + tables) to local JSON cache."""
    cache_path = _get_cache_path(ipo_id, doc_type)
    with open(cache_path, "w") as f:
        json.dump(data, f, default=str)
    return str(cache_path)


def load_cache(ipo_id: int, doc_type: str) -> Optional[dict]:
    """Load previously cached pdfplumber output."""
    cache_path = _get_cache_path(ipo_id, doc_type)
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    return None


async def extract_document(
    url: str,
    client: httpx.AsyncClient,
    ipo_id: Optional[int] = None,
    doc_type: Optional[str] = None,
) -> Optional[dict]:
    """
    Download URL (PDF or ZIP), extract text + tables via pdfplumber.
    Optionally caches to local JSON if ipo_id and doc_type provided.
    
    Returns: dict with text, tables, metadata
    """
    if ipo_id and doc_type:
        cached = load_cache(ipo_id, doc_type)
        if cached:
            logger.info(f"  Using cached: {_get_cache_path(ipo_id, doc_type)}")
            return cached
    
    raw = await download_document(url, client)
    if raw is None:
        return None
    
    is_zip = url.lower().endswith(".zip") or raw[:4] == b'PK\x03\x04'
    
    if is_zip:
        pdf_bytes = _extract_pdf_from_zip(raw)
        if pdf_bytes is None:
            logger.warning(f"  No PDF found in ZIP: {url[:80]}")
            return None
    else:
        pdf_bytes = raw
    
    result = extract_text_and_tables(pdf_bytes)
    if result is None:
        return None
    
    if ipo_id and doc_type and result.get("text"):
        save_cache(ipo_id, doc_type, result)
        logger.info(f"  Cached: {_get_cache_path(ipo_id, doc_type)}")
    
    if result.get("text"):
        logger.info(f"  Extracted {result['metadata']['total_chars']:,} chars, "
                    f"{result['metadata']['total_tables']} tables")
    
    return result
