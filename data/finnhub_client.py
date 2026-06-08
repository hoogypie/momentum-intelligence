"""
data/finnhub_client.py
Finnhub Data Client — v1.1

Wijzigingen t.o.v. v1.0:
    - Timeout verhoogd van 5s naar 12s
    - Exponential backoff retry (3 pogingen: 0s, 2s, 5s)
    - ReadTimeout en ConnectTimeout worden apart gelogd als WARNING
    - Succes/timeout/fout tellers beschikbaar via get_session_stats()
    - reset_session_stats() voor gebruik tussen runs

Verantwoordelijk voor: ophalen van ruwe nieuwsdata via Finnhub API.
Verantwoordelijk NIET voor: classificatie, scoring of catalyst-logica.
Die verantwoordelijkheid ligt bij data/catalyst_classifier.py.

Vereiste env var:
    FINNHUB_API_KEY = sk-... (gratis tier: https://finnhub.io)

Gratis tier limieten:
    60 API calls/minuut
    Company news: beschikbaar

Fallback (geen key of netwerk-fout):
    Retourneert altijd lege lijst — nooit een exception.
"""

import os
import time
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
_BASE_URL    = "https://finnhub.io/api/v1"
_MAX_ITEMS   = 20    # max artikelen per request
_TIMEOUT_SEC = 12.0  # verhoogd van 5s naar 12s

# Exponential backoff: wachttijden tussen pogingen (seconden)
# Poging 1: direct, poging 2: +2s, poging 3: +5s
_RETRY_DELAYS = [0, 2, 5]
_MAX_RETRIES  = 3


# ── SESSIE STATISTIEKEN ───────────────────────────────────────────────────────
# Tellers per run — reset via reset_session_stats() bij start van elke run.

_session_stats: dict = {
    "success":  0,   # tickers waarbij Finnhub data retourneerde
    "timeout":  0,   # ReadTimeout of ConnectTimeout
    "error":    0,   # andere fouten (HTTP error, parse fout, enz.)
    "no_key":   0,   # geen API key geconfigureerd
    "total":    0,   # totaal geprobeerde fetches
}


def reset_session_stats() -> None:
    """Reset alle tellers. Aanroepen aan het begin van een validatierun."""
    global _session_stats
    _session_stats = {
        "success": 0, "timeout": 0, "error": 0, "no_key": 0, "total": 0,
    }


def get_session_stats() -> dict:
    """Retourneert kopie van de huidige sessietellers."""
    return dict(_session_stats)


def format_session_stats(total_tickers: Optional[int] = None) -> str:
    """
    Formatteert succes-rate als leesbare string voor rapportage.

    Voorbeeld output:
        Finnhub success rate:
          42/51 tickers succesvol
           7/51 timeout (ReadTimeout)
           2/51 overige fouten
    """
    s = _session_stats
    n = total_tickers or s["total"] or 1
    lines = ["  Finnhub success rate:"]
    lines.append(f"    {s['success']:>3}/{n} tickers succesvol")
    if s["timeout"] > 0:
        lines.append(f"    {s['timeout']:>3}/{n} timeout (ReadTimeout)")
    if s["error"] > 0:
        lines.append(f"    {s['error']:>3}/{n} overige fouten")
    if s["no_key"] > 0:
        lines.append(f"    {s['no_key']:>3}/{n} geen API key")
    return "\n".join(lines)


# ── RAW DATA STRUCTUUR ────────────────────────────────────────────────────────

@dataclass
class FinnhubNewsItem:
    """
    Ruwe data van Finnhub — geen classificatie, geen scoring.
    Alles wat de API retourneert, bewaard als-is.
    """
    ticker:         str
    headline:       str
    summary:        str             # Samenvatting (kan leeg zijn)
    source:         str             # Nieuwsbron (bijv. "Reuters")
    url:            str
    published_unix: int             # Unix timestamp van publicatie
    published_iso:  str             # ISO 8601 (afgeleid van unix)
    finnhub_id:     int             # Unieke Finnhub artikel-ID
    image_url:      Optional[str]   # Optioneel thumbnail
    sentiment:      Optional[float] # Finnhub sentiment score (-1 tot +1)


def _unix_to_iso(unix_ts: int) -> str:
    """Converteert Unix timestamp naar ISO 8601 UTC string."""
    try:
        return datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
    except (OSError, ValueError, OverflowError):
        return datetime.now(timezone.utc).isoformat()


def _parse_article(ticker: str, article: dict) -> Optional[FinnhubNewsItem]:
    """
    Parseert één Finnhub article-dict naar FinnhubNewsItem.
    Retourneert None als headline ontbreekt.
    """
    try:
        headline = article.get("headline", "").strip()
        if not headline:
            return None

        unix_ts = int(article.get("datetime", 0))

        return FinnhubNewsItem(
            ticker         = ticker.upper(),
            headline       = headline,
            summary        = article.get("summary", "").strip(),
            source         = article.get("source", "").strip(),
            url            = article.get("url", "").strip(),
            published_unix = unix_ts,
            published_iso  = _unix_to_iso(unix_ts),
            finnhub_id     = int(article.get("id", 0)),
            image_url      = article.get("image") or None,
            sentiment      = article.get("sentiment"),
        )
    except Exception as exc:
        logger.debug("finnhub_client: artikel parse fout: %s", exc)
        return None


def _is_timeout(exc: Exception) -> bool:
    """Detecteert ReadTimeout en ConnectTimeout van httpx én requests."""
    name = type(exc).__name__.lower()
    msg  = str(exc).lower()
    return (
        "timeout"  in name or
        "timeout"  in msg  or
        "timed out" in msg
    )


# ── API CALLS ─────────────────────────────────────────────────────────────────

def fetch_company_news(
    ticker: str,
    hours:  int = 48,
) -> list[FinnhubNewsItem]:
    """
    Haalt company-news op voor ticker voor de afgelopen `hours` uur.

    Met FINNHUB_API_KEY: echte Finnhub data, met exponential retry.
    Zonder key: lege lijst (graceful fallback).

    Retourneert altijd een lijst — nooit een exception.
    Bijwerkt sessietellers (_session_stats).
    Gesorteerd: nieuwste artikel eerst.
    """
    ticker = ticker.upper().strip()
    _session_stats["total"] += 1

    if not _FINNHUB_KEY:
        _session_stats["no_key"] += 1
        logger.debug(
            "finnhub_client: geen API key — geen nieuws voor %s "
            "(stel FINNHUB_API_KEY in via .env)",
            ticker,
        )
        return []

    try:
        result = _do_fetch(ticker, hours)
        if result:
            _session_stats["success"] += 1
        else:
            # Lege lijst zonder fout = geldige response maar geen nieuws
            _session_stats["success"] += 1
        return result
    except Exception as exc:
        if _is_timeout(exc):
            _session_stats["timeout"] += 1
        else:
            _session_stats["error"] += 1
        logger.warning(
            "finnhub_client: onverwachte fout voor %s [%s: %s]",
            ticker, type(exc).__name__, exc,
        )
        return []


def _do_fetch(ticker: str, hours: int) -> list[FinnhubNewsItem]:
    """
    Interne fetch met exponential backoff retry.
    Gooit een exception als alle pogingen mislukken.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("finnhub_client: httpx niet geïnstalleerd — pip install httpx")
        return []

    now     = datetime.now(timezone.utc)
    from_dt = now - timedelta(hours=hours)

    params = {
        "symbol": ticker,
        "from":   from_dt.strftime("%Y-%m-%d"),
        "to":     now.strftime("%Y-%m-%d"),
        "token":  _FINNHUB_KEY,
    }

    last_exc: Optional[Exception] = None

    for attempt, wait_secs in enumerate(_RETRY_DELAYS[:_MAX_RETRIES], start=1):
        if wait_secs > 0:
            logger.debug(
                "finnhub_client: %s poging %d/%d na %.0fs wachten",
                ticker, attempt, _MAX_RETRIES, wait_secs,
            )
            time.sleep(wait_secs)

        try:
            r = httpx.get(
                f"{_BASE_URL}/company-news",
                params=params,
                timeout=_TIMEOUT_SEC,
            )
            r.raise_for_status()
            raw = r.json()

            if not isinstance(raw, list):
                logger.warning(
                    "finnhub_client: onverwacht response formaat voor %s (type: %s)",
                    ticker, type(raw).__name__,
                )
                return []

            items: list[FinnhubNewsItem] = []
            for article in raw:
                parsed = _parse_article(ticker, article)
                if parsed is not None:
                    items.append(parsed)
                if len(items) >= _MAX_ITEMS:
                    break

            items.sort(key=lambda x: x.published_unix, reverse=True)

            if attempt > 1:
                logger.info(
                    "finnhub_client: %s geslaagd op poging %d — %d artikelen",
                    ticker, attempt, len(items),
                )
            else:
                logger.info(
                    "finnhub_client: %d artikelen opgehaald voor %s",
                    len(items), ticker,
                )
            return items

        except Exception as exc:
            last_exc = exc
            if _is_timeout(exc):
                logger.warning(
                    "finnhub_client: %s timeout op poging %d/%d (%.0fs) — %s",
                    ticker, attempt, _MAX_RETRIES, _TIMEOUT_SEC, exc,
                )
            else:
                logger.warning(
                    "finnhub_client: %s fout op poging %d/%d — %s: %s",
                    ticker, attempt, _MAX_RETRIES, type(exc).__name__, exc,
                )
            # Timeout: altijd opnieuw proberen
            # HTTP error: ook opnieuw proberen (server kan tijdelijk overbelast zijn)
            continue

    # Alle pogingen mislukt — gooi laatste exception door naar fetch_company_news
    raise last_exc or RuntimeError(f"Alle {_MAX_RETRIES} pogingen mislukt voor {ticker}")


def is_available() -> bool:
    """True als FINNHUB_API_KEY ingesteld is."""
    return bool(_FINNHUB_KEY)
