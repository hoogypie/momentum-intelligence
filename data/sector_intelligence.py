"""
data/sector_intelligence.py
Sector Intelligence — v2.4

Berekent dynamische sector heat op basis van gecachede leader data.
Vervangt de handmatige heat waarden in sectors.json met algoritmische berekeningen.

Werkwijze:
    1. Laad sectoren uit sectors.json
    2. Controleer cache op leader data
    3. Bereken heat score op basis van:
       - Gemiddelde relative strength van leaders vs SPY
       - Gemiddelde volume anomaly van leaders
       - Recente prijs momentum (dag %)
    4. Blend met statische heat (60% dynamisch, 40% static)
       → voorkomt extreme schommelingen bij weinig cache data

Fallback: Als geen leaders gecached zijn → gebruik statische JSON waarde.

Cache-first: deze module werkt ALLEEN op gecachede data.
    Geen extra Yahoo Finance requests. Cache warming via /analyze endpoint.
"""

import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_SECTORS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "sectors.json"
)


def _load_static_sectors() -> list[dict]:
    """Laadt ruwe sector data uit sectors.json."""
    try:
        with open(_SECTORS_PATH) as f:
            return json.load(f).get("sectors", [])
    except Exception as exc:
        logger.warning(f"sector_intelligence: sectors.json laden mislukt: {exc}")
        return []


def _calc_leader_heat(cached_data: dict) -> float:
    """
    Berekent een heat proxy (0-100) voor één leader vanuit cache data.

    Formule:
        RS component  = 50 + (dag_change * 3)   → 50 basislijn, bijgesteld door dag %
        Vol component = min(100, rv * 15)         → volume ratio geschaald
        Heat proxy    = (RS + Vol) / 2
    """
    day_change   = cached_data.get("day_change_pct",  0.0)
    volume_today = cached_data.get("volume_today",     0)
    avg_volume   = cached_data.get("avg_volume_20d",   1)

    rv = volume_today / max(avg_volume, 1)

    rs_score  = max(0.0, min(100.0, 50.0 + day_change * 3))
    vol_score = min(100.0, rv * 15)

    return (rs_score + vol_score) / 2


def get_dynamic_sector_heat(
    sector_id: str,
    static_heat: int,
    leaders:    list[str],
    cache_fn,
    blend_weight: float = 0.6,
) -> int:
    """
    Berekent dynamische sector heat.

    Args:
        sector_id:    Sector ID (voor logging)
        static_heat:  Statische heat uit sectors.json (fallback)
        leaders:      Lijst van leader tickers
        cache_fn:     Functie die get_cached(ticker) aanroept
        blend_weight: Gewicht van dynamische score (0-1). Default 0.6.

    Returns:
        Integer heat score 0-100
    """
    if not leaders:
        return static_heat

    dynamic_scores = []

    for leader in leaders:
        try:
            entry = cache_fn(leader)
            if entry and entry.data:
                score = _calc_leader_heat(entry.data)
                dynamic_scores.append(score)
                logger.debug(
                    f"sector_intelligence: {sector_id}/{leader} "
                    f"heat_proxy={score:.1f}"
                )
        except Exception as exc:
            logger.debug(f"sector_intelligence: {leader} cache fout: {exc}")

    if not dynamic_scores:
        logger.debug(
            f"sector_intelligence: {sector_id} geen cache data — "
            f"gebruik statische heat {static_heat}"
        )
        return static_heat

    avg_dynamic = sum(dynamic_scores) / len(dynamic_scores)

    # Blend: 60% dynamisch + 40% statisch
    blended = blend_weight * avg_dynamic + (1 - blend_weight) * static_heat
    result  = max(0, min(100, int(round(blended))))

    logger.info(
        f"sector_intelligence: {sector_id} heat {static_heat}→{result} "
        f"(dynamic={avg_dynamic:.1f}, leaders_cached={len(dynamic_scores)}/{len(leaders)})"
    )
    return result


def get_all_sector_heats(cache_fn) -> dict[str, int]:
    """
    Berekent dynamische heat voor alle sectoren.

    Returns:
        Dict van sector_id → heat (0-100)
    """
    sectors = _load_static_sectors()
    result  = {}

    for s in sectors:
        sid    = s.get("id", "")
        static = s.get("heat", 50)
        leaders = s.get("leaders", [])

        result[sid] = get_dynamic_sector_heat(
            sector_id=sid,
            static_heat=static,
            leaders=leaders,
            cache_fn=cache_fn,
        )

    return result


def enrich_sector_config(sector_data: dict, cache_fn) -> dict:
    """
    Verrijkt sector dict met dynamische heat.
    Retourneert kopie van sector_data met bijgewerkte heat.
    """
    enriched     = dict(sector_data)
    static_heat  = sector_data.get("heat", 50)
    leaders      = sector_data.get("leaders", [])
    sector_id    = sector_data.get("id", "unknown")

    enriched["heat"] = get_dynamic_sector_heat(
        sector_id=sector_id,
        static_heat=static_heat,
        leaders=leaders,
        cache_fn=cache_fn,
    )
    enriched["heat_source"] = "dynamic" if leaders else "static"
    return enriched
