"""
backend/app.py
Momentum Intelligence API — v2.3

Wijzigingen t.o.v. v2.2:
    - OpenAPI polish: tags, descriptions, response examples, operation IDs
    - RequestLoggingMiddleware toegevoegd
    - GET /cache/stats endpoint (debug)
    - DELETE /cache/{ticker} endpoint (manual invalidation)
    - Structured logging via logging_config
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from datetime import datetime, timezone
import dataclasses, enum, logging, os

from backend.logging_config import (
    setup_logging, get_logger, RequestLoggingMiddleware,
    log_score_event, log_fallback_event,
)
from data.assembler   import build_ticker_input, _load_sectors
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
from cache.market_cache import (
    cache_stats, invalidate, invalidate_all, CACHE_ENABLED,
)

setup_logging()
logger = get_logger("api")

# ── APP ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Momentum Intelligence API",
    description="""
Personal momentum intelligence backend.

Detecteert early-stage market momentum via volume anomaly, sector heat,
catalyst quality, relative strength, float en social acceleration.

**Architectuur:**
- Score engine is deterministisch — geen AI in de scoringsketen
- AI narrative layer is gepland voor fase 3 (nog niet actief)
- Data: Yahoo Finance (unofficial) + in-memory cache

**DataConfidence labels:**
- `LIVE` — data < 5 min oud
- `DELAYED` — data 5-60 min oud (uit cache)
- `STALE` — data 1-2 uur oud (fallback, score minder betrouwbaar)
- `PARTIAL` — prijs aanwezig, optionele velden ontbreken
- `MISSING` — geen bruikbare data
""",
    version="2.3.0",
    openapi_tags=[
        {"name": "health",   "description": "Server status en versie-info."},
        {"name": "analysis", "description": "Momentum scoring per ticker of batch."},
        {"name": "sector",   "description": "Sector snapshots met gemiddeld momentum."},
        {"name": "cache",    "description": "Cache status en invalidatie."},
    ],
)

app.add_middleware(RequestLoggingMiddleware)

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
    """Score één ticker. Raises HTTPException bij fout."""
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

    log_score_event(
        logger, ticker, result.decision.value,
        result.momentum_score, result.skip_score,
        quality.confidence.value, quality.cache_hit,
    )

    if quality.cache_hit and quality.confidence in (
        DataConfidence.STALE, DataConfidence.DELAYED
    ):
        log_fallback_event(
            logger, ticker,
            f"cache fallback ({quality.confidence.value})",
            quality.confidence.value,
        )

    return _build_scoring_response(result, quality)


# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get(
    "/health",
    tags=["health"],
    summary="Server status",
    response_description="Server versie, data bronnen en cache statistieken.",
    operation_id="get_health",
    response_model=HealthResponse,
)
def health() -> HealthResponse:
    """
    Controleert of de server draait.

    Bevat versie-info, actieve data bronnen, bekende beperkingen
    en live cache statistieken.
    """
    return HealthResponse(
        status="ok", version="2.3.0", engine="scoring_v1_2",
        timestamp=datetime.now(timezone.utc),
        data_sources={
            "price_volume": "yahoo_finance (retry+backoff+cache)",
            "news":         "placeholder (fase 2.2: Finnhub)",
            "social":       "placeholder (fase 2.3: StockTwits)",
            "cache":        f"actief (CACHE_ENABLED={CACHE_ENABLED})",
        },
        limitations=[
            "catalyst_type altijd NONE (news_client placeholder)",
            "social_acceleration altijd 0 (geen StockTwits key)",
            "has_sec_investigation altijd False (handmatige check)",
            "float_shares via shares_outstanding (benadering)",
            "DataConfidence.DELAYED/STALE wijst op cache-gebaseerde data",
        ],
        cache_stats=cache_stats(),
    )


# ── ANALYZE SINGLE ────────────────────────────────────────────────────────────

@app.get(
    "/analyze/{ticker}",
    tags=["analysis"],
    summary="Momentum scoring voor één ticker",
    response_description="Volledig ScoringResult met momentum breakdown en data quality.",
    operation_id="analyze_ticker",
    responses={
        200: {"description": "Score berekend"},
        400: {"description": "Ongeldige ticker syntax", "content": {
            "application/json": {"example": {
                "detail": {"error": "INVALID_TICKER", "ticker": "123BAD",
                           "message": "Ongeldige ticker", "hint": "Gebruik alleen letters"}
            }}
        }},
        422: {"description": "Ticker niet gevonden of geen data"},
        429: {"description": "Yahoo Finance rate limit bereikt"},
        500: {"description": "Interne serverfout"},
    },
)
def analyze(
    ticker:  str,
    refresh: bool = Query(
        False,
        description="True = cache bypass, altijd live data ophalen"
    ),
) -> JSONResponse:
    """
    Berekent de volledige momentum score voor één US equity ticker.

    De score bestaat uit zeven componenten (totaal 100 pts):
    - **Volume Anomaly** (22 pts) — huidig vs 20-daags gemiddelde
    - **Sector Heat** (18 pts) — uit sectors.json config
    - **Catalyst Quality** (20 pts) — STRONG/MODERATE/WEAK/NONE
    - **Premarket Strength** (14 pts) — sweet spot 8-20%
    - **Relative Strength** (10 pts) — vs SPY return
    - **Social Acceleration** (8 pts) — mention velocity, quality-capped
    - **Float Score** (8 pts) — lage float = hogere amplificatie

    **Skip Score** blokkeert altijd vóór Momentum Score:
    - SEC investigation / class action / CFD-only → **BLOCKED**
    - Dag >40% / premarket >40% / geen catalyst + laag momentum → **SKIP**

    **DataConfidence** geeft aan hoe betrouwbaar de score is:
    - `LIVE` = verse data, `DELAYED/STALE` = uit cache, `MISSING` = geen data
    """
    try:
        response = _score_one(ticker, force_refresh=refresh)
        return JSONResponse(content=response.model_dump(mode="json"))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"analyze/{ticker}: {exc}", exc_info=True)
        raise HTTPException(500, detail=internal_error(ticker, str(exc)).model_dump())


# ── ANALYZE BATCH ─────────────────────────────────────────────────────────────

@app.get(
    "/analyze",
    tags=["analysis"],
    summary="Batch scoring voor meerdere tickers",
    response_description="Scores voor alle geldige tickers. Fouten in 'errors' veld.",
    operation_id="analyze_batch",
    responses={
        400: {"description": "Ongeldige input of te veel tickers (max 10)"},
    },
)
def analyze_batch(
    tickers: str  = Query(
        ...,
        description="Komma-gescheiden tickers, max 10. Voorbeeld: IONQ,QBTS,RGTI",
        examples={"default": {"value": "IONQ,QBTS,RGTI"}},
    ),
    refresh: bool = Query(False, description="True = cache bypass voor alle tickers"),
) -> JSONResponse:
    """
    Scoort meerdere tickers in één request.

    Ideaal voor sympathy play scanning: geef de leader + bekende sympathy
    plays mee en vergelijk scores direct.

    - Maximum **10 tickers** per request
    - Eén mislukte ticker stopt de rest **niet**
    - Fouten staan in het `errors` veld met de reden per ticker
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

    results, errors = [], {}
    for t in raw:
        try:
            results.append(_score_one(t, force_refresh=refresh))
        except HTTPException as exc:
            detail = exc.detail
            msg = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
            errors[t] = msg
        except Exception as exc:
            errors[t] = str(exc)

    batch = BatchScoringResponse(
        tickers_requested=len(raw), tickers_scored=len(results),
        tickers_failed=len(errors), results=results, errors=errors,
        analyzed_at=datetime.now(timezone.utc),
    )
    return JSONResponse(content=batch.model_dump(mode="json"))


# ── SECTOR SNAPSHOT ───────────────────────────────────────────────────────────

@app.get(
    "/sector/{sector_name}",
    tags=["sector"],
    summary="Sector snapshot met gemiddeld momentum",
    response_description="Leaders, sympathy plays, gemiddelde scores en sector confidence.",
    operation_id="get_sector",
    responses={
        404: {"description": "Sector niet gevonden", "content": {
            "application/json": {"example": {
                "detail": {
                    "error": "SECTOR_NOT_FOUND",
                    "message": "Sector 'xyz' niet gevonden.",
                    "available": ["quantum", "ai_infra", "drones_defense"]
                }
            }}
        }},
    },
)
def sector_snapshot(sector_name: str) -> JSONResponse:
    """
    Geeft een snapshot van een momentum-sector.

    Scoort alle leaders uit `config/sectors.json`.
    Berekent gemiddeld momentum en rapporteert de slechtste confidence
    van alle leaders als `sector_confidence`.

    Beschikbare sectoren:
    `quantum`, `ai_infra`, `drones_defense`, `ai_software`,
    `power_energy`, `robotics`, `cybersecurity`, `ai_pc`
    """
    sector_name = sector_name.lower().strip()
    data = _load_sectors()

    sector_data = next(
        (s for s in data.get("sectors", []) if s["id"] == sector_name), None
    )
    if not sector_data:
        sector_data = next(
            (s for s in data.get("sectors", [])
             if s["label"].lower().replace(" ", "_") == sector_name
             or s["label"].lower() == sector_name),
            None,
        )
    if not sector_data:
        available = [s["id"] for s in data.get("sectors", [])]
        raise HTTPException(404, detail={
            "error":     "SECTOR_NOT_FOUND",
            "message":   f"Sector '{sector_name}' niet gevonden.",
            "available": available,
        })

    leaders_scored, momentum_scores, skip_scores, confidences = [], [], [], []

    for lt in sector_data.get("leaders", []):
        try:
            scored = _score_one(lt)
            leaders_scored.append(LeaderScore(
                ticker=lt, decision=scored.decision,
                momentum_score=scored.momentum_score,
                skip_score=scored.skip_score, phase=scored.phase,
                confidence=scored.data_quality.confidence.value, scored=True,
            ))
            momentum_scores.append(scored.momentum_score)
            skip_scores.append(float(scored.skip_score))
            confidences.append(scored.data_quality.confidence.value)
        except Exception as exc:
            leaders_scored.append(LeaderScore(
                ticker=lt, decision="ERROR", momentum_score=0.0,
                skip_score=0, phase="NEUTRAL",
                confidence="MISSING", scored=False,
            ))
            logger.warning(f"sector/{sector_name}: {lt} mislukt — {exc}")

    conf_rank = {"LIVE": 0, "DELAYED": 1, "STALE": 2, "PARTIAL": 3, "MISSING": 4}
    sector_conf = max(confidences, key=lambda c: conf_rank.get(c, 5)) if confidences else "MISSING"

    snapshot = SectorSnapshotResponse(
        sector_id=sector_data["id"], label=sector_data["label"],
        heat=sector_data["heat"], status=sector_data.get("status", "UNKNOWN"),
        leaders_scored=leaders_scored, sympathy=sector_data.get("sympathy", []),
        avg_momentum=round(sum(momentum_scores) / len(momentum_scores), 1) if momentum_scores else None,
        avg_skip=round(sum(skip_scores) / len(skip_scores), 1) if skip_scores else None,
        sector_confidence=sector_conf,
        analyzed_at=datetime.now(timezone.utc),
    )
    return JSONResponse(content=snapshot.model_dump(mode="json"))


# ── CACHE ENDPOINTS ───────────────────────────────────────────────────────────

@app.get(
    "/cache/stats",
    tags=["cache"],
    summary="Cache statistieken",
    operation_id="get_cache_stats",
)
def get_cache_stats() -> dict:
    """
    Geeft live cache statistieken terug.

    Nuttig voor debugging: hoeveel entries zijn LIVE vs DELAYED vs STALE?
    Welke tickers zijn gecached? Is de markt open?
    """
    return cache_stats()


@app.delete(
    "/cache/{ticker}",
    tags=["cache"],
    summary="Invalideer cache voor één ticker",
    operation_id="invalidate_ticker_cache",
)
def invalidate_ticker(ticker: str) -> dict:
    """
    Verwijdert de cache entry voor de opgegeven ticker.

    Gebruik dit na handmatige aanpassing van sector config
    of als je vermoedt dat de cache verouderde data bevat.
    """
    ticker = ticker.upper().strip()
    removed = invalidate(ticker)
    return {
        "ticker":  ticker,
        "removed": removed,
        "message": f"Cache entry {'verwijderd' if removed else 'niet aanwezig'} voor {ticker}.",
    }
