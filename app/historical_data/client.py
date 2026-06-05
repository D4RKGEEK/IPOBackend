"""Upstox Historical Candle API client."""
from __future__ import annotations

import logging
from datetime import date, timezone
from typing import Optional

import httpx

from app.config import settings
from app.historical_data.schemas import parse_upstox_candles, Candle, CandleSummary

logger = logging.getLogger(__name__)

UPSTOX_BASE = "https://api.upstox.com/v2"
HISTORICAL_URL = f"{UPSTOX_BASE}/historical-candle"

HEADERS = {
    "Accept": "application/json",
}


class UpstoxHistoricalClient:
    """Fetch daily candle data for a given ISIN via Upstox."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self._token = settings.upstox_access_token

    async def fetch(
        self,
        isin: str,
        exchange: str = "NSE_EQ",
        interval: str = "day",
    ) -> Optional[tuple[list[Candle], CandleSummary]]:
        """Fetch historical candles for an ISIN.

        Args:
            isin: The 12-character ISIN (e.g. INE0NDA25011)
            exchange: NSE_EQ or BSE_EQ
            interval: day | week | month | 1minute etc.

        Returns:
            (candles, summary) or None on failure.
        """
        if not self._token:
            logger.warning("UPSTOX_ACCESS_TOKEN not set — skipping historical fetch")
            return None

        today = date.today().isoformat()
        instrument_key = f"{exchange}|{isin}"
        # Upstox: /historical-candle/{instrument_key}/{interval}/{to_date}/{from_date}
        # We fetch from 2024-01-01 to today to build historical chart
        url = f"{HISTORICAL_URL}/{instrument_key}/{interval}/{today}/2024-01-01"

        headers = {
            **HEADERS,
            "Authorization": f"Bearer {self._token}",
        }

        try:
            resp = await self.client.get(url, headers=headers, timeout=20)
            if resp.status_code != 200:
                logger.warning("upstox historical %s: HTTP %d", isin[:8], resp.status_code)
                return None

            raw = resp.json()
            if raw.get("status") != "success":
                logger.warning("upstox historical %s: status=%s", isin[:8], raw.get("status"))
                return None

            candles, summary = parse_upstox_candles(raw)
            if not candles:
                logger.debug("upstox historical %s: 0 candles returned", isin[:8])
                return None

            return candles, summary

        except Exception as e:
            logger.warning("upstox historical %s: %s", isin[:8], e)
            return None
