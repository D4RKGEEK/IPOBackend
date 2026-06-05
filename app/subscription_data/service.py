"""
Subscription service — fetches and stores IPO subscription data.

Logic:
  1. Only fetches if IPO status is "open" (or optionally "closed" for final data).
  2. Calls two NSE APIs: bid-details and active-category.
  3. If APIs fail, retries with NSE session/cookies.
  4. Stores raw + parsed data in `ipo_subscription_snapshots` table.
  5. Updates `ipo_master.subscription_latest` with latest parsed data.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.db.engine import get_session
from app.db.models import IPOMaster, IPOSubscriptionSnapshot
from app.subscription_data.client import NSESubscriptionClient
from app.subscription_data.schemas import (
    NSEBidDetailsResponse,
    NSEActiveCategoryResponse,
    ParsedSubscription,
    parse_bid_details,
    parse_active_category,
)

logger = logging.getLogger(__name__)

# ─── Eligible statuses ─────────────────────────────────────────

FETCH_STATUSES = {"open", "closed"}  # only fetch for these

# ─── Public API ────────────────────────────────────────────────

async def fetch_and_store(ipo_id: int) -> Optional[dict[str, Any]]:
    """
    Fetch subscription data for one IPO and store it.
    Only runs if IPO status is "open" or "closed".

    Returns the parsed data dict, or None if skipped/failed.
    """
    # 1. Check eligibility
    ipo = _get_ipo(ipo_id)
    if not ipo:
        logger.warning("subscription: IPO %d not found", ipo_id)
        return None
    if ipo.status not in FETCH_STATUSES:
        logger.debug(
            "subscription: %s status=%s — not eligible (need open/closed)",
            ipo.company_name, ipo.status,
        )
        return None

    # 2. Resolve symbol from upstox data
    symbol = _get_symbol(ipo)
    if not symbol:
        logger.warning("subscription: %s has no symbol — skipping", ipo.company_name)
        return None

    logger.info("subscription: fetching %s (symbol=%s, status=%s)",
                ipo.company_name, symbol, ipo.status)

    # 3. Fetch from NSE APIs
    async with httpx.AsyncClient(follow_redirects=True) as http_client:
        nse = NSESubscriptionClient(http_client)

        bid_details = await nse.fetch_bid_details(symbol)
        active_category = await nse.fetch_active_category(symbol)

    # 4. Parse into clean structure
    best: Optional[ParsedSubscription] = None
    raw_saved: list[dict] = []

    if bid_details:
        parsed = parse_bid_details(bid_details)
        best = parsed
        raw_saved.append({"source": "bid_details", "raw": bid_details.model_dump(mode="json")})
        # Save snapshot to DB
        _save_snapshot(ipo_id, "bid_details",
                       raw_data=bid_details.model_dump(mode="json"),
                       parsed_data=parsed.model_dump(mode="json"),
                       update_time=parsed.update_time)
        logger.info("subscription: %s bid-details saved", ipo.company_name)

    if active_category:
        parsed = parse_active_category(active_category)
        # active-category is more authoritative (has updateTime + applications)
        best = parsed
        raw_saved.append({"source": "active_category", "raw": active_category.model_dump(mode="json")})
        _save_snapshot(ipo_id, "active_category",
                       raw_data=active_category.model_dump(mode="json"),
                       parsed_data=parsed.model_dump(mode="json"),
                       update_time=parsed.update_time)
        logger.info("subscription: %s active-category saved", ipo.company_name)

    if not best:
        logger.warning("subscription: %s — both APIs failed", ipo.company_name)
        return None

    # 5. Update ipo_master.subscription_latest with best parsed data
    best_dict = best.model_dump(mode="json")
    _update_master(ipo_id, best_dict)

    return best_dict


# ─── Internal helpers ──────────────────────────────────────────

def _get_ipo(ipo_id: int) -> Optional[IPOMaster]:
    """Fetch IPO row by ID."""
    with get_session() as s:
        return s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()


def _get_symbol(ipo: IPOMaster) -> Optional[str]:
    """Extract NSE symbol from upstox_data."""
    if not ipo.upstox_data:
        return None
    return ipo.upstox_data.get("symbol") or None


def _save_snapshot(
    ipo_id: int,
    source: str,
    raw_data: dict,
    parsed_data: dict,
    update_time: Optional[str] = None,
) -> None:
    """Insert a row into ipo_subscription_snapshots."""
    with get_session() as s:
        snap = IPOSubscriptionSnapshot(
            ipo_master_id=ipo_id,
            source=source,
            raw_data=raw_data,
            parsed_data=parsed_data,
            update_time=update_time,
        )
        s.add(snap)
        s.commit()


def _update_master(ipo_id: int, parsed_data: dict) -> None:
    """Update ipo_master.subscription_latest with the latest parsed data."""
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if ipo:
            ipo.subscription_latest = parsed_data
            s.commit()


# ─── Batch helper ──────────────────────────────────────────────

async def fetch_all_open(limit: int = 50) -> dict[str, Any]:
    """
    Fetch subscription data for all eligible IPOs (status=open).

    Returns a summary dict.
    """
    with get_session() as s:
        ipos = (
            s.query(IPOMaster)
            .filter(IPOMaster.status.in_(FETCH_STATUSES))
            .order_by(IPOMaster.last_updated.desc().nullslast())
            .limit(limit)
            .all()
        )

    results = {"fetched": 0, "skipped": 0, "failed": 0, "details": []}
    for ipo in ipos:
        try:
            data = await fetch_and_store(ipo.id)
            if data:
                results["fetched"] += 1
                results["details"].append({"id": ipo.id, "name": ipo.company_name, "status": "ok"})
            else:
                results["skipped"] += 1
                results["details"].append({"id": ipo.id, "name": ipo.company_name, "status": "skipped"})
        except Exception as e:
            logger.error("subscription: %s failed: %s", ipo.company_name, e)
            results["failed"] += 1
            results["details"].append({"id": ipo.id, "name": ipo.company_name, "status": "error", "error": str(e)})

    return results
