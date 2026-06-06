"""
Pydantic models for Chittorgarh/Investorgain GMP API responses.

Two API shapes:
  1. LIST  — /report/data-read/331/...   → All IPOs with current GMP
  2. DETAIL — /ipo/ipo-gmp-read/{id}/... → Daily GMP history per IPO
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# ─── Detail API row (from ipoGmpData array) ───────────────────

class RawGmpDay(BaseModel):
    """One day's GMP entry from the detail API."""
    model_config = {"extra": "ignore"}

    gmp_date: Optional[str] = None
    gmp: Optional[str] = None
    gmp_active_record_flag: Optional[int] = None
    gmp_city: Optional[str] = None
    gmp_rating: Optional[int] = None
    gmp_percent_calc: Optional[str] = None
    ipo_id: Optional[int] = None
    max_ipo_price: Optional[str] = None
    estimated_listing_price: Optional[str] = None
    est_profit: Optional[str] = None
    subject_to_sauda: Optional[str] = None
    sub2: Optional[str] = None
    up_down_status: Optional[str] = None  # U=Up, D=Down, N=Neutral
    last_updated_gmp: Optional[str] = None


class RawGmpDetail(BaseModel):
    """Top-level shape from the detail API."""
    model_config = {"extra": "ignore"}

    msg: Optional[int] = None
    currentTime: Optional[str] = None
    ipoGmpData: Optional[list[RawGmpDay]] = None
    premiumFlag: Optional[bool] = None


# ─── Clean / Output models ────────────────────────────────────

class CleanGmpDay(BaseModel):
    """One day's clean GMP entry."""
    date: str = ""
    gmp: float = 0.0
    up_down: str = ""        # U/D/N
    est_listing_price: float = 0.0
    subject_to_sauda: str = "0"
    est_profit: float = 0.0
    rating: int = 0
    active: bool = False


class CleanGmpSnapshot(BaseModel):
    """Clean GMP data saved to the DB per IPO.

    Stored in ipo_master.gmp_latest (JSON).
    """
    # Current snapshot (from list API)
    gmp: float = 0.0
    gmp_percent: float = 0.0
    subject_to_sauda: str = ""
    price_band_top: float = 0.0
    ipo_size_cr: float = 0.0
    lot_size: int = 0
    open_date: str = ""
    close_date: str = ""
    listing_date: str = ""
    category: str = ""           # IPO / SME
    updated_on: str = ""         # human-readable timestamp from Chittorgarh
    anchor: bool = False

    # Daily history (from detail API)
    daily_history: list[CleanGmpDay] = []

    # Metadata
    last_fetched_at: str = ""    # ISO timestamp of our fetch
