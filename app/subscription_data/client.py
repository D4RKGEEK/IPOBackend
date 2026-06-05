"""
NSE + BSE API clients for IPO subscription data.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.subscription_data.schemas import BidDetailRow, BseCatRow

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
NSE_BID_DETAILS = f"{NSE_BASE}/api/ipo-bid-details"
NSE_HOMEPAGE = NSE_BASE

BSE_API = "https://api.bseindia.com/BseIndiaAPI/api"
BSE_SUBS_URL = f"{BSE_API}/Pubissues_BBS_CumultveCatdem_ng/w"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


class NSEClient:
    """NSE IPO subscription. Phase 1: direct, Phase 2: session retry."""
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._session_ok = False

    async def fetch(self, symbol: str) -> Optional[tuple[list[BidDetailRow], Optional[str]]]:
        for attempt, use_session in enumerate([False, True], 1):
            if use_session and not self._session_ok:
                await self._establish()
            url = f"{NSE_BID_DETAILS}?symbol={symbol}&series=EQ"
            h = {**HEADERS, "Referer": f"{NSE_BASE}/market-data/ipo-subscription-status"}
            try:
                resp = await self.client.get(url, headers=h, timeout=15)
                if resp.status_code != 200:
                    logger.warning("nse[%d] %s: HTTP %d", attempt, symbol, resp.status_code)
                    continue
                raw = resp.json()
                raw_rows = raw.get("data") or []
                update_time = raw.get("updateTime")
                if not raw_rows:
                    continue
                rows = [
                    BidDetailRow(**r) for r in raw_rows
                    if isinstance(r, dict) and (r.get("srNo") or "").strip() not in ("", "Sr.No.", "Sr.No")
                ]
                if rows:
                    return (rows, update_time)
            except Exception as e:
                logger.warning("nse[%d] %s: %s", attempt, symbol, e)
        return None

    async def _establish(self):
        try:
            r = await self.client.get(NSE_HOMEPAGE, headers={
                "User-Agent": HEADERS["User-Agent"], "Accept": "text/html,*/*",
            }, timeout=15)
            if r.status_code in (200, 403):
                self._session_ok = True
                logger.info("nse session ok (HTTP %d)", r.status_code)
        except Exception as e:
            logger.warning("nse session fail: %s", e)


class BSEClient:
    """BSE IPO subscription via ipo_no, fallback to scrip_cd."""
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch(self, ipo_no: int, scrip_cd: Optional[int] = None) -> Optional[tuple[list[BseCatRow], Optional[str]]]:
        result = await self._fetch(f"{BSE_SUBS_URL}?IPO_NO={ipo_no}")
        if result:
            return result
        if scrip_cd:
            result = await self._fetch(f"{BSE_SUBS_URL}?Scrip_cd={scrip_cd}")
            if result:
                return result
        return None

    async def _fetch(self, url: str) -> Optional[tuple[list[BseCatRow], Optional[str]]]:
        h = {**HEADERS, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"}
        try:
            resp = await self.client.get(url, headers=h, timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                return None
            raw = resp.json()
            table = raw.get("Table") or []
            rows = [
                BseCatRow(**r) for r in table
                if isinstance(r, dict) and r.get("SRNo", "").strip() not in ("", "Sr.No.", "Sr.No")
            ]
            if rows:
                update_time = None
                for r in rows:
                    if r.Maxdt:
                        update_time = r.Maxdt
                        break
                return (rows, update_time)
            return None
        except Exception as e:
            logger.warning("bse: %s", e)
            return None
