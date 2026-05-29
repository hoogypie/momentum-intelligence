"""
data/assembler.py
Assembler — v2.5

Wijzigingen t.o.v. v2.4:
    - Gebruikt data/finnhub_client.py voor ruwe nieuwsfetch
    - Gebruikt data/catalyst_classifier.py voor catalyst-detectie
      inclusief OWN / SECTOR / SYMPATHY onderscheid
    - Recency weighting en source-tier multipliers actief
    - Raw headlines beschikbaar in CatalystResult (voor debug/output)
    - news_client.py blijft als fallback voor legacy code
"""

import json, os, logging
from typing import Optional

from data.yahoo_client       import get_snapshot, get_spy_return
from data.finnhub_client     import fetch_company_news, is_available as finnhub_available
from data.news_client        import get_news, has_sec_flag, NewsItem
from data.catalyst_classifier import (
    classify, classify_from_news_items, CatalystResult, CatalystSource,
)
from data.social_client      import get_social_data
from data.market_session     import get_market_session, MarketSession
from data.sector_intelligence import get_dynamic_sector_heat
from schemas.ticker_snapshot  import DataConfidence
from schemas.scoring_response import DataQuality
from scoring.scoring_v1_2    import (
    TickerInput, SectorConfig, CatalystType, RelativeStrength,
)

logger = logging.getLogger(__name__)

_SECTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "sectors.json",
)

_DEFAULT_SECTOR = SectorConfig(
    sector_id="unknown", sector_label="UNKNOWN",
    heat=50, phase=1, leaders=[], sympathy=[],
)


# ── SECTOR LOOKUP ─────────────────────────────────────────────────────────────

def _load_sectors() -> dict:
    if not hasattr(_load_sectors, "_cache"):
        try:
            with open(_SECTORS_PATH) as f:
                _load_sectors._cache = json.load(f)
        except Exception as exc:
            logger.warning(f"assembler: sectors.json laden mislukt: {exc}")
            _load_sectors._cache = {"sectors": []}
    return _load_sectors._cache


def _find_sector(ticker: str, cache_fn=None) -> SectorConfig:
    """
    Zoekt sector op. Met cache_fn: berekent dynamische heat.
    Zonder cache_fn: gebruikt statische heat.
    """
    u = ticker.upper()
    for s in _load_sectors().get("sectors", []):
        if u in s.get("leaders", []) or u in s.get("sympathy", []):
            heat = s["heat"]
            if cache_fn:
                try:
                    heat = get_dynamic_sector_heat(
                        sector_id=s["id"],
                        static_heat=s["heat"],
                        leaders=s.get("leaders", []),
                        cache_fn=cache_fn,
                    )
                except Exception as exc:
                    logger.debug(f"assembler: dynamic heat mislukt: {exc}")

            return SectorConfig(
                sector_id=s["id"], sector_label=s["label"],
                heat=heat, phase=s.get("phase", 1),
                leaders=s.get("leaders", []), sympathy=s.get("sympathy", []),
            )
    return _DEFAULT_SECTOR


# ── CATALYST CLASSIFICATIE ────────────────────────────────────────────────────

def _run_catalyst_classification(
    ticker: str,
    sector: SectorConfig,
    hours:  int = 48,
) -> CatalystResult:
    """
    Haalt nieuws op en classificeert catalyst.

    Prioriteit:
    1. finnhub_client als FINNHUB_API_KEY beschikbaar
    2. news_client fallback (legacy, backward compat)
    3. Lege CatalystResult als geen van beide beschikbaar
    """
    if finnhub_available():
        items = fetch_company_news(ticker, hours=hours)
        return classify(
            ticker          = ticker,
            items           = items,
            sector_leaders  = sector.leaders,
            sector_sympathy = sector.sympathy,
        )

    legacy_news = get_news(ticker, hours=hours)
    if legacy_news:
        return classify_from_news_items(
            ticker   = ticker,
            items    = legacy_news,
            leaders  = sector.leaders,
            sympathy = sector.sympathy,
        )

    from data.catalyst_classifier import CatalystConfidence
    return CatalystResult(
        catalyst_type   = "NONE",
        catalyst_source = CatalystSource.NONE,
        confidence      = CatalystConfidence.LOW,
        score           = 0.0,
        top_headline    = "",
        description     = "Geen Finnhub key en geen legacy news beschikbaar",
        news_available  = False,
        articles_used   = 0,
    )


def _catalyst_result_to_engine_type(
    result: CatalystResult,
) -> tuple[CatalystType, str, list[str]]:
    """
    Converteert CatalystResult naar engine-typen.
    Returns: (CatalystType, description, negative_flags)
    """
    cat_map = {
        "STRONG":   CatalystType.STRONG,
        "MODERATE": CatalystType.MODERATE,
        "WEAK":     CatalystType.WEAK,
        "NONE":     CatalystType.NONE,
    }
    engine_type = cat_map.get(result.catalyst_type, CatalystType.NONE)
    return engine_type, result.description, result.negative_flags


def _classify_catalyst(news: list) -> tuple[CatalystType, str, list[str]]:
    """
    Backward-compat shim. Gebruikt door bestaande tests in test_backend.py.
    Delegeert naar classify_from_news_items() in catalyst_classifier.py.
    """
    result = classify_from_news_items("UNKNOWN", news)
    return _catalyst_result_to_engine_type(result)


# ── RELATIVE STRENGTH ─────────────────────────────────────────────────────────

def _classify_relative_strength(
    stock_pct: float,
    spy_pct:   float,
) -> RelativeStrength:
    diff = stock_pct - spy_pct

    if spy_pct < -0.3 and stock_pct > 0.3:
        return RelativeStrength.STRONG_POSITIVE

    if diff > 2.5:    return RelativeStrength.STRONG_POSITIVE
    if diff > 1.0:    return RelativeStrength.MODERATE_POSITIVE
    if diff < -2.0:   return RelativeStrength.UNDERPERFORMING
    return RelativeStrength.NEUTRAL


# ── MISSING FIELD HANDLING ────────────────────────────────────────────────────

def _safe_market_cap(snapshot) -> float:
    if snapshot.market_cap and snapshot.market_cap > 0:
        return snapshot.market_cap
    return 1_000_000_000


def _safe_volume(snapshot) -> tuple[int, int]:
    vol = max(snapshot.volume_today, 0)
    avg = max(snapshot.avg_volume_20d, 1)
    if vol == 0 and avg > 1:
        return avg, avg
    return vol, avg


# ── DATA QUALITY ──────────────────────────────────────────────────────────────

def _build_data_quality(
    snapshot,
    catalyst_result: CatalystResult,
    session: MarketSession,
) -> DataQuality:
    return DataQuality(
        price_available     = snapshot.price > 0,
        volume_available    = snapshot.volume_today > 0,
        float_available     = snapshot.float_shares is not None,
        premarket_available = snapshot.premarket_available,
        news_available      = catalyst_result.news_available,
        social_available    = False,
        sec_check_automated = bool(os.getenv("FINNHUB_API_KEY", "")),
        confidence          = snapshot.confidence,
        fetch_error         = snapshot.error,
        retries_used        = snapshot.retries_used,
        cache_hit           = snapshot.cache_hit,
        data_age_seconds    = snapshot.data_age_seconds,
    )


# ── MAIN ASSEMBLER ────────────────────────────────────────────────────────────

def build_ticker_input(
    ticker:        str,
    force_refresh: bool = False,
) -> tuple[TickerInput, DataQuality]:
    """
    Bouwt TickerInput van live/gecachede data.
    v2.5: finnhub_client + catalyst_classifier met OWN/SECTOR/SYMPATHY onderscheid.
    """
    ticker   = ticker.upper().strip()
    snapshot = get_snapshot(ticker, force_refresh=force_refresh)
    social   = get_social_data(ticker)
    spy      = get_spy_return()
    session  = get_market_session()

    from cache.market_cache import get_cached as _cache_fn

    rs     = _classify_relative_strength(snapshot.day_change_pct, spy)
    sector = _find_sector(ticker, cache_fn=_cache_fn)

    cat_result = _run_catalyst_classification(ticker, sector, hours=48)
    catalyst_type, catalyst_desc, neg_flags = _catalyst_result_to_engine_type(cat_result)

    logger.debug(
        "assembler: %s catalyst=%s source=%s confidence=%s score=%.2f",
        ticker,
        cat_result.catalyst_type,
        cat_result.catalyst_source.value,
        cat_result.confidence.value,
        cat_result.score,
    )

    vol, avg_vol = _safe_volume(snapshot)
    quality = _build_data_quality(snapshot, cat_result, session)

    input_obj = TickerInput(
        ticker=ticker,
        price=snapshot.price, day_change_pct=snapshot.day_change_pct,
        premarket_pct=snapshot.premarket_pct,
        volume_today=vol, avg_volume_20d=avg_vol,
        market_cap_usd=_safe_market_cap(snapshot),
        float_shares=snapshot.float_shares,
        is_cfd_only=False,
        catalyst_type=catalyst_type, catalyst_description=catalyst_desc,
        relative_strength=rs, sector=sector,
        social_mentions_today=social.mentions_today,
        social_mentions_avg=social.mentions_avg,
        has_sec_investigation=has_sec_flag(ticker),
        has_class_action=False,
        insider_sells_90d=0,
    )

    if neg_flags:
        logger.warning(
            "assembler: negatieve signalen voor %s: %s",
            ticker, " | ".join(neg_flags[:2]),
        )

    return input_obj, quality
