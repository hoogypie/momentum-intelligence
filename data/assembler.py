"""
data/assembler.py
Assembler — v2.0

Bouwt een TickerInput van losse data bronnen:
    yahoo_client  → prijs, volume, market cap, float
    news_client   → headlines (placeholder in v2.0)
    sectors.json  → sector heat, leaders, sympathy
    SPY return    → relative strength

De score engine (scoring_v1_2.py) verandert niet.
Alleen de input verandert: mock data → live data.

Beperkingen in v2.0 (zie KNOWN_FAILURE_MODES.md):
    - Catalyst type: altijd NONE (news_client is placeholder)
    - Social acceleration: altijd 0 (geen StockTwits in v2.0)
    - SEC/class action: altijd False (handmatige check)
    - Data quality velden informeren de gebruiker over deze beperkingen
"""

import json
import os
import logging
from typing import Optional

from data.yahoo_client import get_quote, get_spy_return, QuoteData
from data.news_client  import get_news, has_sec_flag, NewsItem
from scoring.scoring_v1_2 import (
    TickerInput, SectorConfig,
    CatalystType, RelativeStrength,
)

logger = logging.getLogger(__name__)

_SECTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "sectors.json"
)


# ── SECTOR LOOKUP ─────────────────────────────────────────────────────────────

def _load_sectors() -> dict:
    """Laadt config/sectors.json. Cached na eerste aanroep."""
    if not hasattr(_load_sectors, "_cache"):
        try:
            with open(_SECTORS_PATH) as f:
                _load_sectors._cache = json.load(f)
        except Exception as exc:
            logger.warning(f"assembler: sectors.json laden mislukt: {exc}")
            _load_sectors._cache = {"sectors": []}
    return _load_sectors._cache


def _find_sector(ticker: str) -> SectorConfig:
    """
    Zoekt sector op basis van ticker in leaders + sympathy lijsten.
    Geeft neutrale sector terug als ticker niet gevonden is.
    """
    data = _load_sectors()
    ticker_upper = ticker.upper()

    for s in data.get("sectors", []):
        if (ticker_upper in s.get("leaders", []) or
                ticker_upper in s.get("sympathy", [])):
            return SectorConfig(
                sector_id=s["id"],
                sector_label=s["label"],
                heat=s["heat"],
                phase=s.get("phase", 1),
                leaders=s.get("leaders", []),
                sympathy=s.get("sympathy", []),
            )

    logger.debug(f"assembler: {ticker} niet in sectors.json — neutrale sector")
    return SectorConfig(
        sector_id="unknown",
        sector_label="UNKNOWN",
        heat=50,
        phase=1,
        leaders=[],
        sympathy=[],
    )


# ── CLASSIFIERS ───────────────────────────────────────────────────────────────

def _classify_catalyst(news: list[NewsItem]) -> tuple[CatalystType, str]:
    """
    Bepaalt catalyst kwaliteit op basis van headline keywords.
    Geeft NONE terug als er geen nieuws is (placeholder in v2.0).
    """
    if not news:
        return CatalystType.NONE, "Geen nieuws opgehaald (news_client placeholder)"

    # Meest recente headline
    headline = news[0].headline.lower()

    STRONG = [
        "earnings beat", "beats estimate", "exceeds", "record revenue",
        "contract awarded", "government contract", "dod contract",
        "guidance raised", "raised guidance", "acquisition", "merger",
        "ipo", "fda approval", "blowout", "massive beat",
    ]
    MODERATE = [
        "upgrade", "partnership", "collaboration", "expansion",
        "new product", "launch", "deal signed", "analyst", "outperform",
    ]
    WEAK = [
        "explores", "considers", "plans to", "evaluates", "looking at",
        "announces", "update", "appoints",
    ]

    if any(kw in headline for kw in STRONG):
        return CatalystType.STRONG, news[0].headline
    if any(kw in headline for kw in MODERATE):
        return CatalystType.MODERATE, news[0].headline
    if any(kw in headline for kw in WEAK):
        return CatalystType.WEAK, news[0].headline

    return CatalystType.MODERATE, news[0].headline  # nieuws aanwezig = minimaal MODERATE


def _classify_relative_strength(
    stock_pct: float,
    spy_pct: float,
) -> RelativeStrength:
    """
    Vergelijkt dagsrendement van stock met SPY.
    """
    diff = stock_pct - spy_pct

    if spy_pct < 0 and stock_pct > 0:
        return RelativeStrength.STRONG_POSITIVE    # groen bij rode markt
    if diff > 1.5:
        return RelativeStrength.MODERATE_POSITIVE  # outperformt markt
    if diff < -1.5:
        return RelativeStrength.UNDERPERFORMING
    return RelativeStrength.NEUTRAL


# ── DATA QUALITY ──────────────────────────────────────────────────────────────

def _data_quality(quote: QuoteData, news: list[NewsItem]) -> dict:
    """
    Transparantie over welke data beschikbaar was.
    Wordt meegestuurd in de API response.
    """
    return {
        "price_available":     quote.price > 0,
        "volume_available":    quote.volume_today > 0,
        "float_available":     quote.float_shares is not None,
        "premarket_available": quote.premarket_price is not None,
        "news_available":      len(news) > 0,
        "social_available":    False,   # fase 2.1: StockTwits
        "sec_check_automated": False,   # fase 2.1: Finnhub scan
        "fetch_error":         quote.error,
    }


# ── MAIN ASSEMBLER ────────────────────────────────────────────────────────────

def build_ticker_input(ticker: str) -> tuple[TickerInput, dict]:
    """
    Bouwt TickerInput van live data bronnen.

    Returns:
        (TickerInput, data_quality_dict)

    Gooit nooit een exception — veilige defaults bij elke ophaalfout.

    Beperkingen v2.0:
        - catalyst_type = NONE (news placeholder)
        - social_mentions = 0/1 (geen StockTwits)
        - has_sec_investigation = False (handmatig)
    """
    ticker = ticker.upper().strip()

    # Data ophalen
    quote = get_quote(ticker)
    news  = get_news(ticker, hours=48)
    spy   = get_spy_return()

    # Classificaties
    catalyst_type, catalyst_desc = _classify_catalyst(news)
    rs = _classify_relative_strength(quote.day_change_pct, spy)
    sector = _find_sector(ticker)
    sec_flag = has_sec_flag(ticker)     # False in v2.0
    quality = _data_quality(quote, news)

    input_obj = TickerInput(
        ticker=ticker,

        # Prijs & volume
        price=quote.price,
        day_change_pct=quote.day_change_pct,
        premarket_pct=quote.premarket_pct,
        volume_today=quote.volume_today,
        avg_volume_20d=max(quote.avg_volume_20d, 1),

        # Bedrijfsdata
        market_cap_usd=quote.market_cap or 1_000_000_000,  # default SMALL
        float_shares=quote.float_shares,
        is_cfd_only=False,              # handmatige override via query param later

        # Fundamentele context
        catalyst_type=catalyst_type,
        catalyst_description=catalyst_desc,
        relative_strength=rs,
        sector=sector,

        # Social — placeholder v2.0
        social_mentions_today=0,
        social_mentions_avg=1,          # voorkom deling door nul

        # Risico flags — handmatig in v2.0
        has_sec_investigation=sec_flag,
        has_class_action=False,
        insider_sells_90d=0,
    )

    return input_obj, quality
