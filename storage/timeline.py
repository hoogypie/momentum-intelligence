"""
storage/timeline.py
Timeline Utilities — v2.6

Functies om de tijdlijn van een ticker samen te vatten.
Puur functioneel — leest snapshots, geen IO buiten snapshot_store.

Biedt:
    first_seen()        Eerste snapshot ooit
    last_updated()      Meest recente snapshot
    strongest_signal()  Snapshot met hoogste momentum score
    weakest_signal()    Snapshot met laagste momentum score
    confidence_history() Lijst van (timestamp, confidence) tuples
    score_timeline()    Lijst van (timestamp, score) tuples
    phase_history()     Wanneer was de ticker in welke fase?
    get_ticker_summary() Alles in één dict
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from storage.snapshot_store import (
    load_snapshots, load_latest, load_since,
    count_snapshots, list_tracked_tickers,
)

logger = logging.getLogger(__name__)

_MAX_TIMELINE_POINTS = 200


def first_seen(ticker: str) -> Optional[dict]:
    """Geeft het oudste opgeslagen snapshot terug."""
    snaps = load_snapshots(ticker, limit=_MAX_TIMELINE_POINTS)
    return snaps[-1] if snaps else None


def last_updated(ticker: str) -> Optional[dict]:
    """Geeft het meest recente snapshot terug."""
    return load_latest(ticker)


def strongest_signal(ticker: str, limit: int = _MAX_TIMELINE_POINTS) -> Optional[dict]:
    """Snapshot met de hoogste momentum_score."""
    snaps = load_snapshots(ticker, limit=limit)
    if not snaps:
        return None
    return max(snaps, key=lambda s: s.get("momentum_score", 0.0))


def weakest_signal(ticker: str, limit: int = _MAX_TIMELINE_POINTS) -> Optional[dict]:
    """Snapshot met de laagste momentum_score (excl. BLOCKED/MISSING)."""
    snaps = load_snapshots(ticker, limit=limit)
    valid = [s for s in snaps if s.get("decision") not in ("BLOCKED",)]
    if not valid:
        return None
    return min(valid, key=lambda s: s.get("momentum_score", 999.0))


def confidence_history(
    ticker: str,
    limit:  int = _MAX_TIMELINE_POINTS,
) -> list[dict]:
    """
    Lijst van confidence-veranderingen over tijd.
    Filtert op unieke opeenvolgende confidence labels (sla duplicaten over).
    Retourneert: [{"timestamp": ..., "confidence": ..., "score": ...}, ...]
    Nieuwste eerst.
    """
    snaps    = load_snapshots(ticker, limit=limit)
    result   = []
    prev_conf = None

    for snap in snaps:
        conf = snap.get("confidence", "UNKNOWN")
        if conf != prev_conf:
            result.append({
                "timestamp":  snap.get("timestamp", ""),
                "confidence": conf,
                "score":      snap.get("momentum_score", 0.0),
                "decision":   snap.get("decision", ""),
            })
            prev_conf = conf

    return result


def score_timeline(
    ticker: str,
    limit:  int = _MAX_TIMELINE_POINTS,
) -> list[dict]:
    """
    Score-tijdlijn voor plotting.
    Retourneert: [{"timestamp": ..., "score": ..., "decision": ..., "phase": ...}]
    Oudste eerst (voor chronologisch plotten).
    """
    snaps = load_snapshots(ticker, limit=limit)
    return [
        {
            "timestamp": s.get("timestamp", ""),
            "score":     s.get("momentum_score", 0.0),
            "decision":  s.get("decision", ""),
            "phase":     s.get("phase", ""),
        }
        for s in reversed(snaps)  # Omdraaien naar chronologische volgorde
    ]


def phase_history(
    ticker: str,
    limit:  int = _MAX_TIMELINE_POINTS,
) -> list[dict]:
    """
    Wanneer was de ticker in welke fase?
    Filtert op fase-veranderingen (geen duplicaten).
    Retourneert: [{"timestamp": ..., "phase": ..., "score": ..., "decision": ...}]
    Nieuwste eerst.
    """
    snaps      = load_snapshots(ticker, limit=limit)
    result     = []
    prev_phase = None

    for snap in snaps:
        phase = snap.get("phase", "NEUTRAL")
        if phase != prev_phase:
            result.append({
                "timestamp": snap.get("timestamp", ""),
                "phase":     phase,
                "score":     snap.get("momentum_score", 0.0),
                "decision":  snap.get("decision", ""),
            })
            prev_phase = phase

    return result


def get_ticker_summary(ticker: str) -> dict:
    """
    Samenvatting van de volledige tijdlijn van een ticker.

    Returns:
        {
            ticker, snapshot_count,
            first_seen, last_updated,
            strongest_signal, weakest_signal,
            current_decision, current_phase,
            score_range: {"min": ..., "max": ...},
            confidence_changes: int,
            phase_changes: int,
            days_tracked: float,
        }
    """
    count    = count_snapshots(ticker)
    if count == 0:
        return {"ticker": ticker, "snapshot_count": 0, "tracked": False}

    first   = first_seen(ticker)
    latest  = last_updated(ticker)
    best    = strongest_signal(ticker)
    worst   = weakest_signal(ticker)
    conf_h  = confidence_history(ticker)
    phase_h = phase_history(ticker)
    scores  = load_snapshots(ticker, limit=_MAX_TIMELINE_POINTS)

    all_scores = [s.get("momentum_score", 0.0) for s in scores]

    # Dagen getrackt
    days_tracked = 0.0
    if first and latest:
        try:
            t1 = datetime.fromisoformat(first["timestamp"].replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(latest["timestamp"].replace("Z", "+00:00"))
            days_tracked = round((t2 - t1).total_seconds() / 86400, 1)
        except Exception:
            pass

    return {
        "ticker":           ticker.upper(),
        "snapshot_count":   count,
        "tracked":          True,
        "first_seen":       first.get("timestamp") if first else None,
        "last_updated":     latest.get("timestamp") if latest else None,
        "current_decision": latest.get("decision") if latest else None,
        "current_phase":    latest.get("phase") if latest else None,
        "current_score":    latest.get("momentum_score") if latest else None,
        "strongest_signal": {
            "score":     best.get("momentum_score"),
            "decision":  best.get("decision"),
            "timestamp": best.get("timestamp"),
        } if best else None,
        "weakest_signal": {
            "score":     worst.get("momentum_score"),
            "decision":  worst.get("decision"),
            "timestamp": worst.get("timestamp"),
        } if worst else None,
        "score_range": {
            "min": round(min(all_scores), 1) if all_scores else 0.0,
            "max": round(max(all_scores), 1) if all_scores else 0.0,
            "avg": round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0,
        },
        "confidence_changes": len(conf_h),
        "phase_changes":      len(phase_h),
        "days_tracked":       days_tracked,
    }


def get_all_tracked_summaries() -> list[dict]:
    """Geeft een summary voor elke getrackte ticker."""
    tickers = list_tracked_tickers()
    return [get_ticker_summary(t) for t in sorted(tickers)]
