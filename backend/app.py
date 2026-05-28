"""
backend/app.py
Momentum Intelligence API — v2.0

Endpoints:
    GET /health             Liveness check + versie-info
    GET /analyze/{ticker}   Volledige momentum scoring voor één ticker

Geen AI narrative, geen frontend, geen auth.
Output: ScoringResult als JSON + data quality metadata.

Starten:
    uvicorn backend.app:app --reload --port 8000

Aanroepen:
    curl http://localhost:8000/health
    curl http://localhost:8000/analyze/NVDA
"""

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
import dataclasses
import enum
import logging
from datetime import datetime, timezone

from data.assembler import build_ticker_input
from scoring.scoring_v1_2 import score_ticker

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Momentum Intelligence API",
    description="Score engine v1.2 — geen AI, pure formules",
    version="2.0.0",
)


# ── SERIALISATIE ──────────────────────────────────────────────────────────────

def _serialize(obj) -> dict | list | str | float | int | bool | None:
    """
    Recursieve serialisatie van dataclasses + Enums naar JSON-compatibele types.
    FastAPI kan dataclasses niet direct serialiseren als ze geneste Enums bevatten.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(i) for i in obj]
    return obj


# ── HEALTH ENDPOINT ───────────────────────────────────────────────────────────

@app.get("/health")
def health() -> dict:
    """
    Liveness check.

    Response:
        status          "ok"
        version         API versie
        engine          Score engine versie
        timestamp       UTC ISO timestamp
        data_sources    Actieve data bronnen
        limitations     Bekende beperkingen in deze versie
    """
    return {
        "status": "ok",
        "version": "2.0.0",
        "engine": "scoring_v1_2",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data_sources": {
            "price_volume": "yahoo_finance (unofficial)",
            "news":         "placeholder (fase 2.1: Finnhub)",
            "social":       "placeholder (fase 2.1: StockTwits)",
        },
        "limitations": [
            "catalyst_type altijd NONE (news_client placeholder)",
            "social_acceleration altijd 0 (geen StockTwits key)",
            "has_sec_investigation altijd False (handmatige check)",
            "float_shares via shares_outstanding (benadering)",
        ],
    }


# ── ANALYZE ENDPOINT ──────────────────────────────────────────────────────────

@app.get("/analyze/{ticker}")
def analyze(ticker: str) -> JSONResponse:
    """
    Volledige momentum scoring voor één ticker.

    Path parameter:
        ticker      US equity ticker symbol (bijv. NVDA, AAPL, UMAC)

    Response:
        ScoringResult velden (zie scoring_v1_2.py)
        + data_quality: transparantie over beschikbare data

    Errors:
        400     Lege ticker
        422     Ticker niet gevonden op Yahoo Finance
        500     Onverwachte fout
    """
    ticker = ticker.upper().strip()

    if not ticker or not ticker.isalpha():
        raise HTTPException(
            status_code=400,
            detail=f"Ongeldige ticker: '{ticker}'. Gebruik alleen letters (bijv. NVDA)."
        )

    logger.info(f"analyze: ophalen data voor {ticker}")

    try:
        ticker_input, quality = build_ticker_input(ticker)

        # Controleer of data succesvol opgehaald werd
        if quality.get("fetch_error"):
            raise HTTPException(
                status_code=422,
                detail={
                    "error":   "Data ophalen mislukt",
                    "ticker":  ticker,
                    "message": quality["fetch_error"],
                    "hint":    "Controleer of de ticker correct is en beschikbaar op Yahoo Finance.",
                }
            )

        if ticker_input.price == 0.0:
            raise HTTPException(
                status_code=422,
                detail={
                    "error":   "Ticker niet gevonden of geen koersdata",
                    "ticker":  ticker,
                    "hint":    "Controleer spelling. Sommige niet-US tickers zijn niet beschikbaar.",
                }
            )

        # Score berekenen
        result = score_ticker(ticker_input)

        # Response samenstellen
        response_data = _serialize(result)
        response_data["data_quality"] = quality
        response_data["analyzed_at"]  = datetime.now(timezone.utc).isoformat()

        logger.info(
            f"analyze: {ticker} → {result.decision.value} "
            f"(momentum={result.momentum_score:.1f}, skip={result.skip_score})"
        )

        return JSONResponse(content=response_data)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"analyze: onverwachte fout bij {ticker}: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error":   "Interne serverfout",
                "ticker":  ticker,
                "message": str(exc),
            }
        )
