"""
storage/replay_engine.py
Replay Engine — v2.6

Bouwt replay-views van opgeslagen snapshots.
Leest storage — raakt scoring nooit aan.

Drie replay-modi:
    1. Ticker replay    — alle snapshots van één ticker + diffs
    2. Sector replay    — sector history + leader performance
    3. Session replay   — alle snapshots op een specifieke datum
"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from storage.snapshot_store import (
    load_snapshots, load_since, list_tracked_tickers, _TICKERS_DIR,
)
from storage.snapshot_diff  import diff_series, find_significant_changes
from storage.signal_decay   import apply_decay_to_snapshot
from storage.signal_tracker import (
    get_transitions, get_catalyst_timeline,
    calculate_momentum_trend, get_decision_distribution,
)
from storage.sector_history import load_sector_history, get_heat_trend
from storage.timeline       import (
    first_seen, last_updated, strongest_signal,
    confidence_history, score_timeline, phase_history,
)

logger = logging.getLogger(__name__)


# ── TICKER REPLAY ─────────────────────────────────────────────────────────────

def replay_ticker(
    ticker:   str,
    limit:    int   = 100,
    hours:    Optional[float] = None,
) -> dict:
    """
    Volledige replay van een ticker.

    Returns een dict met:
        snapshots            Alle snapshots (nieuwste eerst)
        diffs                Opeenvolgende diffs
        significant_changes  Alleen significante diffs
        score_timeline       Chronologische score-tijdlijn
        phase_history        Fase-overgangen
        confidence_history   Confidence-veranderingen
        transitions          Geregistreerde fase-overgangen (signal_tracker)
        catalyst_timeline    Catalyst-veranderingen
        summary              Ticker-samenvatting
        momentum_trend       IMPROVING / DETERIORATING / STABLE
        effective_signals    Decay toegepast op recente snapshots
    """
    ticker   = ticker.upper()
    snaps    = (
        load_since(ticker, hours=hours)
        if hours else
        load_snapshots(ticker, limit=limit)
    )

    diffs        = diff_series(snaps)
    sig_changes  = find_significant_changes(diffs)
    transitions  = get_transitions(ticker, limit=50)
    catalysts    = get_catalyst_timeline(ticker, limit=50)
    trend        = calculate_momentum_trend(snaps)
    dist         = get_decision_distribution(snaps)

    # Decay op recente snapshots
    effective = []
    for snap in snaps[:20]:
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

    best     = strongest_signal(ticker)
    latest   = last_updated(ticker)
    first    = first_seen(ticker)

    summary = {
        "ticker":           ticker,
        "snapshot_count":   len(snaps),
        "momentum_trend":   trend,
        "decision_distribution": dist,
        "current":          latest,
        "strongest_ever":   best,
        "first_seen":       first.get("timestamp") if first else None,
        "significant_changes_count": len(sig_changes),
    }

    return {
        "ticker":              ticker,
        "snapshot_count":      len(snaps),
        "snapshots":           snaps,
        "diffs":               [_diff_to_dict(d) for d in diffs],
        "significant_changes": [_diff_to_dict(d) for d in sig_changes],
        "score_timeline":      score_timeline(ticker, limit=limit),
        "phase_history":       phase_history(ticker, limit=limit),
        "confidence_history":  confidence_history(ticker, limit=limit),
        "transitions":         transitions,
        "catalyst_timeline":   catalysts,
        "momentum_trend":      trend,
        "decision_distribution": dist,
        "effective_signals":   effective,
        "summary":             summary,
    }


def _diff_to_dict(diff) -> dict:
    """Serialiseert SnapshotDiff naar dict."""
    import dataclasses
    return dataclasses.asdict(diff)


# ── SECTOR REPLAY ─────────────────────────────────────────────────────────────

def replay_sector(
    sector_id: str,
    limit:     int = 50,
) -> dict:
    """
    Sector replay: history + leader snapshots.
    """
    sector_id = sector_id.lower()
    history   = load_sector_history(sector_id, limit=limit)
    heat_t    = get_heat_trend(sector_id, limit=limit)

    # Leaders die ooit in de sector gescoord zijn
    leader_data: dict[str, dict] = {}
    for snap in history:
        for leader, decision in snap.get("leader_decisions", {}).items():
            if leader not in leader_data:
                leader_data[leader] = {
                    "ticker":       leader,
                    "decisions":    [],
                    "best_decision": None,
                    "snapshots":    [],
                }
            leader_data[leader]["decisions"].append(decision)

    # Voeg individu ticker summaries toe voor leaders
    all_tickers = list_tracked_tickers()
    for ticker in all_tickers:
        if ticker in leader_data:
            snaps = load_snapshots(ticker, limit=20)
            if snaps:
                leader_data[ticker]["snapshots"] = snaps[:5]  # Top 5 recent
                scores = [s.get("momentum_score", 0) for s in snaps]
                best_snap = max(snaps, key=lambda s: s.get("momentum_score", 0))
                leader_data[ticker]["best_decision"] = best_snap.get("decision")
                leader_data[ticker]["avg_score"] = round(
                    sum(scores) / len(scores), 1
                )

    # Heat delta
    heat_delta = None
    if len(heat_t) >= 2:
        heat_delta = heat_t[0] - heat_t[-1]  # Recente vs oudste

    return {
        "sector_id":      sector_id,
        "snapshot_count": len(history),
        "heat_trend":     heat_t,
        "heat_delta":     heat_delta,
        "recent_heat":    heat_t[0] if heat_t else None,
        "leader_data":    list(leader_data.values()),
        "history":        history[:20],  # Laatste 20 sector snapshots
    }


# ── SESSION REPLAY ────────────────────────────────────────────────────────────

def replay_session(
    date_str: str,
    max_tickers: int = 50,
) -> dict:
    """
    Replay van alle snapshots op een specifieke datum.

    Args:
        date_str: Datum in YYYY-MM-DD formaat
        max_tickers: Max aantal tickers om te scannen

    Returns:
        Dict met alle activiteit van die dag, gegroepeerd per ticker.
    """
    # Valideer datum
    try:
        session_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return {
            "error":    "INVALID_DATE",
            "message":  f"Ongeldig datumformaat: '{date_str}'. Verwacht YYYY-MM-DD.",
            "date":     date_str,
        }

    tickers = list_tracked_tickers()[:max_tickers]
    session_data: dict[str, list] = {}
    total_snapshots = 0

    for ticker in tickers:
        snaps = load_snapshots(ticker, limit=500)
        day_snaps = [
            s for s in snaps
            if _snap_on_date(s, session_date)
        ]
        if day_snaps:
            session_data[ticker] = day_snaps
            total_snapshots += len(day_snaps)

    # Samenvatting van de sessie
    all_day_snaps = [
        s for snaps in session_data.values() for s in snaps
    ]
    decisions_seen = {}
    for s in all_day_snaps:
        d = s.get("decision", "")
        decisions_seen[d] = decisions_seen.get(d, 0) + 1

    best_ticker = None
    best_score  = -1
    for ticker, snaps in session_data.items():
        top_score = max(s.get("momentum_score", 0) for s in snaps)
        if top_score > best_score:
            best_score  = top_score
            best_ticker = ticker

    return {
        "date":               date_str,
        "tickers_active":     len(session_data),
        "total_snapshots":    total_snapshots,
        "session_by_ticker":  {
            t: {
                "snapshots":      snaps,
                "count":          len(snaps),
                "peak_score":     max(s.get("momentum_score", 0) for s in snaps),
                "peak_decision":  max(snaps,
                                      key=lambda s: s.get("momentum_score", 0)
                                  ).get("decision"),
            }
            for t, snaps in session_data.items()
        },
        "decisions_seen":     decisions_seen,
        "best_ticker":        best_ticker,
        "best_score":         best_score if best_ticker else None,
        "summary":            _session_summary(date_str, session_data, total_snapshots),
    }


def _snap_on_date(snap: dict, target_date) -> bool:
    """Controleert of een snapshot op de opgegeven datum valt."""
    try:
        ts = datetime.fromisoformat(snap["timestamp"].replace("Z", "+00:00"))
        return ts.date() == target_date
    except Exception:
        return False


def _session_summary(
    date_str: str,
    session_data: dict,
    total_snapshots: int,
) -> str:
    n = len(session_data)
    if n == 0:
        return f"{date_str}: Geen activiteit gevonden."
    return (
        f"{date_str}: {n} tickers actief, "
        f"{total_snapshots} snapshots opgeslagen."
    )
