"""Identity resolution — match IPOs across different sources.

Strategy priority:
  1. exact normalized_name match (95% coverage)
  2. symbol/ISIN match (for listed IPOs, ~87% coverage)
  3. fuzzy name match (threshold > 85%)
  4. alias lookup (brand→legal name: OYO→Oravel Stays)
  5. date+name combo (secondary verifier)
  6. manual (fallback for <1% edge cases)
"""
import logging
from typing import Any, Optional

from rapidfuzz import fuzz

from app.utils import normalize_company_name

logger = logging.getLogger(__name__)

# Known brand → legal name mappings. Add more as edge cases appear.
KNOWN_ALIASES: dict[str, list[str]] = {
    "oyo": ["oravel stays", "oravel stays limited", "oravel stays pvt ltd", "oyo ipo"],
    "boat": ["imagine marketing", "imagine marketing limited", "boat ipo"],
    "zepto": ["zepto", "zepto ipo"],
    "phonepe": ["phonepe", "phonepe ipo"],
    "flipkart": ["flipkart", "flipkart internet", "flipkart internet private limited", "flipkart ipo"],
    "jio": ["reliance jio", "reliance jio infocomm", "reliance jio infocomm limited", "reliance jio ipo"],
    "nse": ["national stock exchange", "national stock exchange of india", "national stock exchange of india ipo"],
}


def match_ipo(name: str, symbol: Optional[str] = None, isin: Optional[str] = None,
              drhp_date: Optional[str] = None,
              existing_normalized_names: Optional[set[str]] = None,
              existing_symbols: Optional[dict[str, int]] = None,
              existing_isins: Optional[dict[str, int]] = None) -> Optional[dict]:
    """Try to match an IPO name to an existing normalized_name.

    Returns dict with {normalized_name, method, confidence} or None.
    """
    norm = normalize_company_name(name)
    if not norm:
        return None

    # 1. Exact match
    if existing_normalized_names and norm in existing_normalized_names:
        return {"normalized_name": norm, "method": "exact", "confidence": 1.0}

    # 2. Symbol/ISIN match
    if symbol and existing_symbols:
        sym_key = symbol.upper().strip()
        if sym_key in existing_symbols:
            return {"normalized_name": sym_key, "method": "symbol", "confidence": 1.0}

    if isin and existing_isins:
        isin_key = isin.upper().strip()
        if isin_key in existing_isins:
            return {"normalized_name": isin_key, "method": "isin", "confidence": 1.0}

    # 3. Fuzzy match against all existing names
    if existing_normalized_names:
        best_score = 0
        best_match = None
        for existing in existing_normalized_names:
            score = fuzz.ratio(norm, existing)
            if score > best_score:
                best_score = score
                best_match = existing

        if best_score >= 85:
            return {"normalized_name": best_match, "method": "fuzzy", "confidence": best_score / 100.0}

    # 4. Alias lookup
    name_lower = name.lower().strip()
    for canonical, aliases in KNOWN_ALIASES.items():
        for alias in aliases:
            if name_lower == alias or normalize_company_name(name_lower) == normalize_company_name(alias):
                norm_canonical = normalize_company_name(canonical)
                if existing_normalized_names and norm_canonical in existing_normalized_names:
                    return {"normalized_name": norm_canonical, "method": "alias", "confidence": 0.95}
                # If canonical isn't in DB, try the alias
                for alias2 in aliases:
                    alias_norm = normalize_company_name(alias2)
                    if existing_normalized_names and alias_norm in existing_normalized_names:
                        return {"normalized_name": alias_norm, "method": "alias", "confidence": 0.9}
                # Create new entry with canonical name
                return {"normalized_name": norm_canonical, "method": "alias_new", "confidence": 0.85}

    # 5. Date+name combo (if we have a date to match)
    if drhp_date and existing_normalized_names:
        # Too complex for this — rely on fuzzy or alias
        pass

    return None


def get_existing_identifiers() -> tuple[set[str], dict[str, int], dict[str, int]]:
    """Load existing IPO identifiers from DB for matching.

    Returns:
        (normalized_names_set, symbol_to_id_map, isin_to_id_map)
    """
    from app.db.engine import get_session
    from app.db.models import IPOMaster

    with get_session() as s:
        ipos = s.query(IPOMaster).all()

    names: set[str] = set()
    symbols: dict[str, int] = {}
    isins: dict[str, int] = {}

    for ipo in ipos:
        if ipo.normalized_name:
            names.add(ipo.normalized_name)

        u = ipo.upstox_data or {}
        sym = u.get("symbol")
        if sym and sym not in (None, "", "-"):
            symbols[sym.upper().strip()] = ipo.id

        isin = u.get("isin")
        if isin and isin not in (None, "", "-"):
            isins[isin.upper().strip()] = ipo.id

    return names, symbols, isins
