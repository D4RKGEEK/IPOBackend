"""
Chittorgarh/Investorgain API client for GMP (Grey Market Premium) data.

Two API shapes:
  1. LIST   → All IPOs with current GMP snapshot  (30 items, paginated)
  2. DETAIL → Day-wise GMP history per IPO         (12 days)

List URL:  /cloud/v2/report/data-read/331/1/6/2026/2026-27/0/all?search=&v=13-18
Detail URL: /cloud/v2/ipo/ipo-gmp-read/{ipo_gmp_id}/true?v=13-51
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

from app.gmp_data.schemas import RawGmpDetail

logger = logging.getLogger(__name__)

BASE = "https://webnodejs.investorgain.com"

# The version/v param changes — we just pass it through. The server accepts
# most values; we use a fixed recent one.
LIST_URL = BASE + "/cloud/v2/report/data-read/331/1/6/2026/2026-27/0/all?search=&v=13-18"
DETAIL_URL = BASE + "/cloud/v2/ipo/ipo-gmp-read/{ipo_gmp_id}/true?v=13-51"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.investorgain.com/",
    "Origin": "https://www.investorgain.com",
}

# ─── HTML field parsers ───────────────────────────────────────


def _parse_gmp_html(gmp_html: str) -> tuple[float, float]:
    """Extract numeric GMP value and percent from HTML like:
    '₹<b>11.75</b> (24.44%)<br><small...'
    Returns (gmp_value, gmp_percent). '--' means 0.
    """
    if not gmp_html:
        return (0.0, 0.0)

    # GMP value: <b>11.75</b> or <b>--</b>
    val_match = re.search(r"<b>\s*([\d.]+|--)\s*</b>", gmp_html)
    gmp_val = 0.0
    if val_match and val_match.group(1) != "--":
        try:
            gmp_val = float(val_match.group(1))
        except ValueError:
            gmp_val = 0.0

    # GMP percent: (24.44%) or (0.00%)
    pct_match = re.search(r"\(\s*([\d.]+)\s*%\)", gmp_html)
    gmp_pct = 0.0
    if pct_match:
        try:
            gmp_pct = float(pct_match.group(1))
        except ValueError:
            gmp_pct = 0.0

    return (gmp_val, gmp_pct)


def _parse_name(name_html: str) -> str:
    """Extract clean company name from HTML like:
    '<a href="/gmp/..." title="Hexagon Nutrition" ...>Hexagon Nutrition</a> <span class="badge"...'
    """
    m = re.search(r'title="([^"]+)"', name_html)
    return m.group(1) if m else re.sub(r"<[^>]+>", "", name_html).strip()


def _parse_anchor(anchor_html: Optional[str]) -> bool:
    """Anchor status: ✅ means YES."""
    if not anchor_html:
        return False
    return "✅" in anchor_html or "green" in anchor_html.lower()


def _parse_size(size_html: str) -> float:
    """Extract IPO size in Crores from '₹54.27 Cr' or '&#8377;138.87 Cr' or '₹3.75 Cr'."""
    # Remove HTML entities like &#8377; first, then parse
    import re
    clean = re.sub(r"&#\d+;", "", size_html)  # strip HTML entities
    m = re.search(r"([\d.]+)\s*Cr", clean.replace(",", ""), re.IGNORECASE)
    if m:
        return float(m.group(1))
    # Fallback: find last decimal number before Cr
    m = re.search(r"([\d.]+)\s*Cr", size_html.replace(",", ""), re.IGNORECASE)
    return float(m.group(1)) if m else 0.0


def _parse_price(price_str: str) -> float:
    """Parse price band string → top value (for GMP % calculation)."""
    m = re.search(r"([\d.]+)", str(price_str))
    return float(m.group(1)) if m else 0.0


def _parse_lot(lot_str: str) -> int:
    """Parse lot size string."""
    m = re.search(r"(\d+)", str(lot_str))
    return int(m.group(1)) if m else 0


def _parse_updated_on(html: str) -> str:
    """Extract readable update timestamp from '...<b>6-Jun 13:28</b>...'"""
    m = re.search(r"<b>([^<]+)</b>", html)
    return m.group(1) if m else html


# ─── List API ─────────────────────────────────────────────────

def parse_list_row(row: dict) -> Optional[dict]:
    """Parse one raw row from the list API reportTableData into clean dict.

    Returns dict suitable for CleanGmpSnapshot or None if row is unusable.
    """
    try:
        gmp_val, gmp_pct = _parse_gmp_html(row.get("GMP", ""))
        name = _parse_name(row.get("Name", ""))

        return {
            "ipo_gmp_id": row.get("~id"),
            "company_name": name,
            "gmp": gmp_val,
            "gmp_percent": gmp_pct,
            "price_band_top": _parse_price(row.get("Price (₹)", "")),
            "ipo_size_cr": _parse_size(row.get("IPO Size", "")),
            "lot_size": _parse_lot(row.get("Lot", "")),
            "open_date": row.get("~Srt_Open", ""),
            "close_date": row.get("~Srt_Close", ""),
            "listing_date": row.get("~Str_Listing", ""),
            "category": row.get("~IPO_Category", ""),
            "updated_on": _parse_updated_on(row.get("Updated-On", "")),
            "anchor": _parse_anchor(row.get("Anchor")),
            "raw_pe": row.get("~P/E", ""),
            "url_slug": row.get("~urlrewrite_folder_name", ""),
        }
    except Exception as e:
        logger.debug("parse_list_row: %s — %s", e, str(row)[:100])
        return None


async def fetch_list(client: httpx.AsyncClient) -> list[dict]:
    """Fetch the GMP list. Returns list of clean row dicts."""
    try:
        resp = await client.get(LIST_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            logger.warning("gmp list: HTTP %d", resp.status_code)
            return []
        data = resp.json()
        raw_rows = data.get("reportTableData") or []
        parsed = []
        for row in raw_rows:
            p = parse_list_row(row)
            if p:
                parsed.append(p)
        logger.info("gmp list: %d/%d rows parsed", len(parsed), len(raw_rows))
        return parsed
    except Exception as e:
        logger.error("gmp list fetch failed: %s", e)
        return []


# ─── Detail API ───────────────────────────────────────────────

def parse_detail(raw: RawGmpDetail) -> list[dict]:
    """Parse detail API response into clean daily GMP entries.

    Returns list of dicts sorted by date descending (as API returns them).
    """
    if not raw.ipoGmpData:
        return []

    clean = []
    for day in raw.ipoGmpData:
        try:
            gmp_val = float(day.gmp) if day.gmp and day.gmp.replace(".", "", 1).isdigit() else 0.0
        except (ValueError, TypeError):
            gmp_val = 0.0

        try:
            est_price = float(day.estimated_listing_price) if day.estimated_listing_price else 0.0
        except (ValueError, TypeError):
            est_price = 0.0

        try:
            profit = float(day.est_profit) if day.est_profit else 0.0
        except (ValueError, TypeError):
            profit = 0.0

        clean.append({
            "date": day.gmp_date or "",
            "gmp": gmp_val,
            "up_down": day.up_down_status or "",
            "est_listing_price": est_price,
            "subject_to_sauda": day.subject_to_sauda or "0",
            "est_profit": profit,
            "rating": day.gmp_rating or 0,
            "active": bool(day.gmp_active_record_flag),
        })

    return clean


async def fetch_detail(client: httpx.AsyncClient, ipo_gmp_id: int) -> Optional[list[dict]]:
    """Fetch day-wise GMP history for one IPO. Returns list of clean day entries
    or None on failure."""
    url = DETAIL_URL.format(ipo_gmp_id=ipo_gmp_id)
    try:
        resp = await client.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            logger.warning("gmp detail %s: HTTP %d", ipo_gmp_id, resp.status_code)
            return None
        raw = RawGmpDetail(**resp.json())
        if raw.msg != 1:
            logger.warning("gmp detail %s: msg=%s", ipo_gmp_id, raw.msg)
            return None
        result = parse_detail(raw)
        logger.info("gmp detail %s: %d days", ipo_gmp_id, len(result))
        return result
    except Exception as e:
        logger.error("gmp detail %s failed: %s", ipo_gmp_id, e)
        return None
