"""
Raw-markdown parser for IPO document sections — refined regex patterns.

Fills gaps that Firecrawl LLM misses — especially ratios/KPIs embedded
in OBJECTS_OF_THE_OFFER text that is too complex for LLM extraction.

Usage:
    from app.raw_md_parser import enrich_from_raw_md
    extra = enrich_from_raw_md(raw_md_text, section_name)
    unified.update(extra)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  OBJECTS_OF_THE_OFFER — EPS + KPI data
# ──────────────────────────────────────────────

def _extract_eps(text: str) -> dict[str, Any]:
    """Extract EPS table.

    Pattern (idx=14658 in raw_md):
        Period         Basic EPS(₹)  Diluted EPS(₹)  Weights
        Mar 31, 2023   0.51          0.47             1
        Mar 31, 2024   1.10          0.99             2
        Mar 31, 2025   1.75          1.75*            3
        Weighted Avg   1.33          1.29
        Nine-month Dec 2025  2.44   2.20              -
    """
    result: dict[str, Any] = {}

    # Find EPS section via "Basic EPS" heading
    idx = text.find("Basic EPS")
    if idx < 0:
        return result

    chunk = text[idx: idx + 1000]

    # Extract per-period values
    # Fiscal year ended March 31, 2023 0.51 0.47 1
    years = re.findall(
        r"Fiscal year ended March 31, 20(\d{2})\s+([\d.]+)\s+([\d.]+)",
        chunk
    )
    for yr_num, basic, diluted in years:
        year_key = f"fy20{yr_num}"
        result[f"eps_basic_{year_key}"] = basic
        result[f"eps_diluted_{year_key}"] = diluted

    # Weighted average EPS
    m = re.search(r"Weighted Average EPS\s+([\d.]+)\s+([\d.]+)", chunk)
    if m:
        result["eps_basic"] = m.group(1)
        result["eps_diluted"] = m.group(2)

    # Nine-month/current period EPS — handle newlines in date
    m = re.search(
        r"(?:Nine-month|Nine month)[\s\S]*?(?:December|Dec)\s+\d+[\s\S]*?(\d{4})\s+([\d.]+)\s+([\d.]+)",
        chunk
    )
    if m:
        result["eps_basic_current"] = m.group(2)
        result["eps_diluted_current"] = m.group(3)

    return result


def _extract_ronw(text: str) -> dict[str, Any]:
    """Extract Return on Net Worth.

    Pattern:
        Return on Net Worth (%):
        Fiscal year ended March 31, 2023   3.55   1
        Fiscal year ended March 31, 2024   6.93   2
        Fiscal year ended March 31, 2025   12.46  3
        Weighted Average                    9.13
        Nine month period ended Dec 31, 2025  12.12
    """
    result: dict[str, Any] = {}

    idx = text.find("Return on Net Worth")
    if idx < 0:
        return result

    chunk = text[idx: idx + 800]

    # Current period (nine month) — handle newlines
    m = re.search(
        r"(?:Nine month|Interim)[\s\S]*?(?:December|Dec\s+\d+)[\s\S]*?(\d+\.\d+)",
        chunk, re.IGNORECASE
    )
    if not m:
        m = re.search(r"(?:Nine month|Interim)[\s\S]*?(\d+\.\d+)", chunk, re.IGNORECASE)
    if m:
        result["roe_percent"] = m.group(1)

    # Weighted average
    m = re.search(r"Weighted Average\s+(\d+\.\d+)", chunk)
    if m:
        result["ronw_weighted_avg"] = m.group(1)

    # Individual years
    years = re.findall(r"Fiscal year ended[^\\n]*?(\d+\.\d+)\s*\d*", chunk)
    if len(years) >= 3:
        result["ronw_fy2023"] = years[0]
        result["ronw_fy2024"] = years[1]
        result["ronw_fy2025"] = years[2]

    return result


def _extract_peer_comparison(text: str) -> dict[str, Any]:
    """Extract peer comparison — Hexagon's EPS, NAV, RoNW from the peer table.

    Pattern (idx=20866):
        Hexagon Nutrition Limited  1  3,249.29  1.75  1.75  N.A  12.46  15.91
    """
    result: dict[str, Any] = {}

    idx = text.find("Hexagon Nutrition")
    if idx < 0:
        return result

    chunk = text[idx: idx + 500]

    # Find the row with values — the peer table header has many columns
    # Extract: face_val revenue basic_eps dil_eps NA/PE ronw nav
    m = re.search(
        r"Hexagon[^\\n]*?"
        r"\d+\s+"                    # face value col
        r"([\d,]+\.?\d*)\s+"         # revenue
        r"([\d.]+)\s+"               # basic EPS
        r"([\d.]+)\s+"               # diluted EPS
        r"(?:N\.?A\.?|[●]|[\d.]+)\s+"  # P/E
        r"([\d.]+)\s+"               # RoNW %
        r"([\d.]+)",                 # NAV
        chunk
    )
    if m:
        # Only set if we don't already have EPS from the EPS table
        result.setdefault("eps_basic", m.group(2))
        result.setdefault("eps_diluted", m.group(3))
        result.setdefault("roe_percent", m.group(4))
        result.setdefault("nav_per_share", m.group(5))

    return result


def _extract_promoter_holding(text: str) -> dict[str, Any]:
    """Extract promoter holding from OBJECTS section shareholding table."""
    result: dict[str, Any] = {}

    # Look for "Promoter and Promoter Group" followed by percentage values
    m = re.search(
        r"Promoter[^\\n]*?(\d+\.?\d*)\s*%[^\\n]*?(\d+\.?\d*)\s*%",
        text
    )
    if m:
        result["promoter_holding_pre_ipo_percent"] = m.group(1)
        result["promoter_holding_post_ipo_percent"] = m.group(2)

    return result


# ──────────────────────────────────────────────
#  RESTATED_FINANCIAL_STATEMENTS  (backup)
# ──────────────────────────────────────────────

def _extract_pl_financials(text: str) -> dict[str, Any]:
    """Extract P&L items from Restated P&L statement.

    Format:
        Revenue from Operations
        28              (note number — skipped)
        2,675.87        (period 1)
        3,249.29        (period 2)
        2,977.31        (period 3)
        2,785.01        (period 4)
    """
    result: dict[str, Any] = {}

    idx = text.find("STATEMENT OF PROFIT AND LOSS")
    if idx < 0:
        idx = text.find("Statement of Profit and Loss")
    if idx < 0:
        return result

    chunk = text[idx: idx + 20000]
    PERIOD_LABELS = ["Dec 31, 2025", "Mar 31, 2025", "Mar 31, 2024", "Mar 31, 2023"]

    LABEL_MAP = {
        "Revenue from Operations": "total_revenue",
        "Total Income": "total_income",
        "Profit Before Tax": "profit_before_tax",
        "Profit before tax": "profit_before_tax",
        "Profit for the year": "profit_after_tax",
        "Profit for the Year": "profit_after_tax",
        "Profit for the period": "profit_after_tax",
        "Profit for the Period": "profit_after_tax",
    }

    for label, field in LABEL_MAP.items():
        pos = chunk.find(label)
        if pos < 0:
            continue
        sub = chunk[pos + len(label): pos + len(label) + 200]
        nums: list[str] = []
        for line in sub.split("\n"):
            stripped = line.strip().replace(",", "")
            m = re.match(r"^(\d+\.?\d*)$", stripped)
            if m:
                val = m.group(1)
                if not nums and val.isdigit() and int(val) <= 99:
                    continue
                nums.append(val)
                if len(nums) == 4:
                    break

        if len(nums) == 4:
            result[field] = dict(zip(PERIOD_LABELS, nums))

    return result


def _extract_bs_financials(text: str) -> dict[str, Any]:
    """Extract Balance Sheet items."""
    result: dict[str, Any] = {}

    idx = text.find("RESTATED CONSOLIDATED STATEMENT OF ASSETS AND LIABILITIES")
    if idx < 0:
        idx = text.find("STATEMENT OF ASSETS AND LIABILITIES")
    if idx < 0:
        idx = text.find("Statement of Assets and Liabilities")
    if idx < 0:
        return result

    chunk = text[idx: idx + 30000]
    PERIOD_LABELS = ["Dec 31, 2025", "Mar 31, 2025", "Mar 31, 2024", "Mar 31, 2023"]

    LABEL_MAP = {
        "Total Equity": "net_worth",
        "Total equity": "net_worth",
        "Total Assets": "total_assets",
        "Reserves and Surplus": "reserves_and_surplus",
        "Borrowings": "total_borrowings",
    }

    for label, field in LABEL_MAP.items():
        pos = chunk.find(label)
        if pos < 0:
            continue
        sub = chunk[pos + len(label): pos + len(label) + 250]
        nums: list[str] = []
        for line in sub.split("\n"):
            stripped = line.strip().replace(",", "")
            m = re.match(r"^(\d+\.?\d*)$", stripped)
            if m:
                val = m.group(1)
                if not nums and val.isdigit() and int(val) <= 99:
                    continue
                nums.append(val)
                if len(nums) == 4:
                    break

        if len(nums) == 4:
            result[field] = dict(zip(PERIOD_LABELS, nums))
        elif nums:
            result[field] = nums[0]

    return result


# ──────────────────────────────────────────────
#  DISPATCHER
# ──────────────────────────────────────────────

_SECTION_PARSERS: dict[str, list[callable]] = {
    "OBJECTS_OF_THE_OFFER": [
        _extract_eps,
        _extract_ronw,
        _extract_peer_comparison,
        _extract_promoter_holding,
    ],
    "RESTATED_FINANCIAL_STATEMENTS": [
        _extract_pl_financials,
        _extract_bs_financials,
    ],
}


def enrich_from_raw_md(text: str, section_name: str) -> dict[str, Any]:
    """Run all applicable raw-md parsers for a section.

    Args:
        text: Full raw_md of the section.
        section_name: Canonical section name.

    Returns:
        Dict of extracted fields (may be empty).
    """
    from app.parsers.section_schemas import SECTION_ALIASES

    normalized = section_name.upper().replace(" ", "_").replace("&", "AND")
    canon = SECTION_ALIASES.get(normalized, normalized)

    parsers = _SECTION_PARSERS.get(canon, [])
    if not parsers:
        return {}

    result: dict[str, Any] = {}
    for parser in parsers:
        try:
            parsed = parser(text)
            result.update(parsed)
        except Exception:
            logger.warning("raw_md_parser %s failed for %s", parser.__name__, canon, exc_info=True)

    return result


def enrich_multisection(section_texts: dict[str, str]) -> dict[str, Any]:
    """Run raw_md parsers across multiple sections.

    Args:
        section_texts: {section_name: raw_md_text, ...}

    Returns:
        Merged dict. First section's value wins for each field.
    """
    result: dict[str, Any] = {}
    for section_name, text in section_texts.items():
        parsed = enrich_from_raw_md(text, section_name)
        for k, v in parsed.items():
            result.setdefault(k, v)
    return result
