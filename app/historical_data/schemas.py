"""Pydantic models for historical candle data."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class Candle(BaseModel):
    """Simplified candle — one trading day."""
    time: str       # "2026-06-05T00:00:00+05:30"
    open: float
    high: float
    low: float
    close: float
    volume: int


class CandleSummary(BaseModel):
    """Aggregated view of all candles returned."""
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    prev_close: Optional[float] = None
    total_volume: int = 0
    change_pct: Optional[float] = None
    color: int = 0           # 1=bullish, -1=bearish, 0=flat
    num_candles: int = 0
    first_date: Optional[str] = None
    last_date: Optional[str] = None


def _float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ─── Parsers ──────────────────────────────────────────────────────

def parse_upstox_candles(raw: dict) -> tuple[list[Candle], CandleSummary]:
    """Parse Upstox /historical-candle response into simplified candles + summary."""
    data = raw.get("data", {})
    raw_candles = data.get("candles", [])

    candles: list[Candle] = []
    for c in raw_candles:
        if not isinstance(c, (list, tuple)) or len(c) < 6:
            continue
        candles.append(Candle(
            time=_fmt_ts(c[0]),
            open=_float(c[1]),
            high=_float(c[2]),
            low=_float(c[3]),
            close=_float(c[4]),
            volume=_int(c[5]),
        ))

    if not candles:
        return [], CandleSummary()

    # Summary from first (most recent) candle for current snapshot
    latest = candles[-1]  # candles are chronological: oldest first
    first = candles[0]

    prev_close = _float(data.get("prev_close") or data.get("previous_close") or 0)
    change_pct = ((latest.close - prev_close) / prev_close * 100) if prev_close else None
    color = 1 if latest.close > first.open else (-1 if latest.close < first.open else 0)

    return candles, CandleSummary(
        open=first.open,
        high=max(c.high for c in candles),
        low=min(c.low for c in candles),
        close=latest.close,
        prev_close=prev_close or None,
        total_volume=sum(c.volume for c in candles),
        change_pct=round(change_pct, 2) if change_pct is not None else None,
        color=color,
        num_candles=len(candles),
        first_date=candles[0].time[:10],
        last_date=candles[-1].time[:10],
    )


def _fmt_ts(ts: Any) -> str:
    """Normalize timestamp to ISO string."""
    if isinstance(ts, str):
        return ts
    return str(ts)
