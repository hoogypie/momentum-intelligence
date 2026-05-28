"""
storage/signal_evaluator.py
Signal Evaluator — v2.7

Evalueert momentum signalen achteraf door toekomstige opgeslagen
snapshot-prijzen te vergelijken met de entry-prijs van het signaal.

Geen ML, geen predictie, geen extra API calls.
Werkt puur op opgeslagen snapshot data.

Grade logica:
    BUY_* signalen:
        SUCCESS   return_1d ≥ +SUCCESS_THRESHOLD (+3%)
        FAILED    return_1d ≤ FAILED_THRESHOLD (-3%)
        NEUTRAL   tussenin

    SKIP/BLOCKED signalen:
        SUCCESS   return_1d ≤ -2%  (juist bearish)
        FAILED    return_1d ≥ +2%
        NEUTRAL   tussenin

    WATCH signalen:
        NEUTRAL   altijd (geen richting-positie)

Tijdshorizon prioriteit voor grading:
    1d → 4h → 1h → PENDING
    (gebruikt de meest informatieve beschikbare horizon)

Future price lookup:
    Zoekt naar latere snapshots binnen tolerantievensters:
    +1h  → [T+45min,  T+75min]
    +4h  → [T+3h,     T+5h]
    +1d  → [T+20h,    T+28h]
    +3d  → [T+60h,    T+84h]
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from storage.snapshot_store    import load_snapshots
from storage.evaluation_store  import (
    SignalOutcome, save_outcome, load_outcomes, load_graded_outcomes,
    GRADE_SUCCESS, GRADE_NEUTRAL, GRADE_FAILED, GRADE_PENDING,
    SUCCESS_THRESHOLD, FAILED_THRESHOLD, HORIZONS, PRIMARY_HORIZON,
)

logger = logging.getLogger(__name__)

# Tolerantie-vensters per horizon (in uren)
_HORIZON_WINDOWS = {
    "1h":  (0.75,  1.25),
    "4h":  (3.0,   5.0),
    "1d":  (20.0,  28.0),
    "3d":  (60.0,  84.0),
}

# BUY beslissingen
_BUY_DECISIONS  = {"BUY_MAX", "BUY_STRONG", "BUY_MODERATE", "BUY_SMALL"}
_SKIP_DECISIONS = {"SKIP", "BLOCKED"}


# ── PRIJS LOOKUP ──────────────────────────────────────────────────────────────

def _find_future_price(
    snapshots_sorted:  list[dict],  # Alle snapshots, oudste eerst
    signal_ts:         datetime,
    horizon_key:       str,
) -> Optional[float]:
    """
    Zoekt de dichtst bijzijnde prijs binnen het tolerantievenster.

    Args:
        snapshots_sorted: Chronologisch gesorteerd (oudste eerst)
        signal_ts:        Tijdstip van het signaal
        horizon_key:      "1h", "4h", "1d" of "3d"

    Returns:
        Prijs binnen het venster, of None als niet gevonden.
    """
    lo_h, hi_h = _HORIZON_WINDOWS[horizon_key]
    lo = signal_ts + timedelta(hours=lo_h)
    hi = signal_ts + timedelta(hours=hi_h)

    candidates = []
    for snap in snapshots_sorted:
        try:
            ts = datetime.fromisoformat(snap["timestamp"].replace("Z", "+00:00"))
            if lo <= ts <= hi:
                price = snap.get("price", 0.0)
                if price > 0:
                    diff = abs((ts - (signal_ts + timedelta(hours=HORIZONS[horizon_key]))).total_seconds())
                    candidates.append((diff, price))
        except Exception:
            continue

    if not candidates:
        return None
    # Dichtst bij het ideale tijdstip
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _calc_return(entry: float, future: Optional[float]) -> Optional[float]:
    if future is None or entry <= 0:
        return None
    return round((future - entry) / entry * 100, 2)


# ── GRADING ───────────────────────────────────────────────────────────────────

def _grade_buy_signal(return_val: Optional[float]) -> str:
    if return_val is None:
        return GRADE_PENDING
    if return_val >= SUCCESS_THRESHOLD:
        return GRADE_SUCCESS
    if return_val <= FAILED_THRESHOLD:
        return GRADE_FAILED
    return GRADE_NEUTRAL


def _grade_skip_signal(return_val: Optional[float]) -> str:
    """SKIP/BLOCKED is succesvol als de prijs daadwerkelijk daalde."""
    if return_val is None:
        return GRADE_PENDING
    if return_val <= -2.0:   return GRADE_SUCCESS
    if return_val >= 2.0:    return GRADE_FAILED
    return GRADE_NEUTRAL


def grade_signal(
    decision:   str,
    return_1h:  Optional[float],
    return_4h:  Optional[float],
    return_1d:  Optional[float],
    return_3d:  Optional[float],
) -> tuple[str, Optional[str]]:
    """
    Bepaalt de grade en de gebruikte tijdshorizon.

    Returns:
        (grade, basis_horizon)   basis_horizon = "1d", "4h", "1h" of None
    """
    is_buy  = decision in _BUY_DECISIONS
    is_skip = decision in _SKIP_DECISIONS

    if decision == "WATCH":
        return GRADE_NEUTRAL, None

    # Gebruik beste beschikbare horizon (prioriteit: 1d > 4h > 1h)
    for horizon_key, return_val in [
        ("1d", return_1d),
        ("4h", return_4h),
        ("1h", return_1h),
    ]:
        if return_val is not None:
            if is_buy:
                return _grade_buy_signal(return_val), horizon_key
            elif is_skip:
                return _grade_skip_signal(return_val), horizon_key

    return GRADE_PENDING, None


# ── EVALUATE SNAPSHOT ─────────────────────────────────────────────────────────

def evaluate_snapshot(
    snapshot:          dict,
    all_ticker_snaps:  list[dict],
) -> SignalOutcome:
    """
    Evalueert één snapshot door toekomstige prijzen op te zoeken.

    Args:
        snapshot:         Het te evalueren snapshot
        all_ticker_snaps: Alle snapshots voor dezelfde ticker (nieuwste eerst)

    Returns:
        SignalOutcome met grade (of PENDING bij ontbrekende data)
    """
    ticker     = snapshot.get("ticker", "")
    version_id = snapshot.get("version_id", "")
    ts_str     = snapshot.get("timestamp", "")
    entry_price = float(snapshot.get("price", 0.0))

    # Parse timestamp
    try:
        signal_ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return SignalOutcome(
            version_id=version_id, ticker=ticker, timestamp=ts_str,
            decision=snapshot.get("decision", ""),
            momentum_score=float(snapshot.get("momentum_score", 0)),
            phase=snapshot.get("phase", ""), catalyst_type=snapshot.get("catalyst_type", ""),
            sector_id=snapshot.get("sector_id", ""), entry_price=entry_price,
            grade=GRADE_PENDING, graded_at=None,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    # Chronologisch sorteren (oudste eerst) voor future price lookup
    sorted_snaps = sorted(
        all_ticker_snaps,
        key=lambda s: s.get("timestamp", ""),
    )

    # Future prices
    p1h  = _find_future_price(sorted_snaps, signal_ts, "1h")
    p4h  = _find_future_price(sorted_snaps, signal_ts, "4h")
    p1d  = _find_future_price(sorted_snaps, signal_ts, "1d")
    p3d  = _find_future_price(sorted_snaps, signal_ts, "3d")

    r1h  = _calc_return(entry_price, p1h)
    r4h  = _calc_return(entry_price, p4h)
    r1d  = _calc_return(entry_price, p1d)
    r3d  = _calc_return(entry_price, p3d)

    decision = snapshot.get("decision", "")
    grade, basis = grade_signal(decision, r1h, r4h, r1d, r3d)

    now = datetime.now(timezone.utc).isoformat()

    return SignalOutcome(
        version_id       = version_id,
        ticker           = ticker,
        timestamp        = ts_str,
        decision         = decision,
        momentum_score   = float(snapshot.get("momentum_score", 0)),
        phase            = snapshot.get("phase", "NEUTRAL"),
        catalyst_type    = snapshot.get("catalyst_type", "NONE"),
        sector_id        = snapshot.get("sector_id", "unknown"),
        entry_price      = entry_price,
        price_1h=p1h,  price_4h=p4h,  price_1d=p1d,  price_3d=p3d,
        return_1h=r1h, return_4h=r4h, return_1d=r1d, return_3d=r3d,
        grade            = grade,
        grade_basis      = basis,
        graded_at        = now if grade != GRADE_PENDING else None,
        evaluated_at     = now,
    )


# ── EVALUATE TICKER ───────────────────────────────────────────────────────────

def evaluate_ticker(ticker: str, limit: int = 200) -> dict:
    """
    Evalueert alle snapshots voor één ticker.

    Returns:
        {evaluated, pending, outcomes: [SignalOutcome dicts]}
    """
    ticker    = ticker.upper()
    snapshots = load_snapshots(ticker, limit=limit)

    if not snapshots:
        return {
            "ticker":    ticker,
            "evaluated": 0,
            "pending":   0,
            "outcomes":  [],
        }

    outcomes_list = []
    evaluated = 0
    pending   = 0

    for snap in snapshots:
        # Skip snapshots zonder prijs
        if not snap.get("price", 0.0):
            continue

        outcome = evaluate_snapshot(snap, snapshots)
        save_outcome(outcome)
        outcomes_list.append(outcome)

        if outcome.grade == GRADE_PENDING:
            pending += 1
        else:
            evaluated += 1

    from dataclasses import asdict
    return {
        "ticker":    ticker,
        "evaluated": evaluated,
        "pending":   pending,
        "outcomes":  [asdict(o) for o in outcomes_list],
    }


# ── SIGNAL STATISTICS ─────────────────────────────────────────────────────────

def compute_signal_statistics(ticker: str) -> dict:
    """
    Berekent statistieken voor alle gegradeerde signalen van één ticker.

    Returns:
        Statistieken dict met success_rate, by_phase, by_catalyst, etc.
    """
    graded = load_graded_outcomes(ticker)

    if not graded:
        return {
            "ticker":          ticker,
            "total_graded":    0,
            "success_rate":    None,
            "message":         "Geen gegradeerde signalen. Roep /evaluation/run/{ticker} aan.",
        }

    total   = len(graded)
    success = sum(1 for o in graded if o.get("grade") == GRADE_SUCCESS)
    failed  = sum(1 for o in graded if o.get("grade") == GRADE_FAILED)
    neutral = sum(1 for o in graded if o.get("grade") == GRADE_NEUTRAL)

    # Per dimensie
    by_phase     = _breakdown(graded, "phase")
    by_catalyst  = _breakdown(graded, "catalyst_type")
    by_sector    = _breakdown(graded, "sector_id")
    by_decision  = _breakdown(graded, "decision")

    # Score analyse
    scores_s = [o.get("momentum_score", 0) for o in graded if o.get("grade") == GRADE_SUCCESS]
    scores_f = [o.get("momentum_score", 0) for o in graded if o.get("grade") == GRADE_FAILED]
    scores_n = [o.get("momentum_score", 0) for o in graded if o.get("grade") == GRADE_NEUTRAL]

    # Return analyse
    r1d_graded = [o.get("return_1d") for o in graded if o.get("return_1d") is not None]

    # Best en worst signal
    graded_with_return = [o for o in graded if o.get("return_1d") is not None]
    best_signal  = max(graded_with_return, key=lambda x: x.get("return_1d", 0)) if graded_with_return else None
    worst_signal = min(graded_with_return, key=lambda x: x.get("return_1d", 0)) if graded_with_return else None

    return {
        "ticker":           ticker,
        "total_graded":     total,
        "success_count":    success,
        "failed_count":     failed,
        "neutral_count":    neutral,
        "success_rate":     round(success / total, 3) if total > 0 else 0.0,
        "failed_rate":      round(failed  / total, 3) if total > 0 else 0.0,
        "neutral_rate":     round(neutral / total, 3) if total > 0 else 0.0,
        "by_phase":         by_phase,
        "by_catalyst":      by_catalyst,
        "by_sector":        by_sector,
        "by_decision":      by_decision,
        "avg_score_success": round(sum(scores_s) / len(scores_s), 1) if scores_s else None,
        "avg_score_failed":  round(sum(scores_f) / len(scores_f), 1) if scores_f else None,
        "avg_score_neutral": round(sum(scores_n) / len(scores_n), 1) if scores_n else None,
        "avg_return_1d":     round(sum(r1d_graded) / len(r1d_graded), 2) if r1d_graded else None,
        "best_signal":       best_signal,
        "worst_signal":      worst_signal,
    }


def _breakdown(outcomes: list[dict], field: str) -> dict:
    """Berekent success/fail/neutral counts per waarde van `field`."""
    groups: dict[str, dict] = {}
    for o in outcomes:
        key = o.get(field, "unknown") or "unknown"
        if key not in groups:
            groups[key] = {"success": 0, "failed": 0, "neutral": 0, "total": 0}
        groups[key]["total"] += 1
        grade = o.get("grade", GRADE_PENDING)
        if grade == GRADE_SUCCESS:   groups[key]["success"] += 1
        elif grade == GRADE_FAILED:  groups[key]["failed"]  += 1
        elif grade == GRADE_NEUTRAL: groups[key]["neutral"] += 1

    # Voeg success_rate toe
    for k, v in groups.items():
        t = v["total"]
        v["success_rate"] = round(v["success"] / t, 3) if t > 0 else 0.0
    return groups


# ── CROSS-TICKER STATISTICS ────────────────────────────────────────────────────

def compute_global_statistics(max_tickers: int = 50) -> dict:
    """
    Aggregeert statistieken over alle geëvalueerde tickers.
    """
    from storage.evaluation_store import list_evaluated_tickers

    tickers  = list_evaluated_tickers()[:max_tickers]
    all_graded: list[dict] = []

    for ticker in tickers:
        all_graded.extend(load_graded_outcomes(ticker))

    if not all_graded:
        return {
            "total_graded": 0,
            "message": "Geen geëvalueerde signalen. Roep /evaluation/run/{ticker} aan.",
        }

    total   = len(all_graded)
    success = sum(1 for o in all_graded if o.get("grade") == GRADE_SUCCESS)
    failed  = sum(1 for o in all_graded if o.get("grade") == GRADE_FAILED)
    neutral = sum(1 for o in all_graded if o.get("grade") == GRADE_NEUTRAL)

    return {
        "tickers_evaluated":   len(tickers),
        "total_graded":        total,
        "success_count":       success,
        "failed_count":        failed,
        "neutral_count":       neutral,
        "success_rate":        round(success / total, 3) if total > 0 else 0.0,
        "by_phase":            _breakdown(all_graded, "phase"),
        "by_catalyst":         _breakdown(all_graded, "catalyst_type"),
        "by_decision":         _breakdown(all_graded, "decision"),
    }


# ── TOP SIGNALS ────────────────────────────────────────────────────────────────

def get_top_signals(
    n:           int  = 10,
    grade:       str  = GRADE_SUCCESS,
    max_tickers: int  = 50,
) -> list[dict]:
    """
    Geeft de N beste (of slechtste) signalen over alle tickers.

    Args:
        n:    Aantal resultaten
        grade: GRADE_SUCCESS (beste) of GRADE_FAILED (slechtste)
    """
    from storage.evaluation_store import list_evaluated_tickers

    tickers   = list_evaluated_tickers()[:max_tickers]
    all_graded: list[dict] = []

    for ticker in tickers:
        all_graded.extend(load_graded_outcomes(ticker))

    filtered = [o for o in all_graded if o.get("grade") == grade
                and o.get("return_1d") is not None]

    if grade == GRADE_SUCCESS:
        filtered.sort(key=lambda x: x.get("return_1d", 0), reverse=True)
    else:
        filtered.sort(key=lambda x: x.get("return_1d", 0))

    return filtered[:n]
