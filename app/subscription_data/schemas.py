"""
Pydantic models for NSE/BSE IPO subscription API responses.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


class BidDetailRow(BaseModel):
    """One row from NSE /api/ipo-bid-details or /api/ipo-active-category."""
    srNo: Optional[str] = None
    category: Optional[str] = None
    noOfSharesOffered: Optional[str] = None
    noOfsharesBid: Optional[str] = None
    noOfTime: Optional[str] = None


class CategoryData(BaseModel):
    """Cleaned category-level subscription data."""
    offered: int = 0
    bid: int = 0
    times: float = 0.0


class BseCatRow(BaseModel):
    """One row from BSE Pubissues_BBS_CumultveCatdem_ng response."""
    SRNo: Optional[str] = None
    col2: Optional[str] = None    # Category name
    col3: Optional[str] = None    # Shares offered
    col4: Optional[str] = None    # Shares bid
    col5: Optional[str] = None    # Times
    Maxdt: Optional[str] = None   # Update timestamp


# ─── Helpers ───────────────────────────────────────────────────

def _int(v: Optional[str]) -> int:
    if not v: return 0
    v = v.strip()
    if not v: return 0
    try: return int(float(v))
    except: return 0


def _float(v: Optional[str]) -> float:
    if not v: return 0.0
    v = v.strip()
    if not v: return 0.0
    try: return float(v)
    except: return 0.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
