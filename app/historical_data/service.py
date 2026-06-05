"""Historical price service — fetch, parse, store per IPO.

Flow per IPO:
  1. Get ISIN from upstox_data (primary) or bse_data (fallback)
  2. Try NSE_EQ first, fallback to BSE_EQ
  3. Parse candles → summary
  4. Upsert into ipo_historical_prices table
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx

from app.config import settings
from app.db.engine import get_session
from app.db.models import IPOMaster, IPOHistoricalPrice
from app.historical_data.client import UpstoxHistoricalClient

logger = logging.getLogger(__name__)


# ─── Main entry ────────────────────────────────────────────────

async def fetch_and_store(ipo_id: int) -> Optional[dict[str, Any]]:
    """Fetch historical candle data, parse, upsert in DB.

    Returns stored dict or None if:
      - IPO not found
      - No ISIN available
      - Both exchanges failed
    """
    ipo = _get_ipo(ipo_id)
    if not ipo:
        return None

    isin, exchanges = _resolve_isin(ipo)
    if not isin:
        logger.debug("hist: %s — no ISIN available", ipo.company_name)
        return None

    async with httpx.AsyncClient(follow_redirects=True) as c:
        client = UpstoxHistoricalClient(c)
        result = None
        used_exchange = None

        for exchange in exchanges:
            fetched = await client.fetch(isin, exchange=exchange)
            if fetched:
                candles, summary = fetched
                result = _build_record(isin, exchange, candles, summary)
                used_exchange = exchange
                logger.info(
                    "hist: %s ← %s (%s) — %d candles, %.2f",
                    ipo.company_name, exchange, isin[:8],
                    summary.num_candles, summary.close or 0,
                )
                break
            logger.debug("hist: %s — %s failed, trying next", ipo.company_name, exchange)

    if not result:
        logger.debug("hist: %s — all exchanges failed", ipo.company_name)
        return None

    _upsert(ipo_id, result)
    return result


async def fetch_all_open(limit: int = 200) -> dict[str, Any]:
    """Fetch historical prices for all IPOs (listed first, then open/closed)."""
    with get_session() as s:
        # Priority: listed (most relevant) → open/closed
        ipos = (
            s.query(IPOMaster)
            .filter(IPOMaster.status.in_({"open", "closed", "listed"}))
            .order_by(
                IPOMaster.status.desc(),  # 'listed' sorts after 'open'/'closed' alphabetically
                IPOMaster.last_updated.desc().nullslast(),
            )
            .limit(limit)
            .all()
        )

    results = {"fetched": 0, "skipped": 0, "failed": 0, "details": []}
    for ipo in ipos:
        try:
            d = await fetch_and_store(ipo.id)
            status = "ok" if d else "skipped"
            results["details"].append({
                "id": ipo.id, "name": ipo.company_name, "status": status,
            })
            if d:
                results["fetched"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            results["failed"] += 1
            results["details"].append({
                "id": ipo.id, "name": ipo.company_name, "status": "error", "error": str(e),
            })

    return results


# ─── Helpers ────────────────────────────────────────────────────────

def _resolve_isin(ipo: IPOMaster) -> tuple[Optional[str], list[str]]:
    """Extract ISIN and determine which exchanges to try.

    Returns (isin, [exchange_try_order]).
    """
    upstox = ipo.upstox_data or {}
    bse = ipo.bse_data or {}

    isin = upstox.get("isin") or bse.get("isin")
    if not isin:
        return None, []

    # Try NSE first (most common for mainboard), BSE as fallback
    exchanges = ["NSE_EQ"]
    listing_exchange = (upstox.get("listing_exchange") or "").upper()
    if listing_exchange == "BSE":
        exchanges = ["BSE_EQ", "NSE_EQ"]
    elif platform := (upstox.get("platform") or "").lower():
        if platform == "bse":
            exchanges = ["BSE_EQ", "NSE_EQ"]

    return isin, exchanges


def _build_record(
    isin: str, exchange: str,
    candles: list, summary: Any,
) -> dict[str, Any]:
    """Build the record dict for DB upsert."""
    today = date.today().isoformat()
    # Convert candles to dicts for JSON storage
    candle_dicts = [
        {"time": c.time, "open": c.open, "high": c.high,
         "low": c.low, "close": c.close, "volume": c.volume}
        for c in candles
    ]

    return {
        "isin": isin,
        "exchange_type": exchange,
        "open": summary.open,
        "high": summary.high,
        "low": summary.low,
        "close": summary.close,
        "volume": summary.total_volume,
        "prev_close": summary.prev_close,
        "change_pct": summary.change_pct,
        "color": summary.color,
        "num_candles": summary.num_candles,
        "candles": candle_dicts,
        "fetch_date": today,
        "fetched_at": datetime.now(timezone.utc),
    }


def _get_ipo(ipo_id: int) -> Optional[IPOMaster]:
    with get_session() as s:
        return s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()


def _upsert(ipo_id: int, data: dict) -> None:
    """Upsert historical price row — one row per IPO."""
    with get_session() as s:
        existing = (
            s.query(IPOHistoricalPrice)
            .filter(IPOHistoricalPrice.ipo_master_id == ipo_id)
            .first()
        )
        now_dt = datetime.now(timezone.utc)

        if existing:
            for k, v in data.items():
                setattr(existing, k, v)
            existing.last_updated = now_dt
        else:
            record = IPOHistoricalPrice(
                ipo_master_id=ipo_id,
                **data,
            )
            s.add(record)
        s.commit()
