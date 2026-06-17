"""
scripts/validation_runner.py
Validation Runner — v1.0

Batch-analyseert alle tickers uit research/validation_watchlist.json
via de lokale assembler + score engine. Geen backend-server vereist.

Output:
    research/validation/validation_YYYYMMDD_HHMMSS.json   Volledige resultaten
    research/validation/validation_YYYYMMDD_HHMMSS.csv    Gesorteerd op score

Gebruik:
    python3 scripts/validation_runner.py
    python3 scripts/validation_runner.py --group quantum
    python3 scripts/validation_runner.py --ticker NVDA IONQ MU
    python3 scripts/validation_runner.py --delay 2.0
    python3 scripts/validation_runner.py --no-persist

Opties:
    --group GROEP     Analyseer alleen één groep uit de watchlist
    --ticker A B C    Analyseer specifieke tickers (overschrijft watchlist)
    --delay N         Seconden wachten tussen requests (default: 1.5)
    --no-persist      Sla geen snapshots op in storage/
    --force-refresh   Negeer cache, forceer live Yahoo fetch

Structuur:
    Roept build_ticker_input() + score_ticker() direct aan —
    geen HTTP, geen backend nodig.
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# ── Path setup: scripts/ draait vanuit repo root ─────────────────────────────
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── .env laden — FINNHUB_API_KEY en andere variabelen ────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass  # python-dotenv niet geïnstalleerd — env vars moeten handmatig gezet zijn

from data.assembler import build_ticker_input
from data.finnhub_client import reset_session_stats, format_session_stats
from scoring.scoring_v1_2 import score_ticker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("validation")

_WATCHLIST_PATH = os.path.join(_REPO_ROOT, "research", "validation_watchlist.json")
_OUTPUT_DIR     = os.path.join(_REPO_ROOT, "research", "validation")

_DEFAULT_DELAY = 1.5  # seconden tussen Yahoo-requests — beschermt tegen rate limits


# ── Watchlist laden ───────────────────────────────────────────────────────────

def _load_watchlist(group_filter: Optional[str] = None) -> list[dict]:
    """
    Laadt tickers uit validation_watchlist.json.
    Retourneert lijst van dicts met ticker, group_id, group_label, cap, note.
    """
    with open(_WATCHLIST_PATH) as f:
        wl = json.load(f)

    entries = []
    for group in wl.get("groups", []):
        if group_filter and group["id"] != group_filter:
            continue
        for t in group.get("tickers", []):
            if not t.get("active", True):
                continue
            entries.append({
                "ticker":       t["ticker"].upper(),
                "group_id":     group["id"],
                "group_label":  group["label"],
                "sector_heat":  group.get("sector_heat", 50),
                "cap":          t.get("cap", "UNKNOWN"),
                "note":         t.get("note", ""),
                "cohort":       group.get("cohort", "A"),
                "expansion":    group.get("expansion", ""),
            })
    return entries


# ── Eén ticker analyseren ─────────────────────────────────────────────────────

def _analyze_one(
    ticker:        str,
    force_refresh: bool = False,
    persist:       bool = True,
    paper_trade:   bool = True,
) -> dict:
    """
    Bouwt TickerInput en scoort één ticker.
    Retourneert een plat resultaat-dict. Gooit nooit een exception.
    paper_trade=True: sla BUY-signalen op in paper_trade_store.
    """
    started_at = datetime.now(timezone.utc)

    try:
        ticker_input, quality = build_ticker_input(ticker, force_refresh=force_refresh)
        result = score_ticker(ticker_input)

        # Catalyst source en headlines ophalen via classifier direct
        # (assembler geeft deze niet terug via TickerInput — we lezen ze apart)
        cat_source   = "UNKNOWN"
        cat_conf     = "UNKNOWN"
        raw_headlines: list[str] = []
        try:
            from data.finnhub_client      import fetch_company_news, is_available as finnhub_available
            from data.catalyst_classifier import classify
            from data.assembler           import _find_sector
            sector = _find_sector(ticker)
            if finnhub_available():
                items = fetch_company_news(ticker, hours=48)
            else:
                from data.news_client import get_news
                from data.catalyst_classifier import classify_from_news_items
                legacy = get_news(ticker, hours=48)
                cat_result_extra = classify_from_news_items(
                    ticker, legacy, sector.leaders, sector.sympathy
                )
                cat_source     = cat_result_extra.catalyst_source.value
                cat_conf       = cat_result_extra.confidence.value
                raw_headlines  = cat_result_extra.raw_headlines[:5]
                items          = []
            if items:
                cat_result_extra = classify(ticker, items, sector.leaders, sector.sympathy)
                cat_source     = cat_result_extra.catalyst_source.value
                cat_conf       = cat_result_extra.confidence.value
                raw_headlines  = cat_result_extra.raw_headlines[:5]
        except Exception as cat_exc:
            logger.debug("validation_runner: catalyst enrichment mislukt voor %s: %s", ticker, cat_exc)

        # Top reasons: combineer skip blocking_reasons en momentum breakdown
        top_reasons = _extract_top_reasons(result, quality)

        # Paper trade: sla BUY-signalen op
        if paper_trade and result.decision.value in ("BUY_SMALL", "BUY_MODERATE", "BUY_STRONG", "BUY_MAX"):
            try:
                from storage.paper_trade_store import save_trade_from_result
                from data.assembler import _find_sector
                sector   = _find_sector(ticker)
                vol_ratio = (
                    ticker_input.volume_today / ticker_input.avg_volume_20d
                    if ticker_input.avg_volume_20d > 0 else 0.0
                )
                trade_id = save_trade_from_result(
                    ticker          = ticker,
                    decision        = result.decision.value,
                    momentum_score  = result.momentum_score,
                    skip_score      = result.skip_score,
                    phase           = result.phase.value,
                    sector_id       = sector.sector_id,
                    sector_heat     = sector.heat,
                    catalyst_type   = ticker_input.catalyst_type.value,
                    catalyst_source = cat_source,
                    catalyst_desc   = ticker_input.catalyst_description[:120],
                    entry_price     = ticker_input.price,
                    day_change_pct  = ticker_input.day_change_pct,
                    volume_ratio    = round(vol_ratio, 2),
                    premarket_pct   = ticker_input.premarket_pct,
                    data_confidence = quality.confidence.value,
                    is_partial_data = (quality.confidence.value == "PARTIAL"),
                )
                logger.debug("paper_trade opgeslagen: %s / %s", ticker, trade_id)
            except Exception as pt_exc:
                logger.debug("paper_trade opslaan mislukt voor %s: %s", ticker, pt_exc)

        return {
            "ticker":          ticker,
            "status":          "ok",
            "decision":        result.decision.value,
            "momentum_score":  round(result.momentum_score, 1),
            "skip_score":      result.skip_score,
            "phase":           result.phase.value,
            "phase_desc":      result.phase_description,
            "market_cap_tier": result.market_cap_tier.value,
            "sizing_eur":      result.sizing_eur,
            "summary":         result.summary,
            # Momentum componenten
            "m_volume":        round(result.momentum_detail.volume_anomaly, 1),
            "m_catalyst":      round(result.momentum_detail.catalyst_quality, 1),
            "m_sector_heat":   round(result.momentum_detail.sector_heat_score, 1),
            "m_premarket":     round(result.momentum_detail.premarket_strength, 1),
            "m_rel_strength":  round(result.momentum_detail.relative_strength_score, 1),
            "m_social":        round(result.momentum_detail.social_acceleration, 1),
            "m_float":         round(result.momentum_detail.float_score, 1),
            "social_capped":   result.momentum_detail.social_was_capped,
            # Skip details
            "skip_blocked":    result.skip_detail.is_hard_blocked,
            "skip_reasons":    " | ".join(result.skip_detail.reasons[:3]),
            # Catalyst intelligence (v2.5)
            "catalyst_source": cat_source,
            "catalyst_conf":   cat_conf,
            "top_headline":    raw_headlines[0] if raw_headlines else "",
            "raw_headlines":   " || ".join(raw_headlines),
            # Data kwaliteit
            "data_confidence": quality.confidence.value,
            "price_ok":        quality.price_available,
            "volume_ok":       quality.volume_available,
            "news_ok":         quality.news_available,
            "cache_hit":       quality.cache_hit,
            "fetch_error":     quality.fetch_error or "",
            # Top reasons
            "top_reasons":     " | ".join(top_reasons[:3]),
            # Meta
            "analyzed_at":     started_at.isoformat(),
        }

    except Exception as exc:
        logger.error("  %s MISLUKT: %s: %s", ticker, type(exc).__name__, exc)
        return {
            "ticker":          ticker,
            "status":          "error",
            "decision":        "ERROR",
            "momentum_score":  0.0,
            "skip_score":      0,
            "phase":           "",
            "phase_desc":      "",
            "market_cap_tier": "",
            "sizing_eur":      "€0",
            "summary":         f"ERROR: {type(exc).__name__}: {exc}",
            "m_volume": 0.0, "m_catalyst": 0.0, "m_sector_heat": 0.0,
            "m_premarket": 0.0, "m_rel_strength": 0.0, "m_social": 0.0,
            "m_float": 0.0, "social_capped": False,
            "skip_blocked": False, "skip_reasons": "",
            "catalyst_source": "", "catalyst_conf": "", "top_headline": "",
            "raw_headlines": "",
            "data_confidence": "MISSING",
            "price_ok": False, "volume_ok": False, "news_ok": False,
            "cache_hit": False, "fetch_error": str(exc),
            "top_reasons":     f"FETCH FAILED: {type(exc).__name__}",
            "analyzed_at":     started_at.isoformat(),
        }


def _extract_top_reasons(result, quality) -> list[str]:
    """
    Destilleert de 3 meest relevante redenen voor de beslissing.
    Gebruikt skip_reasons (als geblokkeerd) en momentum breakdown.
    """
    reasons = []

    # Hard blocks gaan altijd voor
    if result.skip_detail.is_hard_blocked:
        reasons += [f"BLOCKED: {r}" for r in result.skip_detail.blocking_reasons[:2]]
        return reasons

    # Skip redenen
    if result.skip_score >= 30:
        reasons += [f"SKIP: {r}" for r in result.skip_detail.reasons[:2]]

    # Dominante momentum component
    md = result.momentum_detail
    components = [
        ("volume",      md.volume_anomaly,           22.0, "volume_anomaly"),
        ("catalyst",    md.catalyst_quality,          20.0, "catalyst_quality"),
        ("sector_heat", md.sector_heat_score,         18.0, "sector_heat"),
        ("premarket",   md.premarket_strength,        14.0, "premarket"),
        ("rel_str",     md.relative_strength_score,   10.0, "relative_strength"),
        ("float",       md.float_score,                8.0, "float_score"),
        ("social",      md.social_acceleration,        8.0, "social"),
    ]
    # Sorteer op % van maximum
    scored = sorted(
        [(name, val, max_val, key) for name, val, max_val, key in components],
        key=lambda x: x[1] / x[2] if x[2] > 0 else 0,
        reverse=True,
    )
    for name, val, max_val, key in scored[:2]:
        pct = int(val / max_val * 100) if max_val > 0 else 0
        reasons.append(f"{name}={val:.1f}/{max_val:.0f} ({pct}%)")

    # Data kwaliteit waarschuwing
    if quality.fetch_error:
        reasons.append(f"data_warn: {quality.fetch_error[:40]}")
    elif not quality.news_available:
        reasons.append("news=NONE (catalyst onzeker)")

    return reasons


# ── Output schrijven ──────────────────────────────────────────────────────────

_CSV_COLUMNS = [
    "ticker", "decision", "momentum_score", "skip_score",
    "phase", "market_cap_tier", "sizing_eur",
    "m_volume", "m_catalyst", "m_sector_heat", "m_premarket",
    "m_rel_strength", "m_social", "m_float",
    "catalyst_source", "catalyst_conf", "top_headline",
    "skip_blocked", "skip_reasons",
    "data_confidence", "cache_hit", "fetch_error",
    "top_reasons", "summary", "cohort",
]

# ── Unicode sanitizer ────────────────────────────────────────────────────────

_UNICODE_MAP = {
    "‑": "-",   # non-breaking hyphen
    "–": "-",   # en dash
    "—": "-",   # em dash
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "…": "...", # ellipsis
    " ": " ",   # non-breaking space
}

def _sanitize(value: object) -> object:
    """Vervangt problematische Unicode-tekens in strings. Andere typen ongewijzigd."""
    if not isinstance(value, str):
        return value
    for char, replacement in _UNICODE_MAP.items():
        value = value.replace(char, replacement)
    return value

def _sanitize_result(result: dict) -> dict:
    """Sanitized kopie van een resultaat-dict."""
    return {k: _sanitize(v) for k, v in result.items()}


def _write_outputs(
    results:    list[dict],
    metadata:   dict,
    timestamp:  str,
) -> tuple[str, str]:
    """Schrijft JSON + CSV naar research/validation/. Retourneert (json_path, csv_path)."""
    os.makedirs(_OUTPUT_DIR, exist_ok=True)

    base      = f"validation_{timestamp}"
    json_path = os.path.join(_OUTPUT_DIR, f"{base}.json")
    csv_path  = os.path.join(_OUTPUT_DIR, f"{base}.csv")

    # JSON — volledig, inclusief metadata, expliciete UTF-8
    payload = {
        "meta":    metadata,
        "results": results,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str, ensure_ascii=False)

    # CSV — gesorteerd, gesanitized, expliciete UTF-8
    sorted_results = sorted(
        results,
        key=lambda r: (r["status"] != "ok", -r["momentum_score"]),
    )
    sanitized = [_sanitize_result(r) for r in sorted_results]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        # utf-8-sig voegt BOM toe zodat Excel het correct opent op Windows
        writer = csv.DictWriter(
            f,
            fieldnames=_CSV_COLUMNS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(sanitized)

    return json_path, csv_path


# ── Rapport printen ───────────────────────────────────────────────────────────

_DECISION_ORDER = ["BLOCKED", "SKIP", "WATCH", "BUY_SMALL", "BUY_MODERATE", "BUY_STRONG", "BUY_MAX", "ERROR"]

def _print_report(results: list[dict], metadata: dict) -> None:
    """Print een leesbaar validatierapport naar stdout."""
    ok      = [r for r in results if r["status"] == "ok"]
    errors  = [r for r in results if r["status"] == "error"]
    total   = len(results)

    print()
    print("=" * 70)
    print(f"  VALIDATION RAPPORT — {metadata['run_timestamp'][:16]}")
    print(f"  {total} tickers  |  {len(ok)} ok  |  {len(errors)} errors")
    print("=" * 70)

    # Beslissings-distributie
    from collections import Counter
    dist = Counter(r["decision"] for r in ok)
    print()
    print("  BESLISSINGS-DISTRIBUTIE:")
    for decision in _DECISION_ORDER:
        count = dist.get(decision, 0)
        if count > 0:
            bar = "█" * count
            print(f"    {decision:<14}  {bar}  ({count})")

    # Top scorers per beslissing
    print()
    print("  RESULTATEN (gesorteerd op momentum score):")
    print()
    print(f"  {'TICKER':<8} {'BESLISSING':<14} {'MOM':>5} {'SKIP':>4} {'PHASE':<12} {'CAP':<7} {'TOP REASON'}")
    print(f"  {'-'*7} {'-'*13} {'-'*5} {'-'*4} {'-'*11} {'-'*6} {'-'*35}")

    sorted_ok = sorted(ok, key=lambda r: -r["momentum_score"])
    for r in sorted_ok:
        decision_str = r["decision"]
        top_reason   = r["top_reasons"].split(" | ")[0] if r["top_reasons"] else ""
        print(
            f"  {r['ticker']:<8} {decision_str:<14} {r['momentum_score']:>5.1f} "
            f"{r['skip_score']:>4} {r['phase']:<12} {r['market_cap_tier']:<7} {top_reason[:40]}"
        )

    # Data kwaliteits-waarschuwingen
    partial = [r for r in ok if r["data_confidence"] in ("PARTIAL", "MISSING", "STALE")]
    if partial:
        print()
        print(f"  ⚠️  DATA KWALITEIT ({len(partial)} tickers met onvolledig/oud data):")
        for r in partial:
            print(f"    {r['ticker']:<8}  {r['data_confidence']:<8}  {r['fetch_error'][:50]}")

    # Errors
    if errors:
        print()
        print(f"  ❌ ERRORS ({len(errors)}):")
        for r in errors:
            print(f"    {r['ticker']:<8}  {r['summary'][:60]}")

    # Engine calibratie observaties
    print()
    print("  ENGINE OBSERVATIES:")

    catalyst_none = [r for r in ok if r["m_catalyst"] == 0.0]
    if catalyst_none:
        print(f"    • {len(catalyst_none)} tickers met catalyst=NONE "
              f"(geen Finnhub key? → scores conservatief)")
        tickers_str = ", ".join(r["ticker"] for r in catalyst_none[:6])
        print(f"      Betroffen: {tickers_str}{'...' if len(catalyst_none) > 6 else ''}")

    social_capped = [r for r in ok if r["social_capped"]]
    if social_capped:
        print(f"    • {len(social_capped)} tickers met gecapte social score")

    blocked = [r for r in ok if r["skip_blocked"]]
    if blocked:
        print(f"    • {len(blocked)} HARD BLOCKED: "
              + ", ".join(r["ticker"] for r in blocked))

    # Finnhub success rate
    print()
    print(format_session_stats(total_tickers=len(results)))

    print()
    print(f"  Gem. momentum score:  {sum(r['momentum_score'] for r in ok)/len(ok):.1f}" if ok else "")
    print(f"  Hoogste score:        {max((r['momentum_score'] for r in ok), default=0):.1f}")
    print(f"  Laagste score:        {min((r['momentum_score'] for r in ok), default=0):.1f}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch validation runner voor momentum engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--group",         help="Analyseer één groep (bv. quantum)")
    parser.add_argument("--cohort",        help="Analyseer één cohort: A of B")
    parser.add_argument("--ticker",        nargs="+", help="Specifieke tickers (overschrijft watchlist)")
    parser.add_argument("--delay",         type=float, default=_DEFAULT_DELAY,
                        help=f"Seconden tussen requests (default: {_DEFAULT_DELAY})")
    parser.add_argument("--no-persist",    action="store_true", help="Geen storage snapshots")
    parser.add_argument("--force-refresh", action="store_true", help="Negeer cache")
    args = parser.parse_args()

    persist       = not args.no_persist
    force_refresh = args.force_refresh
    delay         = max(args.delay, 0.0)

    # Bepaal welke tickers
    if args.ticker:
        entries = [
            {"ticker": t.upper(), "group_id": "manual", "group_label": "Manual",
             "sector_heat": 50, "cap": "UNKNOWN", "note": "CLI override"}
            for t in args.ticker
        ]
    else:
        entries = _load_watchlist(group_filter=args.group)
        if args.cohort:
            entries = [e for e in entries if e.get("cohort", "A") == args.cohort.upper()]

    if not entries:
        logger.error("Geen tickers gevonden. Check --group naam of validation_watchlist.json.")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    total     = len(entries)

    logger.info("Validation runner v1.0 — %d tickers — delay %.1fs", total, delay)
    if args.group:
        logger.info("Filter: group=%s", args.group)
    if force_refresh:
        logger.info("Force refresh: cache genegeerd")
    if not persist:
        logger.info("No-persist: geen storage snapshots")

    print()

    # Reset Finnhub statistieken voor deze run
    reset_session_stats()

    # Analyse loop
    results     = []
    group_order = []

    for i, entry in enumerate(entries, 1):
        ticker = entry["ticker"]

        # Groep-header bij overgang
        gid = entry["group_id"]
        if not group_order or group_order[-1] != gid:
            group_order.append(gid)
            print(f"\n  ── {entry['group_label']} (heat: {entry['sector_heat']}) ──")

        logger.info("[%d/%d] %s  (%s, %s)", i, total, ticker, entry["cap"], entry["note"])

        result = _analyze_one(ticker, force_refresh=force_refresh, persist=persist)

        # Verrijk met watchlist-metadata
        result["group_id"]    = entry["group_id"]
        result["group_label"] = entry["group_label"]
        result["cap_expected"] = entry["cap"]
        result["note"]        = entry["note"]
        result["cohort"]      = entry.get("cohort", "A")
        result["expansion"]   = entry.get("expansion", "")

        results.append(result)

        # Progress inline
        status_icon = {
            "BUY_MAX":      "🔥",
            "BUY_STRONG":   "✅",
            "BUY_MODERATE": "📈",
            "BUY_SMALL":    "👍",
            "WATCH":        "👁 ",
            "SKIP":         "⏭ ",
            "BLOCKED":      "🚫",
            "ERROR":        "❌",
        }.get(result["decision"], "❓")

        print(
            f"    {status_icon} {ticker:<7}  {result['decision']:<14}  "
            f"mom={result['momentum_score']:>5.1f}  "
            f"skip={result['skip_score']:>3}  "
            f"{result['phase']}"
        )

        # Rate limit bescherming — niet na de laatste
        if i < total and delay > 0:
            time.sleep(delay)

    # Metadata voor output
    ok_results = [r for r in results if r["status"] == "ok"]
    metadata = {
        "run_timestamp":   timestamp,
        "total_tickers":   total,
        "ok_count":        len(ok_results),
        "error_count":     len(results) - len(ok_results),
        "delay_seconds":   delay,
        "force_refresh":   force_refresh,
        "persist":         persist,
        "group_filter":    args.group,
        "ticker_filter":   args.ticker,
        "decision_counts": {
            d: sum(1 for r in ok_results if r["decision"] == d)
            for d in _DECISION_ORDER
            if any(r["decision"] == d for r in ok_results)
        },
        "avg_momentum":    round(
            sum(r["momentum_score"] for r in ok_results) / len(ok_results), 1
        ) if ok_results else 0.0,
    }

    # Rapport printen
    _print_report(results, metadata)

    # Output schrijven
    json_path, csv_path = _write_outputs(results, metadata, timestamp)
    print(f"  📄 JSON: {json_path}")
    print(f"  📊 CSV:  {csv_path}")
    print()


if __name__ == "__main__":
    main()
