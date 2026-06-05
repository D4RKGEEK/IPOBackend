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


async def check_chittorgarh_docs(ipo_id: int, slug: Optional[str] = None) -> dict[str, Optional[str]]:
    """Check Chittorgarh for any document PDF URL (DRHP, RHP, Prospectus).

    Tries all known suffixes (-rhp, -drhp, -prospectus) via HEAD requests.
    Returns dict with keys 'drhp', 'rhp', 'prospectus' — whichever exists.
    """
    from app.clients.chittorgarh import find_document_url, PDF_SUFFIXES

    if not slug:
        from app.db.engine import get_session
        from app.db.models import IPOMaster
        with get_session() as s:
            ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
            if not ipo:
                return {}
            from app.utils import normalize_company_name
            slug = normalize_company_name(ipo.company_name).lower().replace(" ", "-").replace("ipo-", "").replace("-ipo", "")
            for suffix in ["-ltd", "-limited", "-pvt-ltd", "-private-limited"]:
                slug = slug.replace(suffix, "")
            slug = slug.strip("-")

    logger.info(f"Chittorgarh: checking docs for slug={slug}")
    result: dict[str, Optional[str]] = {}
    for suffix in PDF_SUFFIXES:
        url = await find_document_url(slug, prefer=[suffix])
        if url:
            doc_type = suffix.lstrip("-")  # "rhp", "drhp", "prospectus"
            result[doc_type] = url
            logger.info(f"Chittorgarh: found {doc_type} at {url}")
    return result


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

        # ─── Fallback: Chittorgarh for missing DRHP/RHP ──────────
        # Only check if IPO status suggests docs might exist
        current_drhp = pick_best("drhp_url", sources.get("drhp_url", {}))[0]
        current_rhp = pick_best("rhp_url", sources.get("rhp_url", {}))[0]
        if (not current_drhp or not current_rhp) and ipo.status not in ("unknown", "discovered"):
            slug = None
            # Build slug from company name
            from app.utils import normalize_company_name
            raw = (u.get("name", "") or ipo.company_name).lower().replace(" ipo", "")
            slug = raw.lower().replace(" ", "-").replace("&", "and")
            import re
            slug = re.sub(r"[^a-z0-9-]", "", slug).strip("-")

            chitto_docs = await check_chittorgarh_docs(ipo_id, slug=slug)
            if not current_drhp and chitto_docs.get("drhp"):
                sources.setdefault("drhp_url", {})["chittorgarh"] = chitto_docs["drhp"]
            if not current_rhp and chitto_docs.get("rhp"):
                sources.setdefault("rhp_url", {})["chittorgarh"] = chitto_docs["rhp"]
            # If neither drhp nor rhp found, try prospectus as RHP fallback
            if not current_rhp and not chitto_docs.get("rhp") and chitto_docs.get("prospectus"):
                sources.setdefault("rhp_url", {})["chittorgarh"] = chitto_docs["prospectus"]
            if not current_drhp and not chitto_docs.get("drhp") and chitto_docs.get("prospectus"):
                sources.setdefault("drhp_url", {})["chittorgarh"] = chitto_docs["prospectus"]

        # Pick best values and build provenance
        updates = {}
        provenance = {}
        doc_changed = False  # ← Naya: track if any document URL changed

        field_mapping = {
            "status": "status", "open_date": "open_date",
            "close_date": "close_date", "price_band": "price_band",
            "drhp_url": "drhp_url", "rhp_url": "rhp_url",
        }

        for field, db_field in field_mapping.items():
            if field in sources:
                val, source_name = pick_best(field, sources[field])
                if val is not None:
                    old_val = getattr(ipo, db_field, None)
                    updates[db_field] = val
                    provenance[db_field] = source_name
                    # Detect document URL change
                    if db_field in ("rhp_url", "drhp_url") and val != old_val:
                        doc_changed = True

        # ─── Naya: URL change → reset processing flags ──────────
        if doc_changed:
            updates["publish_status"] = "pending"
            updates["confidence_score"] = 0.0
            if "rhp_url" in updates and updates["rhp_url"]:
                updates["rhp_processed"] = 0
            if "drhp_url" in updates and updates["drhp_url"]:
                updates["drhp_processed"] = 0

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
