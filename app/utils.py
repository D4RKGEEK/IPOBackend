import asyncio
import re
import time
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse


SEBI_BASE_URL = "https://www.sebi.gov.in"


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
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%b %d, %Y", "%B %d, %Y"):
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
