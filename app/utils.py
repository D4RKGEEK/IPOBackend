import asyncio
import io
import re
import time
import zipfile
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx


SEBI_BASE_URL = "https://www.sebi.gov.in"
NSE_DOWNLOAD_BASE = "https://nsearchives.nseindia.com"

# Maximum ZIP size to automatically extract (50 MB)
MAX_ZIP_EXTRACT_SIZE = 50 * 1024 * 1024


def normalize_company_name(name: str) -> str:
    normalized = (name or "").upper().strip()
    normalized = re.sub(r"\s*-\s*(DRHP|RHP|UDRHP|IPO|FPO)$", "", normalized)
    normalized = re.sub(r"\s+PRIVATE\s+LIMITED$", " PVT LTD", normalized)
    normalized = re.sub(r"\s+LIMITED$", " LTD", normalized)
    normalized = re.sub(r"[^A-Z0-9 ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def parse_source_date(value: str) -> Optional[date]:
    if not value:
        return None

    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass

    try:
        return parsedate_to_datetime(value).date()
    except (TypeError, ValueError):
        return None


def format_date(value: str) -> Optional[str]:
    parsed = parse_source_date(value)
    return parsed.isoformat() if parsed else value or None


def to_sebi_date(value: str) -> str:
    parsed = parse_source_date(value)
    return parsed.strftime("%d-%m-%Y") if parsed else value


def absolutize_url(url: Optional[str], base_url: str = SEBI_BASE_URL) -> Optional[str]:
    if not url:
        return None
    return url if url.startswith("http") else urljoin(base_url, url)


def extract_file_url(src: str) -> Optional[str]:
    parsed = urlparse(src or "")
    query = parse_qs(parsed.query)
    file_values = query.get("file")
    if file_values:
        return unquote(file_values[0])

    match = re.search(r"file=([^&\s]+)", src or "")
    if match:
        return unquote(match.group(1))

    return None


def clean_url(url: Optional[str]) -> Optional[str]:
    """Strip trailing whitespace, \r, \n, \t from URLs.

    NSE sometimes returns URLs with trailing \r characters or trailing spaces.
    """
    if not url:
        return None
    cleaned = url.strip()
    # Also strip any trailing control characters
    cleaned = re.sub(r"[\x00-\x1f]+$", "", cleaned)
    return cleaned if cleaned else None


def is_zip_url(url: Optional[str]) -> bool:
    """Check if a URL points to a ZIP file."""
    if not url:
        return False
    return url.lower().endswith(".zip")


async def extract_pdf_from_zip(
    zip_url: str,
    client: httpx.AsyncClient,
    max_size: int = MAX_ZIP_EXTRACT_SIZE,
) -> Optional[str]:
    """Download a ZIP file and return the URL of the first PDF found inside.

    Returns the first .pdf filename found in the ZIP.
    A helper endpoint can later serve the extracted content.
    """
    try:
        response = await client.get(zip_url, timeout=60, follow_redirects=True)
        response.raise_for_status()

        content_length = len(response.content)
        if content_length > max_size:
            return None  # Too large to auto-extract

        with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
            # Find the DRHP PDF first, then any PDF
            pdf_files = []
            for name in zf.namelist():
                if name.lower().endswith(".pdf"):
                    pdf_files.append(name)

            if not pdf_files:
                return None

            def sort_key(n):
                lower = n.lower()
                # Prefer DRHP PDF over abridged prospectus
                if "drhp" in lower:
                    return 0
                if "draft" in lower and "prospectus" in lower:
                    return 1
                return 2

            pdf_files.sort(key=sort_key)

            # Extract the best PDF
            best_pdf_name = pdf_files[0]
            pdf_data = zf.read(best_pdf_name)

            # Determine a filename to save as
            import hashlib
            url_hash = hashlib.md5(zip_url.encode()).hexdigest()[:12]
            safe_name = re.sub(r"[^\w\-.]", "_", best_pdf_name)
            output_filename = f"{url_hash}_{safe_name}"

            # Save to a temp/cache directory
            import os
            cache_dir = os.path.join(os.path.dirname(__file__), "..", ".doc_cache")
            os.makedirs(cache_dir, exist_ok=True)
            output_path = os.path.join(cache_dir, output_filename)
            with open(output_path, "wb") as f:
                f.write(pdf_data)

            return output_path

    except Exception:
        return None


class AsyncRateLimiter:
    def __init__(self, min_interval_seconds: float):
        self.min_interval_seconds = min_interval_seconds
        self.last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self.last_call
            if elapsed < self.min_interval_seconds:
                await asyncio.sleep(self.min_interval_seconds - elapsed)
            self.last_call = time.monotonic()