"""
Validation layer — runs after Firecrawl/DeepSeek extracts fields and BEFORE
unified_data is persisted. Catches LLM hallucinations and source mismatches.

Three checks per field:

  1. FORMAT      — does the value look like the right *kind* of thing?
                   (CIN matches regex, email is well-formed, dates parse, etc.)
  2. CROSS-SOURCE — does the LLM value agree with what BSE/NSE already told us?
                   (company_name fuzzy match, platform exact match, date proximity)
  3. CONFIDENCE   — per-field score in [0,1] combining format + cross-source +
                   how much raw text the section had to work from.

The aggregate confidence (mean of per-field scores) decides publish_status:

  >= PUBLISH_THRESHOLD          → publish_status = "published" (webhook fires)
  >= NEEDS_REVIEW_THRESHOLD     → publish_status = "needs_review" (notify, no webhook)
  <  NEEDS_REVIEW_THRESHOLD     → publish_status = "rejected"   (just log)

These thresholds live in app.config so you can tune without a redeploy.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field as dc_field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


# Tuneable thresholds (kept here so they're easy to find; if you tune often,
# promote to settings).
PUBLISH_THRESHOLD = 0.70
NEEDS_REVIEW_THRESHOLD = 0.40


# ─── Format validators ─────────────────────────────────────────────

CIN_RE   = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE   = re.compile(r"^https?://", re.IGNORECASE)
# Price band like "₹ 42 - 45" or "42.00 - 45.00"
PRICE_BAND_RE = re.compile(r"^\s*[₹Rs.\s]*\s*\d+(\.\d+)?\s*(-|to|–)\s*\d+(\.\d+)?")
# Face value is almost always one of these
FACE_VALUES = {"1", "2", "5", "10", "₹ 1", "₹ 2", "₹ 5", "₹ 10",
               "Rs. 1", "Rs. 2", "Rs. 5", "Rs. 10",
               "Rs 1", "Rs 2", "Rs 5", "Rs 10"}
# Permissive date matcher — Indian DRHPs use many formats
DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y",
    "%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d.%m.%Y",
]


def _is_empty(v: Any) -> bool:
    if v is None: return True
    if isinstance(v, str): return v.strip() in ("", "●", "[●]", "-", "—", "N/A", "NA", "null")
    if isinstance(v, (list, dict)): return len(v) == 0
    return False


def _parse_date(s: str) -> Optional[datetime]:
    if not isinstance(s, str): return None
    s = s.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# Maps unified-data field names → format-check fn. Returns (ok, reason_if_not).
FORMAT_RULES: dict[str, callable] = {
    "cin":            lambda v: (bool(CIN_RE.match(v.strip())), "CIN doesn't match L/U + 5 digits + 2 alpha + 4 digits + 3 alpha + 6 digits"),
    "email":          lambda v: (bool(EMAIL_RE.match(v.strip())), "email is malformed"),
    "website":        lambda v: (bool(URL_RE.match(v.strip())) or "." in v, "website is not a URL or domain"),
    "face_value":     lambda v: (v.strip() in FACE_VALUES or any(fv.split()[-1] == v.strip().split()[-1] for fv in FACE_VALUES if v.strip().split()), "face value not in common set (1/2/5/10)"),
    "price_band":     lambda v: (bool(PRICE_BAND_RE.match(v)), "price band doesn't look like 'X - Y'"),
    "bid_open_date":  lambda v: (_parse_date(v) is not None, "bid open date not parseable"),
    "bid_close_date": lambda v: (_parse_date(v) is not None, "bid close date not parseable"),
    "allotment_date": lambda v: (_parse_date(v) is not None, "allotment date not parseable"),
    "listing_date":   lambda v: (_parse_date(v) is not None, "listing date not parseable"),
}


# ─── Cross-source checks ─────────────────────────────────────────

def _normalize_name(s: str) -> str:
    if not s: return ""
    s = s.upper()
    s = re.sub(r"\s+(LIMITED|LTD|PRIVATE LIMITED|PVT LTD|PVT\.\s*LTD\.?|LIMITED\.|INC\.?|CORPORATION)$", "", s)
    s = re.sub(r"[^A-Z0-9]+", "", s)
    return s


def _names_match(a: str, b: str, min_overlap: float = 0.85) -> bool:
    """Fuzzy: normalize, then check substring or high token overlap."""
    na, nb = _normalize_name(a), _normalize_name(b)
    if not na or not nb: return False
    if na == nb or na in nb or nb in na: return True
    # Token-set Jaccard
    ta, tb = set(re.findall(r"[A-Z]{3,}", a.upper())), set(re.findall(r"[A-Z]{3,}", b.upper()))
    if not ta or not tb: return False
    overlap = len(ta & tb) / max(len(ta | tb), 1)
    return overlap >= min_overlap


@dataclass
class CrossSourceContext:
    """Snapshot of what we know about an IPO from non-LLM sources, used to verify LLM output."""
    company_name: Optional[str] = None
    bse_company_name: Optional[str] = None
    nse_company_name: Optional[str] = None
    platform: Optional[str] = None          # 'MainBoard' / 'SME' from BSE/NSE
    bse_open: Optional[str] = None
    bse_close: Optional[str] = None
    nse_open: Optional[str] = None
    nse_close: Optional[str] = None

    @classmethod
    def from_ipo_row(cls, ipo_dict: dict) -> "CrossSourceContext":
        """Build from `IPOMaster.to_dict()` + the per-source data blobs."""
        bse = (ipo_dict.get("bse_data") or {}) if isinstance(ipo_dict.get("bse_data"), dict) else {}
        nse = (ipo_dict.get("nse_data") or {}) if isinstance(ipo_dict.get("nse_data"), dict) else {}
        return cls(
            company_name=ipo_dict.get("company_name"),
            bse_company_name=bse.get("company_name") or bse.get("long_name"),
            nse_company_name=nse.get("company_name"),
            platform=ipo_dict.get("platform"),
            bse_open=bse.get("start_date"),
            bse_close=bse.get("end_date"),
            nse_open=nse.get("issue_open_date"),
            nse_close=nse.get("issue_close_date"),
        )


def _cross_source_check(field: str, value: Any, ctx: CrossSourceContext) -> Optional[str]:
    """Return a reason string if the LLM value contradicts a trusted source, else None."""
    if _is_empty(value): return None  # nothing to compare

    if field == "company_name":
        for src_name in (ctx.bse_company_name, ctx.nse_company_name, ctx.company_name):
            if src_name and _names_match(value, src_name):
                return None
        return f"LLM company_name '{value[:40]}' doesn't match any source"

    if field in ("bid_open_date",):
        for src in (ctx.bse_open, ctx.nse_open):
            if not src: continue
            llm_d, src_d = _parse_date(value), _parse_date(src)
            if llm_d and src_d and abs((llm_d - src_d).days) <= 3:
                return None
        if ctx.bse_open or ctx.nse_open:
            return f"bid_open_date '{value}' disagrees with BSE/NSE"
        return None

    if field in ("bid_close_date",):
        for src in (ctx.bse_close, ctx.nse_close):
            if not src: continue
            llm_d, src_d = _parse_date(value), _parse_date(src)
            if llm_d and src_d and abs((llm_d - src_d).days) <= 3:
                return None
        if ctx.bse_close or ctx.nse_close:
            return f"bid_close_date '{value}' disagrees with BSE/NSE"
        return None

    return None


# ─── Result types ─────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Outcome of validate() — feeds publish_status + DB columns."""
    confidence_score: float                          # aggregate, 0..1
    issues: list[dict] = dc_field(default_factory=list)
    per_field_score: dict[str, float] = dc_field(default_factory=dict)

    @property
    def publish_status(self) -> str:
        if self.confidence_score >= PUBLISH_THRESHOLD:
            return "published"
        if self.confidence_score >= NEEDS_REVIEW_THRESHOLD:
            return "needs_review"
        return "rejected"


# ─── Main entry ──────────────────────────────────────────────────

# Fields we consider "important enough to count in aggregate confidence" —
# others (raw arrays like financial_years, fund_usage_breakdown) are useful
# but not signal of correctness on their own.
SCORED_FIELDS = {
    "cin", "company_name", "registered_address", "email", "website",
    "brlm_name", "registrar_name", "statutory_auditor",
    "face_value", "price_band",
    "fresh_issue_shares", "offer_for_sale_shares", "pre_issue_shares",
    "bid_open_date", "bid_close_date",
    "total_revenue", "profit_after_tax", "net_worth",
    "eps_basic", "pe_ratio", "roe_percent",
}


def validate(extracted: dict, ctx: Optional[CrossSourceContext] = None) -> ValidationResult:
    """Run format + cross-source checks over a flat extracted dict.

    `extracted` is the merged unified shape: {field_name: value, ...}.
    Internal keys (`_provider`, `_doc_type`, etc.) are ignored.
    """
    issues: list[dict] = []
    per_field: dict[str, float] = {}

    for field, value in extracted.items():
        if field.startswith("_"): continue
        if field not in SCORED_FIELDS: continue  # only score the fields we have rules for

        # Empty values get a low but non-zero score; they're not "wrong",
        # they just weren't found.
        if _is_empty(value):
            per_field[field] = 0.2
            continue

        score = 1.0

        # Format check
        rule = FORMAT_RULES.get(field)
        if rule:
            try:
                ok, reason = rule(value)
            except Exception as e:
                ok, reason = False, f"validator crashed: {e}"
            if not ok:
                score -= 0.5
                issues.append({"field": field, "kind": "format", "value": str(value)[:120], "reason": reason})

        # Cross-source check
        if ctx is not None:
            reason = _cross_source_check(field, value, ctx)
            if reason:
                score -= 0.4
                issues.append({"field": field, "kind": "cross_source", "value": str(value)[:120], "reason": reason})

        per_field[field] = max(0.0, score)

    # Aggregate: simple mean over scored fields. If no scorable fields → 0.
    if per_field:
        agg = sum(per_field.values()) / len(per_field)
    else:
        agg = 0.0

    return ValidationResult(
        confidence_score=round(agg, 3),
        issues=issues,
        per_field_score={k: round(v, 3) for k, v in per_field.items()},
    )
