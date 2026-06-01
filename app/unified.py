"""
Unified-data builder.

Walks `document_sections.parsed_data` for an IPO and produces a single flat
dict that is the contract shipped to Next.js.

Conflict resolution: when the same field appears in multiple sections OR
across multiple doc_types, the most authoritative source wins.

  doc_type preference:  FP > RHP > DRHP   (final prospectus is the truth)
  within same doc_type: later parse wins

Provenance is recorded per-field so consumers can audit:
    {
        "cin":            {"doc_type": "drhp", "parsed_at": "...", "schema_version": 1},
        "bid_open_date":  {"doc_type": "rhp",  "parsed_at": "...", "schema_version": 1}
    }

After building, runs validation and stamps publish_status + confidence on
ipo_master.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from app.db.models import DocumentSection, IPOMaster
from app.db.engine import get_session
from app.validation import CrossSourceContext, validate
from app.parsers.section_schemas import SCHEMA_VERSION

logger = logging.getLogger(__name__)


_DOC_PREFERENCE = {"fp": 0, "rhp": 1, "drhp": 2}   # lower = more authoritative


def _is_better(new_doc_type: str, new_parsed_at: Optional[str],
               cur_doc_type: str, cur_parsed_at: Optional[str]) -> bool:
    """Should new_* overwrite cur_*?"""
    new_rank = _DOC_PREFERENCE.get(new_doc_type, 99)
    cur_rank = _DOC_PREFERENCE.get(cur_doc_type, 99)
    if new_rank != cur_rank:
        return new_rank < cur_rank
    # Same doc type → later parse wins
    return (new_parsed_at or "") > (cur_parsed_at or "")


def _is_empty(v: Any) -> bool:
    """Treat empties from BOTH LLM providers as 'no value'.

    DeepSeek used to write 0 for missing numeric fields, "" for text, [] for arrays.
    Firecrawl writes "" / [] / sometimes "●" (DRHP placeholder) / sometimes "[●]".
    All of these are "this field wasn't found" and shouldn't overwrite a real value.
    """
    if v is None: return True
    if isinstance(v, str): return v.strip() in ("", "●", "[●]", "-", "—", "N/A", "NA")
    if isinstance(v, (list, dict)): return len(v) == 0
    # NUMERIC ZERO from the legacy DeepSeek parser also means "missing"
    if isinstance(v, (int, float)) and v == 0: return True
    return False


def _compute_application_lot_table(unified: dict, ipo: IPOMaster) -> Optional[list[dict]]:
    """Compute the application lot table from price band + lot size + bidding rules.

    The RHP only specifies thresholds (₹2L retail min, ₹10L S-HNI/B-HNI split)
    but not exact lot counts. These are calculated from the upper price band.

    Returns a list of per-category entries, or None if price band is unavailable.
    """
    import json

    # Get price band from unified_data or from upstox_data on the IPO row
    max_price = None
    upstox = ipo.upstox_data
    if upstox:
        if isinstance(upstox, str):
            try:
                upstox = json.loads(upstox)
            except Exception:
                upstox = {}
        max_price = upstox.get("maximum_price") or upstox.get("max_price")
        min_price = upstox.get("minimum_price") or upstox.get("min_price")
        lot_size = upstox.get("lot_size") or upstox.get("minimum_quantity")
        face_value = upstox.get("face_value")

    # Fall back to unified_data if upstox didn't have them
    if not max_price:
        pb = unified.get("price_band", "")
        if pb and "-" in str(pb):
            parts = str(pb).split("-")
            try:
                min_price = float(parts[0])
                max_price = float(parts[1])
            except (ValueError, IndexError):
                return None

    if not max_price or not lot_size:
        # Try unified_data fields
        max_price = unified.get("maximum_price") or unified.get("max_price")
        lot_size = unified.get("lot_size") or unified.get("market_lot")
        if not max_price or not lot_size:
            return None

    try:
        max_price = float(max_price)
        lot_size = int(float(str(lot_size).replace(",", "")))
    except (ValueError, TypeError):
        return None

    # Per-lot value at upper price band
    lot_value = lot_size * max_price

    # Thresholds from SEBI / standard IPO rules
    RETAIL_MIN_AMOUNT = 200000       # ₹2,00,000
    SHNI_MAX_AMOUNT = 1000000        # ₹10,00,000
    EMPLOYEE_MAX_AMOUNT = 500000     # ₹5,00,000 (standard employee cap)

    # Retail: min 2 lots (to reach ₹2L), max 2 lots (retail capped at ₹2L)
    retail_min_lots = max(2, -(-RETAIL_MIN_AMOUNT // int(lot_value)))  # ceil division
    retail_max_lots = retail_min_lots  # retail is fixed at min application

    # S-HNI: above retail max lots up to ₹10L
    shni_min_lots = retail_max_lots + 1
    shni_max_lots = int(SHNI_MAX_AMOUNT / lot_value)

    # B-HNI: above ₹10L
    bhni_min_lots = shni_max_lots + 1

    # Employee: 2 lots min (matching retail), max up to ₹5L
    emp_min_lots = retail_min_lots
    emp_max_lots = int(EMPLOYEE_MAX_AMOUNT / lot_value)

    def _entry(cat, min_l, max_l):
        min_shares = min_l * lot_size
        max_shares = (max_l * lot_size) if max_l is not None else None
        return {
            "category": cat,
            "min_lots": min_l,
            "max_lots": max_l,
            "min_shares": min_shares,
            "max_shares": max_shares,
            "min_amount": min_shares * max_price,
            "max_amount": (max_shares * max_price) if max_shares is not None else None,
        }

    return [
        _entry("Retail (RII)", retail_min_lots, retail_max_lots),
        _entry("S-HNI (sNII)", shni_min_lots, shni_max_lots),
        _entry("B-HNI (bNII)", bhni_min_lots, None),
        _entry("Employee", emp_min_lots, emp_max_lots),
    ]


def build_unified(ipo_id: int) -> dict[str, Any]:
    """Build (or rebuild) ipo_master.unified_data from current parsed sections.

    Returns the unified dict that was written. If no parsed data exists,
    leaves the row unchanged and returns {}.

    Side effects on ipo_master:
        unified_data, unified_provenance, unified_version, unified_updated_at,
        confidence_score, publish_status, validation_issues
    """
    unified: dict[str, Any] = {}
    provenance: dict[str, dict] = {}


_AMOUNT_FIELDS = {
    "total_revenue", "total_income", "profit_after_tax", "ebitda",
    "total_assets", "net_worth", "reserves_and_surplus", "total_borrowings",
    "authorized_amount", "paid_up_amount", "total_project_cost",
    "minimum_application", "market_cap_pre_ipo",
}


def _lakhs_to_crores(value: Any) -> Any:
    """Convert a lakh-denominated string/dict to crores. Non-amount values pass through."""
    if isinstance(value, dict):
        return {k: _lakhs_to_crores(v) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    m = re.match(r'^[₹Rs.]*\s*([\d,]+\.?\d*)\s*(lakhs?|Lakhs?)', value.strip())
    if not m:
        # Try embedded pattern for fund_usage_breakdown items
        m = re.search(r'[₹Rs.]+\s*([\d,]+\.?\d*)\s*(lakhs?|Lakhs?)', value)
    if not m:
        return value
    try:
        num = float(m.group(1).replace(",", ""))
    except ValueError:
        return value
    is_lakh = m.group(2) and m.group(2).lower().startswith("lakh")
    if not is_lakh:
        return value
    crore_val = num / 100
    if m.group(0).startswith("₹"):
        prefix = "₹"
    else:
        prefix = "₹"
    if crore_val == int(crore_val):
        return value[:m.start()] + f"{prefix}{int(crore_val):,} crore" + value[m.end():]
    return value[:m.start()] + f"{prefix}{crore_val:,.2f} crore" + value[m.end():]


def _normalize_amounts(unified: dict) -> None:
    """Convert lakh-denominated fields to crores in-place."""
    for field in _AMOUNT_FIELDS:
        if field in unified:
            unified[field] = _lakhs_to_crores(unified[field])
    if "fund_usage_breakdown" in unified and isinstance(unified["fund_usage_breakdown"], list):
        unified["fund_usage_breakdown"] = [
            _lakhs_to_crores(item) for item in unified["fund_usage_breakdown"]
        ]


def _restructure_sectioned(unified: dict) -> dict:
    """Restructure flat unified dict into section-wise groups.
    
    All values remain as strings. Missing fields are omitted.
    """
    sectioned: dict[str, Any] = {}

    def _pick(fields: list[str]) -> dict[str, str]:
        return {k: unified[k] for k in fields if k in unified and unified[k] not in (None, "", [], {})}

    sectioned["company"] = _pick([
        "cin", "company_name", "registered_address", "telephone", "email", "website",
        "sector", "listing_exchange", "face_value",
    ])

    sectioned["timeline"] = _pick([
        "bid_open_date", "bid_close_date", "allotment_date",
        "refund_date", "credit_of_shares_date", "listing_date",
    ])

    sectioned["pricing"] = _pick([
        "price_band", "face_value", "lot_size", "market_lot",
        "minimum_application", "retail_min_lots",
    ])

    sectioned["issue_breakdown"] = _pick([
        "total_issue_shares", "total_issue_amount",
        "fresh_issue_shares", "offer_for_sale_shares",
        "pre_issue_shares", "post_issue_shares",
        "qib_shares", "anchor_shares", "qib_ex_anchor_shares",
        "nii_shares", "bhnii_shares", "shnii_shares",
        "retail_shares",
        "market_maker_shares", "employee_shares",
        "net_issue_to_public",
        "qib_percent", "nii_percent", "retail_percent",
    ])

    sectioned["capital_structure"] = _pick([
        "authorized_shares", "authorized_amount",
        "paid_up_shares", "paid_up_amount",
        "pre_issue_shares", "post_issue_shares",
    ])

    sectioned["financials"] = _pick([
        "financial_years", "total_revenue", "total_income",
        "profit_after_tax", "ebitda", "total_assets",
        "net_worth", "reserves_and_surplus", "total_borrowings",
    ])

    sectioned["ratios"] = _pick([
        "eps_basic", "eps_diluted", "nav_per_share",
        "roe_percent", "roce_percent", "debt_equity_ratio",
        "pe_ratio", "price_to_book_value",
        "revenue_growth_percent", "pat_margin_percent",
        "ebitda_margin_percent",
    ])

    sectioned["promoters"] = _pick([
        "promoter_names", "promoter_group_names",
        "promoter_holding_pre_ipo_percent", "promoter_holding_post_ipo_percent",
    ])

    sectioned["objects_of_issue"] = _pick([
        "total_project_cost", "fund_usage_breakdown",
    ])

    sectioned["contacts"] = _pick([
        "brlm_name", "brlm_phone", "brlm_email",
        "registrar_name", "registrar_phone", "registrar_email", "registrar_website",
        "statutory_auditor", "legal_advisor", "cfo_name", "company_secretary_name",
    ])

    if "application_lot_table" in unified:
        sectioned["application_lot_table"] = unified["application_lot_table"]

    # Keep any unrecognized fields in a catch-all
    all_picked = set()
    for v in sectioned.values():
        if isinstance(v, dict):
            all_picked.update(v.keys())
    extras = {k: unified[k] for k in unified if k not in all_picked
              and k != "application_lot_table"}
    if extras:
        sectioned["other"] = extras

    # Convert numeric values to strings for consistency
    def _ensure_str(val):
        if isinstance(val, list):
            return [_ensure_str(v) for v in val]
        if isinstance(val, dict):
            return {k: _ensure_str(v) for k, v in val.items()}
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            # Format: remove .0 for whole numbers
            if val == int(val):
                return str(int(val))
            return str(val)
        return val

    return _ensure_str(sectioned)


def build_unified(ipo_id: int) -> dict[str, Any]:
    """Build (or rebuild) ipo_master.unified_data from current parsed sections.

    Returns the unified dict that was written. If no parsed data exists,
    leaves the row unchanged and returns {}.

    Side effects on ipo_master:
        unified_data, unified_provenance, unified_version, unified_updated_at,
        confidence_score, publish_status, validation_issues
    """
    unified: dict[str, Any] = {}
    provenance: dict[str, dict] = {}

    with get_session() as s:
        ipo = s.query(IPOMaster).filter(IPOMaster.id == ipo_id).first()
        if not ipo:
            raise ValueError(f"IPO {ipo_id} not found")

        sections = s.query(DocumentSection).filter(
            DocumentSection.ipo_master_id == ipo_id,
            DocumentSection.parsed == 1,
            DocumentSection.parsed_data.isnot(None),
        ).all()

        # Snapshot ipo_dict + cross-source context BEFORE we mutate the row
        ipo_dict = ipo.to_dict()
        ipo_dict["bse_data"] = ipo.bse_data
        ipo_dict["nse_data"] = ipo.nse_data
        ctx = CrossSourceContext.from_ipo_row(ipo_dict)

        # Prefer sections written by the new pipeline. Legacy DeepSeek sections
        # stuffed all 60 fields into every section with zeros/empties — those
        # leak garbage into unified_data if we merge them with real Firecrawl data.
        firecrawl_sections = [s for s in sections if (s.parsed_data or {}).get("_provider") == "firecrawl"]
        consider = firecrawl_sections if firecrawl_sections else sections

        for sec in consider:
            data = sec.parsed_data or {}
            doc_type = sec.doc_type
            parsed_at_iso = sec.parsed_at.isoformat() if sec.parsed_at else None
            section_schema_version = data.get("_schema_version", 1)

            for field, value in data.items():
                if field.startswith("_"): continue          # internal keys
                if _is_empty(value): continue               # empty values never overwrite

                existing_prov = provenance.get(field)
                if existing_prov is None or _is_better(
                    doc_type, parsed_at_iso,
                    existing_prov["doc_type"], existing_prov.get("parsed_at"),
                ):
                    unified[field] = value
                    provenance[field] = {
                        "doc_type": doc_type,
                        "parsed_at": parsed_at_iso,
                        "section_name": sec.section_name,
                        "schema_version": section_schema_version,
                    }

        # Run validation
        result = validate(unified, ctx=ctx)

        # Compute application lot table from price band + lot size + bidding rules
        app_table = _compute_application_lot_table(unified, ipo)
        if app_table:
            unified["application_lot_table"] = app_table
            provenance["application_lot_table"] = {
                "doc_type": "computed",
                "parsed_at": datetime.now(timezone.utc).isoformat(),
                "section_name": "__computed__",
                "schema_version": SCHEMA_VERSION,
            }

        # Normalize lakh-denominated values to crores
        # (must run AFTER provenance tracking so unit conversion doesn't cause false diffs)
        _normalize_amounts(unified)

        # Restructure into section-wise groups (all values as strings)
        sectioned = _restructure_sectioned(unified)

        # Bump unified_version only if anything actually changed
        previous = ipo.unified_data or {}
        changed = previous != sectioned

        ipo.unified_data = sectioned
        ipo.unified_provenance = provenance
        if changed:
            ipo.unified_version = (ipo.unified_version or 0) + 1
            ipo.unified_updated_at = datetime.now(timezone.utc)
        ipo.confidence_score = result.confidence_score
        ipo.validation_issues = result.issues or None
        ipo.publish_status = result.publish_status
        s.commit()

    logger.info(
        "[unified] ipo=%d fields=%d confidence=%.2f publish_status=%s issues=%d",
        ipo_id, len(unified), result.confidence_score, result.publish_status, len(result.issues),
    )
    return sectioned


def diff_unified(previous: dict, current: dict) -> dict:
    """Return {added, removed, changed} between two unified dicts.

    Useful for webhook payloads — tells Next.js exactly which fields changed.
    """
    prev_keys = {k for k in previous if not k.startswith("_")}
    cur_keys = {k for k in current if not k.startswith("_")}

    added = sorted(cur_keys - prev_keys)
    removed = sorted(prev_keys - cur_keys)
    changed = sorted(
        k for k in (prev_keys & cur_keys)
        if previous.get(k) != current.get(k)
    )
    return {"added": added, "removed": removed, "changed": changed}
