"""
data/news_client.py
Nieuws Client — v2.4

Wijzigingen t.o.v. v2.3:
    - Echte Finnhub integratie (key-aware)
    - NewsConfidence scoring per headline
    - Source tier mapping
    - Recency scoring
    - Graceful fallback bij ontbrekende key

Vereiste env var:
    FINNHUB_API_KEY = sk-... (ophalen via https://finnhub.io — gratis tier)

Gratis tier limieten:
    60 API calls/min
    Company news: beschikbaar

Fallback gedrag (geen key):
    Geeft altijd lege lijst terug.
    Catalyst wordt NONE → score is conservatief maar nooit verkeerd.
"""

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)

_FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", "").strip()


# ── NEWS CONFIDENCE ───────────────────────────────────────────────────────────

class NewsConfidence(str, Enum):
    HIGH   = "HIGH"    # Grote bron, recent (<2u), sterke keywords
    MEDIUM = "MEDIUM"  # Gemiddelde bron of 2-24u oud
    LOW    = "LOW"     # Kleine bron of oud nieuws (>24u)


# Source tier (tier 1 = meest betrouwbaar)
_SOURCE_TIERS: dict[str, int] = {
    # Tier 1 — major financial media
    "reuters":           1, "bloomberg":        1,
    "wall street journal": 1, "wsj":            1,
    "financial times":   1, "ft":               1,
    "barron's":          1, "barrons":          1,
    "cnbc":              1, "marketwatch":      1,
    # Tier 2 — general financial
    "yahoo finance":     2, "seeking alpha":    2,
    "the motley fool":   2, "investing.com":    2,
    "benzinga":          2, "thestreet":        2,
    # Tier 3 — PR / social
    "pr newswire":       3, "business wire":    3,
    "globenewswire":     3, "accesswire":       3,
}


def _source_tier(source: str) -> int:
    """Geeft tier 1-3 voor een nieuwsbron. Onbekende bronnen = tier 2."""
    src = source.lower()
    for name, tier in _SOURCE_TIERS.items():
        if name in src:
            return tier
    return 2  # default: onbekend


def _news_confidence(
    source:        str,
    published_at:  str,
    catalyst_type: str,  # STRONG/MODERATE/WEAK/NONE
) -> NewsConfidence:
    """
    Berekent confidence op basis van bron, ouderdom en catalyst sterkte.
    """
    # Recency
    try:
        pub = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        age_hours = (datetime.now(timezone.utc) - pub).total_seconds() / 3600
    except Exception:
        age_hours = 24.0  # onbekend → matig

    tier = _source_tier(source)

    # HIGH: tier-1 bron + recent (<2u) of sterke catalyst
    if tier == 1 and age_hours < 2 and catalyst_type in ("STRONG", "MODERATE"):
        return NewsConfidence.HIGH
    if tier == 1 and catalyst_type == "STRONG":
        return NewsConfidence.HIGH

    # LOW: oud nieuws (>24u) of tier-3 bron
    if age_hours > 24 or (tier == 3 and catalyst_type == "NONE"):
        return NewsConfidence.LOW

    return NewsConfidence.MEDIUM


# ── NEWS ITEM ─────────────────────────────────────────────────────────────────

@dataclass
class NewsItem:
    headline:       str
    source:         str
    published_at:   str             # ISO timestamp
    sentiment:      Optional[float]  # -1.0 tot +1.0 (Finnhub sentiment)
    confidence:     NewsConfidence   = NewsConfidence.MEDIUM
    published_unix: Optional[int]    = None  # Unix timestamp voor sortering


# ── KEYWORD CLASSIFICATIE ─────────────────────────────────────────────────────

_STRONG_KEYWORDS = [
    # Earnings
    "earnings beat", "beats estimate", "exceeds expectations", "record revenue",
    "record earnings", "blowout quarter", "blowout earnings", "guidance raised",
    "raised guidance", "raised full-year", "revenue beat",
    # Deals / Contracts
    "contract awarded", "government contract", "dod contract", "pentagon contract",
    "billion dollar contract", "multibillion", "acquisition", "merger", "buyout",
    "joint venture announced",
    # Regulatory
    "fda approval", "fda approved", "clearance granted", "authorized",
    # Capital
    "buyback", "share repurchase", "dividend increase", "special dividend",
]

_MODERATE_KEYWORDS = [
    "upgrade", "upgraded to buy", "outperform", "overweight",
    "partnership", "collaboration agreement", "strategic alliance",
    "expansion", "new product", "product launch", "launch announced",
    "deal signed", "letter of intent", "loi signed",
    "quarterly results", "q1 results", "q2 results", "q3 results", "q4 results",
]

_WEAK_KEYWORDS = [
    "explores", "considering", "evaluating", "plans to", "intends to",
    "announces", "appoints", "hires", "joins", "update", "conference",
    "presentation", "webinar", "investor day",
]

_NEGATIVE_KEYWORDS = [
    "investigation", "lawsuit", "fraud", "restates", "restatement",
    "bankruptcy", "chapter 11", "delisted", "sec charges", "class action",
    "recall", "safety issue", "downgrade", "cuts guidance", "guidance cut",
    "revenue miss", "earnings miss", "below expectations",
]


def classify_catalyst_from_headlines(news: list[NewsItem]) -> tuple[str, str, list[str]]:
    """
    Bepaalt catalyst type, beschrijving en negatieve signalen.

    Returns:
        (catalyst_type, description, negative_flags)
        catalyst_type: STRONG/MODERATE/WEAK/NONE
    """
    if not news:
        return "NONE", "Geen nieuws beschikbaar", []

    negative_flags: list[str] = []
    best_type   = "NONE"
    best_desc   = ""
    best_conf   = 99   # laagste = beste (voor sortering)

    # Rangorde: STRONG > MODERATE > WEAK > NONE
    type_rank = {"STRONG": 0, "MODERATE": 1, "WEAK": 2, "NONE": 3}

    for item in news:
        h = item.headline.lower()

        # Negatieve signalen scannen
        for kw in _NEGATIVE_KEYWORDS:
            if kw in h:
                negative_flags.append(f"{kw}: {item.headline[:60]}")

        # Positieve classificatie
        cat = "NONE"
        if any(kw in h for kw in _STRONG_KEYWORDS):
            cat = "STRONG"
        elif any(kw in h for kw in _MODERATE_KEYWORDS):
            cat = "MODERATE"
        elif any(kw in h for kw in _WEAK_KEYWORDS):
            cat = "WEAK"
        else:
            cat = "MODERATE"  # nieuws aanwezig maar onbekend = matig

        if type_rank[cat] < type_rank[best_type]:
            best_type = cat
            best_desc = item.headline
            best_conf = type_rank[cat]

    if best_desc == "" and news:
        best_desc = news[0].headline
        best_type = "MODERATE"

    return best_type, best_desc, list(set(negative_flags))


# ── FINNHUB CLIENT ────────────────────────────────────────────────────────────

def get_news(ticker: str, hours: int = 48) -> list[NewsItem]:
    """
    Haalt nieuws op voor ticker.

    Met FINNHUB_API_KEY: echte Finnhub data.
    Zonder key: lege lijst (graceful fallback).

    Altijd een lijst terug — nooit een exception.
    """
    if not _FINNHUB_KEY:
        logger.debug(
            f"news_client: geen FINNHUB_API_KEY ingesteld — "
            f"geen nieuws voor {ticker} (stel in via .env)"
        )
        return []

    return _fetch_finnhub_news(ticker, hours)


def _fetch_finnhub_news(ticker: str, hours: int) -> list[NewsItem]:
    """Haalt nieuws op via Finnhub company-news endpoint."""
    try:
        import httpx
    except ImportError:
        logger.warning("news_client: httpx niet geïnstalleerd — geen Finnhub nieuws")
        return []

    now      = datetime.now(timezone.utc)
    from_dt  = now - timedelta(hours=hours)

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": ticker.upper(),
        "from":   from_dt.strftime("%Y-%m-%d"),
        "to":     now.strftime("%Y-%m-%d"),
        "token":  _FINNHUB_KEY,
    }

    try:
        r = httpx.get(url, params=params, timeout=5.0)
        r.raise_for_status()
        raw = r.json()

        if not isinstance(raw, list):
            logger.warning(f"news_client: onverwacht formaat voor {ticker}")
            return []

        items: list[NewsItem] = []
        for article in raw[:15]:  # max 15 artikelen
            try:
                unix_ts = article.get("datetime", 0)
                pub_iso = (
                    datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat()
                    if unix_ts else now.isoformat()
                )
                headline = article.get("headline", "").strip()
                source   = article.get("source",   "").strip()

                if not headline:
                    continue

                item = NewsItem(
                    headline=headline,
                    source=source,
                    published_at=pub_iso,
                    sentiment=article.get("sentiment"),
                    published_unix=unix_ts,
                )
                # Confidence nog niet ingesteld (classifier doet dat later)
                items.append(item)

            except Exception as exc:
                logger.debug(f"news_client: artikel parse fout: {exc}")
                continue

        # Sorteer op recency (nieuwste eerst)
        items.sort(key=lambda x: x.published_unix or 0, reverse=True)
        logger.info(f"news_client: {len(items)} artikelen voor {ticker}")
        return items

    except Exception as exc:
        logger.warning(f"news_client: Finnhub fout voor {ticker}: {exc}")
        return []


def has_sec_flag(ticker: str) -> bool:
    """
    Controleert op SEC investigation keywords in nieuws.
    Vereist FINNHUB_API_KEY. Retourneert False zonder key.
    """
    if not _FINNHUB_KEY:
        return False
    try:
        news = _fetch_finnhub_news(ticker, hours=168)  # 7 dagen
        SEC_KEYWORDS = [
            "sec investigation", "securities fraud", "class action",
            "doj investigation", "subpoena", "sec charges",
            "securities and exchange", "class-action",
        ]
        for item in news:
            h = item.headline.lower()
            if any(kw in h for kw in SEC_KEYWORDS):
                logger.warning(f"news_client: SEC-gerelateerd nieuws voor {ticker}: {item.headline[:60]}")
                return True
    except Exception as exc:
        logger.debug(f"news_client: SEC check fout voor {ticker}: {exc}")
    return False
