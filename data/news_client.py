"""
data/news_client.py
Nieuws client — v2.0 (placeholder)

Huidig gedrag: geeft altijd een lege lijst terug.
Assembler behandelt lege lijst als catalyst_type=NONE.

Fase 2.1: vervangen door echte Finnhub integratie.
    - API key via .env (FINNHUB_API_KEY)
    - Endpoint: https://finnhub.io/api/v1/company-news
    - Gratis tier: 60 calls/min
    - Headlines van afgelopen 48u ophalen
    - classify_catalyst() in assembler.py verwerkt de headlines

Zie FM-008 in KNOWN_FAILURE_MODES.md:
    SEC keywords moeten ook via Finnhub worden gescand zodra de
    integratie live is. Tot dan: has_sec_investigation=False (handmatig).
"""

from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    headline: str
    source: str
    published_at: str           # ISO timestamp
    sentiment: Optional[float]  # -1.0 tot +1.0 als beschikbaar


def get_news(ticker: str, hours: int = 48) -> list[NewsItem]:
    """
    Haalt nieuws op voor ticker van afgelopen `hours` uur.

    Huidig: placeholder — geeft altijd lege lijst terug.
    Fase 2.1: echte Finnhub integratie.

    Returns altijd een lijst, nooit een exception.
    """
    # TODO fase 2.1: Finnhub integratie
    # import os, httpx
    # api_key = os.getenv("FINNHUB_API_KEY")
    # if not api_key:
    #     logger.warning("FINNHUB_API_KEY niet ingesteld — geen nieuws")
    #     return []
    # ... fetch en parse ...

    logger.debug(f"news_client: placeholder — geen nieuws voor {ticker}")
    return []


def has_sec_flag(ticker: str) -> bool:
    """
    Controleert op SEC investigation of class action in nieuws.

    Huidig: altijd False (handmatige check vereist).
    Fase 2.1: scan Finnhub headlines op keywords.

    Zie FM-008 in KNOWN_FAILURE_MODES.md.
    """
    # TODO fase 2.1:
    # news = get_news(ticker, hours=168)  # 7 dagen
    # SEC_KEYWORDS = ["sec investigation", "class action", "securities fraud",
    #                 "subpoena", "doj investigation"]
    # headlines = [n.headline.lower() for n in news]
    # return any(kw in h for kw in SEC_KEYWORDS for h in headlines)
    return False
