"""
backend/app.py
Momentum Intelligence API — v2.5

Wijzigingen t.o.v. v2.4:
    - Snapshot persistentie na elke /analyze call
    - Phase transition tracking via signal_tracker
    - GET /history/{ticker}          → signaal evolutie
    - GET /history/{ticker}/window   → is momentum window nog open?
    - GET /history/{ticker}/transitions → fase-overgangen
    - GET /sector/{name}/trend       → sector heat trend
    - Sector snapshots worden opgeslagen na elke /sector call
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
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
    SignalEvolutionResponse, MomentumWindowResponse, SectorEvolutionResponse,
)
from schemas.ticker_snapshot import DataConfidence
from cache.market_cache import (
    cache_stats, invalidate, CACHE_ENABLED,
)
from storage.snapshot_store import (
    save_snapshot_dict, load_snapshots, load_latest,
    list_tracked_tickers, count_snapshots,
)
from storage.signal_tracker import (
    record_transition_if_changed, record_catalyst_if_changed,
    get_transitions, get_catalyst_timeline, calculate_momentum_trend,
    get_decision_distribution,
)
from storage.signal_decay import apply_decay_to_snapshot
from storage.history_replay import (
    get_signal_evolution, get_sector_evolution, get_momentum_window,
)
from storage.sector_history import save_sector_snapshot

setup_logging()
logger = get_logger("api")

app = FastAPI(
    title="Momentum Intelligence API",
    description="""
Personal momentum intelligence backend.

**v2.5** — Historical Memory Layer: snapshots worden opgeslagen na elke
scoring, zodat trends, fase-overgangen en signal decay inzichtelijk worden.

**DataConfidence labels:**
- `LIVE` — data < 5 min oud
- `DELAYED` — 5-60 min (cache)
- `STALE` — 1-2 uur (fallback)
- `PARTIAL` — optionele velden ontbreken
- `MISSING` — geen data

**Signal Age (na opslag):**
- `FRESH` < 2u | `AGING` 2-8u | `STALE` 8-24u | `OLD` 24-48u | `EXPIRED` > 48u
""",
    version="2.5.0",
    openapi_tags=[
        {"name": "health",   "description": "Server status en versie-info."},
        {"name": "analysis", "description": "Momentum scoring per ticker of batch."},
        {"name": "sector",   "description": "Sector snapshots met gemiddeld momentum."},
        {"name": "cache",    "description": "Cache status en invalidatie."},
        {"name": "history",  "description": "Historische signaal evolutie en decay."},
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


def _persist_snapshot(
    ticker:  str,
    result,
    quality: DataQuality,
    ticker_input=None,
) -> str:
    """
    Slaat scoring resultaat op in storage.
    Detecteert phase transitions en catalyst wijzigingen.
    Nooit een exception — storage is best-effort.
    """
    try:
        now = datetime.now(timezone.utc)
        from storage.snapshot_store import _make_version_id
        vid = _make_version_id(ticker, now)

        # Volume ratio uit breakdown
        bd = result.momentum_detail.breakdown
        vol_str   = next((v for k, v in bd.items() if "Volume" in k), "")
        vol_ratio = 0.0
        try:
            raw = vol_str.strip().split("—")[0].strip()
            vol_ratio = float(raw.split()[0].replace("x", ""))
        except Exception:
            pass

        # Catalyst type uit breakdown
        cat_str = next((v for k, v in bd.items() if "Catalyst" in k), "")
        cat_type_raw = cat_str.split("—")[-1].strip() if "—" in cat_str else ""

        snap_dict = {
            "version_id":          vid,
            "ticker":              ticker.upper(),
            "timestamp":           now.isoformat(),
            "decision":            result.decision.value,
            "momentum_score":      result.momentum_score,
            "skip_score":          result.skip_score,
            "phase":               result.phase.value,
            "confidence":          quality.confidence.value,
            "cache_hit":           quality.cache_hit,
            "data_age_seconds":    quality.data_age_seconds,
            "retries_used":        quality.retries_used,
            "catalyst_type":       cat_type_raw[:20],
            "catalyst_description": cat_type_raw[:100],
            "day_change_pct":      getattr(ticker_input, "day_change_pct", 0.0),
            "volume_ratio":        vol_ratio,
            "sector_heat":         int(result.momentum_detail.sector_heat_score / 18 * 100),
            "sector_id":           getattr(
                getattr(ticker_input, "sector", None), "sector_id", "unknown"
            ),
            "market_session":      getattr(ticker_input, "market_session", None),
            "price":               getattr(ticker_input, "price", 0.0),
            "premarket_pct":       getattr(ticker_input, "premarket_pct", 0.0),
            "stored_at":           now.isoformat(),
        }

        save_snapshot_dict(ticker, snap_dict)

        # Recente snapshots voor transition detectie
        recent = load_snapshots(ticker, limit=10)

        record_transition_if_changed(
            ticker         = ticker,
            new_phase      = result.phase.value,
            momentum_score = result.momentum_score,
            decision       = result.decision.value,
            version_id     = vid,
            snapshots      = recent,
        )

        record_catalyst_if_changed(
            ticker         = ticker,
            catalyst_type  = cat_type_raw[:20],
            catalyst_desc  = cat_type_raw[:100],
            version_id     = vid,
            snapshots      = recent,
        )

        return vid

    except Exception as exc:
        logger.warning(f"persist_snapshot: {ticker} mislukt: {exc}")
        return ""


def _score_one(
    ticker:        str,
    force_refresh: bool = False,
    persist:       bool = True,
) -> tuple[ScoringResponse, object, DataQuality]:
    """
    Score één ticker. Returns (response, ticker_input, quality).
    Raises HTTPException bij validatiefouten.
    """
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
        log_fallback_event(logger, ticker,
                           f"cache fallback ({quality.confidence.value})",
                           quality.confidence.value)

    if persist:
        _persist_snapshot(ticker, result, quality, ticker_input)

    return _build_scoring_response(result, quality), ticker_input, quality


# ── HEALTH ────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["health"], summary="Server status",
         operation_id="get_health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Server versie, cache stats, data bronnen en v2.5 storage info."""
    tracked = list_tracked_tickers()
    return HealthResponse(
        status="ok", version="2.5.0", engine="scoring_v1_2",
        timestamp=datetime.now(timezone.utc),
        data_sources={
            "price_volume": "yahoo_finance (retry+backoff+cache)",
            "news":         "finnhub (key-aware) / placeholder",
            "social":       "placeholder (fase 3)",
            "cache":        f"actief (CACHE_ENABLED={CACHE_ENABLED})",
            "history":      f"storage/data/ ({len(tracked)} tickers getrackt)",
        },
        limitations=[
            "catalyst_type NONE zonder FINNHUB_API_KEY",
            "social_acceleration altijd 0 (fase 3)",
            "has_sec_investigation False zonder FINNHUB_API_KEY",
            "float_shares via shares_outstanding (benadering)",
        ],
        cache_stats=cache_stats(),
    )


# ── ANALYZE SINGLE ────────────────────────────────────────────────────────────

@app.get(
    "/analyze/{ticker}",
    tags=["analysis"],
    summary="Momentum scoring voor één ticker",
    operation_id="analyze_ticker",
    responses={
        200: {"description": "Score berekend"},
        400: {"description": "Ongeldige ticker syntax"},
        422: {"description": "Ticker niet gevonden of geen data"},
        429: {"description": "Rate limit"},
        500: {"description": "Serverfout"},
    },
)
def analyze(
    ticker:  str,
    refresh: bool  = Query(False, description="Cache bypass"),
    persist: bool  = Query(True,  description="False = score zonder opslaan"),
) -> JSONResponse:
    """
    Berekent momentum score. Slaat resultaat op in storage (tenzij persist=false).

    Opgeslagen data is beschikbaar via /history/{ticker}.
    """
    try:
        response, _, _ = _score_one(ticker, force_refresh=refresh, persist=persist)
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
    summary="Batch scoring — max 10 tickers",
    operation_id="analyze_batch",
)
def analyze_batch(
    tickers: str  = Query(..., description="Komma-gescheiden, max 10",
                          examples={"default": {"value": "IONQ,QBTS,RGTI"}}),
    refresh: bool = Query(False),
    persist: bool = Query(True, description="False = geen opslag"),
) -> JSONResponse:
    """Scoort meerdere tickers. Eén fout stopt de batch niet."""
    raw = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    if not raw:
        raise HTTPException(400, detail={"error": "INVALID_TICKER",
                                         "message": "Geen tickers opgegeven."})
    if len(raw) > _BATCH_MAX:
        raise HTTPException(400, detail={
            "error":   "TOO_MANY_TICKERS",
            "message": f"Maximaal {_BATCH_MAX} tickers.",
            "hint":    f"Opgegeven: {len(raw)}.",
        })

    results, errors = [], {}
    for t in raw:
        try:
            resp, _, _ = _score_one(t, force_refresh=refresh, persist=persist)
            results.append(resp)
        except HTTPException as exc:
            detail = exc.detail
            errors[t] = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
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
    summary="Sector snapshot met leaders",
    operation_id="get_sector",
    responses={404: {"description": "Sector niet gevonden"}},
)
def sector_snapshot(sector_name: str) -> JSONResponse:
    """
    Scoort alle leaders uit de sector. Slaat sector snapshot op in history.
    Beschikbaar via /sector/{sector_name}/trend.
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
             or s["label"].lower() == sector_name), None,
        )
    if not sector_data:
        available = [s["id"] for s in data.get("sectors", [])]
        raise HTTPException(404, detail={
            "error": "SECTOR_NOT_FOUND",
            "message": f"Sector '{sector_name}' niet gevonden.",
            "available": available,
        })

    leaders_scored, momentum_scores, skip_scores, confidences = [], [], [], []
    leader_decisions = {}

    for lt in sector_data.get("leaders", []):
        try:
            scored, _, q = _score_one(lt)
            leaders_scored.append(LeaderScore(
                ticker=lt, decision=scored.decision,
                momentum_score=scored.momentum_score,
                skip_score=scored.skip_score, phase=scored.phase,
                confidence=q.confidence.value, scored=True,
            ))
            momentum_scores.append(scored.momentum_score)
            skip_scores.append(float(scored.skip_score))
            confidences.append(q.confidence.value)
            leader_decisions[lt] = scored.decision
        except Exception as exc:
            leaders_scored.append(LeaderScore(
                ticker=lt, decision="ERROR", momentum_score=0.0,
                skip_score=0, phase="NEUTRAL", confidence="MISSING", scored=False,
            ))
            logger.warning(f"sector/{sector_name}: {lt} mislukt — {exc}")

    conf_rank = {"LIVE": 0, "DELAYED": 1, "STALE": 2, "PARTIAL": 3, "MISSING": 4}
    sector_conf = max(confidences, key=lambda c: conf_rank.get(c, 5)) if confidences else "MISSING"
    avg_momentum = round(sum(momentum_scores) / len(momentum_scores), 1) if momentum_scores else None
    avg_skip     = round(sum(skip_scores) / len(skip_scores), 1) if skip_scores else None

    # Persist sector snapshot
    if avg_momentum is not None:
        try:
            save_sector_snapshot(
                sector_id=sector_data["id"],
                heat=sector_data["heat"],
                avg_momentum=avg_momentum,
                avg_skip=avg_skip or 0.0,
                leader_decisions=leader_decisions,
                sector_confidence=sector_conf,
            )
        except Exception as exc:
            logger.debug(f"sector snapshot persist mislukt: {exc}")

    snapshot = SectorSnapshotResponse(
        sector_id=sector_data["id"], label=sector_data["label"],
        heat=sector_data["heat"], status=sector_data.get("status", "UNKNOWN"),
        leaders_scored=leaders_scored, sympathy=sector_data.get("sympathy", []),
        avg_momentum=avg_momentum, avg_skip=avg_skip,
        sector_confidence=sector_conf,
        analyzed_at=datetime.now(timezone.utc),
    )
    return JSONResponse(content=snapshot.model_dump(mode="json"))


# ── HISTORY ENDPOINTS ─────────────────────────────────────────────────────────

@app.get(
    "/history/{ticker}",
    tags=["history"],
    summary="Signaal evolutie voor één ticker",
    operation_id="get_ticker_history",
    responses={404: {"description": "Geen historie beschikbaar"}},
)
def ticker_history(
    ticker: str,
    hours:  float = Query(24.0, description="Tijdvenster in uren", ge=1, le=168),
    limit:  int   = Query(50,   description="Max aantal snapshots",  ge=1, le=200),
) -> JSONResponse:
    """
    Volledige signaal evolutie: snapshots, decay, trend, fase-overgangen,
    catalyst tijdlijn en decision distributie.

    Snapshots worden opgeslagen na elke /analyze call.
    De 'effective_signals' lijst bevat decay toegepast — hiermee zie je of een
    oud BUY_STRONG signaal nog steeds actionable is.
    """
    ticker = ticker.upper().strip()
    evolution = get_signal_evolution(ticker, hours=hours, max_snaps=limit)

    if evolution["snapshot_count"] == 0:
        raise HTTPException(404, detail={
            "error":  "NO_HISTORY",
            "ticker": ticker,
            "message": f"Geen historische data voor {ticker}. "
                       f"Roep /analyze/{ticker} aan om tracking te starten.",
        })

    return JSONResponse(content={**evolution, "analyzed_at": datetime.now(timezone.utc).isoformat()})


@app.get(
    "/history/{ticker}/window",
    tags=["history"],
    summary="Is het momentum window nog open?",
    operation_id="get_momentum_window",
)
def momentum_window(
    ticker: str,
    hours:  float = Query(6.0, description="Lookback uren voor trend", ge=1, le=48),
) -> JSONResponse:
    """
    Combineert signal age + decay + momentum trend tot één oordeel:
    is het signaal nog actionable?

    - `window_open: true`  → signaal is nog vers en trend is niet dalend
    - `window_open: false` → te oud, vervallen of dalend momentum
    """
    ticker = ticker.upper().strip()
    window = get_momentum_window(ticker, hours=hours)
    return JSONResponse(content={**window, "analyzed_at": datetime.now(timezone.utc).isoformat()})


@app.get(
    "/history/{ticker}/transitions",
    tags=["history"],
    summary="Fase-overgangen voor één ticker",
    operation_id="get_phase_transitions",
)
def phase_transitions(
    ticker: str,
    limit:  int = Query(20, description="Max overgangen", ge=1, le=100),
) -> JSONResponse:
    """
    Geeft alle geregistreerde fase-overgangen terug.
    Voorbeeld: NEUTRAL → ACCUMULATION → BREAKOUT

    Fase-overgangen worden automatisch gedetecteerd bij /analyze calls.
    """
    ticker      = ticker.upper().strip()
    transitions = get_transitions(ticker, limit=limit)
    catalysts   = get_catalyst_timeline(ticker, limit=limit)
    recent      = load_snapshots(ticker, limit=20)
    trend       = calculate_momentum_trend(recent)
    dist        = get_decision_distribution(recent)

    return JSONResponse(content={
        "ticker":               ticker,
        "phase_transitions":    transitions,
        "catalyst_timeline":    catalysts,
        "momentum_trend":       trend,
        "decision_distribution": dist,
        "analyzed_at":          datetime.now(timezone.utc).isoformat(),
    })


# ── SECTOR TREND ──────────────────────────────────────────────────────────────

@app.get(
    "/sector/{sector_name}/trend",
    tags=["sector", "history"],
    summary="Sector heat trend over tijd",
    operation_id="get_sector_trend",
)
def sector_trend(
    sector_name: str,
    limit:       int = Query(20, description="Max snapshots", ge=1, le=100),
) -> JSONResponse:
    """
    Sector heat en momentum trend over tijd.
    Data wordt opgebouwd via /sector/{sector_name} calls.

    `is_heating_up: true` als recente heat hoger is dan eerdere heat.
    """
    evolution = get_sector_evolution(sector_name.lower(), limit=limit)
    return JSONResponse(content={**evolution, "analyzed_at": datetime.now(timezone.utc).isoformat()})


# ── CACHE ENDPOINTS ───────────────────────────────────────────────────────────

@app.get("/cache/stats", tags=["cache"], summary="Cache statistieken",
         operation_id="get_cache_stats")
def get_cache_stats() -> dict:
    """Live cache statistieken + storage overzicht."""
    tracked = list_tracked_tickers()
    stats   = cache_stats()
    stats["history"] = {
        "tracked_tickers": len(tracked),
        "tickers": sorted(tracked),
    }
    return stats


@app.delete("/cache/{ticker}", tags=["cache"], summary="Invalideer cache",
            operation_id="invalidate_ticker_cache")
def invalidate_ticker(ticker: str) -> dict:
    """Verwijdert cache entry. Historische data blijft intact."""
    ticker  = ticker.upper().strip()
    removed = invalidate(ticker)
    return {
        "ticker":  ticker,
        "removed": removed,
        "message": f"Cache {'verwijderd' if removed else 'niet aanwezig'} voor {ticker}. "
                   f"Historische data blijft intact.",
    }
