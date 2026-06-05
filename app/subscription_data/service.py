"""
Subscription service — NSE first, BSE fallback, consolidated JSON in subscription_latest.

Flow per IPO:
  1. Only if status in ("open", "closed")
  2. Try NSE (symbol from upstox_data)
  3. NSE failed? → Try BSE (ipo_no from bse_data)
  4. Parse into consolidated JSON → store in ipo_master.subscription_latest

Stored shape:
  {
    "qib": {"offered": int, "bid": int, "times": float},
    "hni_above_10l": {...},
    "hni_2l_to_10l": {...},
    "retail": {...},
    "employee": {...},
    "shareholder": {...},
    "total": {...},
    "source": "nse" | "bse",
    "lastFetched": "2026-06-05T19:30:00Z",
    "lastUpdate": "2026-06-05T19:00:00Z",
    "lastUpdateNse": "2026-06-05T19:00:00Z",
    "lastUpdateBse": null
  }
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.db.engine import get_session
from app.db.models import IPOMaster
from app.subscription_data.client import NSEClient, BSEClient
from app.subscription_data.schemas import (
    CategoryData, BidDetailRow, BseCatRow, _int, _float, now_iso,
)

logger = logging.getLogger(__name__)

FETCH_STATUSES = {"open", "closed"}


# ─── Main entry ────────────────────────────────────────────────

async def fetch_and_store(ipo_id: int) -> Optional[dict[str, Any]]:
    """Fetch subscription, parse, store in DB. Returns stored dict or None."""
    ipo = _get_ipo(ipo_id)
    if not ipo or ipo.status not in FETCH_STATUSES:
        return None

    async with httpx.AsyncClient(follow_redirects=True) as c:
        result = None

        # 1. NSE
        symbol = _get_symbol(ipo)
        if symbol:
            nse = NSEClient(c)
            fetched = await nse.fetch(symbol)
            if fetched:
                rows, update_time = fetched
                result = _parse_nse(rows, update_time)
                logger.info("subs: %s ← NSE (%s)", ipo.company_name, symbol)

        # 2. BSE fallback
        if not result:
            bse_data = ipo.bse_data or {}
            ipo_no = bse_data.get("ipo_no")
            scrip_cd = bse_data.get("scrip_cd")
            if ipo_no:
                bse = BSEClient(c)
                fetched = await bse.fetch(ipo_no, scrip_cd)
                if fetched:
                    rows, update_time = fetched
                    result = _parse_bse(rows, update_time)
                    logger.info("subs: %s ← BSE (ipo_no=%s)", ipo.company_name, ipo_no)

    if not result:
        logger.debug("subs: %s — both sources failed", ipo.company_name)
        return None

    result["lastFetched"] = now_iso()
    _write(ipo_id, result)
    return result


# ─── NSE parser ────────────────────────────────────────────────

def _parse_nse(rows: list[BidDetailRow], update_time: Optional[str] = None) -> dict[str, Any]:
    cats = {}
    for r in rows:
        sr = (r.srNo or "").strip()
        cat = (r.category or "").strip()
        cd = CategoryData(offered=_int(r.noOfSharesOffered),
                          bid=_int(r.noOfsharesBid),
                          times=_float(r.noOfTime))
        _map(cats, sr, cat, cd)

    _fill_totals(cats)
    return {
        **cats,
        "source": "nse",
        "lastUpdate": update_time,
        "lastUpdateNse": update_time,
        "lastUpdateBse": None,
    }


def _parse_bse(rows: list[BseCatRow], update_time: Optional[str] = None) -> dict[str, Any]:
    cats = {}
    for r in rows:
        sr = (r.SRNo or "").strip()
        cat = (r.col2 or "").strip()
        cd = CategoryData(offered=_int(r.col3),
                          bid=_int(r.col4),
                          times=_float(r.col5))
        ts = (r.Maxdt or "").strip()
        if ts and not update_time:
            update_time = ts
        _map(cats, sr, cat, cd)

    _fill_totals(cats)
    return {
        **cats,
        "source": "bse",
        "lastUpdate": update_time,
        "lastUpdateNse": None,
        "lastUpdateBse": update_time,
    }


# ─── Category mapper ───────────────────────────────────────────

def _map(cats: dict, sr: str, cat: str, cd: CategoryData) -> None:
    d = cd.model_dump()
    if sr == "1":
        cats["qib"] = d
    elif sr == "2" or sr == "2":
        cats["hni_above_10l"] = d
    elif sr == "2.1":
        cats["hni_above_10l"] = d
    elif sr == "2.2":
        cats["hni_2l_to_10l"] = d
    elif sr == "3":
        cats["retail"] = d
    elif sr == "4":
        cats["employee"] = d
    elif sr == "5":
        cats["shareholder"] = d
    elif sr in ("", None) and "total" in cat.lower():
        cats["total"] = d
    elif not sr and "total" in cat.lower():
        cats["total"] = d

    # Also match BSE's "Total" row (no SRNo)
    if not sr and cat.upper() == "TOTAL":
        cats["total"] = d


def _fill_totals(cats: dict) -> None:
    if "total" not in cats:
        offered = sum(cats.get(k, {}).get("offered", 0)
                      for k in ("qib", "hni_above_10l", "hni_2l_to_10l", "retail"))
        bid = sum(cats.get(k, {}).get("bid", 0)
                  for k in ("qib", "hni_above_10l", "hni_2l_to_10l", "retail"))
        times = round(bid / offered, 4) if offered else 0.0
        cats["total"] = {"offered": offered, "bid": bid, "times": times}


# ─── DB helpers ────────────────────────────────────────────────

def _get_ipo(ipo_id: int) -> Optional[IPOMaster]:
    with get_session() as s:
        return s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()


def _get_symbol(ipo: IPOMaster) -> Optional[str]:
    u = ipo.upstox_data or {}
    return u.get("symbol") if isinstance(u, dict) else None


def _write(ipo_id: int, data: dict) -> None:
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if ipo:
            ipo.subscription_latest = data
            s.commit()


# ─── Batch ─────────────────────────────────────────────────────

async def fetch_all_open(limit: int = 50) -> dict[str, Any]:
    with get_session() as s:
        ipos = (s.query(IPOMaster)
                .filter(IPOMaster.status.in_(FETCH_STATUSES))
                .order_by(IPOMaster.last_updated.desc().nullslast())
                .limit(limit).all())

    results = {"fetched": 0, "skipped": 0, "failed": 0, "details": []}
    for ipo in ipos:
        try:
            d = await fetch_and_store(ipo.id)
            results["details"].append({
                "id": ipo.id, "name": ipo.company_name, "status": "ok" if d else "skipped",
            })
            if d: results["fetched"] += 1
            else: results["skipped"] += 1
        except Exception as e:
            results["failed"] += 1
            results["details"].append({"id": ipo.id, "name": ipo.company_name, "status": "error", "error": str(e)})
    return results
