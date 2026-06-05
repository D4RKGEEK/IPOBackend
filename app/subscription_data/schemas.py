"""
Pydantic models for NSE IPO subscription API responses.

Maps the JSON from:
  - /api/ipo-bid-details?symbol=X&series=EQ
  - /api/ipo-active-category?symbol=X
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class BidDetailRow(BaseModel):
    """One row from the bidDetails/bidData array."""
    srNo: Optional[str] = None
    category: Optional[str] = None
    noOfSharesOffered: Optional[str] = None
    noOfsharesBid: Optional[str] = None
    noOfTime: Optional[str] = None


class NSEBidDetailsResponse(BaseModel):
    """Response from /api/ipo-bid-details?symbol=X&series=EQ"""
    data: list[BidDetailRow] = Field(default_factory=list)


class ActiveCategoryRow(BaseModel):
    """One row from the activeCategory response."""
    category: Optional[str] = None
    noOfSharesOffered: Optional[str] = None
    noOfsharesBid: Optional[str] = None
    noOfTime: Optional[str] = None
    noOfApplications: Optional[str] = None  # Only present in active-category


class NSEActiveCategoryResponse(BaseModel):
    """Response from /api/ipo-active-category?symbol=X"""
    data: list[ActiveCategoryRow] = Field(default_factory=list)
    updateTime: Optional[str] = None


# ─── Parsed / Normalised Models ───────────────────────────────

class CategoryData(BaseModel):
    """Cleaned category-level subscription data."""
    offered: int = 0
    bid: int = 0
    times: float = 0.0
    applications: int = 0  # only from active-category endpoint


class ParsedSubscription(BaseModel):
    """
    Full parsed subscription snapshot stored in DB.
    Fields are populated from whichever source provided them.
    """
    qib: CategoryData = Field(default_factory=CategoryData)
    hni_above_10l: CategoryData = Field(default_factory=CategoryData)   # > ₹10L
    hni_2l_to_10l: CategoryData = Field(default_factory=CategoryData)   # ₹2L–₹10L
    retail: CategoryData = Field(default_factory=CategoryData)
    employee: CategoryData = Field(default_factory=CategoryData)
    total: CategoryData = Field(default_factory=CategoryData)

    # Sub-breakdown enrichments (where available)
    qib_fii: CategoryData = Field(default_factory=CategoryData)
    qib_dii: CategoryData = Field(default_factory=CategoryData)
    qib_mf: CategoryData = Field(default_factory=CategoryData)
    qib_others: CategoryData = Field(default_factory=CategoryData)
    hni_corporates: CategoryData = Field(default_factory=CategoryData)
    hni_individuals: CategoryData = Field(default_factory=CategoryData)
    hni_others: CategoryData = Field(default_factory=CategoryData)

    # Metadata
    source: str = ""            # "bid_details", "active_category"
    update_time: Optional[str] = None
    fetched_at: str = ""


def parse_bid_details(raw: NSEBidDetailsResponse) -> ParsedSubscription:
    """Convert raw bid-details rows into a clean ParsedSubscription."""
    result = ParsedSubscription(source="bid_details")

    for row in raw.data:
        sr = (row.srNo or "").strip()
        cat = (row.category or "").strip()
        offered = _int(row.noOfSharesOffered)
        bid = _int(row.noOfsharesBid)
        times = _float(row.noOfTime)

        cd = CategoryData(offered=offered, bid=bid, times=times)

        if sr == "1":
            result.qib = cd
        elif sr == "1(a)":
            result.qib_fii = cd
        elif sr == "1(b)":
            result.qib_dii = cd
        elif sr == "1(c)":
            result.qib_mf = cd
        elif sr == "1(d)":
            result.qib_others = cd
        elif sr == "2":
            result.hni_above_10l = cd
        elif sr == "2.1":
            result.hni_above_10l = cd
        elif sr == "2.2":
            result.hni_2l_to_10l = cd
        elif sr == "2.1(a)":
            result.hni_corporates = cd
        elif sr == "2.1(b)":
            result.hni_individuals = cd
        elif sr == "2.1(c)":
            result.hni_others = cd
        elif sr == "3":
            result.retail = cd
        elif sr == "4":
            result.employee = cd
        elif sr is None or cat == "Total":
            result.total = cd

    return result


def parse_active_category(raw: NSEActiveCategoryResponse) -> ParsedSubscription:
    """Convert raw active-category rows into a clean ParsedSubscription."""
    result = ParsedSubscription(
        source="active_category",
        update_time=raw.updateTime,
    )

    for row in raw.data:
        cat = (row.category or "").strip().upper()
        offered = _int(row.noOfSharesOffered)
        bid = _int(row.noOfsharesBid)
        times = _float(row.noOfTime)
        apps = _int(row.noOfApplications)

        cd = CategoryData(offered=offered, bid=bid, times=times, applications=apps)

        if "QIB" in cat or "QUALIFIED INSTITUTIONAL" in cat:
            result.qib = cd
        elif "NON INSTITUTIONAL INVESTORS(BID AMOUNT OF MORE THAN TEN LAKH" in cat:
            result.hni_above_10l = cd
        elif "NON INSTITUTIONAL INVESTORS(BID AMOUNT OF MORE THAN TWO LAKH" in cat:
            result.hni_2l_to_10l = cd
        elif "RETAIL" in cat and "INDIVIDUAL" in cat:
            result.retail = cd
        elif "EMPLOYEE" in cat:
            result.employee = cd
        elif cat == "TOTAL":
            result.total = cd

    return result


# ─── Helpers ───────────────────────────────────────────────────

def _int(v: Optional[str]) -> int:
    if not v:
        return 0
    v = v.strip()
    if not v:
        return 0
    # Handle scientific notation like "2.1602008E7"
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return 0


def _float(v: Optional[str]) -> float:
    if not v:
        return 0.0
    v = v.strip()
    if not v:
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0
