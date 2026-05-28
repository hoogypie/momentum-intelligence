"""
storage/sector_history.py
Sector History — v2.5

Slaat periodieke sector heat en gemiddeld momentum op.
Gebruikt voor heat trend analyse: "is quantum heating up or cooling down?"

Opslag: storage/data/sectors/{SECTOR_ID}.jsonl
Max:    MAX_SECTOR_SNAPSHOTS per sector
"""

import json
import os
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_STORAGE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "storage", "data"
)
_SECTORS_DIR  = os.path.join(_STORAGE_ROOT, "sectors")
MAX_SECTOR_SNAPSHOTS = int(os.getenv("MAX_SECTOR_SNAPSHOTS", "200"))


@dataclass
class SectorSnapshot:
    """Periodieke meting van sector performance."""
    sector_id:       str
    timestamp:       str        # ISO UTC
    heat:            int        # 0-100
    avg_momentum:    float      # Gemiddeld momentum van leaders
    avg_skip:        float      # Gemiddeld skip score van leaders
    leader_decisions: dict      # ticker → decision string
    leader_count:    int        # Aantal leaders gescoord
    sector_confidence: str      # Slechtste confidence van leaders


def _sector_path(sector_id: str) -> str:
    os.makedirs(_SECTORS_DIR, exist_ok=True)
    return os.path.join(_SECTORS_DIR, f"{sector_id.lower()}.jsonl")


def save_sector_snapshot(
    sector_id:        str,
    heat:             int,
    avg_momentum:     float,
    avg_skip:         float,
    leader_decisions: dict,
    sector_confidence: str = "LIVE",
) -> None:
    """Slaat een sector snapshot op."""
    snap = SectorSnapshot(
        sector_id=sector_id.lower(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        heat=heat,
        avg_momentum=round(avg_momentum, 1),
        avg_skip=round(avg_skip, 1),
        leader_decisions=leader_decisions,
        leader_count=len(leader_decisions),
        sector_confidence=sector_confidence,
    )
    try:
        path = _sector_path(sector_id)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(snap)) + "\n")
        _trim_sector(sector_id)
        logger.debug(f"sector_history: {sector_id} opgeslagen (heat={heat})")
    except Exception as exc:
        logger.warning(f"sector_history: opslaan mislukt voor {sector_id}: {exc}")


def _trim_sector(sector_id: str) -> None:
    path = _sector_path(sector_id)
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_SECTOR_SNAPSHOTS:
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_SECTOR_SNAPSHOTS:])
    except Exception:
        pass


def load_sector_history(sector_id: str, limit: int = 50) -> list[dict]:
    """Laadt recente sector snapshots. Nieuwste eerst."""
    path = _sector_path(sector_id)
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
        logger.warning(f"sector_history: laden mislukt voor {sector_id}: {exc}")
        return []


def get_heat_trend(sector_id: str, limit: int = 10) -> list[int]:
    """
    Geeft de heat waardes van de laatste `limit` snapshots.
    Nieuwste eerst. Nuttig voor grafiek of trend detectie.
    """
    history = load_sector_history(sector_id, limit=limit)
    return [s.get("heat", 0) for s in history]


def is_sector_heating_up(sector_id: str, window: int = 5) -> bool:
    """
    Geeft True als sector heat gemiddeld stijgt over de laatste `window` snapshots.
    Vergelijkt eerste helft met tweede helft.
    """
    trend = get_heat_trend(sector_id, limit=window * 2)
    if len(trend) < window:
        return False
    # trend is nieuwste-eerst; tweede helft = ouder
    recent = trend[:window]
    older  = trend[window:window * 2]
    if not older:
        return False
    return sum(recent) / len(recent) > sum(older) / len(older)


def get_momentum_trend(sector_id: str, limit: int = 10) -> list[float]:
    """Geeft avg_momentum van de laatste `limit` sector snapshots."""
    history = load_sector_history(sector_id, limit=limit)
    return [s.get("avg_momentum", 0.0) for s in history]
