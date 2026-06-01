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


def get_rhp_url(slug: str) -> str:
    """Construct RHP PDF URL from IPO slug. No API call needed.

    Pattern: https://www.chittorgarh.net/reports/ipo_notes/{slug}-rhp.pdf
    Returns URL even if file doesn't exist — caller should verify with HEAD.
    """
    return f"{PDF_BASE}/{slug}-rhp.pdf"


async def verify_rhp_url(slug: str) -> Optional[str]:
    """Check if RHP PDF actually exists for this slug. Returns URL or None."""
    url = get_rhp_url(slug)
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.head(url, headers=HEADERS, timeout=10, follow_redirects=True)
            if resp.status_code == 200:
                return url
        except Exception:
            pass
    return None
