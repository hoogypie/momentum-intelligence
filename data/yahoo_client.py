"""
data/yahoo_client.py
Yahoo Finance data client — v2.1

Wijzigingen t.o.v. v2.0:
    - Retourneert TickerSnapshot i.p.v. QuoteData
    - Retry met exponential backoff (max 3 pogingen)
    - Rate limit detectie (429 / herhaalde 403)
    - DataConfidence label per snapshot
    - Timestamp op elke response
    - Veld-validatie via Pydantic (ongeldig float → None)
    - Nooit een exception gooien — altijd TickerSnapshot terug

Retry strategie:
    Poging 1: direct
    Poging 2: 0.5s wachten
    Poging 3: 1.5s wachten
    Na 3 pogingen: MISSING snapshot teruggeven

Rate limiting:
    Yahoo geeft 429 of herhaalde 403 bij te veel requests.
    Detectie via error keywords, cooldown prep in cache/market_cache.py.
    In v2.1 nog geen automatische cooldown — alleen detectie + melding.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional

from schemas.ticker_snapshot import (
    TickerSnapshot, DataConfidence, determine_confidence
)

logger = logging.getLogger(__name__)

# Retry configuratie
_MAX_RETRIES   = 3
_BACKOFF_SECS  = [0.0, 0.5, 1.5]   # wachttijd per poging


# ── RATE LIMIT DETECTIE ────────────────────────────────────────────────────────

class YahooRateLimitError(Exception):
    """Raised wanneer Yahoo Finance rate limiting detecteert."""


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in [
        "429", "too many requests", "rate limit", "rate_limit"
    ])


def _is_auth_error(exc: Exception) -> bool:
    """403 kan rate limit of geo-blocking zijn."""
    return "403" in str(exc) or "forbidden" in str(exc).lower()


# ── CORE FETCH ─────────────────────────────────────────────────────────────────

def _fetch_once(ticker: str) -> TickerSnapshot:
    """
    Één ophaalpoging zonder retry. Gooit exceptions door naar caller.
    """
    import yfinance as yf

    t  = yf.Ticker(ticker)
    fi = t.fast_info

    price      = _safe_float(fi, "last_price",      0.0)
    prev_close = _safe_float(fi, "previous_close",  price)

    day_change_pct = (
        ((price - prev_close) / prev_close * 100)
        if prev_close > 0 else 0.0
    )

    # Pre-market
    pm_price = _safe_float(fi, "pre_market_price", None)
    if pm_price and prev_close > 0:
        premarket_pct = (pm_price - prev_close) / prev_close * 100
        premarket_available = True
    else:
        premarket_pct = 0.0
        premarket_available = False

    # Volume (1 maand history voor gemiddelde)
    hist = t.history(period="1mo", auto_adjust=True)
    if not hist.empty:
        avg_volume_20d = max(int(hist["Volume"].mean()), 1)
        volume_today   = int(hist["Volume"].iloc[-1])
    else:
        avg_volume_20d = 1
        volume_today   = 0

    # Market cap + float
    market_cap   = _safe_float(fi, "market_cap",          None)
    shares_out   = _safe_float(fi, "shares_outstanding",  None)
    float_shares = int(shares_out) if shares_out and shares_out > 0 else None

    confidence = determine_confidence(
        price=price,
        volume_today=volume_today,
        market_cap=market_cap,
        float_shares=float_shares,
        premarket_available=premarket_available,
        error=None,
    )

    return TickerSnapshot(
        ticker=ticker.upper(),
        timestamp=datetime.now(timezone.utc),
        confidence=confidence,
        price=round(price, 2),
        prev_close=round(prev_close, 2),
        day_change_pct=round(day_change_pct, 2),
        premarket_price=round(pm_price, 2) if pm_price else None,
        premarket_pct=round(premarket_pct, 2),
        premarket_available=premarket_available,
        volume_today=volume_today,
        avg_volume_20d=avg_volume_20d,
        market_cap=float(market_cap) if market_cap and market_cap > 0 else None,
        float_shares=float_shares,
    )


# ── PUBLIC INTERFACE ───────────────────────────────────────────────────────────

def get_snapshot(ticker: str) -> TickerSnapshot:
    """
    Haalt marktdata op met retry + exponential backoff.

    Altijd een TickerSnapshot terug — nooit een exception.
    Bij alle fouten: MISSING snapshot met error veld ingevuld.

    Retry strategie: max 3 pogingen, 0s / 0.5s / 1.5s backoff.
    """
    ticker = ticker.upper().strip()
    last_error: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        wait = _BACKOFF_SECS[attempt]
        if wait > 0:
            time.sleep(wait)

        try:
            snapshot = _fetch_once(ticker)
            if attempt > 0:
                logger.info(f"yahoo: {ticker} opgehaald na {attempt + 1} pogingen")
            snapshot.retries_used = attempt
            return snapshot

        except Exception as exc:
            last_error = exc

            if _is_rate_limited(exc):
                logger.warning(f"yahoo: rate limit bereikt voor {ticker}")
                # Cache cooldown prep (actief in v2.2)
                # from cache.market_cache import set_cooldown
                # set_cooldown(ticker, seconds=60)
                break   # Niet opnieuw proberen bij rate limit

            if _is_auth_error(exc) and attempt == 0:
                logger.warning(f"yahoo: 403 voor {ticker}, één retry")
                continue

            logger.debug(
                f"yahoo: poging {attempt + 1}/{_MAX_RETRIES} mislukt voor "
                f"{ticker}: {exc}"
            )

    # Alle pogingen mislukt
    error_msg = str(last_error) if last_error else "Onbekende fout"
    logger.warning(f"yahoo: {ticker} mislukt na {_MAX_RETRIES} pogingen: {error_msg}")

    return TickerSnapshot(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.MISSING,
        price=0.0,
        prev_close=0.0,
        day_change_pct=0.0,
        premarket_pct=0.0,
        premarket_available=False,
        volume_today=0,
        avg_volume_20d=1,
        error=error_msg,
        retries_used=_MAX_RETRIES - 1,
    )


def get_spy_return() -> float:
    """
    Dagsrendement van SPY voor relative strength.
    Geeft 0.0 bij fout.
    """
    try:
        import yfinance as yf
        fi = yf.Ticker("SPY").fast_info
        price      = _safe_float(fi, "last_price",     0.0)
        prev_close = _safe_float(fi, "previous_close", price)
        if prev_close > 0:
            return round((price - prev_close) / prev_close * 100, 2)
    except Exception as exc:
        logger.debug(f"yahoo: SPY ophalen mislukt: {exc}")
    return 0.0


# ── HELPERS ────────────────────────────────────────────────────────────────────

def _safe_float(obj, attr: str, default):
    """Attribuut ophalen zonder crash bij None of ontbrekend veld."""
    try:
        val = getattr(obj, attr, default)
        if val is None:
            return default
        f = float(val)
        # NaN en Inf zijn ongeldige marktdata
        if f != f or f == float("inf") or f == float("-inf"):
            return default
        return f
    except (TypeError, ValueError):
        return default
