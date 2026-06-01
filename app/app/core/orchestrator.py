"""Waterfall orchestrator — runs sources in priority order, merges per-field.

Flow:
  1. Upstox (status, dates, price band — always, fast)
  2. NSE (document URLs, symbol — for missing docs)
  3. BSE (SME docs, scrip codes)
  4. Chittorgarh (RHP URL via their API — last resort fallback)
  5. Merge per-field with highest priority source wins
  6. Store field_provenance so we know where each value came from
"""
import logging
from typing import Any, Optional

from .identity import match_ipo, get_existing_identifiers

logger = logging.getLogger(__name__)

# Per-field source priority (higher = more trusted)
FIELD_PRIORITY: dict[str, dict[str, int]] = {
    "status":       {"upstox": 10, "bse": 7, "nse": 7},
    "open_date":    {"upstox": 10, "bse": 8, "nse": 8},
    "close_date":   {"upstox": 10, "bse": 8, "nse": 8},
    "price_band":   {"upstox": 10, "bse": 8, "chittorgarh": 5},
    "drhp_url":     {"nse": 9, "sebi": 8, "upstox": 6, "chittorgarh": 5},
    "rhp_url":      {"nse": 9, "upstox": 6, "chittorgarh": 5},
    "symbol":       {"upstox": 10, "nse": 8, "chittorgarh": 6},
    "isin":         {"upstox": 10, "nse": 8},
}


def pick_best(field: str, sources: dict[str, Any]) -> tuple[Optional[Any], Optional[str]]:
    """Pick the best value for a field from multiple sources."""
    priorities = FIELD_PRIORITY.get(field, {})
    best_val = None
    best_source = None
    best_priority = -1

    for source_name, value in sources.items():
        if value is None or value == "" or value == "-":
            continue
        pri = priorities.get(source_name, 1)
        if pri > best_priority:
            best_val = value
            best_source = source_name
            best_priority = pri

    return best_val, best_source


async def check_chittorgarh_rhp(ipo_id: int, slug: Optional[str] = None) -> Optional[str]:
    """Check Chittorgarh for RHP URL if no other source has it.

    Uses Chittorgarh's direct PDF URL pattern: chittorgarh.net/reports/ipo_notes/{slug}-rhp.pdf
    No HTML scraping needed — just a HEAD request to verify the URL exists.
    """
    from app.clients.chittorgarh import verify_rhp_url, get_rhp_url

    if not slug:
        from app.db.engine import get_session
        from app.db.models import IPOMaster
        with get_session() as s:
            ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
            if not ipo:
                return None
            # Try to derive slug from company name
            from app.utils import normalize_company_name
            slug = normalize_company_name(ipo.company_name).lower().replace(" ", "-").replace("ipo-", "").replace("-ipo", "")
            # Remove common suffixes
            for suffix in ["-ltd", "-limited", "-pvt-ltd", "-private-limited"]:
                slug = slug.replace(suffix, "")
            slug = slug.strip("-")

    logger.info(f"Chittorgarh: checking RHP for slug={slug}")
    url = await verify_rhp_url(slug)
    if url:
        logger.info(f"Chittorgarh: found RHP at {url}")
        return url
    return None


async def run_waterfall(ipo_id: int) -> dict[str, Any]:
    """Run the full waterfall for a single IPO.

    Loads existing data from DB, tries each source for missing fields,
    checks Chittorgarh as last resort for RHP URLs,
    merges with per-field priority.
    """
    from app.db.engine import get_session
    from app.db.models import IPOMaster
    from app.db.operations import update_ipo_field

    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if not ipo:
            return {}

        # Build sources dict for each field from existing DB data
        sources: dict[str, dict[str, Any]] = {}
        u = ipo.upstox_data or {}
        n = ipo.nse_data or {}
        b = ipo.bse_data or {}

        # Upstox data
        if u:
            sources.setdefault("status", {})["upstox"] = ipo.status
            sources.setdefault("open_date", {})["upstox"] = u.get("bidding_start_date")
            sources.setdefault("close_date", {})["upstox"] = u.get("bidding_end_date")
            sources.setdefault("price_band", {})["upstox"] = (
                f"{u.get('minimum_price')}-{u.get('maximum_price')}"
                if u.get("minimum_price") and u.get("maximum_price") else None
            )
            sources.setdefault("symbol", {})["upstox"] = u.get("symbol")
            sources.setdefault("isin", {})["upstox"] = u.get("isin")
            sources.setdefault("drhp_url", {})["upstox"] = u.get("drhp_url")
            sources.setdefault("rhp_url", {})["upstox"] = u.get("rhp_url")

        # NSE data
        if n:
            sources.setdefault("drhp_url", {})["nse"] = (
                n.get("drhp_attach", {}).get("url") if isinstance(n.get("drhp_attach"), dict) else None
            )
            sources.setdefault("rhp_url", {})["nse"] = (
                n.get("rhp_attach", {}).get("url") if isinstance(n.get("rhp_attach"), dict) else None
            )
            sources.setdefault("symbol", {})["nse"] = n.get("symbol")
            sources.setdefault("isin", {})["nse"] = n.get("isin")
            sources.setdefault("open_date", {})["nse"] = n.get("issue_open_date")
            sources.setdefault("close_date", {})["nse"] = n.get("issue_close_date")

        # BSE data
        if b:
            sources.setdefault("open_date", {})["bse"] = b.get("start_date")
            sources.setdefault("close_date", {})["bse"] = b.get("end_date")
            sources.setdefault("price_band", {})["bse"] = b.get("price_band")

        # ─── Fallback: Chittorgarh for missing RHP ─────────────
        # Only check if IPO has no RHP URL and has a status suggesting docs exist
        current_rhp = pick_best("rhp_url", sources.get("rhp_url", {}))[0]
        if not current_rhp and ipo.status not in ("unknown", "discovered"):
            slug = None
            # Build slug from company name
            from app.utils import normalize_company_name
            raw = (u.get("name", "") or ipo.company_name).lower().replace(" ipo", "")
            slug = raw.lower().replace(" ", "-").replace("&", "and")
            import re
            slug = re.sub(r"[^a-z0-9-]", "", slug).strip("-")

            chitto_url = await check_chittorgarh_rhp(ipo_id, slug=slug)
            if chitto_url:
                sources.setdefault("rhp_url", {})["chittorgarh"] = chitto_url

        # Pick best values and build provenance
        updates = {}
        provenance = {}

        field_mapping = {
            "status": "status", "open_date": "open_date",
            "close_date": "close_date", "price_band": "price_band",
            "drhp_url": "drhp_url", "rhp_url": "rhp_url",
        }

        for field, db_field in field_mapping.items():
            if field in sources:
                val, source_name = pick_best(field, sources[field])
                if val is not None:
                    updates[db_field] = val
                    provenance[db_field] = source_name

        # Status from Upstox takes priority
        if u.get("status"):
            status_map = {"upcoming": "upcoming", "open": "open", "closed": "closed", "listed": "listed"}
            mapped = status_map.get(u["status"])
            if mapped and mapped != ipo.status:
                updates["status"] = mapped

        return {"updates": updates, "provenance": provenance, "sources": sources}


async def waterfall_for_all(year: int = 2026) -> dict[str, Any]:
    """Run waterfall for all IPOs in DB. Returns summary."""
    from app.db.engine import get_session
    from app.db.models import IPOMaster

    import time
    start = time.monotonic()

    with get_session() as s:
        ipos = s.query(IPOMaster).all()

    results = {"updated": 0, "errors": 0, "total": len(ipos)}
    for ipo in ipos:
        try:
            result = await run_waterfall(ipo.id)
            if result.get("updates"):
                from app.db.operations import update_ipo_field
                for field, val in result["updates"].items():
                    update_ipo_field(ipo.id, field, val)
                results["updated"] += 1
        except Exception as e:
            logger.error(f"Waterfall failed for IPO {ipo.id}: {e}")
            results["errors"] += 1

    results["duration_ms"] = int((time.monotonic() - start) * 1000)
    return results
