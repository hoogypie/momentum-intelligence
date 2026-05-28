"""
storage/signal_decay.py
Signal Decay — v2.5

Een momentum signaal veroudert. Een BUY_STRONG van 6 uur geleden is
minder relevant dan een BUY_STRONG van 15 minuten geleden.

Verschil met cache STALE:
    - Cache STALE = data is oud (prijs/volume niet vers)
    - Signal STALE = de handelskans is voorbij, ook al klopt de data

Decay model (op basis van signaalleeftijd):
    0  –  2u  : Volledig geldig (FRESH)
    2  –  8u  : Licht verouderd (AGING) → momentum_score × 0.85
    8  – 24u  : Significant verouderd (STALE) → score × 0.65, decision ≥ 1 stap lager
    24 – 48u  : Bijna irrelevant (OLD) → score × 0.40, max WATCH
    > 48u     : Irrelevant (EXPIRED) → score × 0.0, SKIP

Waarom niet lineair:
    Momentum is niet lineair. Een FRENZY veroudert sneller dan een
    ACCUMULATION. FRENZY-fase snapshots krijgen extra decay multiplier.
"""

from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SignalAge(str, Enum):
    FRESH   = "FRESH"    # 0-2u   — volledig geldig
    AGING   = "AGING"    # 2-8u   — licht verouderd
    STALE   = "STALE"    # 8-24u  — significant verouderd
    OLD     = "OLD"      # 24-48u — bijna irrelevant
    EXPIRED = "EXPIRED"  # >48u   — irrelevant


# Decay multipliers per leeftijdscategorie
_DECAY_MULTIPLIERS = {
    SignalAge.FRESH:   1.00,
    SignalAge.AGING:   0.85,
    SignalAge.STALE:   0.65,
    SignalAge.OLD:     0.40,
    SignalAge.EXPIRED: 0.00,
}

# Extra decay voor FRENZY fase (sneller verouderd)
_FRENZY_DECAY = 0.70  # Multiplied on top of age decay

# Decision downgrades bij veroudering
_DECISION_ORDER = [
    "BUY_MAX", "BUY_STRONG", "BUY_MODERATE", "BUY_SMALL",
    "WATCH", "SKIP", "BLOCKED",
]


@dataclass
class DecayResult:
    original_decision:  str
    original_score:     float
    effective_decision: str
    effective_score:    float
    signal_age:         SignalAge
    age_hours:          float
    decay_applied:      float   # Multiplier toegepast (0.0-1.0)
    is_actionable:      bool    # False als SKIP/BLOCKED/EXPIRED


def get_signal_age(snapshot_timestamp: str) -> tuple[SignalAge, float]:
    """
    Geeft de leeftijdscategorie en leeftijd in uren van een snapshot.

    Args:
        snapshot_timestamp: ISO datetime string

    Returns:
        (SignalAge, age_hours)
    """
    try:
        ts  = datetime.fromisoformat(snapshot_timestamp.replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
    except (ValueError, TypeError):
        return SignalAge.EXPIRED, 999.0

    if age <= 2:    return SignalAge.FRESH,   age
    if age <= 8:    return SignalAge.AGING,   age
    if age <= 24:   return SignalAge.STALE,   age
    if age <= 48:   return SignalAge.OLD,     age
    return SignalAge.EXPIRED, age


def apply_decay(
    decision:  str,
    score:     float,
    timestamp: str,
    phase:     str = "NEUTRAL",
) -> DecayResult:
    """
    Past decay toe op een signaal op basis van leeftijd en fase.

    Args:
        decision:  Originele decision string
        score:     Originele momentum score
        timestamp: ISO timestamp van het signaal
        phase:     Momentum fase (FRENZY veroudert sneller)

    Returns:
        DecayResult met effectieve waarden
    """
    signal_age, age_hours = get_signal_age(timestamp)
    multiplier = _DECAY_MULTIPLIERS[signal_age]

    # FRENZY fase veroudert sneller (momentum window is korter)
    if phase == "FRENZY" and signal_age in (SignalAge.AGING, SignalAge.STALE):
        multiplier *= _FRENZY_DECAY

    effective_score = round(score * multiplier, 1)

    # Decision downgrade bij veroudering
    effective_decision = _downgrade_decision(decision, signal_age)

    is_actionable = (
        effective_decision not in ("SKIP", "BLOCKED", "WATCH") and
        signal_age not in (SignalAge.OLD, SignalAge.EXPIRED)
    )

    return DecayResult(
        original_decision  = decision,
        original_score     = score,
        effective_decision = effective_decision,
        effective_score    = effective_score,
        signal_age         = signal_age,
        age_hours          = round(age_hours, 1),
        decay_applied      = multiplier,
        is_actionable      = is_actionable,
    )


def _downgrade_decision(decision: str, age: SignalAge) -> str:
    """
    Verlaagt beslissing op basis van leeftijd.
    FRESH/AGING: onveranderd
    STALE:       1 stap lager (BUY_MAX → BUY_STRONG)
    OLD:         2 stappen lager, maar max WATCH
    EXPIRED:     altijd SKIP
    """
    if age == SignalAge.EXPIRED:
        return "SKIP"

    if decision in ("BLOCKED", "SKIP"):
        return decision  # Deze blijven altijd

    if age == SignalAge.FRESH or age == SignalAge.AGING:
        return decision

    if decision not in _DECISION_ORDER:
        return decision

    idx = _DECISION_ORDER.index(decision)

    if age == SignalAge.STALE:
        new_idx = min(idx + 1, len(_DECISION_ORDER) - 2)  # 1 stap lager, max SKIP
    elif age == SignalAge.OLD:
        new_idx = _DECISION_ORDER.index("WATCH")           # Altijd WATCH voor OLD
    else:
        new_idx = idx

    return _DECISION_ORDER[new_idx]


def apply_decay_to_snapshot(snapshot: dict) -> DecayResult:
    """
    Past decay toe op een opgeslagen snapshot dict.
    Convenience wrapper voor storage gebruik.
    """
    return apply_decay(
        decision  = snapshot.get("decision",       "SKIP"),
        score     = snapshot.get("momentum_score", 0.0),
        timestamp = snapshot.get("timestamp",      ""),
        phase     = snapshot.get("phase",          "NEUTRAL"),
    )
