"""
data/yahoo_client.py
Yahoo Finance data client — v2.5

Wijzigingen t.o.v. v2.4:
    - market_session toegevoegd aan TickerSnapshot
    - Premarket onderscheid: alleen premarket_pct als sessie=PREMARKET
    - get_snapshot() accepteert force_refresh kwarg

Wijzigingen v2.5 (debug/fallback):
    - Verbeterde logging: exception type + message + welke yfinance-call faalde
    - Traceback NIET geslikken in debug mode (MOMENTUM_DEBUG=1)
    - Fallback: als fast_info faalt, probeer history(period="5d")
    - Prijs, volume en prev_close worden afgeleid uit history bij fast_info-fout
    - Nieuwe helper: _fetch_from_history()
"""

import os
import time
import logging
import traceback as _tb
from datetime import datetime, timezone
from typing import Optional

from schemas.ticker_snapshot import (
    TickerSnapshot, DataConfidence,
    determine_confidence, age_to_confidence, worst_confidence,
)
from cache.market_cache import (
    get_cached, set_cached, set_cooldown,
    get_market_ttl, CACHE_ENABLED,
)
from data.market_session import get_market_session, MarketSession

logger = logging.getLogger(__name__)

# Zet MOMENTUM_DEBUG=1 in je shell voor volledige tracebacks in de logs
_DEBUG_MODE = os.getenv("MOMENTUM_DEBUG", "0").strip() == "1"

_MAX_RETRIES  = 3
_BACKOFF_SECS = [0.0, 0.5, 1.5]


class YahooRateLimitError(Exception):
    pass


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in ["429", "too many requests", "rate limit"])


def _is_auth_error(exc: Exception) -> bool:
    return "403" in str(exc) or "forbidden" in str(exc).lower()


def _log_fetch_error(ticker: str, call_name: str, exc: Exception) -> None:
    """
    Logt een yfinance-fout met: exception type, message en welke call faalde.
    In debug mode wordt ook de volledige traceback gelogd.
    """
    exc_type = type(exc).__name__
    exc_msg  = str(exc)
    logger.warning(
        "yahoo: %s — %s mislukt [%s: %s]",
        ticker, call_name, exc_type, exc_msg,
    )
    if _DEBUG_MODE:
        logger.debug(
            "yahoo: %s — %s volledige traceback:\n%s",
            ticker, call_name, _tb.format_exc(),
        )


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
        "market_session":      s.market_session,
    }


def _dict_to_snap(ticker: str, d: dict, age: float, ttl_rem: float) -> TickerSnapshot:
    field_conf = determine_confidence(
        price=d.get("price", 0.0),
        volume_today=d.get("volume_today", 0),
        market_cap=d.get("market_cap"),
        float_shares=d.get("float_shares"),
        premarket_available=d.get("premarket_available", False),
        error=None,
    )
    age_conf   = age_to_confidence(age)
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
        market_session=d.get("market_session"),
        cache_hit=True,
        data_age_seconds=round(age, 1),
    )


def _fetch_from_history(ticker: str, t) -> Optional[dict]:
    """
    Fallback: haal prijs en volume op via history(period='5d').
    Retourneert een dict met de beschikbare velden, of None als ook dit faalt.

    Afgeleid uit history:
        price      → laatste slotkoers
        prev_close → slotkoers één dag eerder (of zelfde als price bij 1 rij)
        volume_today   → volume van de laatste handelsdag
        avg_volume_20d → gemiddeld volume over alle beschikbare rijen
    Niet afleidbaar uit history:
        market_cap, float_shares, premarket_price
    """
    try:
        hist = t.history(period="5d", auto_adjust=True)
    except Exception as exc:
        _log_fetch_error(ticker, "history(period='5d')", exc)
        return None

    if hist is None or hist.empty:
        logger.warning(
            "yahoo: %s — history(period='5d') retourneerde lege DataFrame "
            "(ticker ongeldig, delisted, of Yahoo geblokkeerd)",
            ticker,
        )
        return None

    try:
        price          = float(hist["Close"].iloc[-1])
        prev_close     = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else price
        volume_today   = int(hist["Volume"].iloc[-1])
        avg_volume_20d = max(int(hist["Volume"].mean()), 1)

        logger.info(
            "yahoo: %s — history-fallback OK "
            "(price=%.2f prev_close=%.2f vol=%d avg_vol=%d)",
            ticker, price, prev_close, volume_today, avg_volume_20d,
        )
        return {
            "price":         price,
            "prev_close":    prev_close,
            "volume_today":  volume_today,
            "avg_volume_20d": avg_volume_20d,
        }
    except Exception as exc:
        _log_fetch_error(ticker, "history-parsing", exc)
        return None


def _fetch_once(ticker: str) -> TickerSnapshot:
    import yfinance as yf

    session = get_market_session()
    t       = yf.Ticker(ticker)

    # ── PAD 1: fast_info ─────────────────────────────────────────────────────
    fast_info_ok   = False
    price          = 0.0
    prev_close     = 0.0
    day_change_pct = 0.0
    pm_price       = None
    premarket_pct  = 0.0
    premarket_available = False
    volume_today   = 0
    avg_volume_20d = 1
    market_cap     = None
    shares_out     = None

    try:
        fi = t.fast_info

        price_raw      = _safe_float(fi, "last_price",     0.0)
        prev_close_raw = _safe_float(fi, "previous_close", price_raw)

        if price_raw <= 0:
            raise ValueError(
                f"fast_info.last_price retourneerde {price_raw!r} — "
                "geen geldige prijs"
            )

        price      = price_raw
        prev_close = prev_close_raw

        day_change_pct = (
            ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
        )

        pm_price = _safe_float(fi, "pre_market_price", None)
        if pm_price and prev_close > 0 and session == MarketSession.PREMARKET:
            premarket_pct       = (pm_price - prev_close) / prev_close * 100
            premarket_available = True
        else:
            premarket_pct       = 0.0
            premarket_available = False
            pm_price            = None

        hist = t.history(period="1mo", auto_adjust=True)
        if not hist.empty:
            avg_volume_20d = max(int(hist["Volume"].mean()), 1)
            volume_today   = int(hist["Volume"].iloc[-1])
        else:
            avg_volume_20d = 1
            volume_today   = 0

        market_cap = _safe_float(fi, "market_cap",         None)
        shares_out = _safe_float(fi, "shares_outstanding", None)

        fast_info_ok = True
        logger.debug("yahoo: %s — fast_info OK (price=%.2f)", ticker, price)

    except Exception as exc:
        _log_fetch_error(ticker, "fast_info", exc)

    # ── PAD 2: history-fallback als fast_info faalde ──────────────────────────
    if not fast_info_ok:
        logger.info("yahoo: %s — fast_info mislukt, probeer history-fallback", ticker)
        fallback = _fetch_from_history(ticker, t)

        if fallback is not None:
            price          = fallback["price"]
            prev_close     = fallback["prev_close"]
            volume_today   = fallback["volume_today"]
            avg_volume_20d = fallback["avg_volume_20d"]
            day_change_pct = (
                ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0.0
            )
        else:
            # Beide paden kapot — laat get_snapshot() de fout afhandelen
            raise RuntimeError(
                f"Zowel fast_info als history(period='5d') mislukten voor '{ticker}'. "
                "Yahoo Finance is mogelijk geblokkeerd of de ticker is ongeldig."
            )

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
        market_session=session.value,
        cache_hit=False,
        data_age_seconds=0.0,
    )


def get_snapshot(ticker: str, force_refresh: bool = False) -> TickerSnapshot:
    """Cache-first, retry-safe snapshot ophalen."""
    ticker = ticker.upper().strip()

    if not force_refresh and CACHE_ENABLED:
        entry = get_cached(ticker)
        if entry and not entry.is_expired():
            return _dict_to_snap(ticker, entry.data,
                                 entry.age_seconds(), entry.ttl_remaining())

    last_error: Optional[Exception] = None

    for attempt in range(_MAX_RETRIES):
        if _BACKOFF_SECS[attempt] > 0:
            time.sleep(_BACKOFF_SECS[attempt])
        try:
            snap = _fetch_once(ticker)
            snap.retries_used = attempt
            if CACHE_ENABLED:
                set_cached(ticker, _snap_to_dict(snap), ttl_seconds=get_market_ttl())
            return snap
        except Exception as exc:
            last_error = exc
            exc_type   = type(exc).__name__
            exc_msg    = str(exc)
            logger.warning(
                "yahoo: %s — poging %d/%d mislukt [%s: %s]",
                ticker, attempt + 1, _MAX_RETRIES, exc_type, exc_msg,
            )
            if _DEBUG_MODE:
                logger.debug(
                    "yahoo: %s — poging %d traceback:\n%s",
                    ticker, attempt + 1, _tb.format_exc(),
                )
            if _is_rate_limited(exc):
                set_cooldown(ticker, seconds=60)
                break
            if _is_auth_error(exc) and attempt == 0:
                continue

    # Cache fallback
    error_msg = f"{type(last_error).__name__}: {last_error}" if last_error else "Onbekende fout"
    if CACHE_ENABLED:
        entry = get_cached(ticker)
        if entry:
            snap = _dict_to_snap(ticker, entry.data,
                                 entry.age_seconds(), entry.ttl_remaining())
            snap.error = f"Live fetch mislukt: {error_msg} — cache fallback"
            return snap

    return TickerSnapshot(
        ticker=ticker, timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.MISSING,
        price=0.0, prev_close=0.0, day_change_pct=0.0,
        premarket_pct=0.0, premarket_available=False,
        volume_today=0, avg_volume_20d=1,
        error=error_msg, retries_used=_MAX_RETRIES - 1,
        cache_hit=False, data_age_seconds=0.0,
    )


def get_spy_return() -> float:
    try:
        import yfinance as yf
        t          = yf.Ticker("SPY")
        fi         = t.fast_info
        price      = _safe_float(fi, "last_price",     0.0)
        prev_close = _safe_float(fi, "previous_close", price)
        if prev_close > 0:
            return round((price - prev_close) / prev_close * 100, 2)
    except Exception as exc:
        _log_fetch_error("SPY", "fast_info", exc)
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
