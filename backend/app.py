"""
backend/app.py
Momentum Intelligence API — v2.2

Nieuwe endpoints:
    GET /analyze?tickers=A,B,C     Batch scoring
    GET /sector/{sector_name}      Sector snapshot
    GET /analyze/{ticker}?refresh  Force cache bypass

Wijzigingen t.o.v. v2.1:
    - Batch endpoint met max 10 tickers, partial failure tolerant
    - Sector endpoint leest uit sectors.json, scoort leaders vanuit cache
    - refresh=true query parameter voor cache invalidatie
    - cache_stats in /health response
    - Rate limit → 429 met Retry-After header
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from datetime import datetime, timezone
import dataclasses, enum, logging, json, os

from data.assembler   import build_ticker_input, _find_sector, _load_sectors
from scoring.scoring_v1_2 import score_ticker
from schemas.api_error import (
    invalid_ticker, ticker_not_found, rate_limited as rate_limited_err,
    fetch_error, internal_error,
)
from schemas.scoring_response import (
    ScoringResponse, HealthResponse, DataQuality,
    MomentumBreakdown, SkipBreakdown,
    BatchScoringResponse, SectorSnapshotResponse, LeaderScore,
)
from schemas.ticker_snapshot import DataConfidence
from cache.market_cache import cache_stats, invalidate, CACHE_ENABLED

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Momentum Intelligence API",
    description="Score engine v1.2 — cache actief, geen AI",
    version="2.2.0",
)

_BATCH_MAX = 10


# ── SERIALISATIE ──────────────────────────────────────────────────────────────

def _serialize(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    return obj


def _build_scoring_response(result, quality: DataQuality) -> ScoringResponse:
    md, sd = result.momentum_detail, result.skip_detail
    return ScoringResponse(
        ticker=result.ticker, decision=result.decision.value,
        momentum_score=result.momentum_score, skip_score=result.skip_score,
        phase=result.phase.value, phase_description=result.phase_description,
        market_cap_tier=result.market_cap_tier.value,
        sizing_eur=result.sizing_eur, summary=result.summary,
        analyzed_at=datetime.now(timezone.utc),
        momentum_detail=MomentumBreakdown(
            total=md.total, volume_anomaly=md.volume_anomaly,
            sector_heat_score=md.sector_heat_score,
            catalyst_quality=md.catalyst_quality,
            premarket_strength=md.premarket_strength,
            relative_strength_score=md.relative_strength_score,
            social_acceleration=md.social_acceleration,
            float_score=md.float_score,
            social_was_capped=md.social_was_capped,
            social_cap_reason=md.social_cap_reason or "",
            breakdown=md.breakdown,
        ),
        skip_detail=SkipBreakdown(
            total=sd.total, is_hard_blocked=sd.is_hard_blocked,
            reasons=sd.reasons, blocking_reasons=sd.blocking_reasons,
        ),
        data_quality=quality,
    )


def _score_one(ticker: str, force_refresh: bool = False) -> ScoringResponse:
    """Score één ticker. Raises HTTPException bij validatie-fout."""
    ticker = ticker.upper().strip()

    if not ticker or not ticker.replace("-", "").isalpha():
        raise HTTPException(400, detail=invalid_ticker(ticker).model_dump())

    ticker_input, quality = build_ticker_input(ticker, force_refresh=force_refresh)

    if quality.fetch_error and any(
        kw in (quality.fetch_error or "").lower()
        for kw in ["429", "rate limit", "too many"]
    ):
        raise HTTPException(429, detail=rate_limited_err(ticker).model_dump())

    if quality.fetch_error and not quality.cache_hit:
        raise HTTPException(422, detail=fetch_error(ticker, quality.fetch_error).model_dump())

    if ticker_input.price == 0.0 and not quality.cache_hit:
        raise HTTPException(422, detail=ticker_not_found(ticker).model_dump())

    result = score_ticker(ticker_input)
    logger.info(
        f"analyze: {ticker} → {result.decision.value} "
        f"(score={result.momentum_score:.1f}, skip={result.skip_score}, "
        f"conf={quality.confidence.value}, cache={quality.cache_hit})"
    )
    return _build_scoring_response(result, quality)


# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", version="2.2.0", engine="scoring_v1_2",
        timestamp=datetime.now(timezone.utc),
        data_sources={
            "price_volume": "yahoo_finance (retry+backoff+cache)",
            "news":         "placeholder (fase 2.1: Finnhub)",
            "social":       "placeholder (fase 2.2: StockTwits)",
            "cache":        f"actief (CACHE_ENABLED={CACHE_ENABLED})",
        },
        limitations=[
            "catalyst_type altijd NONE (news_client placeholder)",
            "social_acceleration altijd 0 (geen StockTwits key)",
            "has_sec_investigation altijd False (handmatige check)",
            "float_shares via shares_outstanding (benadering)",
        ],
        cache_stats=cache_stats(),
    )


# ── ANALYZE SINGLE ────────────────────────────────────────────────────────────

@app.get("/analyze/{ticker}")
def analyze(
    ticker:  str,
    refresh: bool = Query(False, description="True = cache bypass"),
) -> JSONResponse:
    """
    Momentum scoring voor één ticker.

    Query params:
        refresh=true    Bypass cache, haal altijd live data op

    Error codes: 400 | 422 | 429 | 500
    """
    try:
        response = _score_one(ticker, force_refresh=refresh)
        if refresh:
            logger.info(f"analyze: forced refresh voor {ticker.upper()}")
        return JSONResponse(content=response.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"analyze/{ticker}: {exc}", exc_info=True)
        raise HTTPException(500, detail=internal_error(ticker, str(exc)).model_dump())


# ── ANALYZE BATCH ─────────────────────────────────────────────────────────────

@app.get("/analyze")
def analyze_batch(
    tickers: str  = Query(..., description="Komma-gescheiden tickers, max 10"),
    refresh: bool = Query(False, description="True = cache bypass voor alle tickers"),
) -> JSONResponse:
    """
    Batch scoring voor meerdere tickers.

    Query params:
        tickers=IONQ,QBTS,RGTI    Max 10, komma-gescheiden
        refresh=true              Cache bypass

    Één mislukking stopt de batch niet.
    Fouten staan in het 'errors' veld.
    """
    raw = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    if not raw:
        raise HTTPException(400, detail={"error": "INVALID_TICKER",
                                         "message": "Geen tickers opgegeven."})
    if len(raw) > _BATCH_MAX:
        raise HTTPException(400, detail={
            "error":   "TOO_MANY_TICKERS",
            "message": f"Maximaal {_BATCH_MAX} tickers per batch-request.",
            "hint":    f"Opgegeven: {len(raw)}. Splits in meerdere requests.",
        })

    results: list[ScoringResponse] = []
    errors:  dict[str, str]        = {}

    for t in raw:
        try:
            results.append(_score_one(t, force_refresh=refresh))
        except HTTPException as exc:
            detail = exc.detail
            msg = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
            errors[t] = msg
            logger.warning(f"batch: {t} mislukt — {msg}")
        except Exception as exc:
            errors[t] = str(exc)
            logger.error(f"batch: {t} onverwachte fout — {exc}")

    batch = BatchScoringResponse(
        tickers_requested=len(raw),
        tickers_scored=len(results),
        tickers_failed=len(errors),
        results=results,
        errors=errors,
        analyzed_at=datetime.now(timezone.utc),
    )

    logger.info(
        f"batch: {len(results)}/{len(raw)} geslaagd, {len(errors)} mislukt"
    )
    return JSONResponse(content=batch.model_dump(mode="json"))


# ── SECTOR SNAPSHOT ───────────────────────────────────────────────────────────

@app.get("/sector/{sector_name}")
def sector_snapshot(sector_name: str) -> JSONResponse:
    """
    Sector snapshot: leaders + sympathy + gemiddeld momentum.

    Leaders worden gescoord vanuit cache (of live als niet gecached).
    Eén mislukte leader stopt de andere niet.
    """
    sector_name = sector_name.lower().strip()
    data = _load_sectors()

    sector_data = next(
        (s for s in data.get("sectors", []) if s["id"] == sector_name),
        None
    )

    if not sector_data:
        # Probeer ook op label te matchen
        sector_data = next(
            (s for s in data.get("sectors", [])
             if s["label"].lower().replace(" ", "_") == sector_name
             or s["label"].lower() == sector_name),
            None
        )

    if not sector_data:
        available = [s["id"] for s in data.get("sectors", [])]
        raise HTTPException(404, detail={
            "error":     "SECTOR_NOT_FOUND",
            "message":   f"Sector '{sector_name}' niet gevonden.",
            "available": available,
        })

    leaders_scored: list[LeaderScore] = []
    momentum_scores: list[float]      = []
    skip_scores:     list[float]      = []
    confidences:     list[str]        = []

    for leader_ticker in sector_data.get("leaders", []):
        try:
            scored = _score_one(leader_ticker)
            leaders_scored.append(LeaderScore(
                ticker=leader_ticker,
                decision=scored.decision,
                momentum_score=scored.momentum_score,
                skip_score=scored.skip_score,
                phase=scored.phase,
                confidence=scored.data_quality.confidence.value,
                scored=True,
            ))
            momentum_scores.append(scored.momentum_score)
            skip_scores.append(float(scored.skip_score))
            confidences.append(scored.data_quality.confidence.value)
        except Exception as exc:
            leaders_scored.append(LeaderScore(
                ticker=leader_ticker,
                decision="ERROR", momentum_score=0.0,
                skip_score=0, phase="NEUTRAL",
                confidence="MISSING", scored=False,
            ))
            logger.warning(f"sector/{sector_name}: {leader_ticker} mislukt — {exc}")

    avg_momentum = round(sum(momentum_scores) / len(momentum_scores), 1) if momentum_scores else None
    avg_skip     = round(sum(skip_scores) / len(skip_scores), 1) if skip_scores else None

    # Slechtste confidence van alle leaders
    conf_rank = {"LIVE": 0, "DELAYED": 1, "STALE": 2, "PARTIAL": 3, "MISSING": 4}
    sector_conf = (
        max(confidences, key=lambda c: conf_rank.get(c, 5))
        if confidences else "MISSING"
    )

    status = sector_data.get("status", "UNKNOWN")

    snapshot = SectorSnapshotResponse(
        sector_id        = sector_data["id"],
        label            = sector_data["label"],
        heat             = sector_data["heat"],
        status           = status,
        leaders_scored   = leaders_scored,
        sympathy         = sector_data.get("sympathy", []),
        avg_momentum     = avg_momentum,
        avg_skip         = avg_skip,
        sector_confidence = sector_conf,
        analyzed_at      = datetime.now(timezone.utc),
    )

    logger.info(
        f"sector/{sector_name}: {len(leaders_scored)} leaders, "
        f"avg_momentum={avg_momentum}, conf={sector_conf}"
    )
    return JSONResponse(content=snapshot.model_dump(mode="json"))
