"""
alerting/cooldown_manager.py
Cooldown Manager — v2.9

Voorkomt alert-spam door dezelfde trigger voor dezelfde ticker
binnen een cooldown-venster te supprimeren.

Cooldown vensters per severity:
    INFO      → 15 minuten
    WATCH     → 30 minuten
    HIGH      → 60 minuten
    CRITICAL  → 120 minuten

Key formaat: "{TICKER}::{TRIGGER_TYPE}"
Extra specifiek (optioneel): "{TICKER}::{TRIGGER_TYPE}::{context}"

Persistentie: in-memory (verliest state bij herstart).
Acceptabel: alerts zijn best-effort, niet kritisch.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Cooldown vensters in minuten
_COOLDOWN_MINUTES = {
    "INFO":     15,
    "WATCH":    30,
    "HIGH":     60,
    "CRITICAL": 120,
}

# In-memory registry: key → expires_at datetime
_cooldowns: dict[str, datetime] = {}


def _make_key(ticker: str, trigger_type: str, context: Optional[str] = None) -> str:
    base = f"{ticker.upper()}::{trigger_type}"
    return f"{base}::{context}" if context else base


def is_suppressed(
    ticker:       str,
    trigger_type: str,
    severity:     str,
    context:      Optional[str] = None,
) -> bool:
    """
    Controleert of dit alert gesupprimeerd moet worden.

    Returns True als het alert binnen de cooldown-periode valt.
    Side effect: verwijdert verlopen cooldowns automatisch.
    """
    key     = _make_key(ticker, trigger_type, context)
    expires = _cooldowns.get(key)

    if expires is None:
        return False

    now = datetime.now(timezone.utc)
    if now >= expires:
        # Cooldown verlopen — verwijder en sta toe
        del _cooldowns[key]
        return False

    remaining = (expires - now).total_seconds() / 60
    logger.debug(
        f"cooldown: {ticker}/{trigger_type} gesupprimeerd "
        f"({remaining:.0f}m resterend)"
    )
    return True


def set_cooldown(
    ticker:       str,
    trigger_type: str,
    severity:     str,
    context:      Optional[str] = None,
    override_minutes: Optional[int] = None,
) -> None:
    """
    Registreert een cooldown na het firen van een alert.
    Aanroepen NA save_alert(), niet ervoor.
    """
    minutes = override_minutes or _COOLDOWN_MINUTES.get(severity, 30)
    key     = _make_key(ticker, trigger_type, context)
    _cooldowns[key] = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    logger.debug(f"cooldown: {ticker}/{trigger_type} ingesteld voor {minutes}m")


def clear_cooldown(
    ticker:       str,
    trigger_type: Optional[str] = None,
) -> int:
    """
    Verwijdert cooldown(s) voor een ticker.
    Zonder trigger_type: alle cooldowns voor de ticker.
    Returns: aantal verwijderde entries.
    """
    prefix  = ticker.upper() + "::"
    if trigger_type:
        prefix += trigger_type

    to_remove = [k for k in _cooldowns if k.startswith(prefix)]
    for k in to_remove:
        del _cooldowns[k]

    if to_remove:
        logger.debug(f"cooldown: {len(to_remove)} entry(s) verwijderd voor {ticker}")
    return len(to_remove)


def clear_all_cooldowns() -> int:
    """Verwijdert alle cooldowns (debug/test gebruik)."""
    count = len(_cooldowns)
    _cooldowns.clear()
    return count


def get_active_cooldowns() -> dict[str, float]:
    """
    Geeft alle actieve cooldowns terug als dict van key → minuten_resterend.
    Verlopen entries worden verwijderd.
    """
    now    = datetime.now(timezone.utc)
    result = {}
    expired = []

    for key, expires in _cooldowns.items():
        if now >= expires:
            expired.append(key)
        else:
            result[key] = round((expires - now).total_seconds() / 60, 1)

    for k in expired:
        del _cooldowns[k]

    return result


def cooldown_stats() -> dict:
    """Statistieken over actieve cooldowns."""
    active = get_active_cooldowns()
    return {
        "active_cooldowns": len(active),
        "details": active,
    }
