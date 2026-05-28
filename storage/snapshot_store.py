"""
storage/snapshot_store.py
Snapshot Persistence — v2.5

Slaat ScoringResult + DataQuality op als tijdgestempelde snapshots.
Eén .jsonl bestand per ticker: append-only, elke regel = één snapshot.

Formaat: JSON Lines (.jsonl)
Locatie: storage/data/tickers/{TICKER}.jsonl
Max:     MAX_SNAPSHOTS_PER_TICKER regels (oudste verwijderd na trim)

Versie ID: {YYYYMMDDTHHMMSSZ}_{TICKER}
Uniek per opslag — gebruikt als referentie in transitions + replay.

Design keuzes:
    - Geen database dependency (draait op elke machine)
    - Append-only → nooit corruptie bij crash
    - .gitignore voor storage/data/ → geen gevoelige data in git
    - MAX_SNAPSHOTS_PER_TICKER = 500 per ticker (± 50KB per ticker)
"""

import json
import os
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

_STORAGE_ROOT     = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "storage", "data"
)
_TICKERS_DIR      = os.path.join(_STORAGE_ROOT, "tickers")
MAX_SNAPSHOTS_PER_TICKER = int(os.getenv("MAX_SNAPSHOTS_PER_TICKER", "500"))


# ── STORED SNAPSHOT SCHEMA ────────────────────────────────────────────────────

@dataclass
class StoredSnapshot:
    """
    Persistente snapshot van één scoring moment.
    Bevat kernvelden voor trend-analyse en replay.
    """
    version_id:          str      # Unieke ID: YYYYMMDDTHHMMSSZ_TICKER
    ticker:              str
    timestamp:           str      # ISO UTC

    # Score
    decision:            str
    momentum_score:      float
    skip_score:          int
    phase:               str

    # Data context
    confidence:          str
    cache_hit:           bool
    data_age_seconds:    float
    retries_used:        int

    # Signal components (voor trend analyse)
    catalyst_type:       str
    catalyst_description: str
    day_change_pct:      float
    volume_ratio:        float    # volume_today / avg_volume_20d
    sector_heat:         int
    sector_id:           str
    market_session:      Optional[str]
    price:               float
    premarket_pct:       float

    # Metadata
    stored_at:           str      # ISO UTC, kan afwijken van timestamp


def _make_version_id(ticker: str, ts: datetime) -> str:
    return f"{ts.strftime('%Y%m%dT%H%M%S')}_{ts.microsecond:06d}_{ticker.upper()}"


def _ticker_path(ticker: str) -> str:
    os.makedirs(_TICKERS_DIR, exist_ok=True)
    return os.path.join(_TICKERS_DIR, f"{ticker.upper()}.jsonl")


# ── WRITE ─────────────────────────────────────────────────────────────────────

def save_snapshot(
    ticker:       str,
    result,       # ScoringResult (dataclass)
    quality,      # DataQuality (pydantic)
) -> str:
    """
    Slaat scoring resultaat op als StoredSnapshot.

    Returns:
        version_id van de opgeslagen snapshot.

    Nooit een exception — logging bij fout, versie-ID als fallback return.
    """
    now = datetime.now(timezone.utc)
    vid = _make_version_id(ticker, now)

    try:
        # Volume ratio uit momentum detail
        bd = result.momentum_detail.breakdown
        vol_key   = next((k for k in bd if "Volume" in k), None)
        vol_ratio = 0.0
        if vol_key:
            val = bd[vol_key].strip().split("—")[0].strip()
            try:
                vol_ratio = float(val.replace("x", "").strip().split()[0])
            except (ValueError, IndexError):
                vol_ratio = 0.0

        snap = StoredSnapshot(
            version_id          = vid,
            ticker              = ticker.upper(),
            timestamp           = now.isoformat(),
            decision            = result.decision.value
                                  if hasattr(result.decision, "value")
                                  else str(result.decision),
            momentum_score      = result.momentum_score,
            skip_score          = result.skip_score,
            phase               = result.phase.value
                                  if hasattr(result.phase, "value")
                                  else str(result.phase),
            confidence          = quality.confidence.value
                                  if hasattr(quality.confidence, "value")
                                  else str(quality.confidence),
            cache_hit           = quality.cache_hit,
            data_age_seconds    = quality.data_age_seconds,
            retries_used        = quality.retries_used,
            catalyst_type       = result.momentum_detail.breakdown.get(
                                      "Catalyst Quality  (max 20)", ""
                                  ).split("—")[-1].strip().split(":")[0].strip(),
            catalyst_description = (
                result.momentum_detail.breakdown.get(
                    "Catalyst Quality  (max 20)", ""
                ).split("—")[-1].strip()
            ),
            day_change_pct      = 0.0,   # Not in ScoringResult — filled by caller
            volume_ratio        = vol_ratio,
            sector_heat         = int(
                result.momentum_detail.sector_heat_score / 18.0 * 100
            ),
            sector_id           = "unknown",  # Filled by caller if available
            market_session      = None,
            price               = 0.0,        # Filled by caller
            premarket_pct       = 0.0,
            stored_at           = now.isoformat(),
        )

        path = _ticker_path(ticker)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(snap)) + "\n")

        logger.debug(f"snapshot_store: opgeslagen {vid}")
        _trim_if_needed(ticker)
        return vid

    except Exception as exc:
        logger.warning(f"snapshot_store: opslaan mislukt voor {ticker}: {exc}")
        return vid


def save_snapshot_dict(ticker: str, snap_dict: dict) -> str:
    """
    Slaat een al samengestelde StoredSnapshot-dict op.
    Genereert altijd een vers version_id (microseconde-precisie).
    """
    now = datetime.now(timezone.utc)
    vid = _make_version_id(ticker, now)
    snap_dict = dict(snap_dict)   # kopie — nooit de caller dict muteren
    snap_dict["version_id"] = vid
    snap_dict["stored_at"]  = now.isoformat()

    try:
        path = _ticker_path(ticker)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap_dict) + "\n")
        logger.debug(f"snapshot_store: dict opgeslagen {vid}")
        _trim_if_needed(ticker)
    except Exception as exc:
        logger.warning(f"snapshot_store: dict opslaan mislukt voor {ticker}: {exc}")

    return vid


def _trim_if_needed(ticker: str) -> None:
    """Behoudt max MAX_SNAPSHOTS_PER_TICKER regels. Verwijdert de oudste."""
    path = _ticker_path(ticker)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if len(lines) > MAX_SNAPSHOTS_PER_TICKER:
            keep = lines[-MAX_SNAPSHOTS_PER_TICKER:]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(keep)
            logger.debug(
                f"snapshot_store: {ticker} getrimed naar "
                f"{MAX_SNAPSHOTS_PER_TICKER} entries"
            )
    except Exception as exc:
        logger.debug(f"snapshot_store: trim fout voor {ticker}: {exc}")


# ── READ ──────────────────────────────────────────────────────────────────────

def load_snapshots(
    ticker: str,
    limit:  int = 50,
) -> list[dict]:
    """
    Laadt de laatste `limit` snapshots voor een ticker.
    Nieuwste eerst gesorteerd.
    """
    path = _ticker_path(ticker)
    if not os.path.exists(path):
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        snapshots = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(snapshots) >= limit:
                break

        return snapshots

    except Exception as exc:
        logger.warning(f"snapshot_store: laden mislukt voor {ticker}: {exc}")
        return []


def load_latest(ticker: str) -> Optional[dict]:
    """Geeft de meest recente snapshot terug, of None."""
    snaps = load_snapshots(ticker, limit=1)
    return snaps[0] if snaps else None


def load_since(ticker: str, hours: float = 24.0) -> list[dict]:
    """Laadt alle snapshots van de afgelopen `hours` uur."""
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    all_snaps = load_snapshots(ticker, limit=MAX_SNAPSHOTS_PER_TICKER)
    result = []
    for s in all_snaps:
        try:
            ts = datetime.fromisoformat(s["timestamp"]).timestamp()
            if ts >= cutoff:
                result.append(s)
        except (KeyError, ValueError):
            continue
    return result


def count_snapshots(ticker: str) -> int:
    """Geeft het aantal opgeslagen snapshots voor een ticker."""
    path = _ticker_path(ticker)
    if not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def list_tracked_tickers() -> list[str]:
    """Geeft lijst van alle tickers waarvoor snapshots opgeslagen zijn."""
    try:
        os.makedirs(_TICKERS_DIR, exist_ok=True)
        return [
            f.replace(".jsonl", "")
            for f in os.listdir(_TICKERS_DIR)
            if f.endswith(".jsonl")
        ]
    except Exception:
        return []


def delete_ticker_history(ticker: str) -> bool:
    """Verwijdert alle snapshots voor een ticker. Geeft True als verwijderd."""
    path = _ticker_path(ticker)
    if os.path.exists(path):
        os.remove(path)
        logger.info(f"snapshot_store: geschiedenis verwijderd voor {ticker}")
        return True
    return False
