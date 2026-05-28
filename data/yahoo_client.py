"""
data/yahoo_client.py
Yahoo Finance data client — v2.2

Wijzigingen t.o.v. v2.1:
    - Cache integratie: check → fetch → store
    - Fallback: Yahoo faalt → stale cache leveren
    - FreshnessInfo meegestuurd per snapshot
    - force_refresh flag voor cache bypass
    - Confidence combineert veld-kwaliteit + cache-leeftijd
"""

import time
import logging
from datetime import datetime, timezone
from typing import Optional

from schemas.ticker_snapshot import (
    TickerSnapshot, DataConfidence,
    determine_confidence, age_to_confidence, worst_confidence,
    FreshnessInfo,
)
from cache.market_cache import (
    get_cached, set_cached, set_cooldown, is_cooling_down,
    get_market_ttl, CACHE_ENABLED,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES  = 3
_BACKOFF_SECS = [0.0, 0.5, 1.5]


# ── RATE LIMIT DETECTIE ───────────────────────────────────────────────────────

class YahooRateLimitError(Exception):
    pass


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in ["429", "too many requests", "rate limit"])


def _is_auth_error(exc: Exception) -> bool:
    return "403" in str(exc) or "forbidden" in str(exc).lower()


# ── SNAPSHOT → DICT (voor cache opslag) ──────────────────────────────────────

def _snap_to_dict(s: TickerSnapshot) -> dict:
    return {
        "price":               s.price,
        "prev_close":          s.prev_close,
        "day_change_pct":      s.day_change_pct,
        "premarket_price":     s.premarket_price,
        "premarket_pct":       s.premarket_pct,
        "premarket_available": s.premarket_available,
        "volume_today":        s.volume_today,
        "avg_volume_20d":      s.avg_volume_20d,
        "market_cap":          s.market_cap,
        "float_shares":        s.float_shares,
    }


def _dict_to_snap(ticker: str, d: dict, age: float, ttl_rem: float) -> TickerSnapshot:
    """Herbouwt TickerSnapshot van cache-dict met juiste confidence labels."""
    field_conf = determine_confidence(
        price=d.get("price", 0.0),
        volume_today=d.get("volume_today", 0),
        market_cap=d.get("market_cap"),
        float_shares=d.get("float_shares"),
        premarket_available=d.get("premarket_available", False),
        error=None,
    )
    age_conf  = age_to_confidence(age)
    final_conf = worst_confidence(field_conf, age_conf)

    return TickerSnapshot(
        ticker=ticker.upper(),
        timestamp=datetime.now(timezone.utc),
        confidence=final_conf,
        price=d.get("price", 0.0),
        prev_close=d.get("prev_close", 0.0),
        day_change_pct=d.get("day_change_pct", 0.0),
        premarket_price=d.get("premarket_price"),
        premarket_pct=d.get("premarket_pct", 0.0),
        premarket_available=d.get("premarket_available", False),
        volume_today=d.get("volume_today", 0),
        avg_volume_20d=max(d.get("avg_volume_20d", 1), 1),
        market_cap=d.get("market_cap"),
        float_shares=d.get("float_shares"),
        cache_hit=True,
        data_age_seconds=round(age, 1),
    )


# ── LIVE FETCH ────────────────────────────────────────────────────────────────

def _fetch_once(ticker: str) -> TickerSnapshot:
    """Één live ophaalpoging. Gooit exceptions door naar caller."""
    import yfinance as yf

    t  = yf.Ticker(ticker)
    fi = t.fast_info

    price      = _safe_float(fi, "last_price",     0.0)
    prev_close = _safe_float(fi, "previous_close", price)

    day_change_pct = (
        ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
    )

    pm_price = _safe_float(fi, "pre_market_price", None)
    if pm_price and prev_close > 0:
        premarket_pct       = (pm_price - prev_close) / prev_close * 100
        premarket_available = True
    else:
        premarket_pct       = 0.0
        premarket_available = False

    hist = t.history(period="1mo", auto_adjust=True)
    if not hist.empty:
        avg_volume_20d = max(int(hist["Volume"].mean()), 1)
        volume_today   = int(hist["Volume"].iloc[-1])
    else:
        avg_volume_20d = 1
        volume_today   = 0

    market_cap   = _safe_float(fi, "market_cap",         None)
    shares_out   = _safe_float(fi, "shares_outstanding", None)
    float_shares = int(shares_out) if shares_out and shares_out > 0 else None

    confidence = determine_confidence(
        price=price, volume_today=volume_today, market_cap=market_cap,
        float_shares=float_shares, premarket_available=premarket_available,
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
        cache_hit=False,
        data_age_seconds=0.0,
    )


# ── PUBLIC INTERFACE ──────────────────────────────────────────────────────────

def get_snapshot(ticker: str, force_refresh: bool = False) -> TickerSnapshot:
    """
    Haalt snapshot op. Volgorde:
        1. Cache check (tenzij force_refresh)
        2. Live fetch met retry
        3. Cache fallback als live faalt

    Altijd TickerSnapshot terug — nooit een exception.
    """
    ticker = ticker.upper().strip()

    # 1. Cache check
    if not force_refresh and CACHE_ENABLED:
        entry = get_cached(ticker)
        if entry and not entry.is_expired():
            logger.debug(f"yahoo: cache hit {ticker} ({entry.age_seconds():.0f}s oud)")
            snap = _dict_to_snap(ticker, entry.data, entry.age_seconds(),
                                 entry.ttl_remaining())
            return snap

    # 2. Live fetch met retry
    last_error: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        wait = _BACKOFF_SECS[attempt]
        if wait > 0:
            time.sleep(wait)

        try:
            snap = _fetch_once(ticker)
            snap.retries_used = attempt

            # Opslaan in cache
            if CACHE_ENABLED:
                set_cached(ticker, _snap_to_dict(snap), ttl_seconds=get_market_ttl())
                logger.debug(f"yahoo: {ticker} gecached (TTL {get_market_ttl()}s)")

            return snap

        except Exception as exc:
            last_error = exc

            if _is_rate_limited(exc):
                logger.warning(f"yahoo: rate limit {ticker}")
                set_cooldown(ticker, seconds=60)
                break

            if _is_auth_error(exc) and attempt == 0:
                continue

            logger.debug(f"yahoo: poging {attempt + 1}/{_MAX_RETRIES} {ticker}: {exc}")

    # 3. Cache fallback bij live-fout
    error_msg = str(last_error) if last_error else "Onbekende fout"

    if CACHE_ENABLED:
        entry = get_cached(ticker)
        if entry:
            age = entry.age_seconds()
            logger.warning(
                f"yahoo: live mislukt, cache fallback {ticker} ({age:.0f}s oud)"
            )
            snap = _dict_to_snap(ticker, entry.data, age, entry.ttl_remaining())
            snap.error = f"Live fetch mislukt: {error_msg} — serveren vanuit cache"
            return snap

    # 4. Volledig MISSING
    logger.warning(f"yahoo: {ticker} volledig mislukt: {error_msg}")
    return TickerSnapshot(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.MISSING,
        price=0.0, prev_close=0.0, day_change_pct=0.0,
        premarket_pct=0.0, premarket_available=False,
        volume_today=0, avg_volume_20d=1,
        error=error_msg,
        retries_used=_MAX_RETRIES - 1,
        cache_hit=False,
        data_age_seconds=0.0,
    )


def get_spy_return() -> float:
    """SPY dagsrendement voor relative strength. Geeft 0.0 bij fout."""
    try:
        import yfinance as yf
        fi         = yf.Ticker("SPY").fast_info
        price      = _safe_float(fi, "last_price",     0.0)
        prev_close = _safe_float(fi, "previous_close", price)
        if prev_close > 0:
            return round((price - prev_close) / prev_close * 100, 2)
    except Exception as exc:
        logger.debug(f"yahoo: SPY mislukt: {exc}")
    return 0.0


def _safe_float(obj, attr: str, default):
    try:
        val = getattr(obj, attr, default)
        if val is None:
            return default
        f = float(val)
        if f != f or abs(f) == float("inf"):
            return default
        return f
    except (TypeError, ValueError):
        return default
