"""
NSE API client for IPO subscription data.

Primary: direct GET to NSE IPO endpoints (works without cookies for most symbols).
Fallback: visit nseindia.com → capture cookies → retry with session cookies.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from app.subscription_data.schemas import (
    NSEBidDetailsResponse,
    NSEActiveCategoryResponse,
)

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────

NSE_BASE = "https://www.nseindia.com"
BID_DETAILS_URL = f"{NSE_BASE}/api/ipo-bid-details"
ACTIVE_CATEGORY_URL = f"{NSE_BASE}/api/ipo-active-category"
NSE_HOMEPAGE = NSE_BASE

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": f"{NSE_BASE}/market-data/ipo-subscription-status",
}


# ─── Client ────────────────────────────────────────────────────

class NSESubscriptionClient:
    """
    Fetches IPO subscription data from NSE APIs.

    Two-phase strategy:
      1. Direct GET with standard headers (works in most cases).
      2. If that fails, establish a session by visiting nseindia.com,
         capture cookies, and retry.
    """

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._session_established = False

    async def fetch_bid_details(self, symbol: str) -> Optional[NSEBidDetailsResponse]:
        """Fetch /api/ipo-bid-details for a symbol. Returns None on failure."""
        for attempt, with_session in enumerate([False, True], 1):
            if with_session and not self._session_established:
                await self._establish_session()

            url = f"{BID_DETAILS_URL}?symbol={symbol}&series=EQ"
            try:
                resp = await self.client.get(url, headers=self._headers(with_session), timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    return NSEBidDetailsResponse(**data)
                logger.warning(
                    "NSE bid-details attempt %d: HTTP %d for %s",
                    attempt, resp.status_code, symbol,
                )
            except Exception as e:
                logger.warning(
                    "NSE bid-details attempt %d failed for %s: %s",
                    attempt, symbol, e,
                )

            if attempt == 1 and not with_session:
                # First attempt failed — fall through to session retry
                continue
            break  # Session attempt also failed — give up

        return None

    async def fetch_active_category(self, symbol: str) -> Optional[NSEActiveCategoryResponse]:
        """Fetch /api/ipo-active-category for a symbol. Returns None on failure."""
        for attempt, with_session in enumerate([False, True], 1):
            if with_session and not self._session_established:
                await self._establish_session()

            url = f"{ACTIVE_CATEGORY_URL}?symbol={symbol}"
            try:
                resp = await self.client.get(url, headers=self._headers(with_session), timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    return NSEActiveCategoryResponse(**data)
                logger.warning(
                    "NSE active-category attempt %d: HTTP %d for %s",
                    attempt, resp.status_code, symbol,
                )
            except Exception as e:
                logger.warning(
                    "NSE active-category attempt %d failed for %s: %s",
                    attempt, symbol, e,
                )

            if attempt == 1 and not with_session:
                continue
            break

        return None

    # ── Session management ──────────────────────────────────

    async def _establish_session(self) -> None:
        """Visit nseindia.com to get cookies, then store them in the client."""
        try:
            resp = await self.client.get(
                NSE_HOMEPAGE,
                headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml,*/*",
                },
                timeout=15,
            )
            if resp.status_code in (200, 403):
                # 403 is fine — Akamai blocks the page but cookies may still be set
                logger.info("NSE session established (HTTP %d)", resp.status_code)
                self._session_established = True
            else:
                logger.warning("NSE session establishment returned HTTP %d", resp.status_code)
        except Exception as e:
            logger.warning("NSE session establishment failed: %s", e)

    def _headers(self, with_session: bool = False) -> dict[str, str]:
        """Return headers — optionally include Referer from within nseindia.com."""
        h = dict(HEADERS)
        if with_session:
            h["Referer"] = f"{NSE_BASE}/market-data/ipo-subscription-status"
        return h
