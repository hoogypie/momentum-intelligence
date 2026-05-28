"""
data/assembler.py
Assembler — v2.1

Wijzigingen t.o.v. v2.0:
    - Accepteert TickerSnapshot i.p.v. QuoteData
    - Alle missing-field scenarios afgedekt
    - DataQuality schema gebruikt Pydantic
    - Missing market_cap → SMALL tier default, duidelijk gelogd
    - Missing float → neutrale score (4/8 pts), geen crash
    - Missing premarket → 0.0, geen crash
    - Missing volume → avg_volume_20d als fallback
"""

import json
import os
import logging
from typing import Optional

from data.yahoo_client import get_snapshot, get_spy_return
from data.news_client  import get_news, has_sec_flag, NewsItem
from schemas.ticker_snapshot import DataConfidence
from schemas.scoring_response import DataQuality
from scoring.scoring_v1_2 import (
    TickerInput, SectorConfig,
    CatalystType, RelativeStrength,
)

logger = logging.getLogger(__name__)

_SECTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "sectors.json"
)

# Neutrale default sector — gebruikt als ticker niet in sectors.json staat
_DEFAULT_SECTOR = SectorConfig(
    sector_id="unknown",
    sector_label="UNKNOWN",
    heat=50,
    phase=1,
    leaders=[],
    sympathy=[],
)


# ── SECTOR LOOKUP ──────────────────────────────────────────────────────────────

def _load_sectors() -> dict:
    if not hasattr(_load_sectors, "_cache"):
        try:
            with open(_SECTORS_PATH) as f:
                _load_sectors._cache = json.load(f)
        except Exception as exc:
            logger.warning(f"assembler: sectors.json laden mislukt: {exc}")
            _load_sectors._cache = {"sectors": []}
    return _load_sectors._cache


def _find_sector(ticker: str) -> SectorConfig:
    ticker_upper = ticker.upper()
    for s in _load_sectors().get("sectors", []):
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
    return _DEFAULT_SECTOR


# ── CLASSIFIERS ────────────────────────────────────────────────────────────────

def _classify_catalyst(news: list[NewsItem]) -> tuple[CatalystType, str]:
    if not news:
        return CatalystType.NONE, "Geen nieuws opgehaald (news_client placeholder)"

    headline = news[0].headline.lower()

    STRONG = [
        "earnings beat", "beats estimate", "exceeds", "record revenue",
        "contract awarded", "government contract", "dod contract",
        "guidance raised", "raised guidance", "acquisition", "merger",
        "fda approval", "blowout", "massive beat",
    ]
    MODERATE = [
        "upgrade", "partnership", "collaboration", "expansion",
        "new product", "launch", "deal signed", "analyst", "outperform",
    ]
    WEAK = [
        "explores", "considers", "plans to", "evaluates",
        "announces", "update", "appoints",
    ]

    if any(kw in headline for kw in STRONG):
        return CatalystType.STRONG,   news[0].headline
    if any(kw in headline for kw in MODERATE):
        return CatalystType.MODERATE, news[0].headline
    if any(kw in headline for kw in WEAK):
        return CatalystType.WEAK,     news[0].headline

    return CatalystType.MODERATE, news[0].headline


def _classify_relative_strength(stock_pct: float, spy_pct: float) -> RelativeStrength:
    diff = stock_pct - spy_pct
    if spy_pct < 0 and stock_pct > 0:   return RelativeStrength.STRONG_POSITIVE
    if diff > 1.5:                        return RelativeStrength.MODERATE_POSITIVE
    if diff < -1.5:                       return RelativeStrength.UNDERPERFORMING
    return RelativeStrength.NEUTRAL


# ── MISSING FIELD HANDLING ────────────────────────────────────────────────────

def _safe_market_cap(snapshot) -> float:
    """
    Missing market_cap → $1B default (SMALL tier).
    Engine scoort altijd — tier bepaalt sizing, niet de score zelf.
    """
    if snapshot.market_cap and snapshot.market_cap > 0:
        return snapshot.market_cap
    logger.debug(f"assembler: market_cap ontbreekt voor {snapshot.ticker} — default $1B")
    return 1_000_000_000


def _safe_volume(snapshot) -> tuple[int, int]:
    """
    Missing volume → gebruik avg als volume_today, 1 als avg om / 0 te voorkomen.
    Engine berekent relative volume — 0/1 = 0 = geen volume penalty.
    """
    vol   = max(snapshot.volume_today,   0)
    avg   = max(snapshot.avg_volume_20d, 1)
    if vol == 0 and avg > 1:
        # volume_today ontbreekt — gebruik avg als proxy (1x = neutraal)
        logger.debug(f"assembler: volume_today=0 voor {snapshot.ticker} — avg gebruikt")
        return avg, avg
    return vol, avg


# ── DATA QUALITY ───────────────────────────────────────────────────────────────

def _build_data_quality(snapshot, news: list[NewsItem]) -> DataQuality:
    return DataQuality(
        price_available     = snapshot.price > 0,
        volume_available    = snapshot.volume_today > 0,
        float_available     = snapshot.float_shares is not None,
        premarket_available = snapshot.premarket_available,
        news_available      = len(news) > 0,
        social_available    = False,
        sec_check_automated = False,
        confidence          = snapshot.confidence,
        fetch_error         = snapshot.error,
        retries_used        = snapshot.retries_used,
    )


# ── MAIN ASSEMBLER ─────────────────────────────────────────────────────────────

def build_ticker_input(ticker: str) -> tuple[TickerInput, DataQuality]:
    """
    Bouwt TickerInput van live data bronnen.

    Returns: (TickerInput, DataQuality)
    Nooit een exception — graceful defaults bij elke ophaalfout.

    Veld-by-veld fallback strategie:
        price=0        → return MISSING, 422 in API
        volume=0       → avg_volume als fallback
        market_cap=None → $1B default (SMALL tier)
        float=None     → neutrale score (4/8 pts)
        premarket=0    → geen pre-market component
        news=[]        → catalyst=NONE (conservatief)
        social=0/1     → geen social score
        sec=False      → handmatige check vereist
    """
    ticker   = ticker.upper().strip()
    snapshot = get_snapshot(ticker)
    news     = get_news(ticker, hours=48)
    spy      = get_spy_return()
    quality  = _build_data_quality(snapshot, news)

    catalyst_type, catalyst_desc = _classify_catalyst(news)
    rs      = _classify_relative_strength(snapshot.day_change_pct, spy)
    sector  = _find_sector(ticker)
    vol, avg_vol = _safe_volume(snapshot)

    input_obj = TickerInput(
        ticker=ticker,

        price          = snapshot.price,
        day_change_pct = snapshot.day_change_pct,
        premarket_pct  = snapshot.premarket_pct,
        volume_today   = vol,
        avg_volume_20d = avg_vol,

        market_cap_usd = _safe_market_cap(snapshot),
        float_shares   = snapshot.float_shares,
        is_cfd_only    = False,

        catalyst_type        = catalyst_type,
        catalyst_description = catalyst_desc,
        relative_strength    = rs,
        sector               = sector,

        social_mentions_today = 0,
        social_mentions_avg   = 1,

        has_sec_investigation = has_sec_flag(ticker),
        has_class_action      = False,
        insider_sells_90d     = 0,
    )

    return input_obj, quality
