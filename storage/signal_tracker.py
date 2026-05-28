"""
storage/signal_tracker.py
Signal Tracker — v2.5

Detecteert en slaat op:
    - Phase transitions (ACCUMULATION → BREAKOUT → EXPANSION etc.)
    - Catalyst timeline (wanneer appeared/verdween een catalyst)
    - Momentum trend (IMPROVING / DETERIORATING / STABLE)

Opslag: storage/data/tickers/{TICKER}_transitions.jsonl

Momentum trend berekening:
    Vergelijk gemiddelde score van laatste 3 snapshots met vorige 3.
    Delta > +5  → IMPROVING
    Delta < -5  → DETERIORATING
    Overig      → STABLE
"""

import json
import os
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_STORAGE_ROOT  = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "storage", "data"
)
_TICKERS_DIR   = os.path.join(_STORAGE_ROOT, "tickers")


# ── DATA MODELS ───────────────────────────────────────────────────────────────

@dataclass
class PhaseTransition:
    """Registratie van een fase-overgang voor één ticker."""
    ticker:         str
    timestamp:      str      # ISO UTC
    from_phase:     str
    to_phase:       str
    momentum_score: float
    decision:       str
    version_id:     str      # Snapshot die de overgang triggerde


@dataclass
class CatalystEvent:
    """Registratie van een catalyst verandering."""
    ticker:          str
    timestamp:       str
    catalyst_type:   str     # STRONG/MODERATE/WEAK/NONE
    catalyst_desc:   str
    previous_type:   Optional[str]
    version_id:      str


# ── PATHS ─────────────────────────────────────────────────────────────────────

def _transitions_path(ticker: str) -> str:
    os.makedirs(_TICKERS_DIR, exist_ok=True)
    return os.path.join(_TICKERS_DIR, f"{ticker.upper()}_transitions.jsonl")


def _catalysts_path(ticker: str) -> str:
    os.makedirs(_TICKERS_DIR, exist_ok=True)
    return os.path.join(_TICKERS_DIR, f"{ticker.upper()}_catalysts.jsonl")


def _append_jsonl(path: str, obj: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj) + "\n")
    except Exception as exc:
        logger.warning(f"signal_tracker: schrijven mislukt naar {path}: {exc}")


def _read_jsonl(path: str, limit: int = 100) -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        result = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                result.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(result) >= limit:
                break
        return result
    except Exception as exc:
        logger.warning(f"signal_tracker: lezen mislukt van {path}: {exc}")
        return []


# ── PHASE TRANSITIONS ─────────────────────────────────────────────────────────

def record_transition_if_changed(
    ticker:         str,
    new_phase:      str,
    momentum_score: float,
    decision:       str,
    version_id:     str,
    snapshots:      list[dict],
) -> Optional[PhaseTransition]:
    """
    Slaat een phase transition op als de fase veranderd is t.o.v. de vorige.

    Args:
        snapshots: Recente snapshots (voor vergelijking met vorige fase)

    Returns:
        PhaseTransition als er een overgang was, anders None.
    """
    # Zoek de vorige fase
    prev_phase = None
    for snap in snapshots[1:]:  # Skip de eerste (dat is huidige)
        if snap.get("phase") and snap["phase"] != new_phase:
            prev_phase = snap["phase"]
            break
        elif snap.get("phase") == new_phase:
            continue

    if prev_phase is None or prev_phase == new_phase:
        return None  # Geen overgang

    transition = PhaseTransition(
        ticker=ticker.upper(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        from_phase=prev_phase,
        to_phase=new_phase,
        momentum_score=momentum_score,
        decision=decision,
        version_id=version_id,
    )

    _append_jsonl(_transitions_path(ticker), asdict(transition))
    logger.info(
        f"signal_tracker: {ticker} fase {prev_phase} → {new_phase} "
        f"(score={momentum_score:.1f})"
    )
    return transition


def get_transitions(ticker: str, limit: int = 20) -> list[dict]:
    """Geeft recente fase-overgangen voor een ticker."""
    return _read_jsonl(_transitions_path(ticker), limit=limit)


def get_phase_duration(ticker: str) -> Optional[str]:
    """
    Berekent hoe lang de huidige fase al actief is.
    Geeft een beschrijving terug als string.
    """
    transitions = get_transitions(ticker, limit=5)
    if not transitions:
        return None

    latest = transitions[0]
    try:
        ts  = datetime.fromisoformat(latest["timestamp"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - ts).total_seconds() / 3600
        if age < 1:
            return f"{int(age * 60)}m in {latest['to_phase']}"
        elif age < 24:
            return f"{age:.1f}u in {latest['to_phase']}"
        else:
            return f"{age/24:.1f}d in {latest['to_phase']}"
    except Exception:
        return None


# ── CATALYST TIMELINE ─────────────────────────────────────────────────────────

def record_catalyst_if_changed(
    ticker:        str,
    catalyst_type: str,
    catalyst_desc: str,
    version_id:    str,
    snapshots:     list[dict],
) -> Optional[CatalystEvent]:
    """
    Slaat een catalyst verandering op als het type veranderd is.

    Returns:
        CatalystEvent als er een verandering was, anders None.
    """
    prev_type = None
    for snap in snapshots[1:]:
        if "catalyst_type" in snap:
            prev_type = snap["catalyst_type"]
            break

    if prev_type == catalyst_type:
        return None  # Geen verandering

    event = CatalystEvent(
        ticker=ticker.upper(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        catalyst_type=catalyst_type,
        catalyst_desc=catalyst_desc[:100],
        previous_type=prev_type,
        version_id=version_id,
    )

    _append_jsonl(_catalysts_path(ticker), asdict(event))
    logger.info(
        f"signal_tracker: {ticker} catalyst {prev_type} → {catalyst_type}"
    )
    return event


def get_catalyst_timeline(ticker: str, limit: int = 20) -> list[dict]:
    """Geeft de catalyst geschiedenis voor een ticker."""
    return _read_jsonl(_catalysts_path(ticker), limit=limit)


# ── MOMENTUM TREND ────────────────────────────────────────────────────────────

def calculate_momentum_trend(snapshots: list[dict]) -> str:
    """
    Berekent de momentum trend van een ticker.

    Vergelijkt gemiddelde van laatste 3 snapshots met vorige 3.
    Geeft: IMPROVING / DETERIORATING / STABLE / INSUFFICIENT_DATA

    Args:
        snapshots: Gesorteerd nieuwste-eerst
    """
    if len(snapshots) < 4:
        return "INSUFFICIENT_DATA"

    recent  = [s.get("momentum_score", 0.0) for s in snapshots[:3]]
    older   = [s.get("momentum_score", 0.0) for s in snapshots[3:6]]

    if not older:
        return "INSUFFICIENT_DATA"

    avg_recent = sum(recent) / len(recent)
    avg_older  = sum(older)  / len(older)
    delta      = avg_recent - avg_older

    if delta > 5:
        return "IMPROVING"
    elif delta < -5:
        return "DETERIORATING"
    return "STABLE"


def get_decision_distribution(snapshots: list[dict]) -> dict[str, int]:
    """
    Telt hoe vaak elke beslissing voorkwam in de snapshots.
    Nuttig voor consistentie-check: is BUY_STRONG stabiel of vluchtig?
    """
    dist: dict[str, int] = {}
    for snap in snapshots:
        d = snap.get("decision", "UNKNOWN")
        dist[d] = dist.get(d, 0) + 1
    return dist
