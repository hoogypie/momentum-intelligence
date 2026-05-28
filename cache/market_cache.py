"""
cache/market_cache.py
Market data cache architectuur — v2.1 (DISABLED)

Status: architectuur voorbereid, cache NIET actief.
Activeren in v2.2 zodra rate limiting in productie een probleem wordt.

Doel:
    Voorkom herhaalde Yahoo Finance requests voor dezelfde ticker
    binnen een korte tijdspanne. Beschermt tegen rate limiting.

Architectuur:
    In-memory dict cache (geen externe Redis/database vereist).
    TTL per entry (standaard 60 seconden voor live quotes).
    Cooldown registry voor rate-limited tickers.

Activeren:
    In data/yahoo_client.py:
        # from cache.market_cache import get_cached, set_cached
        # cached = get_cached(ticker)
        # if cached: return cached

Waarom nu nog niet actief:
    - Geen aantoonbaar rate limit probleem in v2.0
    - In-memory cache overleeft server restart niet
    - Voortijdige optimalisatie zonder meting = engineering verspilling
    - Cache invalidatie is complex: stale data kan slechtere scores geven
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from dataclasses import dataclass, field


# ── CACHE ENTRY ───────────────────────────────────────────────────────────────

@dataclass
class CacheEntry:
    ticker: str
    data: dict                    # Geserialiseerde QuoteData
    cached_at: datetime
    ttl_seconds: int = 60

    def is_expired(self) -> bool:
        age = (datetime.now(timezone.utc) - self.cached_at).total_seconds()
        return age > self.ttl_seconds

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.cached_at).total_seconds()


# ── IN-MEMORY STORE (DISABLED) ────────────────────────────────────────────────

_cache: dict[str, CacheEntry] = {}          # Ticker → CacheEntry
_cooldowns: dict[str, datetime] = {}         # Ticker → cooldown expires at


# ── PUBLIC API (stub — niet actief) ──────────────────────────────────────────

CACHE_ENABLED = False  # Zet op True in v2.2 om cache te activeren


def get_cached(ticker: str) -> Optional[dict]:
    """
    Geeft gecachede data terug als aanwezig en niet verlopen.
    Geeft None terug als cache uitgeschakeld of entry verlopen.
    """
    if not CACHE_ENABLED:
        return None
    entry = _cache.get(ticker.upper())
    if entry and not entry.is_expired():
        return entry.data
    return None


def set_cached(ticker: str, data: dict, ttl_seconds: int = 60) -> None:
    """Slaat data op in cache met opgegeven TTL."""
    if not CACHE_ENABLED:
        return
    _cache[ticker.upper()] = CacheEntry(
        ticker=ticker.upper(),
        data=data,
        cached_at=datetime.now(timezone.utc),
        ttl_seconds=ttl_seconds,
    )


def set_cooldown(ticker: str, seconds: int = 60) -> None:
    """
    Markeert ticker als rate-limited voor `seconds` seconden.
    Aanroepen wanneer Yahoo Finance een 429 of herhaalde 403 retourneert.
    """
    _cooldowns[ticker.upper()] = (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    )


def is_cooling_down(ticker: str) -> bool:
    """Geeft True als ticker nog in cooldown periode zit."""
    expires_at = _cooldowns.get(ticker.upper())
    if expires_at is None:
        return False
    return datetime.now(timezone.utc) < expires_at


def clear_cache(ticker: Optional[str] = None) -> None:
    """Leegt cache voor één ticker of volledig."""
    if ticker:
        _cache.pop(ticker.upper(), None)
    else:
        _cache.clear()


def cache_stats() -> dict:
    """Debug-info over cache state. Niet blootgesteld via API."""
    return {
        "enabled":        CACHE_ENABLED,
        "entries":        len(_cache),
        "cooling_down":   len(_cooldowns),
        "expired_entries": sum(1 for e in _cache.values() if e.is_expired()),
    }
