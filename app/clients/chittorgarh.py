"""Chittorgarh client — uses their internal JSON APIs. No HTML scraping needed.

API endpoints discovered:
  - /cloud/ipo/list-read          → current IPOs (24 items)
  - /cloud/ipo/ipo-url-lists      → all historical IPOs (3100+ items)
  - /cloud/report/data-read/82/1/5/{year}/... → IPO report with dates/prices
  - chittorgarh.net/reports/ipo_notes/{slug}-rhp.pdf  → RHP PDF (direct URL)
"""
import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://webnodejs.chittorgarh.com/cloud"
PDF_BASE = "https://www.chittorgarh.net/reports/ipo_notes"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


async def fetch_current_ipos() -> list[dict]:
    """Fetch current/live IPOs from Chittorgarh dropdown API."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_BASE}/ipo/list-read", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("ipoDropDownList", [])


async def fetch_all_ipos() -> list[dict]:
    """Fetch ALL historical IPOs (3100+). Good for cross-referencing."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{API_BASE}/ipo/ipo-url-lists", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("lists", [])


async def fetch_report(year: int = 2026, category: int = 1) -> list[dict]:
    """Fetch IPO report data.

    Args:
        year: Financial year (e.g. 2026)
        category: 1=mainboard, 2=SME
    Returns:
        List of IPO records with dates, prices, listing info, ISIN, symbols
    """
    url = (
        f"{API_BASE}/report/data-read/82/{category}/5/{year}/{year}-27/0/all/0"
        "?search=&v=21-12"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("reportTableData", [])


PDF_SUFFIXES = ["-rhp", "-drhp", "-prospectus"]
"""Known PDF suffixes on Chittorgarh. Tried in order — returns first 200."""


def make_pdf_url(slug: str, suffix: str) -> str:
    """Construct a Chittorgarh PDF URL from slug and suffix."""
    return f"{PDF_BASE}/{slug}{suffix}.pdf"


async def verify_url(url: str) -> bool:
    """HEAD-check a URL. Returns True if accessible (200)."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.head(url, headers=HEADERS, timeout=10, follow_redirects=True)
            return resp.status_code == 200
        except Exception:
            return False


async def find_document_url(slug: str, prefer: Optional[list[str]] = None) -> Optional[str]:
    """Try all known PDF suffixes on Chittorgarh, return first verified URL.

    Args:
        slug: Company slug (e.g. 'yaashvi-jewellers')
        prefer: Optional suffix priority (e.g. ['-rhp', '-prospectus']).
                Defaults to all known suffixes in declaration order.
    """
    suffixes = prefer or PDF_SUFFIXES
    for suffix in suffixes:
        url = make_pdf_url(slug, suffix)
        if await verify_url(url):
            return url
    return None


# ─── Backward-compatible aliases ─────────────────────────────


def get_rhp_url(slug: str) -> str:
    """Construct RHP PDF URL (DEPRECATED — use make_pdf_url + find_document_url)."""
    return make_pdf_url(slug, "-rhp")


async def verify_rhp_url(slug: str) -> Optional[str]:
    """Backward-compatible: check only -rhp suffix. Use find_document_url for multi-suffix."""
    url = make_pdf_url(slug, "-rhp")
    if await verify_url(url):
        return url
    return None
