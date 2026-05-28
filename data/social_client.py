"""
data/social_client.py
Social Velocity Client — v2.4 (Architecture Prep)

Status: Stub. Architectuur gereed voor StockTwits/Reddit integratie.

Huidig gedrag:
    Retourneert altijd SocialData(mentions_today=0, mentions_avg=1).
    social_acceleration in score = 0 pts.

Fase 3: Echte StockTwits integratie.
    API endpoint: https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json
    Gratis, geen API key nodig voor basis gebruik.
    Rate limit: 200 requests/hour (unauthenticated).

Metric die telt: VELOCITY (versnelling), niet totaal.
    mentions_today = 4000 bij normaal 200 = 20x = VIRAL
    mentions_today = 400  bij normaal 200 = 2x  = ELEVATED

Niet te implementeren zonder echte data:
    - Sentiment analyse op post-tekst
    - Influencer weging (verified accounts)
    - Platform cross-referencing (Twitter/X)
"""

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SocialData:
    """Social media mention statistieken voor één ticker."""
    ticker:              str
    mentions_today:      int            # Mentions in de afgelopen 24u
    mentions_avg:        int            # 20-daags gemiddelde per dag
    velocity:            float          # mentions_today / mentions_avg
    platform:            str            # "stocktwits" | "reddit" | "combined"
    available:           bool           # True als echte data beschikbaar
    note:                Optional[str]  # "Placeholder v2.4" of None


def get_social_data(ticker: str) -> SocialData:
    """
    Haalt social velocity data op voor ticker.

    v2.4: Retourneert altijd placeholder data.
    Fase 3: Echte StockTwits integratie.

    Retourneert altijd SocialData — nooit een exception.
    """
    # TODO fase 3: StockTwits integratie
    # try:
    #     import httpx
    #     url = f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
    #     r = httpx.get(url, timeout=3.0)
    #     messages = r.json().get("messages", [])
    #     mentions_today = len(messages)  # simpele proxy
    #     ...

    logger.debug(
        f"social_client: placeholder voor {ticker} "
        f"(fase 3: StockTwits integratie)"
    )

    return SocialData(
        ticker=ticker.upper(),
        mentions_today=0,
        mentions_avg=1,       # Voorkom deling door nul in score engine
        velocity=0.0,
        platform="placeholder",
        available=False,
        note="Social data niet beschikbaar (fase 3: StockTwits)",
    )


def is_social_available() -> bool:
    """Geeft True als social data beschikbaar is (altijd False in v2.4)."""
    return False
