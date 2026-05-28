"""
storage/evaluation_store.py
Signal Evaluation Storage — v2.7

Slaat SignalOutcome objecten op per ticker.
Formaat: JSON Lines — storage/data/evaluations/{TICKER}.jsonl

Elke entry koppelt aan een snapshot via version_id.
Evaluaties zijn idempotent: dezelfde version_id overschrijft vorige grade.

Design:
    - Evaluaties worden op aanvraag berekend, niet automatisch
    - PENDING blijft staan totdat er toekomstige snapshot data is
    - Grade is onveranderlijk zodra bepaald (SUCCESS/NEUTRAL/FAILED)
"""

import json
import os
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_STORAGE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "storage", "data"
)
_EVALS_DIR    = os.path.join(_STORAGE_ROOT, "evaluations")

# Grade waarden
GRADE_SUCCESS = "SUCCESS"
GRADE_NEUTRAL = "NEUTRAL"
GRADE_FAILED  = "FAILED"
GRADE_PENDING = "PENDING"   # Nog geen toekomstige data

# Grade thresholds (configureerbaar via env vars)
SUCCESS_THRESHOLD = float(os.getenv("EVAL_SUCCESS_THRESHOLD", "3.0"))
FAILED_THRESHOLD  = float(os.getenv("EVAL_FAILED_THRESHOLD",  "-3.0"))

# Tijdshorizons in uren
HORIZONS = {
    "1h":  1.0,
    "4h":  4.0,
    "1d":  24.0,
    "3d":  72.0,
}
PRIMARY_HORIZON = os.getenv("EVAL_PRIMARY_HORIZON", "1d")


@dataclass
class SignalOutcome:
    """Evaluatie van één signaal (snapshot) met achterafresultaat."""
    version_id:       str
    ticker:           str
    timestamp:        str        # Tijdstip van het signaal
    decision:         str
    momentum_score:   float
    phase:            str
    catalyst_type:    str
    sector_id:        str

    entry_price:      float      # Prijs op het moment van het signaal

    # Toekomstige prijzen (uit latere snapshots)
    price_1h:  Optional[float] = None
    price_4h:  Optional[float] = None
    price_1d:  Optional[float] = None
    price_3d:  Optional[float] = None

    # Rendement (percentage)
    return_1h: Optional[float] = None
    return_4h: Optional[float] = None
    return_1d: Optional[float] = None
    return_3d: Optional[float] = None

    # Grade
    grade:       str           = GRADE_PENDING
    grade_basis: Optional[str] = None  # "1d", "4h", etc.
    graded_at:   Optional[str] = None
    evaluated_at: Optional[str] = None


def _eval_path(ticker: str) -> str:
    os.makedirs(_EVALS_DIR, exist_ok=True)
    return os.path.join(_EVALS_DIR, f"{ticker.upper()}.jsonl")


def save_outcome(outcome: SignalOutcome) -> None:
    """
    Slaat een SignalOutcome op. Overschrijft vorige entry met dezelfde version_id.
    """
    path = _eval_path(outcome.ticker)

    # Lees bestaande entries excl. zelfde version_id
    existing = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        if d.get("version_id") != outcome.version_id:
                            existing.append(d)
                    except json.JSONDecodeError:
                        continue
        except Exception as exc:
            logger.warning(f"eval_store: lezen mislukt voor {outcome.ticker}: {exc}")

    existing.append(asdict(outcome))

    try:
        with open(path, "w", encoding="utf-8") as f:
            for entry in existing:
                f.write(json.dumps(entry) + "\n")
        logger.debug(
            f"eval_store: {outcome.ticker}/{outcome.version_id} "
            f"grade={outcome.grade}"
        )
    except Exception as exc:
        logger.warning(f"eval_store: schrijven mislukt voor {outcome.ticker}: {exc}")


def load_outcomes(ticker: str, limit: int = 200) -> list[dict]:
    """
    Laadt opgeslagen evaluaties voor een ticker.
    Nieuwste eerst gesorteerd op timestamp.
    """
    path = _eval_path(ticker)
    if not os.path.exists(path):
        return []

    outcomes = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    outcomes.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning(f"eval_store: laden mislukt voor {ticker}: {exc}")
        return []

    # Sorteer op timestamp (nieuwste eerst)
    outcomes.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return outcomes[:limit]


def load_outcome_by_version(ticker: str, version_id: str) -> Optional[dict]:
    """Haalt één specifieke outcome op via version_id."""
    for o in load_outcomes(ticker, limit=500):
        if o.get("version_id") == version_id:
            return o
    return None


def load_graded_outcomes(ticker: str) -> list[dict]:
    """Alleen outcomes met een definitieve grade (niet PENDING)."""
    return [
        o for o in load_outcomes(ticker)
        if o.get("grade") != GRADE_PENDING
    ]


def list_evaluated_tickers() -> list[str]:
    """Geeft alle tickers waarvoor evaluaties opgeslagen zijn."""
    try:
        os.makedirs(_EVALS_DIR, exist_ok=True)
        return [
            f.replace(".jsonl", "")
            for f in os.listdir(_EVALS_DIR)
            if f.endswith(".jsonl")
        ]
    except Exception:
        return []


def delete_outcomes(ticker: str) -> bool:
    """Verwijdert alle evaluaties voor een ticker."""
    path = _eval_path(ticker)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
