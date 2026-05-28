"""
storage/snapshot_diff.py
Snapshot Diffing — v2.6

Vergelijkt twee opeenvolgende snapshots en berekent wat er veranderd is.
Puur functioneel — geen state, geen IO, geen side effects.

Gebruik:
    diff = diff_snapshots(older_snap, newer_snap)
    diff.summary  # "BUY_MODERATE → BUY_STRONG (+8.5 score)"

Diff wordt gebruikt in:
    - /replay/ticker/{ticker}  → lijst van diffs per snapshot-paar
    - /replay/session/{date}   → session-level veranderingen
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# Decision ordening voor richting-detectie (lager = beter)
_DECISION_ORDER = [
    "BUY_MAX", "BUY_STRONG", "BUY_MODERATE", "BUY_SMALL",
    "WATCH", "SKIP", "BLOCKED",
]

_CONFIDENCE_ORDER = ["LIVE", "DELAYED", "STALE", "PARTIAL", "MISSING"]


def _decision_rank(d: str) -> int:
    try:
        return _DECISION_ORDER.index(d)
    except ValueError:
        return 4  # WATCH als default


def _confidence_rank(c: str) -> int:
    try:
        return _CONFIDENCE_ORDER.index(c)
    except ValueError:
        return 2


# ── DIFF MODEL ────────────────────────────────────────────────────────────────

@dataclass
class SnapshotDiff:
    """Verschil tussen twee opeenvolgende snapshots."""
    ticker:          str
    version_from:    str
    version_to:      str
    timestamp_from:  str
    timestamp_to:    str
    elapsed_minutes: float

    # Score
    score_from:       float
    score_to:         float
    score_delta:      float    # Positief = verbeterd
    score_pct_change: float    # (delta / from) * 100

    # Beslissing
    decision_from:    str
    decision_to:      str
    decision_changed: bool
    decision_improved: bool    # Richting BUY_MAX

    # Fase
    phase_from:     str
    phase_to:       str
    phase_changed:  bool

    # Confidence
    confidence_from:    str
    confidence_to:      str
    confidence_changed: bool
    confidence_improved: bool  # LIVE > DELAYED > STALE

    # Catalyst
    catalyst_from:    str
    catalyst_to:      str
    catalyst_changed: bool

    # Vlag: is dit een significante verandering?
    is_significant: bool

    # Leesbare samenvatting
    summary: str


# ── CORE DIFF FUNCTIE ─────────────────────────────────────────────────────────

def diff_snapshots(older: dict, newer: dict) -> SnapshotDiff:
    """
    Berekent het verschil tussen twee snapshots.

    Args:
        older: Snapshot dict (oud)
        newer: Snapshot dict (nieuw)

    Returns:
        SnapshotDiff met alle deltas
    """
    score_from = float(older.get("momentum_score", 0.0))
    score_to   = float(newer.get("momentum_score", 0.0))
    score_delta = round(score_to - score_from, 1)
    score_pct   = round((score_delta / max(score_from, 0.01)) * 100, 1)

    decision_from    = older.get("decision",    "UNKNOWN")
    decision_to      = newer.get("decision",    "UNKNOWN")
    decision_changed = decision_from != decision_to
    decision_improved = _decision_rank(decision_to) < _decision_rank(decision_from)

    phase_from   = older.get("phase", "NEUTRAL")
    phase_to     = newer.get("phase", "NEUTRAL")
    phase_changed = phase_from != phase_to

    conf_from    = older.get("confidence", "LIVE")
    conf_to      = newer.get("confidence", "LIVE")
    conf_changed = conf_from != conf_to
    conf_improved = _confidence_rank(conf_to) < _confidence_rank(conf_from)

    cat_from    = older.get("catalyst_type", "NONE")
    cat_to      = newer.get("catalyst_type", "NONE")
    cat_changed = cat_from != cat_to

    # Tijdsverschil
    elapsed = _elapsed_minutes(
        older.get("timestamp", ""), newer.get("timestamp", "")
    )

    is_significant = any([
        decision_changed,
        phase_changed,
        abs(score_delta) >= 10,
        cat_changed and cat_to not in ("NONE", ""),
    ])

    summary = _build_summary(
        decision_from, decision_to, score_delta,
        phase_from, phase_to, conf_from, conf_to,
        cat_from, cat_to,
    )

    return SnapshotDiff(
        ticker=newer.get("ticker", ""),
        version_from=older.get("version_id", ""),
        version_to=newer.get("version_id", ""),
        timestamp_from=older.get("timestamp", ""),
        timestamp_to=newer.get("timestamp", ""),
        elapsed_minutes=elapsed,
        score_from=score_from, score_to=score_to,
        score_delta=score_delta, score_pct_change=score_pct,
        decision_from=decision_from, decision_to=decision_to,
        decision_changed=decision_changed, decision_improved=decision_improved,
        phase_from=phase_from, phase_to=phase_to,
        phase_changed=phase_changed,
        confidence_from=conf_from, confidence_to=conf_to,
        confidence_changed=conf_changed, confidence_improved=conf_improved,
        catalyst_from=cat_from, catalyst_to=cat_to,
        catalyst_changed=cat_changed,
        is_significant=is_significant,
        summary=summary,
    )


def diff_series(snapshots: list[dict]) -> list[SnapshotDiff]:
    """
    Berekent diffs voor een reeks snapshots.

    Args:
        snapshots: Gesorteerd nieuwste-eerst (zoals load_snapshots() retourneert)

    Returns:
        Lijst van diffs, nieuwste verandering eerst.
        diff[0] = verschil tussen snapshots[0] en snapshots[1]
    """
    if len(snapshots) < 2:
        return []

    result = []
    for i in range(len(snapshots) - 1):
        newer = snapshots[i]
        older = snapshots[i + 1]
        try:
            result.append(diff_snapshots(older, newer))
        except Exception as exc:
            logger.debug(f"snapshot_diff: diff fout: {exc}")
    return result


def find_significant_changes(diffs: list[SnapshotDiff]) -> list[SnapshotDiff]:
    """Filtert op significante veranderingen (beslissing, fase of grote score delta)."""
    return [d for d in diffs if d.is_significant]


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _elapsed_minutes(ts_from: str, ts_to: str) -> float:
    try:
        t1 = datetime.fromisoformat(ts_from.replace("Z", "+00:00"))
        t2 = datetime.fromisoformat(ts_to.replace("Z", "+00:00"))
        return round((t2 - t1).total_seconds() / 60, 1)
    except Exception:
        return 0.0


def _build_summary(
    dec_from: str, dec_to: str,
    score_delta: float,
    phase_from: str, phase_to: str,
    conf_from: str, conf_to: str,
    cat_from: str, cat_to: str,
) -> str:
    parts = []

    if dec_from != dec_to:
        parts.append(f"{dec_from} → {dec_to}")
    else:
        parts.append(dec_to)

    if score_delta != 0:
        sign = "+" if score_delta > 0 else ""
        parts.append(f"({sign}{score_delta:.1f} score)")

    if phase_from != phase_to:
        parts.append(f"fase {phase_from} → {phase_to}")

    if cat_from != cat_to and cat_to not in ("NONE", ""):
        parts.append(f"catalyst {cat_from} → {cat_to}")

    if conf_from != conf_to:
        parts.append(f"conf {conf_from} → {conf_to}")

    return "  |  ".join(parts) if parts else "Geen verandering"
