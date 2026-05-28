"""
schemas/ticker_snapshot.py
Typed market data snapshot — v2.2

Wijzigingen t.o.v. v2.1:
    - DataConfidence krijgt STALE (was gereserveerd voor v2.2)
    - FreshnessInfo model toegevoegd
    - TickerSnapshot krijgt freshness velden (optional, backward compat)

DataConfidence:
    LIVE    → velden aanwezig + data < 5 min oud
    DELAYED → data 5-60 min oud (uit cache)
    STALE   → data 60 min-2 uur oud (met waarschuwing serveren)
    PARTIAL → prijs aanwezig, ≥2 optionele velden ontbreken
    MISSING → prijs nul of ophaalfout

Twee assen bepalen de confidence:
    1. Veld-aanwezigheid: determine_confidence() → LIVE/PARTIAL/MISSING
    2. Leeftijd (cache):  age_to_confidence()    → LIVE/DELAYED/STALE
    Eindresultaat = slechtste van de twee.
"""

from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
from typing import Optional
from enum import Enum


class DataConfidence(str, Enum):
    LIVE    = "LIVE"     # < 5 min oud, alle kernvelden aanwezig
    DELAYED = "DELAYED"  # 5-60 min oud, uit cache
    STALE   = "STALE"    # 1-2 uur oud, met waarschuwing
    PARTIAL = "PARTIAL"  # Prijs aanwezig, optionele velden ontbreken
    MISSING = "MISSING"  # Prijs nul of fout

# Slechtste-wins ordening (hogere index = slechter)
_CONFIDENCE_RANK = {
    DataConfidence.LIVE:    0,
    DataConfidence.DELAYED: 1,
    DataConfidence.STALE:   2,
    DataConfidence.PARTIAL: 3,
    DataConfidence.MISSING: 4,
}


def worst_confidence(*labels: DataConfidence) -> DataConfidence:
    """Geeft de slechtste DataConfidence van meerdere labels terug."""
    return max(labels, key=lambda c: _CONFIDENCE_RANK[c])


def determine_confidence(
    price: float,
    volume_today: int,
    market_cap: Optional[float],
    float_shares: Optional[int],
    premarket_available: bool,
    error: Optional[str],
) -> DataConfidence:
    """
    Veld-gebaseerde confidence (ongeacht leeftijd).
    Leeftijdscomponent wordt toegevoegd door age_to_confidence().
    """
    if error or price <= 0:
        return DataConfidence.MISSING

    optional_missing = sum([
        market_cap is None,
        float_shares is None,
        not premarket_available,
    ])

    if optional_missing >= 2:
        return DataConfidence.PARTIAL

    return DataConfidence.LIVE


def age_to_confidence(age_seconds: float) -> DataConfidence:
    """
    Leeftijdsgebaseerde confidence voor cache entries.
    Combineert met determine_confidence() via worst_confidence().
    """
    if age_seconds <= 300:    return DataConfidence.LIVE
    if age_seconds <= 3600:   return DataConfidence.DELAYED
    return DataConfidence.STALE


# ── FRESHNESS INFO ────────────────────────────────────────────────────────────

class FreshnessInfo(BaseModel):
    """Metadata over data-versheid. Meegestuurd in alle responses."""
    fetched_at:          datetime
    data_age_seconds:    float
    confidence:          DataConfidence
    cache_hit:           bool
    cache_ttl_remaining: Optional[float] = None
    is_market_open:      Optional[bool]  = None


# ── TICKER SNAPSHOT ───────────────────────────────────────────────────────────

class TickerSnapshot(BaseModel):
    ticker:              str
    timestamp:           datetime = Field(
                             default_factory=lambda: datetime.now(timezone.utc)
                         )
    confidence:          DataConfidence

    price:               float
    prev_close:          float
    day_change_pct:      float

    premarket_price:     Optional[float] = None
    premarket_pct:       float = 0.0
    premarket_available: bool  = False

    volume_today:        int
    avg_volume_20d:      int

    market_cap:          Optional[float] = None
    float_shares:        Optional[int]   = None

    error:               Optional[str]   = None
    retries_used:        int             = 0

    # Freshness metadata — optional voor backward compat
    cache_hit:           bool  = False
    data_age_seconds:    float = 0.0

    @field_validator("ticker")
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("price", "prev_close")
    @classmethod
    def price_non_negative(cls, v: float) -> float:
        return max(v, 0.0)

    @field_validator("volume_today", "avg_volume_20d")
    @classmethod
    def volume_non_negative(cls, v: int) -> int:
        return max(v, 0)

    @field_validator("float_shares")
    @classmethod
    def float_shares_valid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            return None
        return v

    @field_validator("market_cap")
    @classmethod
    def market_cap_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            return None
        return v
