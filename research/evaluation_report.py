"""
research/evaluation_report.py
Evaluation Report Generation — v2.7

Genereert leesbare rapporten van signal evaluaties.

Rapporten:
    ticker_evaluation_report()  → Markdown rapport voor één ticker
    sector_evaluation_report()  → Sector-level performance
    global_summary_report()     → Overzicht van alle geëvalueerde signalen
    export_evaluation_json()    → JSON export voor verdere analyse
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
_REVIEWS_DIR = os.path.join(_RESEARCH_ROOT, "signal_reviews")


def _ensure_dirs() -> None:
    os.makedirs(_REVIEWS_DIR, exist_ok=True)


# ── TICKER EVALUATION REPORT ──────────────────────────────────────────────────

def ticker_evaluation_report(
    ticker:     str,
    statistics: dict,
    outcomes:   list[dict],
) -> str:
    """Genereert een Markdown rapport voor één ticker."""
    now        = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total      = statistics.get("total_graded", 0)
    succ_rate  = statistics.get("success_rate")
    succ_str   = f"{succ_rate * 100:.1f}%" if succ_rate is not None else "N/A"

    best  = statistics.get("best_signal")
    worst = statistics.get("worst_signal")

    lines = [
        f"# Signal Evaluation: {ticker.upper()}",
        f"*Gegenereerd: {now}*",
        "",
        "## Samenvatting",
        f"| Metric | Waarde |",
        f"|---|---|",
        f"| Gegradeerde signalen | {total} |",
        f"| Success rate | {succ_str} |",
        f"| Success | {statistics.get('success_count', 0)} |",
        f"| Failed | {statistics.get('failed_count', 0)} |",
        f"| Neutral | {statistics.get('neutral_count', 0)} |",
        f"| Gemiddelde score (SUCCESS) | {statistics.get('avg_score_success') or 'N/A'} |",
        f"| Gemiddelde score (FAILED) | {statistics.get('avg_score_failed') or 'N/A'} |",
        f"| Gemiddelde return 1d | {statistics.get('avg_return_1d') or 'N/A'}% |",
        "",
    ]

    # Best signal
    if best:
        lines += [
            "## Beste signaal",
            f"- **Beslissing:** {best.get('decision')}",
            f"- **Score:** {best.get('momentum_score')}",
            f"- **Fase:** {best.get('phase')}",
            f"- **Return 1d:** {best.get('return_1d', 'N/A')}%",
            f"- **Tijdstip:** {best.get('timestamp', '')[:16].replace('T', ' ')}",
            "",
        ]

    if worst:
        lines += [
            "## Slechtste signaal",
            f"- **Beslissing:** {worst.get('decision')}",
            f"- **Score:** {worst.get('momentum_score')}",
            f"- **Return 1d:** {worst.get('return_1d', 'N/A')}%",
            "",
        ]

    # Per fase
    by_phase = statistics.get("by_phase", {})
    if by_phase:
        lines += ["## Per fase", "| Fase | Success | Total | Rate |", "|---|---|---|---|"]
        for phase, stats in sorted(by_phase.items()):
            rate = f"{stats['success_rate'] * 100:.0f}%"
            lines.append(f"| {phase} | {stats['success']} | {stats['total']} | {rate} |")
        lines.append("")

    # Per catalyst
    by_cat = statistics.get("by_catalyst", {})
    if by_cat:
        lines += ["## Per catalyst", "| Catalyst | Success | Total | Rate |", "|---|---|---|---|"]
        for cat, stats in sorted(by_cat.items()):
            rate = f"{stats['success_rate'] * 100:.0f}%"
            lines.append(f"| {cat} | {stats['success']} | {stats['total']} | {rate} |")
        lines.append("")

    # Recente signalen
    recent = [o for o in outcomes[:10] if o.get("grade") != "PENDING"]
    if recent:
        lines += ["## Recente gegradeerde signalen",
                  "| Tijdstip | Beslissing | Score | Grade | Return 1d |",
                  "|---|---|---|---|---|"]
        for o in recent:
            ts    = o.get("timestamp", "")[:16].replace("T", " ")
            dec   = o.get("decision", "")
            score = f"{o.get('momentum_score', 0):.0f}"
            grade = o.get("grade", "")
            ret   = f"{o.get('return_1d', 'N/A')}%"
            lines.append(f"| {ts} | {dec} | {score} | {grade} | {ret} |")
        lines.append("")

    return "\n".join(lines)


# ── GLOBAL SUMMARY REPORT ─────────────────────────────────────────────────────

def global_summary_report(global_stats: dict) -> str:
    """Markdown rapport voor alle geëvalueerde signalen."""
    now       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total     = global_stats.get("total_graded", 0)
    n_tickers = global_stats.get("tickers_evaluated", 0)
    succ      = global_stats.get("success_rate")
    succ_str  = f"{succ * 100:.1f}%" if succ is not None else "N/A"

    lines = [
        "# Global Signal Evaluation Summary",
        f"*Gegenereerd: {now}*",
        "",
        "## Overzicht",
        f"| Metric | Waarde |",
        f"|---|---|",
        f"| Geëvalueerde tickers | {n_tickers} |",
        f"| Totaal gegradeerde signalen | {total} |",
        f"| Globale success rate | {succ_str} |",
        f"| Success | {global_stats.get('success_count', 0)} |",
        f"| Failed | {global_stats.get('failed_count', 0)} |",
        f"| Neutral | {global_stats.get('neutral_count', 0)} |",
        "",
    ]

    by_phase = global_stats.get("by_phase", {})
    if by_phase:
        lines += ["## Per fase",
                  "| Fase | Success rate | Total |",
                  "|---|---|---|"]
        for phase, s in sorted(by_phase.items(),
                                key=lambda x: x[1]["success_rate"], reverse=True):
            lines.append(f"| {phase} | {s['success_rate'] * 100:.0f}% | {s['total']} |")
        lines.append("")

    by_dec = global_stats.get("by_decision", {})
    if by_dec:
        lines += ["## Per beslissing",
                  "| Beslissing | Success rate | Total |",
                  "|---|---|---|"]
        for dec, s in sorted(by_dec.items(),
                              key=lambda x: x[1]["success_rate"], reverse=True):
            lines.append(f"| {dec} | {s['success_rate'] * 100:.0f}% | {s['total']} |")

    return "\n".join(lines)


# ── JSON EXPORT ───────────────────────────────────────────────────────────────

def export_evaluation_json(
    ticker:     str,
    statistics: dict,
    outcomes:   list[dict],
    top_signals: Optional[list[dict]] = None,
) -> str:
    """
    Exporteert volledige evaluatie als JSON naar research/signal_reviews/.

    Returns:
        Pad naar het opgeslagen bestand.
    """
    _ensure_dirs()
    now      = datetime.now(timezone.utc)
    ts_str   = now.strftime("%Y%m%d_%H%M%S")
    filename = f"{ticker.upper()}_eval_{ts_str}.json"
    path     = os.path.join(_REVIEWS_DIR, filename)

    payload = {
        "exported_at": now.isoformat(),
        "ticker":      ticker.upper(),
        "statistics":  statistics,
        "outcomes":    outcomes,
        "top_signals": top_signals or [],
    }

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)
        logger.info(f"evaluation_report: JSON geëxporteerd naar {path}")
    except Exception as exc:
        logger.warning(f"evaluation_report: export mislukt: {exc}")

    return path


def export_markdown_report(
    ticker:     str,
    statistics: dict,
    outcomes:   list[dict],
) -> str:
    """Exporteert Markdown rapport naar research/signal_reviews/."""
    _ensure_dirs()
    now      = datetime.now(timezone.utc)
    ts_str   = now.strftime("%Y%m%d_%H%M%S")
    filename = f"{ticker.upper()}_eval_{ts_str}.md"
    path     = os.path.join(_REVIEWS_DIR, filename)

    md = ticker_evaluation_report(ticker, statistics, outcomes)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(md)
    except Exception as exc:
        logger.warning(f"evaluation_report: markdown export mislukt: {exc}")

    return path
