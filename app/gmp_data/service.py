"""
GMP Service — fetch Chittorgarh/Investorgain GMP data, parse, store in DB.

Flow per run:
  1. Fetch LIST API → all IPOs with current GMP snapshot
  2. For each IPO with gmp > 0, fetch DETAIL API → daily history
  3. Merge + store in ipo_master.gmp_latest (JSON column)

Clean stored shape in ipo_master.gmp_latest:
```json
{
  "ipo_gmp_id": 1927,
  "gmp": 11.75,
  "gmp_percent": 24.44,
  "subject_to_sauda": "2800/39200",
  "price_band_top": 45.0,
  "ipo_size_cr": 138.87,
  "lot_size": 1200,
  "open_date": "2026-06-05",
  "close_date": "2026-06-09",
  "listing_date": "2026-06-12",
  "category": "IPO",
  "updated_on": "6-Jun 13:28",
  "anchor": true,
  "daily_history": [
    {"date": "06-06-2026", "gmp": 11.0, "up_down": "D",
     "est_listing_price": 56.0, "subject_to_sauda": "2800",
     "est_profit": 3663.0, "rating": 4, "active": true},
    ...
  ],
  "last_fetched_at": "2026-06-06T14:00:00Z"
}
```
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.db.engine import get_session
from app.db.models import IPOMaster
from app.gmp_data.client import fetch_list, fetch_detail

logger = logging.getLogger(__name__)

# IPO IDs with positive GMP are the ones we fetch detail for.
# If an IPO has gmp=0, detail is still fetched (shows "GMP not active yet").


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── DB helpers ───────────────────────────────────────────────


def _get_ipo_by_gmp_id(ipo_gmp_id: int) -> Optional[IPOMaster]:
    """Find our IPO via source_ids.chittorgarh_gmp_id or by normalized name match."""
    with get_session() as s:
        # Try source_ids first
        ipo = s.query(IPOMaster).filter(
            IPOMaster.source_ids["chittorgarh_gmp_id"].as_integer() == ipo_gmp_id  # type: ignore
        ).first()
        if ipo:
            return ipo

    # Fallback: try matching by ~id in bse_data or nse_data
    # For now, return None — we'll store by gmp_id primarily
    return None


def _get_or_create_ipo_by_gmp_id(ipo_gmp_id: int, row_data: dict) -> Optional[IPOMaster]:
    """Find IPO by source_ids, or by company name match, or create a minimal placeholder."""
    ipo = _get_ipo_by_gmp_id(ipo_gmp_id)
    if ipo:
        return ipo

    # Try matching by company name first (avoids placeholder creation for existing IPOs)
    company = row_data.get("company_name", "")
    if company:
        import re as _re
        clean = _re.sub(r"[^a-z0-9\s]", "", company.lower()).strip()
        clean = _re.sub(r"\s+", " ", clean)
        from sqlalchemy import text as _text
        import json as _json
        with get_session() as s:
            matched = s.execute(
                _text("SELECT id, normalized_name FROM ipo_master "
                       "WHERE status != 'discovered' AND normalized_name ILIKE :pat LIMIT 1"),
                {"pat": f"%{clean}%"}
            ).fetchone()
            if matched:
                s.execute(
                    _text("UPDATE ipo_master SET source_ids = "
                           "CASE WHEN source_ids IS NULL THEN :new_ids "
                           "ELSE source_ids || :new_ids END "
                           "WHERE id = :mid"),
                    {"new_ids": _json.dumps({"chittorgarh_gmp_id": ipo_gmp_id}),
                     "mid": matched[0]}
                )
                s.commit()
                return s.query(IPOMaster).filter(IPOMaster.id == matched[0]).first()

    # Not found — create a minimal placeholder
    logger.info("gmp: creating placeholder for gmp_id=%s (%s)", ipo_gmp_id, company)
    with get_session() as s:
        import re as _re
        normalized = _re.sub(r"[^a-z0-9\s]", "", company.lower()).strip()
        normalized = _re.sub(r"\s+", "-", normalized)

        ipo = IPOMaster(
            company_name=company,
            normalized_name=normalized,
            status="discovered",
            source_ids={"chittorgarh_gmp_id": ipo_gmp_id},
        )
        s.add(ipo)
        s.commit()
        s.refresh(ipo)
        return ipo


def _store_gmp(ipo_master_id: int, data: dict) -> None:
    """Write clean GMP data to ipo_master.gmp_latest."""
    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_master_id).first()
        if ipo:
            ipo.gmp_latest = data  # type: ignore
            s.commit()


# ─── Main entry ───────────────────────────────────────────────


async def fetch_and_store_all() -> dict[str, Any]:
    """Full GMP fetch cycle:
    1. Fetch list API for current snapshot
    2. For each IPO with gmp > 0, fetch detail API for daily history
    3. Store everything in ipo_master.gmp_latest

    Returns summary dict.
    """
    result = {
        "fetched": 0, "skipped": 0, "failed": 0,
        "details": [], "total_visible": 0,
    }

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Step 1: List API
        rows = await fetch_list(client)
        result["total_visible"] = len(rows)

        # Step 2: Fetch detail in parallel for all IPOs with gmp > 0
        import asyncio
        active_rows = [r for r in rows if r.get("gmp", 0) > 0]

        detail_futures = {}
        for row in active_rows:
            ipo_gmp_id = row["ipo_gmp_id"]
            detail_futures[ipo_gmp_id] = asyncio.ensure_future(
                fetch_detail(client, ipo_gmp_id)
            )

        # Wait for all details
        if detail_futures:
            await asyncio.gather(*detail_futures.values(), return_exceptions=True)

        # Step 3: Process all rows (use cached detail where available)
        for row in rows:
            ipo_gmp_id = row.get("ipo_gmp_id")
            company = row.get("company_name", "?")
            gmp_val = row.get("gmp", 0.0)

            try:
                ipo = _get_or_create_ipo_by_gmp_id(ipo_gmp_id, row)
                if not ipo:
                    result["skipped"] += 1
                    result["details"].append({"id": ipo_gmp_id, "name": company, "status": "no_ipo_record"})
                    continue

                # Get detail result (already fetched in parallel above)
                daily_history = []
                if ipo_gmp_id in detail_futures:
                    detail_future = detail_futures[ipo_gmp_id]
                    if not detail_future.cancelled() and not detail_future.exception():
                        detail = detail_future.result()
                        if detail:
                            daily_history = detail

                # Build clean snapshot
                snapshot = {
                    "ipo_gmp_id": ipo_gmp_id,
                    "gmp": gmp_val,
                    "gmp_percent": row.get("gmp_percent", 0.0),
                    "subject_to_sauda": "",  # populated from detail
                    "price_band_top": row.get("price_band_top", 0.0),
                    "ipo_size_cr": row.get("ipo_size_cr", 0.0),
                    "lot_size": row.get("lot_size", 0),
                    "open_date": row.get("open_date", ""),
                    "close_date": row.get("close_date", ""),
                    "listing_date": row.get("listing_date", ""),
                    "category": row.get("category", ""),
                    "updated_on": row.get("updated_on", ""),
                    "anchor": row.get("anchor", False),
                    "daily_history": daily_history,
                    "last_fetched_at": now_iso(),
                }

                # Copy subject_to_sauda from latest day in history
                if daily_history:
                    latest = daily_history[0]  # API returns newest first
                    snapshot["subject_to_sauda"] = latest.get("subject_to_sauda", "")

                # Store
                _store_gmp(ipo.id, snapshot)
                result["fetched"] += 1
                result["details"].append({
                    "id": ipo.id,
                    "gmp_id": ipo_gmp_id,
                    "name": company,
                    "gmp": gmp_val,
                    "days": len(daily_history),
                    "status": "ok",
                })

            except Exception as e:
                result["failed"] += 1
                result["details"].append({
                    "id": ipo_gmp_id, "name": company,
                    "status": "error", "error": str(e)[:200],
                })
                logger.error("gmp: %s (%s): %s", company, ipo_gmp_id, e)

    logger.info(
        "gmp fetch done: %d fetched, %d skipped, %d failed (total=%d)",
        result["fetched"], result["skipped"], result["failed"], result["total_visible"],
    )
    return result


# ─── Single IPO fetch (for pipeline integration) ──────────────


async def fetch_for_ipo(ipo_master_id: int, ipo_gmp_id: int) -> Optional[dict]:
    """Fetch + store GMP for a single IPO (called from pipeline when needed)."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            # Fetch detail
            detail = await fetch_detail(client, ipo_gmp_id)

            # Build snapshot
            snapshot = {
                "ipo_gmp_id": ipo_gmp_id,
                "gmp": 0.0,
                "gmp_percent": 0.0,
                "subject_to_sauda": "",
                "price_band_top": 0.0,
                "ipo_size_cr": 0.0,
                "lot_size": 0,
                "open_date": "",
                "close_date": "",
                "listing_date": "",
                "category": "",
                "updated_on": "",
                "anchor": False,
                "daily_history": detail or [],
                "last_fetched_at": now_iso(),
            }

            if detail:
                latest = detail[0]
                snapshot["gmp"] = latest.get("gmp", 0.0)
                snapshot["subject_to_sauda"] = latest.get("subject_to_sauda", "")

            _store_gmp(ipo_master_id, snapshot)
            return snapshot

        except Exception as e:
            logger.error("gmp fetch_for_ipo %s/%s: %s", ipo_master_id, ipo_gmp_id, e)
            return None
