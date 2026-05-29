"""
data/catalyst_classifier.py
Catalyst Classifier — v1.0

De centrale intelligentielaag voor catalyst-detectie. Ontvangt ruwe
FinnhubNewsItems en retourneert een CatalystResult met:

    - catalyst_type    STRONG / MODERATE / WEAK / NONE
    - catalyst_source  OWN / SECTOR / SYMPATHY / NONE
    - confidence       HIGH / MEDIUM / LOW
    - score            0.0 – 1.0 (gecombineerd)
    - top_headline     Meest relevante headline
    - raw_headlines    Alle gebruikte headlines (voor debug/output)
    - negative_flags   Risicosignalen gevonden in nieuws
    - news_available   Boolean: was er überhaupt nieuws?

Kernregel (D-005):
    Dit bestand bevat GEEN scoring-logica. Het levert alleen
    gestructureerde input aan de score engine. De engine bepaalt
    hoeveel punten een catalyst waard is.

Drie momentum-typen (Spelregel 27):
    OWN      — ticker heeft een eigen, bedrijfsspecifieke catalyst
    SECTOR   — sectorbreed momentum, geen ticker-specifiek nieuws
    SYMPATHY — ticker beweegt mee door een andere ticker in dezelfde sector

Dit onderscheid bepaalt de kwaliteit van de catalyst-score:
    OWN        → volle catalyst-score toegestaan
    SECTOR     → catalyst-score gemaximeerd op MODERATE niveau
    SYMPATHY   → catalyst-score gemaximeerd op WEAK niveau
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from data.finnhub_client import FinnhubNewsItem

logger = logging.getLogger(__name__)

_KEYWORDS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "news_keywords.json",
)


# ── ENUMS ─────────────────────────────────────────────────────────────────────

class CatalystSource(str, Enum):
    OWN      = "OWN"       # Eigen, bedrijfsspecifieke catalyst
    SECTOR   = "SECTOR"    # Sectorbreed momentum — geen eigen catalyst
    SYMPATHY = "SYMPATHY"  # Sympathy move — andere ticker trok omhoog
    NONE     = "NONE"      # Geen nieuws / niet te bepalen


class CatalystConfidence(str, Enum):
    HIGH   = "HIGH"    # Tier-1 bron + <6u + STRONG of MODERATE
    MEDIUM = "MEDIUM"  # Tier-2 of 6-24u of WEAK
    LOW    = "LOW"     # Tier-3 of >24u of geen nieuws


# ── OUTPUT DATACLASS ──────────────────────────────────────────────────────────

@dataclass
class CatalystResult:
    """
    Gestructureerde catalyst-informatie voor de score engine.
    Aangemaakt door classify() — nooit handmatig.
    """
    # Kern output
    catalyst_type:   str              # STRONG / MODERATE / WEAK / NONE
    catalyst_source: CatalystSource
    confidence:      CatalystConfidence
    score:           float            # 0.0 – 1.0

    # Beschrijving
    top_headline:    str              # Meest relevante headline (leeg als geen nieuws)
    description:     str              # Mensleesbare samenvatting van catalyst

    # Raw data voor transparantie / debug
    raw_headlines:   list[str]        = field(default_factory=list)
    negative_flags:  list[str]        = field(default_factory=list)

    # Meta
    news_available:  bool             = False
    articles_used:   int              = 0
    classified_at:   str              = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── KEYWORD CONFIG LADEN ──────────────────────────────────────────────────────

def _load_keywords() -> dict:
    if not hasattr(_load_keywords, "_cache"):
        try:
            with open(_KEYWORDS_PATH) as f:
                _load_keywords._cache = json.load(f)
        except Exception as exc:
            logger.warning("catalyst_classifier: keywords laden mislukt: %s", exc)
            _load_keywords._cache = {}
    return _load_keywords._cache


def _get_tier_keywords(tier: str) -> list[str]:
    kw = _load_keywords()
    return kw.get("catalyst_tiers", {}).get(tier, {}).get("keywords", [])


def _get_recency_weights() -> dict:
    kw = _load_keywords()
    return kw.get("recency_weights", {
        "under_2h":   1.00, "2h_to_6h":  0.90, "6h_to_12h": 0.75,
        "12h_to_24h": 0.60, "24h_to_48h": 0.40, "over_48h":  0.20,
    })


def _get_source_tiers() -> dict:
    kw = _load_keywords()
    return kw.get("source_tiers", {"tier_1": [], "tier_2": [], "tier_3": []})


def _get_own_catalyst_signals() -> list[str]:
    kw = _load_keywords()
    return kw.get("momentum_type_signals", {}).get("own_catalyst_signals", [])


def _get_sympathy_signals() -> list[str]:
    kw = _load_keywords()
    return kw.get("momentum_type_signals", {}).get("sympathy_signals", [])


def _get_sector_signals() -> list[str]:
    kw = _load_keywords()
    return kw.get("momentum_type_signals", {}).get("sector_momentum_signals", [])


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _age_hours(item: FinnhubNewsItem) -> float:
    """Leeftijd van artikel in uren. Max 9999 bij parse-fout."""
    try:
        pub = datetime.fromtimestamp(item.published_unix, tz=timezone.utc)
        return (datetime.now(timezone.utc) - pub).total_seconds() / 3600
    except Exception:
        return 9999.0


def _recency_multiplier(age_hours: float) -> float:
    """Recency modifier: recent nieuws weegt zwaarder."""
    w = _get_recency_weights()
    if age_hours < 2:     return w.get("under_2h",   1.00)
    if age_hours < 6:     return w.get("2h_to_6h",   0.90)
    if age_hours < 12:    return w.get("6h_to_12h",  0.75)
    if age_hours < 24:    return w.get("12h_to_24h", 0.60)
    if age_hours < 48:    return w.get("24h_to_48h", 0.40)
    return w.get("over_48h", 0.20)


def _source_multiplier(source: str) -> float:
    """Source tier → score multiplier. Tier 1 = 1.0, tier 2 = 0.85, tier 3 = 0.65."""
    src    = source.lower()
    tiers  = _get_source_tiers()
    for name in tiers.get("tier_1", []):
        if name in src:
            return 1.00
    for name in tiers.get("tier_3", []):
        if name in src:
            return 0.65
    return 0.85  # tier 2 / onbekend


def _classify_headline_tier(headline: str) -> tuple[str, float]:
    """
    Matcht headline op STRONG/MODERATE/WEAK/NEGATIVE/NONE.
    Retourneert (tier, base_score).
    NEGATIVE retourneert (NEGATIVE, 0.0) — apart afgehandeld.
    """
    h = headline.lower()

    # Negatief eerst — altijd scannen ongeacht positieve matches
    for kw in _get_tier_keywords("NEGATIVE"):
        if kw in h:
            return "NEGATIVE", 0.0

    if any(kw in h for kw in _get_tier_keywords("STRONG")):
        return "STRONG", 1.0
    if any(kw in h for kw in _get_tier_keywords("MODERATE")):
        return "MODERATE", 0.6
    if any(kw in h for kw in _get_tier_keywords("WEAK")):
        return "WEAK", 0.2

    # Nieuws aanwezig maar geen trefwoord → conservatief WEAK
    return "WEAK", 0.15


def _detect_momentum_source(
    headline:       str,
    ticker:         str,
    sector_leaders: list[str],
    sector_sympathy: list[str],
) -> CatalystSource:
    """
    Bepaalt of momentum van eigen catalyst, sector of sympathy komt.

    Logica:
    1. Sympathy-signalen in headline → SYMPATHY
    2. Sector-signalen zonder eigen catalyst → SECTOR
    3. Ticker staat in sympathy-lijst (niet leaders) → licht SYMPATHY-bias
    4. Eigen catalyst-signalen aanwezig → OWN
    5. Default: OWN (artikel over deze ticker = eigen catalyst)
    """
    h   = headline.lower()
    tkr = ticker.upper()

    # Expliciete sympathy-mentions
    if any(sig in h for sig in _get_sympathy_signals()):
        return CatalystSource.SYMPATHY

    # Expliciete sector-breed sentiment (niet ticker-specifiek)
    if any(sig in h for sig in _get_sector_signals()):
        return CatalystSource.SECTOR

    # Structurele positie: sympathy-list vs leaders
    is_sympathy_ticker = tkr in sector_sympathy and tkr not in sector_leaders
    has_own_signal     = any(sig in h for sig in _get_own_catalyst_signals())

    if is_sympathy_ticker and not has_own_signal:
        return CatalystSource.SYMPATHY

    return CatalystSource.OWN


def _source_cap_for_momentum_type(source: CatalystSource) -> str:
    """
    Maximale catalyst-tier per momentum-type.
    Wordt gebruikt als ceiling — een STRONG sympathy-headline
    wordt WEAK omdat het geen eigen catalyst is.
    """
    caps = {
        CatalystSource.OWN:      "STRONG",    # geen cap
        CatalystSource.SECTOR:   "MODERATE",  # sector momentum ≤ MODERATE
        CatalystSource.SYMPATHY: "WEAK",      # sympathy ≤ WEAK
        CatalystSource.NONE:     "NONE",
    }
    return caps[source]


_TIER_RANK = {"STRONG": 3, "MODERATE": 2, "WEAK": 1, "NONE": 0, "NEGATIVE": -1}


def _apply_source_cap(tier: str, source: CatalystSource) -> str:
    """Verlaagt tier als source een lagere ceiling heeft."""
    cap     = _source_cap_for_momentum_type(source)
    if _TIER_RANK.get(tier, 0) > _TIER_RANK.get(cap, 0):
        return cap
    return tier


def _compute_confidence(
    tier:       str,
    source:     str,
    age_hours:  float,
) -> CatalystConfidence:
    """
    Confidence = f(bron kwaliteit, recency, catalyst sterkte).
    HIGH: tier-1 bron + <6u + STRONG/MODERATE
    LOW:  tier-3 bron of >24u of NONE/NEGATIVE
    Anders: MEDIUM
    """
    src_mult = _source_multiplier(source)

    if tier in ("NONE", "NEGATIVE"):
        return CatalystConfidence.LOW

    if src_mult >= 1.0 and age_hours < 6 and tier in ("STRONG", "MODERATE"):
        return CatalystConfidence.HIGH

    if src_mult <= 0.65 or age_hours > 24:
        return CatalystConfidence.LOW

    return CatalystConfidence.MEDIUM


# ── HOOFDFUNCTIE ──────────────────────────────────────────────────────────────

def classify(
    ticker:          str,
    items:           list[FinnhubNewsItem],
    sector_leaders:  list[str] = None,
    sector_sympathy: list[str] = None,
) -> CatalystResult:
    """
    Classificeert een lijst FinnhubNewsItems naar een CatalystResult.

    Args:
        ticker:          Ticker waarvoor we classificeren
        items:           Ruwe Finnhub artikelen (nieuwste eerst)
        sector_leaders:  Leaders van de sector (uit sectors.json)
        sector_sympathy: Sympathy plays van de sector (uit sectors.json)

    Retourneert altijd een CatalystResult — nooit een exception.
    """
    ticker          = ticker.upper().strip()
    sector_leaders  = [t.upper() for t in (sector_leaders  or [])]
    sector_sympathy = [t.upper() for t in (sector_sympathy or [])]

    # Geen nieuws → NONE
    if not items:
        return CatalystResult(
            catalyst_type   = "NONE",
            catalyst_source = CatalystSource.NONE,
            confidence      = CatalystConfidence.LOW,
            score           = 0.0,
            top_headline    = "",
            description     = "Geen nieuws beschikbaar (geen Finnhub key of geen artikelen)",
            raw_headlines   = [],
            negative_flags  = [],
            news_available  = False,
            articles_used   = 0,
        )

    raw_headlines:  list[str] = []
    negative_flags: list[str] = []

    best_tier       = "NONE"
    best_score      = 0.0
    best_headline   = ""
    best_source_obj = CatalystSource.OWN

    for item in items:
        headline   = item.headline
        raw_headlines.append(headline)
        age        = _age_hours(item)

        tier, base  = _classify_headline_tier(headline)

        # Negatief → vlag, niet als catalyst
        if tier == "NEGATIVE":
            negative_flags.append(headline[:80])
            continue

        # Momentum type detectie
        momentum_src = _detect_momentum_source(
            headline, ticker, sector_leaders, sector_sympathy,
        )

        # Source cap: sympathy-headlines mogen niet als STRONG tellen
        tier = _apply_source_cap(tier, momentum_src)

        # Gecombineerde score: base × recency × bron-kwaliteit
        score = base * _recency_multiplier(age) * _source_multiplier(item.source)
        score = round(min(score, 1.0), 4)

        # Beste (hoogste score) wint
        if (
            _TIER_RANK.get(tier, 0) > _TIER_RANK.get(best_tier, 0)
            or (tier == best_tier and score > best_score)
        ):
            best_tier       = tier
            best_score      = score
            best_headline   = headline
            best_source_obj = momentum_src

    # Als alleen negatief nieuws → NONE
    if best_tier == "NONE" and negative_flags:
        description = f"Alleen negatief nieuws ({len(negative_flags)} flag(s))"
    elif best_tier == "NONE":
        description = "Nieuws aanwezig maar geen herkenbare catalyst"
        best_tier   = "WEAK"   # nieuws aanwezig maar onclassificeerbaar → WEAK
        best_score  = 0.10
    else:
        source_label = {
            CatalystSource.OWN:      "eigen catalyst",
            CatalystSource.SECTOR:   "sector momentum",
            CatalystSource.SYMPATHY: "sympathy move",
            CatalystSource.NONE:     "onbekend",
        }[best_source_obj]
        description = f"{best_tier} [{source_label}]: {best_headline[:80]}"

    # Confidence op basis van beste artikel
    best_item = items[0]   # gesorteerd: nieuwste eerst
    age_best  = _age_hours(best_item)
    confidence = _compute_confidence(best_tier, best_item.source, age_best)

    return CatalystResult(
        catalyst_type   = best_tier,
        catalyst_source = best_source_obj,
        confidence      = confidence,
        score           = best_score,
        top_headline    = best_headline or (items[0].headline if items else ""),
        description     = description,
        raw_headlines   = raw_headlines[:10],  # max 10 voor output
        negative_flags  = list(set(negative_flags)),
        news_available  = True,
        articles_used   = len(items),
    )


# ── CONVENIENCE WRAPPER ───────────────────────────────────────────────────────

def classify_from_news_items(
    ticker:    str,
    items:     list,           # Accepteert ook legacy NewsItem objects
    leaders:   list[str] = None,
    sympathy:  list[str] = None,
) -> CatalystResult:
    """
    Backward-compat wrapper. Accepteert zowel FinnhubNewsItem als
    legacy NewsItem (uit het oude news_client.py) door duck-typing.
    Converteert naar FinnhubNewsItem-achtige structuur.
    """
    if not items:
        return classify(ticker, [], leaders, sympathy)

    converted = []
    for item in items:
        if isinstance(item, FinnhubNewsItem):
            converted.append(item)
        else:
            # Legacy NewsItem duck-typing
            try:
                unix_ts = getattr(item, "published_unix", None)
                if unix_ts is None:
                    # Probeer ISO timestamp te converteren
                    iso = getattr(item, "published_at", "")
                    if iso:
                        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                        unix_ts = int(dt.timestamp())
                    else:
                        unix_ts = 0

                converted.append(FinnhubNewsItem(
                    ticker         = ticker,
                    headline       = getattr(item, "headline", ""),
                    summary        = "",
                    source         = getattr(item, "source", ""),
                    url            = "",
                    published_unix = unix_ts,
                    published_iso  = getattr(item, "published_at", ""),
                    finnhub_id     = 0,
                    image_url      = None,
                    sentiment      = getattr(item, "sentiment", None),
                ))
            except Exception as exc:
                logger.debug("catalyst_classifier: legacy item conversie fout: %s", exc)

    return classify(ticker, converted, leaders, sympathy)
