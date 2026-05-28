"""
alerting/watchlist_manager.py
Watchlist Manager — v2.9

Beheert watchlists als JSON-bestanden in watchlists/.
Standaard watchlists: core, momentum, sector_rotation.
Custom watchlists: watchlists/custom/{name}.json

Elk watchlist-bestand bevat:
    name, description, tickers[], alert thresholds, created_at
"""

import json, os, re, logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_WL_ROOT    = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "watchlists"
)
_CUSTOM_DIR = os.path.join(_WL_ROOT, "custom")

# Standaard alert-instellingen voor nieuwe watchlists
_DEFAULT_SETTINGS = {
    "min_alert_severity":     "WATCH",
    "alert_on_phase_change":  True,
    "alert_on_score_threshold": 60,
    "alert_on_score_drop":    False,
    "alert_on_volume_spike":  False,
    "volume_spike_threshold": 3.0,
}


def _wl_path(name: str) -> str:
    """Geeft pad voor watchlist. Custom lists gaan naar custom/."""
    builtin = os.path.join(_WL_ROOT, f"{name}.json")
    if os.path.exists(builtin):
        return builtin
    os.makedirs(_CUSTOM_DIR, exist_ok=True)
    return os.path.join(_CUSTOM_DIR, f"{name}.json")


def _validate_name(name: str) -> bool:
    """Alleen alfanumerieke namen + underscore."""
    return bool(re.match(r'^[a-z0-9_]{1,32}$', name.lower()))


def _validate_ticker(ticker: str) -> bool:
    return bool(re.match(r'^[A-Za-z]{1,10}$', ticker))


# ── READ ──────────────────────────────────────────────────────────────────────

def load_watchlist(name: str) -> Optional[dict]:
    """Laadt één watchlist. Geeft None als niet gevonden."""
    path = _wl_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning(f"watchlist: laden mislukt voor {name}: {exc}")
        return None


def list_watchlists() -> list[dict]:
    """
    Geeft alle watchlists terug (builtin + custom).
    Elke entry bevat: name, description, ticker_count, is_custom.
    """
    result = []

    # Builtin
    for fname in os.listdir(_WL_ROOT):
        if fname.endswith(".json"):
            wl = load_watchlist(fname.replace(".json", ""))
            if wl:
                result.append({
                    "name":         wl.get("name", fname.replace(".json","")),
                    "description":  wl.get("description", ""),
                    "ticker_count": len(wl.get("tickers", [])),
                    "tickers":      wl.get("tickers", []),
                    "is_custom":    False,
                })

    # Custom
    if os.path.exists(_CUSTOM_DIR):
        for fname in os.listdir(_CUSTOM_DIR):
            if fname.endswith(".json"):
                wl = load_watchlist(fname.replace(".json", ""))
                if wl:
                    result.append({
                        "name":         wl.get("name", fname.replace(".json","")),
                        "description":  wl.get("description", ""),
                        "ticker_count": len(wl.get("tickers", [])),
                        "tickers":      wl.get("tickers", []),
                        "is_custom":    True,
                    })

    return sorted(result, key=lambda x: x["name"])


def get_all_watchlist_tickers() -> list[str]:
    """Geeft alle unieke tickers over alle watchlists."""
    tickers = set()
    for wl_info in list_watchlists():
        tickers.update(wl_info.get("tickers", []))
    return sorted(tickers)


def get_ticker_watchlists(ticker: str) -> list[str]:
    """Geeft alle watchlists waar een ticker in zit."""
    ticker_upper = ticker.upper()
    return [
        wl["name"] for wl in list_watchlists()
        if ticker_upper in [t.upper() for t in wl.get("tickers", [])]
    ]


# ── WRITE ─────────────────────────────────────────────────────────────────────

def _save_watchlist(name: str, data: dict) -> None:
    path = _wl_path(name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        raise RuntimeError(f"Watchlist opslaan mislukt: {exc}")


def create_watchlist(
    name:        str,
    description: str = "",
    tickers:     Optional[list[str]] = None,
    settings:    Optional[dict] = None,
) -> dict:
    """
    Maakt een nieuwe custom watchlist aan.

    Raises ValueError bij ongeldige naam of bestaande naam.
    """
    name = name.lower()
    if not _validate_name(name):
        raise ValueError(f"Ongeldige watchlist naam: '{name}'. Gebruik alleen a-z, 0-9, _")

    if load_watchlist(name):
        raise ValueError(f"Watchlist '{name}' bestaat al.")

    valid_tickers = []
    for t in (tickers or []):
        if _validate_ticker(t):
            valid_tickers.append(t.upper())
        else:
            logger.warning(f"watchlist: ongeldige ticker overgeslagen: {t}")

    wl = {
        "name":        name,
        "description": description,
        "tickers":     valid_tickers,
        **_DEFAULT_SETTINGS,
        **(settings or {}),
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "updated_at":  datetime.now(timezone.utc).isoformat(),
    }

    _save_watchlist(name, wl)
    logger.info(f"watchlist: '{name}' aangemaakt met {len(valid_tickers)} tickers")
    return wl


def add_ticker(name: str, ticker: str) -> dict:
    """
    Voegt ticker toe aan watchlist.

    Returns: bijgewerkte watchlist.
    Raises ValueError als watchlist niet bestaat of ticker ongeldig.
    """
    wl = load_watchlist(name)
    if wl is None:
        raise ValueError(f"Watchlist '{name}' niet gevonden.")

    ticker = ticker.upper()
    if not _validate_ticker(ticker):
        raise ValueError(f"Ongeldige ticker: '{ticker}'")

    tickers = [t.upper() for t in wl.get("tickers", [])]
    if ticker in tickers:
        return wl  # Al aanwezig — geen actie

    tickers.append(ticker)
    wl["tickers"]    = tickers
    wl["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_watchlist(name, wl)
    logger.info(f"watchlist: {ticker} toegevoegd aan '{name}'")
    return wl


def remove_ticker(name: str, ticker: str) -> dict:
    """Verwijdert ticker van watchlist."""
    wl = load_watchlist(name)
    if wl is None:
        raise ValueError(f"Watchlist '{name}' niet gevonden.")

    ticker  = ticker.upper()
    tickers = [t.upper() for t in wl.get("tickers", []) if t.upper() != ticker]
    wl["tickers"]    = tickers
    wl["updated_at"] = datetime.now(timezone.utc).isoformat()

    _save_watchlist(name, wl)
    logger.info(f"watchlist: {ticker} verwijderd van '{name}'")
    return wl


def delete_watchlist(name: str) -> bool:
    """
    Verwijdert een watchlist. Ingebouwde lists kunnen niet verwijderd worden.
    Returns True als verwijderd.
    """
    builtin = os.path.join(_WL_ROOT, f"{name}.json")
    if os.path.exists(builtin):
        raise ValueError(f"Ingebouwde watchlist '{name}' kan niet verwijderd worden.")

    custom = os.path.join(_CUSTOM_DIR, f"{name}.json")
    if os.path.exists(custom):
        os.remove(custom)
        logger.info(f"watchlist: '{name}' verwijderd")
        return True
    return False
