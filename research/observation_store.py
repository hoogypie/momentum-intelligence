"""
research/observation_store.py
Observation Store — v2.6

Beheert de research/ directory:
    research/observations/      Handmatige Markdown notities (user-edited)
    research/replay_notes/      Auto-gegenereerde replay summaries (JSON)
    research/signal_reviews/    Signal review exports (JSON + Markdown)

Auto-gegenereerde bestanden worden overschreven bij herexport.
Handmatige observaties (observations/) worden nooit automatisch overschreven.
"""

import json
import os
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_RESEARCH_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "research"
)
_OBS_DIR     = os.path.join(_RESEARCH_ROOT, "observations")
_NOTES_DIR   = os.path.join(_RESEARCH_ROOT, "replay_notes")
_REVIEWS_DIR = os.path.join(_RESEARCH_ROOT, "signal_reviews")


def _ensure_dirs() -> None:
    for d in (_OBS_DIR, _NOTES_DIR, _REVIEWS_DIR):
        os.makedirs(d, exist_ok=True)


# ── REPLAY NOTES (auto-generated) ─────────────────────────────────────────────

def save_replay_note(
    ticker:      str,
    replay_data: dict,
    overwrite:   bool = True,
) -> str:
    """
    Slaat een auto-gegenereerde replay note op als JSON.
    Bestandsnaam: {TICKER}_{YYYYMMDD}.json

    Returns:
        Pad naar het opgeslagen bestand.
    """
    _ensure_dirs()
    date_str  = datetime.now(timezone.utc).strftime("%Y%m%d")
    filename  = f"{ticker.upper()}_{date_str}.json"
    path      = os.path.join(_NOTES_DIR, filename)

    if not overwrite and os.path.exists(path):
        return path

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ticker":        ticker.upper(),
        **replay_data,
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        logger.debug(f"observation_store: replay note opgeslagen: {filename}")
    except Exception as exc:
        logger.warning(f"observation_store: opslaan mislukt: {exc}")

    return path


def list_replay_notes(ticker: Optional[str] = None) -> list[str]:
    """Geeft alle opgeslagen replay notes terug (optioneel gefilterd op ticker)."""
    _ensure_dirs()
    try:
        files = os.listdir(_NOTES_DIR)
        if ticker:
            files = [f for f in files if f.startswith(ticker.upper() + "_")]
        return sorted(files, reverse=True)  # Nieuwste eerst
    except Exception:
        return []


# ── SIGNAL REVIEWS (export + inspect) ────────────────────────────────────────

def save_signal_review(
    ticker:      str,
    replay_data: dict,
    summary:     str = "",
) -> str:
    """
    Exporteert een signal review als JSON + leesbare Markdown.

    Returns:
        Pad naar het JSON bestand.
    """
    _ensure_dirs()
    now       = datetime.now(timezone.utc)
    ts_str    = now.strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(_REVIEWS_DIR, f"{ticker.upper()}_{ts_str}.json")
    md_path   = os.path.join(_REVIEWS_DIR, f"{ticker.upper()}_{ts_str}.md")

    # JSON export
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "exported_at": now.isoformat(),
                "ticker":      ticker.upper(),
                "summary":     summary,
                **replay_data,
            }, f, indent=2, default=str)
    except Exception as exc:
        logger.warning(f"observation_store: JSON export mislukt: {exc}")

    # Markdown export
    try:
        md = _build_review_markdown(ticker, replay_data, summary, now)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception as exc:
        logger.warning(f"observation_store: Markdown export mislukt: {exc}")

    return json_path


def _build_review_markdown(
    ticker:      str,
    replay_data: dict,
    summary:     str,
    timestamp:   datetime,
) -> str:
    snap_count  = replay_data.get("snapshot_count", 0)
    trend       = replay_data.get("momentum_trend", "?")
    rep_summary = replay_data.get("summary", {})
    current     = rep_summary.get("current") or {}
    strongest   = rep_summary.get("strongest_ever") or {}

    return f"""# Signal Review: {ticker.upper()}
*Gegenereerd: {timestamp.strftime('%Y-%m-%d %H:%M UTC')}*

## Samenvatting
{summary or 'Geen samenvatting opgegeven.'}

## Huidig signaal
- **Beslissing:** {current.get('decision', 'onbekend')}
- **Score:** {current.get('momentum_score', 'onbekend')}
- **Fase:** {current.get('phase', 'onbekend')}
- **Confidence:** {current.get('confidence', 'onbekend')}

## Historische statistieken
- **Snapshots opgeslagen:** {snap_count}
- **Momentum trend:** {trend}
- **Sterkste signaal:** {strongest.get('decision', '?')} (score {strongest.get('score', '?')})

## Significante veranderingen
{_format_sig_changes(replay_data.get('significant_changes', []))}
"""


def _format_sig_changes(changes: list) -> str:
    if not changes:
        return "*Geen significante veranderingen gevonden.*"
    lines = []
    for c in changes[:10]:
        ts = c.get("timestamp_to", "")[:16].replace("T", " ")
        lines.append(f"- `{ts}` — {c.get('summary', '?')}")
    return "\n".join(lines)


def list_signal_reviews(ticker: Optional[str] = None) -> list[str]:
    """Geeft alle opgeslagen signal reviews terug."""
    _ensure_dirs()
    try:
        files = [f for f in os.listdir(_REVIEWS_DIR) if f.endswith(".json")]
        if ticker:
            files = [f for f in files if f.startswith(ticker.upper() + "_")]
        return sorted(files, reverse=True)
    except Exception:
        return []


# ── OBSERVATIONS (manual notes) ───────────────────────────────────────────────

def create_observation_template(ticker: str) -> str:
    """
    Maakt een Markdown template aan voor handmatige notities.
    Wordt NIET overschreven als het al bestaat.

    Returns:
        Pad naar het bestand.
    """
    _ensure_dirs()
    path = os.path.join(_OBS_DIR, f"{ticker.upper()}_observation.md")

    if os.path.exists(path):
        return path

    template = f"""# Observaties: {ticker.upper()}
*Handmatige notities — niet automatisch overschreven*

## Thesis
[Vul in waarom dit aandeel interessant is]

## Entry criteria
- Score ≥ ?
- Fase: ?
- Catalyst: ?

## Red flags
- [Wat zou de thesis breken?]

## Notities
| Datum | Observatie |
|---|---|
| {datetime.now(timezone.utc).strftime('%Y-%m-%d')} | Template aangemaakt |

"""
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(template)
    except Exception as exc:
        logger.warning(f"observation_store: template aanmaken mislukt: {exc}")

    return path


def list_observations() -> list[str]:
    """Geeft alle handmatige observatie-bestanden terug."""
    _ensure_dirs()
    try:
        return sorted([
            f for f in os.listdir(_OBS_DIR)
            if f.endswith(".md")
        ])
    except Exception:
        return []
