"""
schemas/scoring_response.py
Getypeerde API response — v2.1

ScoringResponse is het gecontracteerde formaat van GET /analyze/{ticker}.
Pydantic valideert bij serialisatie — geen losse dict-chaos in de response.

DataQuality bevat transparante metadata over beschikbaarheid van elke databron.
Engine scoort altijd, maar DataQuality vertelt hoe betrouwbaar de score is.
"""

from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional, Any
from schemas.ticker_snapshot import DataConfidence


class DataQuality(BaseModel):
    """Transparante metadata over databeschikbaarheid per bron."""
    price_available:     bool
    volume_available:    bool
    float_available:     bool
    premarket_available: bool
    news_available:      bool
    social_available:    bool
    sec_check_automated: bool
    confidence:          DataConfidence
    fetch_error:         Optional[str] = None
    retries_used:        int = 0


class MomentumBreakdown(BaseModel):
    """Component breakdown van Momentum Score."""
    total:                  float
    volume_anomaly:         float
    sector_heat_score:      float
    catalyst_quality:       float
    premarket_strength:     float
    relative_strength_score: float
    social_acceleration:    float
    float_score:            float
    social_was_capped:      bool
    social_cap_reason:      str = ""
    breakdown:              dict[str, str]


class SkipBreakdown(BaseModel):
    """Breakdown van Skip Score met redenen."""
    total:            int
    is_hard_blocked:  bool
    reasons:          list[str] = []
    blocking_reasons: list[str] = []


class ScoringResponse(BaseModel):
    """
    Volledig getypeerde API response voor GET /analyze/{ticker}.
    Elk veld is gecontracteerd — breaking changes vereisen versie-bump.
    """
    ticker:           str
    decision:         str
    momentum_score:   float
    skip_score:       int
    phase:            str
    phase_description: str
    market_cap_tier:  str
    sizing_eur:       str
    summary:          str
    analyzed_at:      datetime = Field(
                          default_factory=lambda: datetime.now(timezone.utc)
                      )

    momentum_detail:  MomentumBreakdown
    skip_detail:      SkipBreakdown
    data_quality:     DataQuality


class HealthResponse(BaseModel):
    """Response voor GET /health."""
    status:       str
    version:      str
    engine:       str
    timestamp:    datetime
    data_sources: dict[str, str]
    limitations:  list[str]
