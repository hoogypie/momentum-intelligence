"""
schemas/ticker_snapshot.py
Typed snapshot van marktdata voor één ticker — v2.1

DataConfidence geeft aan hoe betrouwbaar de data is:
    LIVE      Alle kernvelden aanwezig, geen fouten
    DELAYED   Data aanwezig maar mogelijk verouderd (actief zodra cache aan is)
    PARTIAL   Prijs aanwezig, maar optionele velden ontbreken (float, market_cap)
    MISSING   Prijs nul of ophaalfout — score is gebaseerd op defaults

Rationale:
    De engine scoort altijd — zelfs bij PARTIAL of MISSING data.
    De confidence label informeert de gebruiker hoe veel gewicht hij
    aan de score moet geven. Een MISSING score van BUY_STRONG is
    fundamenteel minder betrouwbaar dan een LIVE BUY_STRONG.
"""

from pydantic import BaseModel, Field, field_validator
from datetime import datetime, timezone
from typing import Optional
from enum import Enum


class DataConfidence(str, Enum):
    LIVE    = "LIVE"     # Alle kernvelden aanwezig, geen fouten
    DELAYED = "DELAYED"  # Prijs aanwezig, mogelijk verouderd (v2.2: cache)
    PARTIAL = "PARTIAL"  # Prijs aanwezig, ≥1 optioneel veld ontbreekt
    MISSING = "MISSING"  # Prijs nul of ophaalfout


def determine_confidence(
    price: float,
    volume_today: int,
    market_cap: Optional[float],
    float_shares: Optional[int],
    premarket_available: bool,
    error: Optional[str],
) -> DataConfidence:
    """
    Bepaalt DataConfidence op basis van aanwezigheid van velden.

    MISSING  → prijs ontbreekt of fout
    PARTIAL  → prijs aanwezig maar optionele velden ontbreken
    LIVE     → alle kernvelden aanwezig

    DELAYED is gereserveerd voor v2.2 (cache timestamp vergelijking).
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


class TickerSnapshot(BaseModel):
    """
    Getypeerd snapshot van marktdata — contractlaag tussen data en engine.
    Alle velden zijn nullable waar Yahoo Finance geen data levert.
    """
    ticker:              str
    timestamp:           datetime = Field(
                             default_factory=lambda: datetime.now(timezone.utc)
                         )
    confidence:          DataConfidence

    # Prijs
    price:               float
    prev_close:          float
    day_change_pct:      float

    # Pre-market (alleen beschikbaar buiten handelsuren)
    premarket_price:     Optional[float] = None
    premarket_pct:       float = 0.0
    premarket_available: bool  = False

    # Volume
    volume_today:        int
    avg_volume_20d:      int

    # Bedrijfsdata
    market_cap:          Optional[float] = None
    float_shares:        Optional[int]   = None

    # Foutafhandeling
    error:               Optional[str]   = None
    retries_used:        int             = 0

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
        """Ongeldige of negatieve float waarden worden None."""
        if v is not None and v <= 0:
            return None
        return v

    @field_validator("market_cap")
    @classmethod
    def market_cap_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v <= 0:
            return None
        return v
