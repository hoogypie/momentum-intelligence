"""
schemas/scoring_response.py
Typed API responses — v2.2

Wijzigingen t.o.v. v2.1:
    - DataQuality krijgt freshness velden (cache_hit, data_age_seconds)
    - BatchScoringResponse nieuw (GET /analyze?tickers=...)
    - SectorSnapshotResponse nieuw (GET /sector/{name})
    - HealthResponse krijgt cache_stats
"""

from pydantic import BaseModel, Field
from datetime import datetime, timezone
from typing import Optional
from schemas.ticker_snapshot import DataConfidence


class DataQuality(BaseModel):
    price_available:     bool
    volume_available:    bool
    float_available:     bool
    premarket_available: bool
    news_available:      bool
    social_available:    bool
    sec_check_automated: bool
    confidence:          DataConfidence
    fetch_error:         Optional[str]   = None
    retries_used:        int             = 0
    # Freshness (v2.2)
    cache_hit:           bool            = False
    data_age_seconds:    float           = 0.0
    cache_ttl_remaining: Optional[float] = None


class MomentumBreakdown(BaseModel):
    total:                   float
    volume_anomaly:          float
    sector_heat_score:       float
    catalyst_quality:        float
    premarket_strength:      float
    relative_strength_score: float
    social_acceleration:     float
    float_score:             float
    social_was_capped:       bool
    social_cap_reason:       str = ""
    breakdown:               dict[str, str]


class SkipBreakdown(BaseModel):
    total:            int
    is_hard_blocked:  bool
    reasons:          list[str] = []
    blocking_reasons: list[str] = []


class ScoringResponse(BaseModel):
    ticker:            str
    decision:          str
    momentum_score:    float
    skip_score:        int
    phase:             str
    phase_description: str
    market_cap_tier:   str
    sizing_eur:        str
    summary:           str
    analyzed_at:       datetime = Field(
                           default_factory=lambda: datetime.now(timezone.utc)
                       )
    momentum_detail:   MomentumBreakdown
    skip_detail:       SkipBreakdown
    data_quality:      DataQuality


# ── BATCH ─────────────────────────────────────────────────────────────────────

class BatchScoringResponse(BaseModel):
    """Response voor GET /analyze?tickers=A,B,C"""
    tickers_requested: int
    tickers_scored:    int
    tickers_failed:    int
    results:           list[ScoringResponse]
    errors:            dict[str, str]  # ticker → error message
    analyzed_at:       datetime = Field(
                           default_factory=lambda: datetime.now(timezone.utc)
                       )


# ── SECTOR SNAPSHOT ───────────────────────────────────────────────────────────

class LeaderScore(BaseModel):
    """Mini score voor één leader in sector snapshot."""
    ticker:         str
    decision:       str
    momentum_score: float
    skip_score:     int
    phase:          str
    confidence:     str
    scored:         bool = True  # False als scoring mislukte


class SectorSnapshotResponse(BaseModel):
    """Response voor GET /sector/{sector_name}"""
    sector_id:        str
    label:            str
    heat:             int
    status:           str
    leaders_scored:   list[LeaderScore]
    sympathy:         list[str]
    avg_momentum:     Optional[float]  # None als geen leaders gescoord
    avg_skip:         Optional[float]
    sector_confidence: str             # slechtste confidence van alle leaders
    analyzed_at:      datetime = Field(
                          default_factory=lambda: datetime.now(timezone.utc)
                      )


# ── HEALTH ────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status:       str
    version:      str
    engine:       str
    timestamp:    datetime
    data_sources: dict[str, str]
    limitations:  list[str]
    cache_stats:  Optional[dict] = None  # v2.2
