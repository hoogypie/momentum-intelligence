"""
alerting/alert_store.py
Alert Storage — v2.9

Slaat Alert objecten op als JSON Lines.
Formaat: storage/data/alerts/{TICKER}.jsonl
Index:   storage/data/alerts/_index.jsonl (alle alerts, nieuwste eerst)

Design:
    - Elk alert heeft een uniek alert_id (timestamp + ticker + trigger)
    - Index bestand maakt snelle queries mogelijk zonder alle tickerfiles te lezen
    - Max ALERT_HISTORY_LIMIT alerts per ticker
"""

import json, os, logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_STORAGE_ROOT   = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "storage", "data"
)
_ALERTS_DIR     = os.path.join(_STORAGE_ROOT, "alerts")
ALERT_HISTORY_LIMIT = int(os.getenv("ALERT_HISTORY_LIMIT", "500"))

# Severity volgorde (hogere index = ernstiger)
SEVERITY_ORDER = ["INFO", "WATCH", "HIGH", "CRITICAL"]

# Trigger types
TRIGGER_MOMENTUM_THRESHOLD = "momentum_threshold"
TRIGGER_PHASE_TRANSITION   = "phase_transition"
TRIGGER_SECTOR_HEAT_SPIKE  = "sector_heat_spike"
TRIGGER_VOLUME_ANOMALY     = "volume_anomaly"
TRIGGER_CONFIDENCE_DOWN    = "confidence_downgrade"
TRIGGER_BUY_MAX            = "buy_max_signal"
TRIGGER_EVAL_INSIGHT       = "evaluation_insight"
TRIGGER_SCORE_DROP         = "score_drop"


@dataclass
class Alert:
    alert_id:    str
    ticker:      str
    severity:    str        # INFO / WATCH / HIGH / CRITICAL
    trigger_type: str
    title:       str
    message:     str
    timestamp:   str

    # Wat veranderde
    old_value:   Optional[str]   = None
    new_value:   Optional[str]   = None
    score:       Optional[float] = None
    phase:       Optional[str]   = None

    # Historische context
    historical_context: Optional[str] = None

    # Metadata
    watchlist:   Optional[str] = None
    suppressed:  bool          = False
    version_id:  Optional[str] = None   # Triggerende snapshot


def make_alert_id(ticker: str, trigger: str) -> str:
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y%m%dT%H%M%S')}_{now.microsecond:06d}_{ticker}_{trigger}"


def severity_rank(sev: str) -> int:
    try:    return SEVERITY_ORDER.index(sev)
    except: return 0


def _alerts_path(ticker: str) -> str:
    os.makedirs(_ALERTS_DIR, exist_ok=True)
    return os.path.join(_ALERTS_DIR, f"{ticker.upper()}.jsonl")


def _index_path() -> str:
    os.makedirs(_ALERTS_DIR, exist_ok=True)
    return os.path.join(_ALERTS_DIR, "_index.jsonl")


# ── WRITE ─────────────────────────────────────────────────────────────────────

def save_alert(alert: Alert) -> None:
    """Sla alert op in ticker-bestand + index."""
    if alert.suppressed:
        return   # Gesupprimeerde alerts worden niet opgeslagen

    d = asdict(alert)

    # Ticker-specifiek bestand
    try:
        path = _alerts_path(alert.ticker)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(d) + "\n")
        _trim_alerts(alert.ticker)
    except Exception as exc:
        logger.warning(f"alert_store: ticker save mislukt: {exc}")

    # Globale index (nieuwste bovenaan — prepend)
    try:
        idx_path = _index_path()
        existing = []
        if os.path.exists(idx_path):
            with open(idx_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try: existing.append(json.loads(line))
                        except: pass
        existing.insert(0, d)
        existing = existing[:ALERT_HISTORY_LIMIT]
        with open(idx_path, "w", encoding="utf-8") as f:
            for entry in existing:
                f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning(f"alert_store: index save mislukt: {exc}")

    logger.info(
        f"ALERT [{alert.severity}] {alert.ticker}: {alert.title}"
    )


def _trim_alerts(ticker: str) -> None:
    path = _alerts_path(ticker)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [l for l in f.readlines() if l.strip()]
        if len(lines) > ALERT_HISTORY_LIMIT:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-ALERT_HISTORY_LIMIT:])
    except Exception:
        pass


# ── READ ──────────────────────────────────────────────────────────────────────

def load_alerts(
    ticker:   Optional[str] = None,
    severity: Optional[str] = None,
    limit:    int            = 50,
) -> list[dict]:
    """
    Laadt alerts. Zonder ticker: alle alerts via index.
    Met ticker: alleen ticker-specifieke alerts.
    Optioneel filter op minimum severity.
    """
    if ticker:
        alerts = _load_from_file(_alerts_path(ticker), limit=limit * 2)
    else:
        alerts = _load_from_file(_index_path(), limit=limit * 2)

    if severity:
        min_rank = severity_rank(severity)
        alerts = [a for a in alerts if severity_rank(a.get("severity", "INFO")) >= min_rank]

    return alerts[:limit]


def _load_from_file(path: str, limit: int = 200) -> list[dict]:
    if not os.path.exists(path):
        return []
    result = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
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
    except Exception as exc:
        logger.warning(f"alert_store: laden mislukt: {exc}")
    return result


def load_recent_alerts(hours: float = 24.0, limit: int = 50) -> list[dict]:
    """Alerts van de afgelopen `hours` uur."""
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    all_alerts = load_alerts(limit=limit * 3)
    return [a for a in all_alerts if a.get("timestamp", "") >= cutoff][:limit]


def count_alerts_by_severity(ticker: Optional[str] = None) -> dict:
    alerts = load_alerts(ticker=ticker, limit=500)
    result = {s: 0 for s in SEVERITY_ORDER}
    for a in alerts:
        sev = a.get("severity", "INFO")
        if sev in result:
            result[sev] += 1
    return result


def list_alerted_tickers() -> list[str]:
    try:
        os.makedirs(_ALERTS_DIR, exist_ok=True)
        return [
            f.replace(".jsonl", "")
            for f in os.listdir(_ALERTS_DIR)
            if f.endswith(".jsonl") and not f.startswith("_")
        ]
    except Exception:
        return []
