"""
backend/app.py
Momentum Intelligence API — v2.1

Wijzigingen t.o.v. v2.0:
    - Typed responses via Pydantic schemas
    - ApiError schema voor alle foutresponses
    - Rate limit detectie → 429 response
    - ScoringResponse schema validates output
    - HealthResponse schema

Endpoints:
    GET /health             Liveness check
    GET /analyze/{ticker}   Momentum scoring

Starten:
    uvicorn backend.app:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
import dataclasses, enum, logging

from data.assembler import build_ticker_input
from scoring.scoring_v1_2 import score_ticker
from schemas.api_error import (
    invalid_ticker, ticker_not_found, rate_limited as rate_limited_err,
    fetch_error, internal_error, ErrorCode
)
from schemas.scoring_response import (
    ScoringResponse, HealthResponse, DataQuality,
    MomentumBreakdown, SkipBreakdown
)
from schemas.ticker_snapshot import DataConfidence

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Momentum Intelligence API",
    description="Score engine v1.2 — geen AI, pure formules",
    version="2.1.0",
)


# ── SERIALISATIE ──────────────────────────────────────────────────────────────

def _serialize(obj):
    """Recursieve conversie van dataclasses + Enums naar JSON-compatibele types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    return obj


def _build_response(result, quality: DataQuality) -> ScoringResponse:
    """Bouwt getypeerde ScoringResponse van ScoringResult + DataQuality."""
    md = result.momentum_detail
    sd = result.skip_detail

    return ScoringResponse(
        ticker          = result.ticker,
        decision        = result.decision.value,
        momentum_score  = result.momentum_score,
        skip_score      = result.skip_score,
        phase           = result.phase.value,
        phase_description = result.phase_description,
        market_cap_tier = result.market_cap_tier.value,
        sizing_eur      = result.sizing_eur,
        summary         = result.summary,
        analyzed_at     = datetime.now(timezone.utc),
        momentum_detail = MomentumBreakdown(
            total                   = md.total,
            volume_anomaly          = md.volume_anomaly,
            sector_heat_score       = md.sector_heat_score,
            catalyst_quality        = md.catalyst_quality,
            premarket_strength      = md.premarket_strength,
            relative_strength_score = md.relative_strength_score,
            social_acceleration     = md.social_acceleration,
            float_score             = md.float_score,
            social_was_capped       = md.social_was_capped,
            social_cap_reason       = md.social_cap_reason or "",
            breakdown               = md.breakdown,
        ),
        skip_detail = SkipBreakdown(
            total            = sd.total,
            is_hard_blocked  = sd.is_hard_blocked,
            reasons          = sd.reasons,
            blocking_reasons = sd.blocking_reasons,
        ),
        data_quality = quality,
    )


# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status    = "ok",
        version   = "2.1.0",
        engine    = "scoring_v1_2",
        timestamp = datetime.now(timezone.utc),
        data_sources = {
            "price_volume": "yahoo_finance (unofficial, retry+backoff)",
            "news":         "placeholder (fase 2.1: Finnhub)",
            "social":       "placeholder (fase 2.2: StockTwits)",
            "cache":        "disabled (fase 2.2)",
        },
        limitations = [
            "catalyst_type altijd NONE (news_client placeholder)",
            "social_acceleration altijd 0 (geen StockTwits key)",
            "has_sec_investigation altijd False (handmatige check)",
            "float_shares via shares_outstanding (benadering)",
            "DataConfidence.DELAYED niet actief zonder cache",
        ],
    )


# ── ANALYZE ───────────────────────────────────────────────────────────────────

@app.get("/analyze/{ticker}")
def analyze(ticker: str) -> JSONResponse:
    """
    Volledige momentum scoring voor één ticker.

    Error responses (ApiError schema):
        400  Ongeldige ticker syntax
        422  Ticker niet gevonden of geen data
        429  Yahoo Finance rate limit bereikt
        500  Interne serverfout
    """
    ticker = ticker.upper().strip()

    # Validatie
    if not ticker or not ticker.replace("-", "").isalpha():
        err = invalid_ticker(ticker)
        raise HTTPException(status_code=400, detail=err.model_dump())

    logger.info(f"analyze: {ticker}")

    try:
        ticker_input, quality = build_ticker_input(ticker)

        # Rate limit gedetecteerd
        if (quality.fetch_error and
                any(kw in quality.fetch_error.lower()
                    for kw in ["429", "rate limit", "too many"])):
            err = rate_limited_err(ticker)
            raise HTTPException(status_code=429, detail=err.model_dump())

        # Ophaalfout
        if quality.fetch_error:
            err = fetch_error(ticker, quality.fetch_error)
            raise HTTPException(status_code=422, detail=err.model_dump())

        # Geen prijs data
        if ticker_input.price == 0.0:
            err = ticker_not_found(ticker)
            raise HTTPException(status_code=422, detail=err.model_dump())

        result   = score_ticker(ticker_input)
        response = _build_response(result, quality)

        logger.info(
            f"analyze: {ticker} → {result.decision.value} "
            f"(momentum={result.momentum_score:.1f}, skip={result.skip_score}, "
            f"confidence={quality.confidence.value})"
        )

        return JSONResponse(content=response.model_dump(mode="json"))

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"analyze: fout bij {ticker}: {exc}", exc_info=True)
        err = internal_error(ticker, str(exc))
        raise HTTPException(status_code=500, detail=err.model_dump())
