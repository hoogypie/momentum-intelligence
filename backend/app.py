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
from typing import Optional
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


# ── REPLAY ENDPOINTS (v2.6) ───────────────────────────────────────────────────

from storage.replay_engine  import replay_ticker, replay_sector, replay_session
from storage.timeline       import (
    get_ticker_summary, score_timeline, confidence_history,
    get_all_tracked_summaries,
)
from storage.snapshot_diff  import diff_series, find_significant_changes
from research.observation_store import (
    save_replay_note, save_signal_review, list_replay_notes,
    list_signal_reviews, list_observations, create_observation_template,
)


@app.get(
    "/replay/ticker/{ticker}",
    tags=["history"],
    summary="Volledige ticker replay met diffs",
    operation_id="replay_ticker_endpoint",
)
def replay_ticker_endpoint(
    ticker: str,
    limit:  int   = Query(100, description="Max snapshots",   ge=1, le=500),
    hours:  float = Query(None, description="Filter op uren", ge=1, le=720),
    export: bool  = Query(False, description="Sla replay op in research/"),
) -> JSONResponse:
    """
    Volledige replay van een ticker: snapshots, diffs, significante
    veranderingen, score-tijdlijn, fase-history, effective signals.

    Optioneel: `export=true` slaat replay op in research/replay_notes/.
    """
    ticker = ticker.upper().strip()

    if not ticker.replace("-", "").isalpha():
        raise HTTPException(400, detail=invalid_ticker(ticker).model_dump())

    data = replay_ticker(ticker, limit=limit, hours=hours)

    if data["snapshot_count"] == 0:
        raise HTTPException(404, detail={
            "error":   "NO_HISTORY",
            "ticker":  ticker,
            "message": f"Geen snapshots voor {ticker}. "
                       f"Roep /analyze/{ticker} aan om tracking te starten.",
        })

    if export:
        path = save_replay_note(ticker, data)
        data["exported_to"] = path
        logger.info(f"replay: {ticker} geëxporteerd naar {path}")

    return JSONResponse(content={**data, "replayed_at": datetime.now(timezone.utc).isoformat()})


@app.get(
    "/replay/sector/{sector}",
    tags=["sector", "history"],
    summary="Sector replay met leader performance",
    operation_id="replay_sector_endpoint",
)
def replay_sector_endpoint(
    sector: str,
    limit:  int  = Query(50, description="Max sector snapshots", ge=1, le=200),
    export: bool = Query(False, description="Sla replay op in research/"),
) -> JSONResponse:
    """
    Sector replay: heat trend, leader scores over tijd, heat delta.

    Berekent automatisch `heat_delta` (recente heat minus oudste heat).
    Positief = sector warmt op. Negatief = sector koelt af.
    """
    data = replay_sector(sector.lower(), limit=limit)

    if export:
        path = save_replay_note(f"SECTOR_{sector.upper()}", data)
        data["exported_to"] = path

    return JSONResponse(content={**data, "replayed_at": datetime.now(timezone.utc).isoformat()})


@app.get(
    "/replay/session/{date}",
    tags=["history"],
    summary="Sessie replay voor een specifieke datum",
    operation_id="replay_session_endpoint",
)
def replay_session_endpoint(
    date: str,
    max_tickers: int  = Query(50, description="Max tickers om te scannen",
                               ge=1, le=200),
    export:      bool = Query(False),
) -> JSONResponse:
    """
    Alle activiteit van een specifieke datum.

    `date` formaat: YYYY-MM-DD (bijv. 2026-05-28)

    Returns per ticker: snapshots, peak_score, peak_decision.
    Includeert een session summary: beste ticker van de dag.
    """
    data = replay_session(date, max_tickers=max_tickers)

    if "error" in data:
        raise HTTPException(400, detail=data)

    if export and data["total_snapshots"] > 0:
        path = save_replay_note(f"SESSION_{date}", data)
        data["exported_to"] = path

    return JSONResponse(content={**data, "replayed_at": datetime.now(timezone.utc).isoformat()})


@app.get(
    "/replay/summary",
    tags=["history"],
    summary="Overzicht van alle getrackte tickers",
    operation_id="replay_summary",
)
def replay_summary_endpoint() -> JSONResponse:
    """
    Samenvatting van alle tickers die ooit gescoord zijn.
    Per ticker: snapshot count, current decision, score range, days tracked.
    """
    summaries = get_all_tracked_summaries()
    return JSONResponse(content={
        "ticker_count": len(summaries),
        "tickers":      summaries,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@app.get(
    "/replay/ticker/{ticker}/diff",
    tags=["history"],
    summary="Snapshot diffs voor één ticker",
    operation_id="ticker_diffs",
)
def ticker_diffs(
    ticker:      str,
    limit:       int  = Query(50, description="Max snapshots voor diff"),
    significant: bool = Query(False, description="Alleen significante veranderingen"),
) -> JSONResponse:
    """
    Berekent diffs tussen opeenvolgende snapshots.

    `significant=true` filtert op veranderingen waarbij:
    - De beslissing veranderde
    - De fase veranderde
    - De score met ≥10 punten veranderde
    - Een nieuw catalyst verscheen
    """
    from storage.snapshot_store import load_snapshots
    from storage.snapshot_diff  import diff_series, find_significant_changes
    import dataclasses

    ticker = ticker.upper()
    snaps  = load_snapshots(ticker, limit=limit)
    diffs  = diff_series(snaps)

    if significant:
        diffs = find_significant_changes(diffs)

    return JSONResponse(content={
        "ticker":       ticker,
        "diff_count":   len(diffs),
        "diffs":        [dataclasses.asdict(d) for d in diffs],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ── EVALUATION ENDPOINTS (v2.7) ───────────────────────────────────────────────

from storage.signal_evaluator import (
    evaluate_ticker as _evaluate_ticker,
    compute_signal_statistics,
    compute_global_statistics,
    get_top_signals,
    GRADE_SUCCESS, GRADE_FAILED,
)
from storage.evaluation_store import (
    load_outcomes, load_graded_outcomes, list_evaluated_tickers,
)
from research.evaluation_report import (
    export_evaluation_json, export_markdown_report, global_summary_report,
)


@app.post(
    "/evaluation/run/{ticker}",
    tags=["history"],
    summary="Voer signal evaluatie uit voor één ticker",
    operation_id="run_evaluation",
)
def run_evaluation(
    ticker: str,
    limit:  int  = Query(200, description="Max snapshots om te evalueren"),
    export: bool = Query(False, description="Exporteer resultaat naar research/"),
) -> JSONResponse:
    """
    Evalueert alle opgeslagen signalen voor een ticker.

    Vergelijkt elke snapshot-prijs met toekomstige snapshot-prijzen
    om te bepalen of het signaal correct was.

    Grades:
    - **SUCCESS** — BUY signaal gevolgd door ≥+3% in 24u
    - **FAILED**  — BUY signaal gevolgd door ≤-3% in 24u
    - **NEUTRAL** — Geen duidelijke follow-through
    - **PENDING** — Nog geen toekomstige data beschikbaar

    Na het uitvoeren: gebruik `/evaluation/ticker/{ticker}` om resultaten op te halen.
    """
    ticker = ticker.upper().strip()
    if not ticker.replace("-", "").isalpha():
        raise HTTPException(400, detail=invalid_ticker(ticker).model_dump())

    result = _evaluate_ticker(ticker, limit=limit)

    if result["evaluated"] + result["pending"] == 0:
        raise HTTPException(404, detail={
            "error":   "NO_SNAPSHOTS",
            "ticker":  ticker,
            "message": f"Geen snapshots voor {ticker}. Roep /analyze/{ticker} aan.",
        })

    if export and result["evaluated"] > 0:
        stats = compute_signal_statistics(ticker)
        path  = export_evaluation_json(ticker, stats, result["outcomes"])
        result["exported_to"] = path

    return JSONResponse(content={
        **result,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "outcomes":     result["outcomes"][:20],  # Max 20 in response
    })


@app.get(
    "/evaluation/ticker/{ticker}",
    tags=["history"],
    summary="Evaluatieresultaten voor één ticker",
    operation_id="get_ticker_evaluation",
    responses={404: {"description": "Geen evaluaties beschikbaar"}},
)
def get_ticker_evaluation(
    ticker:       str,
    include_pending: bool = Query(False, description="Inclusief PENDING outcomes"),
    export:          bool = Query(False),
) -> JSONResponse:
    """
    Geeft evaluatieresultaten en statistieken voor één ticker.

    Roep eerst `/evaluation/run/{ticker}` aan om evaluatie te triggeren.
    """
    ticker = ticker.upper().strip()

    outcomes = (
        load_outcomes(ticker)
        if include_pending
        else load_graded_outcomes(ticker)
    )

    if not outcomes:
        raise HTTPException(404, detail={
            "error":   "NO_EVALUATIONS",
            "ticker":  ticker,
            "message": f"Geen evaluaties voor {ticker}. Roep /evaluation/run/{ticker} aan.",
        })

    stats = compute_signal_statistics(ticker)

    if export:
        export_evaluation_json(ticker, stats, outcomes)
        export_markdown_report(ticker, stats, outcomes)

    return JSONResponse(content={
        "ticker":      ticker,
        "statistics":  stats,
        "outcomes":    outcomes[:50],
        "total_count": len(outcomes),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@app.get(
    "/evaluation/session/{date}",
    tags=["history"],
    summary="Evaluaties van een specifieke sessie/dag",
    operation_id="get_session_evaluation",
)
def get_session_evaluation(date: str) -> JSONResponse:
    """
    Alle evaluaties van signalen die op een specifieke datum zijn opgeslagen.

    `date` formaat: YYYY-MM-DD

    Combineert replay (wat er die dag was) met evaluatie (wat er daarna
    is gebeurd), zodat je direct kunt zien welke signalen die dag
    daadwerkelijk hebben gevolgd.
    """
    from storage.replay_engine import replay_session

    # Valideer datum
    try:
        from datetime import datetime as _dt
        session_date = _dt.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, detail={
            "error": "INVALID_DATE",
            "message": f"Ongeldig datum formaat: '{date}'. Verwacht YYYY-MM-DD.",
        })

    # Alle tickers die die dag actief waren
    replay = replay_session(date)
    active_tickers = list(replay.get("session_by_ticker", {}).keys())

    session_results = []
    for ticker in active_tickers:
        outcomes = load_graded_outcomes(ticker)
        # Filter op outcomes van die dag
        day_outcomes = [
            o for o in outcomes
            if o.get("timestamp", "").startswith(date)
        ]
        if day_outcomes:
            stats = compute_signal_statistics(ticker)
            session_results.append({
                "ticker":    ticker,
                "outcomes":  day_outcomes,
                "day_count": len(day_outcomes),
                "day_success": sum(1 for o in day_outcomes if o.get("grade") == "SUCCESS"),
            })

    session_results.sort(key=lambda x: x["day_success"], reverse=True)

    return JSONResponse(content={
        "date":             date,
        "tickers_with_evaluations": len(session_results),
        "session_results":  session_results,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
    })


@app.get(
    "/evaluation/top-signals",
    tags=["history"],
    summary="Beste en slechtste signalen",
    operation_id="get_top_signals",
)
def get_top_signals_endpoint(
    n:    int  = Query(10, description="Aantal resultaten", ge=1, le=50),
    best: bool = Query(True, description="True = beste, False = slechtste"),
) -> JSONResponse:
    """
    De N beste (of slechtste) signalen over alle geëvalueerde tickers.

    `best=true`  → gesorteerd op return_1d (hoogste eerst)
    `best=false` → gesorteerd op return_1d (laagste eerst)

    Nuttig voor: "Welke combinaties van fase + catalyst + score werken het best?"
    """
    grade   = GRADE_SUCCESS if best else GRADE_FAILED
    signals = get_top_signals(n=n, grade=grade)

    return JSONResponse(content={
        "type":           "BEST" if best else "WORST",
        "count":          len(signals),
        "signals":        signals,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    })


@app.get(
    "/evaluation/stats",
    tags=["history"],
    summary="Globale evaluatiestatistieken",
    operation_id="get_evaluation_stats",
)
def get_evaluation_stats(export: bool = Query(False)) -> JSONResponse:
    """
    Aggregeert evaluatiestatistieken over alle geëvalueerde tickers.

    Toont success rate per fase, per catalyst type en per beslissing.
    Dit is de snelste manier om te zien welke setup-combinaties
    historisch het beste hebben gewerkt.
    """
    stats = compute_global_statistics()

    if export:
        now  = datetime.now(timezone.utc)
        path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..", "research", "signal_reviews",
            f"GLOBAL_eval_{now.strftime('%Y%m%d_%H%M%S')}.md"
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w") as f:
                f.write(global_summary_report(stats))
        except Exception:
            pass

    return JSONResponse(content={
        **stats,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


# ── ALERTING & WATCHLIST ENDPOINTS (v2.9) ────────────────────────────────────

from alerting.alert_store       import (
    load_alerts, load_recent_alerts, list_alerted_tickers,
    count_alerts_by_severity,
)
from alerting.alert_engine      import scan_ticker, scan_all_watchlists
from alerting.cooldown_manager  import cooldown_stats, clear_all_cooldowns
from alerting.watchlist_manager import (
    list_watchlists, load_watchlist, create_watchlist,
    add_ticker as wl_add, remove_ticker as wl_remove, delete_watchlist,
    get_all_watchlist_tickers, get_ticker_watchlists,
)


@app.get(
    "/alerts",
    tags=["alerts"],
    summary="Recente alerts",
    operation_id="get_alerts",
)
def get_alerts(
    ticker:   Optional[str] = Query(None,   description="Filter op ticker"),
    severity: Optional[str] = Query(None,   description="Minimum severity (INFO/WATCH/HIGH/CRITICAL)"),
    hours:    float          = Query(24.0,   description="Tijdvenster in uren"),
    limit:    int            = Query(50,     description="Max aantal alerts", ge=1, le=200),
) -> JSONResponse:
    """
    Geeft recente alerts terug, optioneel gefilterd op ticker en severity.

    Alerts worden automatisch gegenereerd na elke `/analyze` call
    en bij `/alerts/scan`.
    """
    if ticker:
        alerts = load_alerts(
            ticker=ticker.upper(),
            severity=severity,
            limit=limit,
        )
    else:
        alerts = load_recent_alerts(hours=hours, limit=limit)
        if severity:
            from alerting.alert_store import severity_rank
            min_rank = severity_rank(severity)
            alerts   = [a for a in alerts
                        if severity_rank(a.get("severity", "INFO")) >= min_rank]
            alerts   = alerts[:limit]

    return JSONResponse(content={
        "count":        len(alerts),
        "alerts":       alerts,
        "filter_ticker": ticker,
        "filter_severity": severity,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    })


@app.post(
    "/alerts/scan",
    tags=["alerts"],
    summary="Trigger alert scan voor alle watchlist-tickers",
    operation_id="scan_alerts",
)
def trigger_alert_scan() -> JSONResponse:
    """
    Scant alle tickers in alle watchlists op nieuwe alerts.

    Vergelijkt de twee meest recente snapshots per ticker.
    Vereist minimaal 2 opgeslagen snapshots per ticker
    (via eerdere `/analyze` calls).
    """
    result = scan_all_watchlists()
    total  = sum(len(v) for v in result.values())

    return JSONResponse(content={
        "tickers_scanned": len(get_all_watchlist_tickers()),
        "tickers_with_alerts": len(result),
        "total_alerts_fired":  total,
        "by_ticker":           result,
        "scanned_at":          datetime.now(timezone.utc).isoformat(),
    })


@app.post(
    "/alerts/scan/{ticker}",
    tags=["alerts"],
    summary="Scan één ticker op alerts",
    operation_id="scan_ticker_alerts",
)
def scan_one_ticker(ticker: str) -> JSONResponse:
    """
    Scant één ticker op nieuwe alerts.
    Werkt ook als de ticker niet in een watchlist staat.
    """
    ticker = ticker.upper().strip()
    if not ticker.replace("-", "").isalpha():
        raise HTTPException(400, detail=invalid_ticker(ticker).model_dump())

    from dataclasses import asdict
    wl_names = get_ticker_watchlists(ticker)
    wl_config = None
    if wl_names:
        wl_config = load_watchlist(wl_names[0])

    fired = scan_ticker(ticker, wl_config, wl_names[0] if wl_names else None)

    return JSONResponse(content={
        "ticker":          ticker,
        "alerts_fired":    len(fired),
        "watchlists":      wl_names,
        "alerts":          [asdict(a) for a in fired],
        "scanned_at":      datetime.now(timezone.utc).isoformat(),
    })


# ── WATCHLIST ENDPOINTS ───────────────────────────────────────────────────────

@app.get(
    "/watchlists",
    tags=["alerts"],
    summary="Overzicht van alle watchlists",
    operation_id="get_watchlists",
)
def get_watchlists() -> JSONResponse:
    """
    Geeft alle watchlists terug (ingebouwd + custom).

    Ingebouwde watchlists: core, momentum, sector_rotation.
    Custom watchlists: aangemaakt via POST /watchlists.
    """
    wls = list_watchlists()
    return JSONResponse(content={
        "count":      len(wls),
        "watchlists": wls,
        "all_tickers": get_all_watchlist_tickers(),
    })


@app.get(
    "/watchlists/{name}",
    tags=["alerts"],
    summary="Specifieke watchlist ophalen",
    operation_id="get_watchlist",
    responses={404: {"description": "Watchlist niet gevonden"}},
)
def get_watchlist(name: str) -> JSONResponse:
    """Geeft details van één watchlist incl. alle alert-instellingen."""
    wl = load_watchlist(name.lower())
    if not wl:
        raise HTTPException(404, detail={
            "error":   "WATCHLIST_NOT_FOUND",
            "name":    name,
            "message": f"Watchlist '{name}' niet gevonden.",
        })
    return JSONResponse(content=wl)


@app.post(
    "/watchlists",
    tags=["alerts"],
    summary="Maak nieuwe custom watchlist aan",
    operation_id="create_watchlist",
)
def create_watchlist_endpoint(
    name:        str = Query(..., description="Naam (a-z, 0-9, _)"),
    description: str = Query("",  description="Omschrijving"),
    tickers:     str = Query("",  description="Komma-gescheiden tickers"),
) -> JSONResponse:
    """
    Maakt een nieuwe custom watchlist aan.

    Namen mogen alleen kleine letters, cijfers en underscores bevatten.
    Tickers worden automatisch in hoofdletters omgezet.
    """
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    try:
        wl = create_watchlist(name.lower(), description, ticker_list)
        return JSONResponse(content={
            "created": True,
            "watchlist": wl,
        })
    except ValueError as exc:
        raise HTTPException(400, detail={"error": "INVALID_WATCHLIST", "message": str(exc)})


@app.post(
    "/watchlists/{name}/add",
    tags=["alerts"],
    summary="Voeg ticker toe aan watchlist",
    operation_id="add_to_watchlist",
)
def add_ticker_to_watchlist(
    name:   str,
    ticker: str = Query(..., description="Ticker om toe te voegen"),
) -> JSONResponse:
    """Voegt een ticker toe aan een bestaande watchlist."""
    try:
        wl = wl_add(name.lower(), ticker)
        return JSONResponse(content={"updated": True, "watchlist": wl})
    except ValueError as exc:
        raise HTTPException(400, detail={"error": "WATCHLIST_ERROR", "message": str(exc)})


@app.post(
    "/watchlists/{name}/remove",
    tags=["alerts"],
    summary="Verwijder ticker van watchlist",
    operation_id="remove_from_watchlist",
)
def remove_ticker_from_watchlist(
    name:   str,
    ticker: str = Query(..., description="Ticker om te verwijderen"),
) -> JSONResponse:
    """Verwijdert een ticker van een watchlist."""
    try:
        wl = wl_remove(name.lower(), ticker)
        return JSONResponse(content={"updated": True, "watchlist": wl})
    except ValueError as exc:
        raise HTTPException(400, detail={"error": "WATCHLIST_ERROR", "message": str(exc)})
