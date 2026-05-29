"""
data/finnhub_client.py
Finnhub Data Client — v1.0

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

Gebruik:
    from data.finnhub_client import fetch_company_news, FinnhubNewsItem
    items = fetch_company_news("NVDA", hours=48)
"""

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
_BASE_URL    = "https://finnhub.io/api/v1"
_MAX_ITEMS   = 20   # max artikelen per request — beschermt downstream classifiers
_TIMEOUT_SEC = 5.0


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


# ── API CALLS ─────────────────────────────────────────────────────────────────

def fetch_company_news(
    ticker: str,
    hours:  int = 48,
) -> list[FinnhubNewsItem]:
    """
    Haalt company-news op voor ticker voor de afgelopen `hours` uur.

    Met FINNHUB_API_KEY: echte Finnhub data.
    Zonder key: lege lijst (graceful fallback, logt één debug-bericht).

    Retourneert altijd een lijst — nooit een exception.
    Gesorteerd: nieuwste artikel eerst.
    """
    ticker = ticker.upper().strip()

    if not _FINNHUB_KEY:
        logger.debug(
            "finnhub_client: geen API key — geen nieuws voor %s "
            "(stel FINNHUB_API_KEY in via .env)",
            ticker,
        )
        return []

    try:
        return _do_fetch(ticker, hours)
    except Exception as exc:
        logger.warning(
            "finnhub_client: onverwachte fout voor %s [%s: %s]",
            ticker, type(exc).__name__, exc,
        )
        return []


def _do_fetch(ticker: str, hours: int) -> list[FinnhubNewsItem]:
    """Interne fetch — alleen aangeroepen als key beschikbaar is."""
    try:
        import httpx
    except ImportError:
        logger.warning("finnhub_client: httpx niet geïnstalleerd — pip install httpx")
        return []

    now      = datetime.now(timezone.utc)
    from_dt  = now - timedelta(hours=hours)

    params = {
        "symbol": ticker,
        "from":   from_dt.strftime("%Y-%m-%d"),
        "to":     now.strftime("%Y-%m-%d"),
        "token":  _FINNHUB_KEY,
    }

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

        # Nieuwste eerst
        items.sort(key=lambda x: x.published_unix, reverse=True)

        logger.info("finnhub_client: %d artikelen opgehaald voor %s", len(items), ticker)
        return items

    except Exception as exc:
        exc_type = type(exc).__name__
        logger.warning(
            "finnhub_client: fetch mislukt voor %s [%s: %s]",
            ticker, exc_type, exc,
        )
        return []


def is_available() -> bool:
    """True als FINNHUB_API_KEY ingesteld is."""
    return bool(_FINNHUB_KEY)
