"""
cache/market_cache.py
Market Data Cache — v2.2 (ACTIEF)

Wijzigingen t.o.v. v2.1:
    - CACHE_ENABLED = True
    - TTL tiers per marktperiode (open/pre-market/closed)
    - Age-based confidence: LIVE / DELAYED / STALE
    - Fallback: Yahoo faalt → stale cache leveren met juiste label
    - Cache invalidation: manual refresh + market-aware TTL
    - Uitgebreide stats voor debugging

TTL strategie (markturen Eastern Time):
    Pre-market  (04:00-09:30)   → 120s
    Regular     (09:30-16:00)   → 60s
    After hours (16:00-20:00)   → 300s
    Overnight   (20:00-04:00)   → 1800s

Confidence op basis van leeftijd (ongeacht marktperiode):
    0  – 300s   → LIVE
    300 – 3600s → DELAYED
    3600 – 7200s → STALE  (leveren met waarschuwing)
    >7200s       → verwijderen uit cache (te oud)
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CACHE_ENABLED = True   # actief in v2.2

# Age thresholds (seconden)
LIVE_MAX_AGE    =  300   # 5 min
DELAYED_MAX_AGE = 3600   # 60 min
STALE_MAX_AGE   = 7200   # 2 uur — hierna uit cache verwijderen

# TTL per marktperiode (seconden)
_TTL_PREMARKET    = 120
_TTL_REGULAR      = 60
_TTL_AFTERHOURS   = 300
_TTL_OVERNIGHT    = 1800


# ── CACHE ENTRY ───────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    ticker:      str
    data:        dict
    cached_at:   datetime
    ttl_seconds: int = 60

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.cached_at).total_seconds()

    def is_expired(self) -> bool:
        return self.age_seconds() > self.ttl_seconds

    def is_too_old(self) -> bool:
        """Ouder dan STALE_MAX_AGE → nooit serveren."""
        return self.age_seconds() > STALE_MAX_AGE

    def confidence_label(self) -> str:
        age = self.age_seconds()
        if age <= LIVE_MAX_AGE:    return "LIVE"
        if age <= DELAYED_MAX_AGE: return "DELAYED"
        return "STALE"

    def ttl_remaining(self) -> float:
        return max(0.0, self.ttl_seconds - self.age_seconds())


# ── IN-MEMORY STORE ───────────────────────────────────────────────────────────

_cache:     dict[str, CacheEntry] = {}
_cooldowns: dict[str, datetime]   = {}


# ── MARKET HOURS ──────────────────────────────────────────────────────────────

def get_market_ttl() -> int:
    """
    Geeft cache TTL op basis van huidige marktperiode (Eastern Time proxy).
    Geen pytz vereist — eenvoudige UTC-offset benadering.
    UTC-5 (winter) / UTC-4 (zomer). We gebruiken UTC-5 (conservatief).
    """
    now_et_hour = (datetime.now(timezone.utc).hour - 5) % 24
    if 4 <= now_et_hour < 9:
        return _TTL_PREMARKET
    if 9 <= now_et_hour < 16:
        return _TTL_REGULAR
    if 16 <= now_et_hour < 20:
        return _TTL_AFTERHOURS
    return _TTL_OVERNIGHT


def is_market_open() -> bool:
    """Schatting of de US markt open is. Geen feestdagen."""
    now_utc  = datetime.now(timezone.utc)
    weekday  = now_utc.weekday()         # 0=maandag, 6=zondag
    hour_et  = (now_utc.hour - 5) % 24  # UTC-5 benadering
    return weekday < 5 and 9 <= hour_et < 16


# ── PUBLIC API ────────────────────────────────────────────────────────────────

def get_cached(ticker: str) -> Optional[CacheEntry]:
    """
    Geeft CacheEntry als aanwezig en niet te oud (< STALE_MAX_AGE).
    Geeft None bij cache disabled of ontbrekende/verlopen entry.
    """
    if not CACHE_ENABLED:
        return None
    entry = _cache.get(ticker.upper())
    if entry is None:
        return None
    if entry.is_too_old():
        logger.debug(f"cache: {ticker} verwijderd — te oud ({entry.age_seconds():.0f}s)")
        _cache.pop(ticker.upper(), None)
        return None
    return entry


def set_cached(ticker: str, data: dict,
               ttl_seconds: Optional[int] = None) -> None:
    """Slaat data op. TTL is marktperiode-bewust als niet opgegeven."""
    if not CACHE_ENABLED:
        return
    ttl = ttl_seconds if ttl_seconds is not None else get_market_ttl()
    _cache[ticker.upper()] = CacheEntry(
        ticker=ticker.upper(),
        data=data,
        cached_at=datetime.now(timezone.utc),
        ttl_seconds=ttl,
    )
    logger.debug(f"cache: {ticker} opgeslagen (TTL {ttl}s)")


def invalidate(ticker: str) -> bool:
    """Verwijdert entry voor ticker. Geeft True als verwijderd."""
    removed = _cache.pop(ticker.upper(), None) is not None
    if removed:
        logger.debug(f"cache: {ticker} geïnvalideerd")
    return removed


def invalidate_all() -> int:
    """Leegt volledige cache. Geeft aantal verwijderde entries terug."""
    count = len(_cache)
    _cache.clear()
    logger.info(f"cache: volledig geleegd ({count} entries)")
    return count


def set_cooldown(ticker: str, seconds: int = 60) -> None:
    """Markeert ticker als rate-limited."""
    _cooldowns[ticker.upper()] = (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    )
    logger.warning(f"cache: cooldown {seconds}s voor {ticker}")


def is_cooling_down(ticker: str) -> bool:
    expires_at = _cooldowns.get(ticker.upper())
    if expires_at is None:
        return False
    if datetime.now(timezone.utc) >= expires_at:
        _cooldowns.pop(ticker.upper(), None)  # auto-verwijderen na expiry
        return False
    return True


def clear_cache(ticker: Optional[str] = None) -> None:
    """Alias voor invalidate/invalidate_all (backward compat)."""
    if ticker:
        invalidate(ticker)
    else:
        invalidate_all()


def cache_stats() -> dict:
    """Debug-statistieken over cache state."""
    now = datetime.now(timezone.utc)
    entries = list(_cache.values())
    live    = sum(1 for e in entries if e.age_seconds() <= LIVE_MAX_AGE)
    delayed = sum(1 for e in entries if LIVE_MAX_AGE < e.age_seconds() <= DELAYED_MAX_AGE)
    stale   = sum(1 for e in entries if e.age_seconds() > DELAYED_MAX_AGE)
    expired = sum(1 for e in entries if e.is_expired())

    return {
        "enabled":         CACHE_ENABLED,
        "total_entries":   len(entries),
        "live":            live,
        "delayed":         delayed,
        "stale":           stale,
        "expired":         expired,
        "cooling_down":    len(_cooldowns),
        "market_open":     is_market_open(),
        "current_ttl":     get_market_ttl(),
        "tickers":         [e.ticker for e in entries],
    }
