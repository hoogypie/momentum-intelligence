"""
schemas/api_error.py
Typed API error responses — v2.1

Alle foutresponses gebruiken ApiError als standaard formaat.
HTTP status codes:
    400  Ongeldige input (malformed ticker)
    422  Ticker niet gevonden / data ontbreekt
    429  Rate limit bereikt (Yahoo Finance)
    500  Interne serverfout
"""

from pydantic import BaseModel
from typing import Optional
from enum import Enum


class ErrorCode(str, Enum):
    INVALID_TICKER    = "INVALID_TICKER"    # Ongeldige ticker syntax
    TICKER_NOT_FOUND  = "TICKER_NOT_FOUND"  # Ticker bestaat niet op Yahoo
    DATA_UNAVAILABLE  = "DATA_UNAVAILABLE"  # Prijs nul, geen data
    RATE_LIMITED      = "RATE_LIMITED"      # Yahoo rate limit bereikt
    FETCH_ERROR       = "FETCH_ERROR"       # Netwerk / timeout fout
    INTERNAL_ERROR    = "INTERNAL_ERROR"    # Onverwachte serverfout


class ApiError(BaseModel):
    """
    Gestandaardiseerde foutresponse voor alle API endpoints.
    Consistente structuur zodat clients altijd weten wat ze kunnen verwachten.
    """
    error:   ErrorCode
    ticker:  Optional[str] = None
    message: str
    hint:    Optional[str] = None


# ── FACTORY FUNCTIES ──────────────────────────────────────────────────────────

def invalid_ticker(ticker: str) -> ApiError:
    return ApiError(
        error=ErrorCode.INVALID_TICKER,
        ticker=ticker,
        message=f"Ongeldige ticker: '{ticker}'. Gebruik alleen letters (bijv. NVDA).",
        hint="Ticker mag alleen letters bevatten, geen cijfers of tekens.",
    )


def ticker_not_found(ticker: str) -> ApiError:
    return ApiError(
        error=ErrorCode.TICKER_NOT_FOUND,
        ticker=ticker,
        message=f"Geen koersdata gevonden voor '{ticker}'.",
        hint="Controleer spelling. Niet-US tickers zijn mogelijk niet beschikbaar.",
    )


def data_unavailable(ticker: str, detail: str) -> ApiError:
    return ApiError(
        error=ErrorCode.DATA_UNAVAILABLE,
        ticker=ticker,
        message=f"Onvoldoende data voor scoring van '{ticker}'.",
        hint=detail,
    )


def rate_limited(ticker: str) -> ApiError:
    return ApiError(
        error=ErrorCode.RATE_LIMITED,
        ticker=ticker,
        message="Yahoo Finance rate limit bereikt. Probeer opnieuw over 60 seconden.",
        hint="Verspreid verzoeken. Maximaal ~30 tickers per minuut aanbevolen.",
    )


def fetch_error(ticker: str, detail: str) -> ApiError:
    return ApiError(
        error=ErrorCode.FETCH_ERROR,
        ticker=ticker,
        message=f"Data ophalen mislukt voor '{ticker}'.",
        hint=detail,
    )


def internal_error(ticker: str, detail: str) -> ApiError:
    return ApiError(
        error=ErrorCode.INTERNAL_ERROR,
        ticker=ticker,
        message="Interne serverfout.",
        hint=detail,
    )
