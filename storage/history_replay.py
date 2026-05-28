"""
storage/history_replay.py
Historical Replay — v2.5

Replay support: geef de evolutie van een signaal terug over tijd.

Gebruik cases:
    1. "Hoe is de IONQ score verlopen de afgelopen 24 uur?"
    2. "Wanneer ging UMAC van ACCUMULATION naar BREAKOUT?"
    3. "Is het quantum-sector momentum stijgende of dalende?"
    4. Backtesting voorbereiding (fase 5)

Replay != Live scoring.
    Replay leest opgeslagen data — geen nieuwe Yahoo/Finnhub calls.
    Snapshot data van het moment van opslaan.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from storage.snapshot_store  import load_snapshots, load_since, load_latest
from storage.signal_tracker  import (
    get_transitions, get_catalyst_timeline,
    calculate_momentum_trend, get_decision_distribution,
)
from storage.signal_decay    import apply_decay_to_snapshot, SignalAge
from storage.sector_history  import (
    load_sector_history, get_heat_trend,
    get_momentum_trend, is_sector_heating_up,
)

logger = logging.getLogger(__name__)


# ── TICKER REPLAY ─────────────────────────────────────────────────────────────

def get_signal_evolution(
    ticker:     str,
    hours:      float = 24.0,
    max_snaps:  int   = 50,
) -> dict:
    """
    Volledige signaal evolutie voor een ticker.

    Returns een dict met:
        snapshots           Ruwe snapshots (nieuwste eerst)
        effective_signals   Snapshots met decay toegepast
        momentum_trend      IMPROVING / DETERIORATING / STABLE
        phase_transitions   Recente fase-overgangen
        catalyst_timeline   Catalyst veranderingen
        decision_distribution  Hoe consistent is het signaal?
        summary             Één-regel samenvatting
    """
    snapshots       = load_since(ticker, hours=hours)[:max_snaps]
    all_snaps       = load_snapshots(ticker, limit=max_snaps)
    latest          = load_latest(ticker)
    transitions     = get_transitions(ticker, limit=10)
    catalysts       = get_catalyst_timeline(ticker, limit=10)
    trend           = calculate_momentum_trend(all_snaps)
    dist            = get_decision_distribution(all_snaps[:20])

    # Decay toegepast op elke snapshot
    effective = []
    for snap in snapshots:
        decay = apply_decay_to_snapshot(snap)
        effective.append({
            **snap,
            "effective_decision": decay.effective_decision,
            "effective_score":    decay.effective_score,
            "signal_age":         decay.signal_age.value,
            "age_hours":          decay.age_hours,
            "decay_applied":      decay.decay_applied,
            "is_actionable":      decay.is_actionable,
        })

    # Samenvatting
    summary = _build_summary(ticker, latest, trend, transitions, dist)

    return {
        "ticker":               ticker.upper(),
        "hours_covered":        hours,
        "snapshot_count":       len(snapshots),
        "snapshots":            snapshots,
        "effective_signals":    effective,
        "momentum_trend":       trend,
        "phase_transitions":    transitions,
        "catalyst_timeline":    catalysts,
        "decision_distribution": dist,
        "summary":              summary,
    }


def _build_summary(
    ticker:      str,
    latest:      Optional[dict],
    trend:       str,
    transitions: list[dict],
    dist:        dict[str, int],
) -> str:
    if not latest:
        return f"{ticker}: Geen historische data beschikbaar."

    decision = latest.get("decision", "?")
    score    = latest.get("momentum_score", 0.0)
    phase    = latest.get("phase", "?")

    # Meest voorkomende beslissing
    if dist:
        dominant = max(dist, key=dist.get)
        stability = "consistent" if dist.get(dominant, 0) > len(dist) * 0.6 else "wisselend"
    else:
        dominant, stability = decision, "onbekend"

    trend_desc = {
        "IMPROVING":         "stijgend momentum",
        "DETERIORATING":     "dalend momentum",
        "STABLE":            "stabiel momentum",
        "INSUFFICIENT_DATA": "onvoldoende data",
    }.get(trend, trend)

    trans_note = ""
    if transitions:
        last = transitions[0]
        trans_note = f" Laatste overgang: {last.get('from_phase','?')} → {last.get('to_phase','?')}."

    return (
        f"{ticker}: {decision} (score {score:.0f}, fase {phase}). "
        f"Trend: {trend_desc}. Signaal is {stability}.{trans_note}"
    )


# ── SECTOR REPLAY ─────────────────────────────────────────────────────────────

def get_sector_evolution(
    sector_id: str,
    limit:     int = 20,
) -> dict:
    """
    Sector evolutie: heat trend, momentum trend, heating up / cooling down.
    """
    history      = load_sector_history(sector_id, limit=limit)
    heat_trend   = get_heat_trend(sector_id, limit=limit)
    mom_trend    = get_momentum_trend(sector_id, limit=limit)
    heating      = is_sector_heating_up(sector_id)

    latest = history[0] if history else None
    summary = ""
    if latest:
        direction = "stijgende" if heating else "dalende of stabiele"
        summary = (
            f"{sector_id}: heat {latest.get('heat','?')}/100, "
            f"avg momentum {latest.get('avg_momentum','?'):.1f}. "
            f"{direction.capitalize()} sector heat."
        )

    return {
        "sector_id":       sector_id.lower(),
        "snapshot_count":  len(history),
        "snapshots":       history,
        "heat_trend":      heat_trend,
        "momentum_trend":  mom_trend,
        "is_heating_up":   heating,
        "summary":         summary,
    }


# ── MOMENTUM WINDOW ───────────────────────────────────────────────────────────

def get_momentum_window(
    ticker:    str,
    hours:     float = 6.0,
) -> dict:
    """
    Samenvatting van de momentum-kans-window.

    Beantwoordt: "Is het signaal nu nog actionable?"
    Combineert signal age + decay + trend voor één oordeel.
    """
    latest = load_latest(ticker)
    if not latest:
        return {
            "ticker":       ticker.upper(),
            "window_open":  False,
            "reason":       "Geen historische data",
            "decay":        None,
        }

    decay = apply_decay_to_snapshot(latest)
    recent_snaps = load_since(ticker, hours=hours)
    trend = calculate_momentum_trend(recent_snaps) if len(recent_snaps) >= 4 else "INSUFFICIENT_DATA"

    window_open = (
        decay.is_actionable and
        decay.signal_age not in (SignalAge.OLD, SignalAge.EXPIRED) and
        trend != "DETERIORATING"
    )

    reason_parts = []
    if not decay.is_actionable:
        reason_parts.append(f"signaal {decay.effective_decision}")
    if decay.signal_age in (SignalAge.OLD, SignalAge.EXPIRED):
        reason_parts.append(f"te oud ({decay.age_hours:.1f}u)")
    if trend == "DETERIORATING":
        reason_parts.append("dalend momentum")

    return {
        "ticker":          ticker.upper(),
        "window_open":     window_open,
        "reason":          " + ".join(reason_parts) if reason_parts else "OK",
        "signal_age":      decay.signal_age.value,
        "age_hours":       decay.age_hours,
        "effective_decision": decay.effective_decision,
        "effective_score": decay.effective_score,
        "momentum_trend":  trend,
        "decay": {
            "original_decision": decay.original_decision,
            "original_score":    decay.original_score,
            "multiplier":        decay.decay_applied,
        },
    }
