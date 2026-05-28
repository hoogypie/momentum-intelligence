"""
alerting/alert_engine.py
Alert Engine — v2.9

Evalueert opgeslagen snapshots en genereert alerts bij significante wijzigingen.

Design:
    - Vergelijkt de TWEE meest recente snapshots per ticker
    - Geen live data fetching — werkt alleen op storage
    - Alerting observeert signalen maar verandert scoring nooit
    - Elk trigger type heeft eigen severity-logica

Trigger types:
    momentum_threshold    Score kruist een decision-grens
    phase_transition      Fase verandert
    sector_heat_spike     Sector heat stijgt >10 punten
    volume_anomaly        Volume ratio > drempelwaarde
    confidence_downgrade  Confidence verslechtert
    buy_max_signal        Score ≥ 90 (altijd HIGH of CRITICAL)
    score_drop            Score daalt significant (>15 punten)
    evaluation_insight    Historisch patroon gedetecteerd

Severity bepaling:
    INFO     Kleine verandering, informatief
    WATCH    Potentieel interessant, monitor
    HIGH     Duidelijk signaal, review aanbevolen
    CRITICAL Directe aandacht vereist (BUY_MAX, grote fase-overgang)
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from alerting.alert_store    import (
    Alert, make_alert_id, save_alert, severity_rank,
    TRIGGER_MOMENTUM_THRESHOLD, TRIGGER_PHASE_TRANSITION,
    TRIGGER_SECTOR_HEAT_SPIKE, TRIGGER_VOLUME_ANOMALY,
    TRIGGER_CONFIDENCE_DOWN, TRIGGER_BUY_MAX, TRIGGER_SCORE_DROP,
    TRIGGER_EVAL_INSIGHT,
)
from alerting.cooldown_manager import is_suppressed, set_cooldown
from storage.snapshot_store    import load_snapshots

logger = logging.getLogger(__name__)

# Decision-grens mapping (voor threshold crossing detectie)
_DECISION_THRESHOLDS = {
    "BUY_MAX":      90,
    "BUY_STRONG":   75,
    "BUY_MODERATE": 60,
    "BUY_SMALL":    45,
    "WATCH":        30,
}

_CONFIDENCE_RANK = {
    "LIVE": 0, "DELAYED": 1, "STALE": 2, "PARTIAL": 3, "MISSING": 4
}


# ── CORE ALERT BUILDER ────────────────────────────────────────────────────────

def _fire(
    ticker:       str,
    severity:     str,
    trigger_type: str,
    title:        str,
    message:      str,
    old_value:    Optional[str]   = None,
    new_value:    Optional[str]   = None,
    score:        Optional[float] = None,
    phase:        Optional[str]   = None,
    hist_context: Optional[str]   = None,
    watchlist:    Optional[str]   = None,
    version_id:   Optional[str]   = None,
    cooldown_ctx: Optional[str]   = None,
) -> Optional[Alert]:
    """
    Maakt een alert aan en slaat het op — tenzij in cooldown.

    Returns Alert als gefired, None als gesupprimeerd.
    """
    suppressed = is_suppressed(ticker, trigger_type, severity, cooldown_ctx)

    alert = Alert(
        alert_id     = make_alert_id(ticker, trigger_type),
        ticker       = ticker,
        severity     = severity,
        trigger_type = trigger_type,
        title        = title,
        message      = message,
        timestamp    = datetime.now(timezone.utc).isoformat(),
        old_value    = old_value,
        new_value    = new_value,
        score        = score,
        phase        = phase,
        historical_context = hist_context,
        watchlist    = watchlist,
        suppressed   = suppressed,
        version_id   = version_id,
    )

    if not suppressed:
        save_alert(alert)
        set_cooldown(ticker, trigger_type, severity, cooldown_ctx)

    return None if suppressed else alert


# ── INDIVIDUAL TRIGGERS ───────────────────────────────────────────────────────

def check_momentum_threshold(
    ticker:    str,
    old_snap:  dict,
    new_snap:  dict,
    watchlist: Optional[str] = None,
) -> list[Alert]:
    """Detecteer of score een decision-grens kruist."""
    alerts = []
    old_s  = old_snap.get("momentum_score", 0.0)
    new_s  = new_snap.get("momentum_score", 0.0)
    new_d  = new_snap.get("decision", "")
    old_d  = old_snap.get("decision", "")
    vid    = new_snap.get("version_id", "")

    if new_d == old_d:
        return []  # Geen grensoverschrijding

    # Bepaal severity op basis van nieuwe decision
    sev_map = {
        "BUY_MAX":      "CRITICAL",
        "BUY_STRONG":   "HIGH",
        "BUY_MODERATE": "HIGH",
        "BUY_SMALL":    "WATCH",
        "WATCH":        "INFO",
        "SKIP":         "INFO",
        "BLOCKED":      "CRITICAL",
    }
    severity = sev_map.get(new_d, "INFO")
    delta    = new_s - old_s
    sign     = "+" if delta >= 0 else ""
    moving   = "stijgt" if delta > 0 else "daalt"

    alert = _fire(
        ticker=ticker, severity=severity,
        trigger_type=TRIGGER_MOMENTUM_THRESHOLD,
        title=f"{ticker}: {old_d} → {new_d}",
        message=(
            f"Score {moving} van {old_s:.1f} naar {new_s:.1f} "
            f"({sign}{delta:.1f} pts). Beslissing: {old_d} → {new_d}."
        ),
        old_value=f"{old_d} ({old_s:.1f})",
        new_value=f"{new_d} ({new_s:.1f})",
        score=new_s, phase=new_snap.get("phase"),
        watchlist=watchlist, version_id=vid,
        cooldown_ctx=f"{old_d}->{new_d}",
    )
    if alert:
        alerts.append(alert)

    # Extra CRITICAL voor BUY_MAX
    if new_d == "BUY_MAX":
        a2 = _fire(
            ticker=ticker, severity="CRITICAL",
            trigger_type=TRIGGER_BUY_MAX,
            title=f"🚨 {ticker}: BUY_MAX bereikt (score {new_s:.1f})",
            message=(
                f"Uitzonderlijk sterk signaal. Score {new_s:.1f}/100 "
                f"in fase {new_snap.get('phase','?')}. "
                f"Catalyst: {new_snap.get('catalyst_type','?')}."
            ),
            score=new_s, phase=new_snap.get("phase"),
            watchlist=watchlist, version_id=vid,
        )
        if a2:
            alerts.append(a2)

    return alerts


def check_phase_transition(
    ticker:    str,
    old_snap:  dict,
    new_snap:  dict,
    watchlist: Optional[str] = None,
) -> list[Alert]:
    """Detecteer fase-overgang."""
    old_p = old_snap.get("phase", "NEUTRAL")
    new_p = new_snap.get("phase", "NEUTRAL")

    if old_p == new_p:
        return []

    # Bepaal severity op basis van nieuwe fase
    sev_map = {
        "BREAKOUT":     "HIGH",
        "EXPANSION":    "HIGH",
        "FRENZY":       "CRITICAL",
        "ACCUMULATION": "WATCH",
        "EXHAUSTION":   "WATCH",
        "NEUTRAL":      "INFO",
    }
    severity = sev_map.get(new_p, "INFO")
    score    = new_snap.get("momentum_score", 0.0)

    alert = _fire(
        ticker=ticker, severity=severity,
        trigger_type=TRIGGER_PHASE_TRANSITION,
        title=f"{ticker}: fase {old_p} → {new_p}",
        message=(
            f"Fase-overgang gedetecteerd: {old_p} → {new_p}. "
            f"Score: {score:.1f}. Beslissing: {new_snap.get('decision','?')}."
        ),
        old_value=old_p, new_value=new_p,
        score=score, phase=new_p,
        watchlist=watchlist, version_id=new_snap.get("version_id"),
        cooldown_ctx=f"{old_p}->{new_p}",
    )
    return [alert] if alert else []


def check_volume_anomaly(
    ticker:          str,
    new_snap:        dict,
    threshold:       float     = 3.0,
    watchlist:       Optional[str] = None,
) -> list[Alert]:
    """Detecteer ongebruikelijk volume."""
    vol_ratio = new_snap.get("volume_ratio", 0.0)

    if vol_ratio < threshold:
        return []

    severity  = "CRITICAL" if vol_ratio >= 8.0 else "HIGH" if vol_ratio >= 5.0 else "WATCH"
    intensity = "EXTREEM" if vol_ratio >= 8.0 else "HOOG" if vol_ratio >= 5.0 else "VERHOOGD"

    alert = _fire(
        ticker=ticker, severity=severity,
        trigger_type=TRIGGER_VOLUME_ANOMALY,
        title=f"{ticker}: volume {vol_ratio:.1f}x normaal ({intensity})",
        message=(
            f"Volume {vol_ratio:.1f}× het 20-daags gemiddelde. "
            f"Score: {new_snap.get('momentum_score', 0):.1f}. "
            f"Fase: {new_snap.get('phase', '?')}."
        ),
        new_value=f"{vol_ratio:.1f}x",
        score=new_snap.get("momentum_score"),
        phase=new_snap.get("phase"),
        watchlist=watchlist, version_id=new_snap.get("version_id"),
    )
    return [alert] if alert else []


def check_confidence_downgrade(
    ticker:    str,
    old_snap:  dict,
    new_snap:  dict,
    watchlist: Optional[str] = None,
) -> list[Alert]:
    """Detecteer confidence verslechtering."""
    old_c = old_snap.get("confidence", "LIVE")
    new_c = new_snap.get("confidence", "LIVE")

    if _CONFIDENCE_RANK.get(new_c, 0) <= _CONFIDENCE_RANK.get(old_c, 0):
        return []  # Niet verslechterd

    alert = _fire(
        ticker=ticker, severity="WATCH",
        trigger_type=TRIGGER_CONFIDENCE_DOWN,
        title=f"{ticker}: datakwaliteit {old_c} → {new_c}",
        message=(
            f"Data confidence verslechterd van {old_c} naar {new_c}. "
            f"Score en beslissing zijn minder betrouwbaar."
        ),
        old_value=old_c, new_value=new_c,
        watchlist=watchlist, version_id=new_snap.get("version_id"),
    )
    return [alert] if alert else []


def check_score_drop(
    ticker:          str,
    old_snap:        dict,
    new_snap:        dict,
    threshold:       float = 15.0,
    watchlist:       Optional[str] = None,
) -> list[Alert]:
    """Detecteer significante score-daling."""
    old_s = old_snap.get("momentum_score", 0.0)
    new_s = new_snap.get("momentum_score", 0.0)
    delta = new_s - old_s

    if delta >= -threshold:
        return []  # Geen significante daling

    severity = "HIGH" if delta <= -25 else "WATCH"

    alert = _fire(
        ticker=ticker, severity=severity,
        trigger_type=TRIGGER_SCORE_DROP,
        title=f"{ticker}: score gedaald {delta:.1f} pts ({old_s:.1f} → {new_s:.1f})",
        message=(
            f"Momentum score daalde {abs(delta):.1f} punten. "
            f"Beslissing: {old_snap.get('decision','?')} → {new_snap.get('decision','?')}."
        ),
        old_value=f"{old_s:.1f}", new_value=f"{new_s:.1f}",
        score=new_s, phase=new_snap.get("phase"),
        watchlist=watchlist, version_id=new_snap.get("version_id"),
    )
    return [alert] if alert else []


def check_evaluation_insight(
    ticker:    str,
    new_snap:  dict,
    watchlist: Optional[str] = None,
) -> list[Alert]:
    """
    Voeg historische context toe als evaluatiedata beschikbaar is.
    Alleen voor BUY-beslissingen met voldoende data.
    """
    decision = new_snap.get("decision", "")
    if not decision.startswith("BUY"):
        return []

    try:
        from storage.signal_evaluator import compute_signal_statistics
        from storage.evaluation_store import load_graded_outcomes

        graded = load_graded_outcomes(ticker)
        if len(graded) < 5:
            return []  # Te weinig data voor zinvolle statistiek

        phase   = new_snap.get("phase", "")
        cat     = new_snap.get("catalyst_type", "")
        score   = new_snap.get("momentum_score", 0)

        # Filter op vergelijkbare setups
        similar = [
            g for g in graded
            if g.get("phase") == phase
            and g.get("catalyst_type") == cat
            and abs(g.get("momentum_score", 0) - score) <= 15
        ]

        if len(similar) < 3:
            return []

        successes   = sum(1 for s in similar if s.get("grade") == "SUCCESS")
        success_rate = successes / len(similar)

        if success_rate < 0.4 and success_rate > 0.6:
            return []  # Niet interessant genoeg

        severity = "HIGH" if success_rate >= 0.7 else "INFO" if success_rate < 0.4 else "WATCH"
        direction = f"{success_rate*100:.0f}% success rate" if success_rate >= 0.5 else f"slechts {success_rate*100:.0f}% success rate"

        alert = _fire(
            ticker=ticker, severity=severity,
            trigger_type=TRIGGER_EVAL_INSIGHT,
            title=f"{ticker}: historisch vergelijkbare setup ({direction})",
            message=(
                f"Van {len(similar)} vergelijkbare setups "
                f"({phase} + {cat}, score ~{score:.0f}) "
                f"was {successes} succesvol ({success_rate*100:.0f}%). "
                f"Op basis van {len(graded)} totaal geëvalueerde signalen."
            ),
            score=score, phase=phase,
            hist_context=f"{success_rate*100:.0f}% success in {len(similar)} vergelijkbare setups",
            watchlist=watchlist,
        )
        return [alert] if alert else []

    except Exception as exc:
        logger.debug(f"eval_insight: {ticker} mislukt: {exc}")
        return []


# ── MAIN SCAN ─────────────────────────────────────────────────────────────────

def scan_ticker(
    ticker:           str,
    watchlist_config: Optional[dict] = None,
    watchlist_name:   Optional[str]  = None,
) -> list[Alert]:
    """
    Evalueert een ticker op alle actieve triggers.
    Vergelijkt de twee meest recente opgeslagen snapshots.

    Args:
        ticker:           Ticker symbol
        watchlist_config: Watchlist-instellingen voor drempelwaarden
        watchlist_name:   Naam van de watchlist (voor logging)

    Returns:
        Lijst van gefirde alerts (lege lijst = geen triggers)
    """
    snaps = load_snapshots(ticker, limit=5)
    if len(snaps) < 2:
        logger.debug(f"alert_engine: {ticker} heeft < 2 snapshots, skip")
        return []

    new_snap = snaps[0]   # Nieuwste
    old_snap = snaps[1]   # Vorige

    wl = watchlist_config or {}
    alerts: list[Alert] = []

    # 1. Momentum threshold
    alerts.extend(check_momentum_threshold(ticker, old_snap, new_snap, watchlist_name))

    # 2. Phase transition
    if wl.get("alert_on_phase_change", True):
        alerts.extend(check_phase_transition(ticker, old_snap, new_snap, watchlist_name))

    # 3. Volume anomaly
    if wl.get("alert_on_volume_spike", False):
        vol_thresh = float(wl.get("volume_spike_threshold", 3.0))
        alerts.extend(check_volume_anomaly(ticker, new_snap, vol_thresh, watchlist_name))

    # 4. Confidence downgrade
    alerts.extend(check_confidence_downgrade(ticker, old_snap, new_snap, watchlist_name))

    # 5. Score drop
    if wl.get("alert_on_score_drop", False):
        alerts.extend(check_score_drop(ticker, old_snap, new_snap, 15.0, watchlist_name))

    # 6. Evaluation insight
    alerts.extend(check_evaluation_insight(ticker, new_snap, watchlist_name))

    # Filter op minimum severity van de watchlist
    min_sev = wl.get("min_alert_severity", "INFO")
    min_rank = severity_rank(min_sev)
    alerts = [a for a in alerts if severity_rank(a.severity) >= min_rank]

    if alerts:
        logger.info(
            f"alert_engine: {ticker} → {len(alerts)} alert(s) "
            f"({', '.join(a.severity for a in alerts)})"
        )

    return alerts


def scan_all_watchlists() -> dict:
    """
    Scant alle watchlist-tickers op alerts.

    Returns dict met alerts per ticker.
    """
    from alerting.watchlist_manager import list_watchlists, load_watchlist

    all_alerts: dict[str, list[dict]] = {}
    total = 0

    for wl_info in list_watchlists():
        wl_data = load_watchlist(wl_info["name"])
        if not wl_data:
            continue

        for ticker in wl_data.get("tickers", []):
            ticker = ticker.upper()
            fired  = scan_ticker(ticker, wl_data, wl_info["name"])
            if fired:
                if ticker not in all_alerts:
                    all_alerts[ticker] = []
                from dataclasses import asdict
                all_alerts[ticker].extend([asdict(a) for a in fired])
                total += len(fired)

    logger.info(f"alert_engine: scan compleet — {total} alerts over {len(all_alerts)} tickers")
    return all_alerts
