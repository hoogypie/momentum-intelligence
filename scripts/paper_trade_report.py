"""
scripts/paper_trade_report.py
Paper Trade Report — v1.0

Twee modi:
    record   — Sla BUY-signalen op vanuit de validation runner output
    evaluate — Haal toekomstige prijzen op en update open trades
    report   — Toon win rate, gemiddeld rendement, uitsplitsing per beslissing

Gebruik:
    python scripts/paper_trade_report.py record   --ticker NVDA IONQ RCAT
    python scripts/paper_trade_report.py record   --group drones_defense
    python scripts/paper_trade_report.py evaluate
    python scripts/paper_trade_report.py report
    python scripts/paper_trade_report.py report   --min-trades 3
    python scripts/paper_trade_report.py report   --decision BUY_MODERATE

Workflow:
    1. Run validation runner om signalen te detecteren
    2. Run `record` om BUY-signalen op te slaan
    3. Wacht 1/3/5/10 handelsdagen
    4. Run `evaluate` om marktprijzen op te halen
    5. Run `report` om resultaten te zien
"""

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_REPO_ROOT, ".env"))
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("paper_trade")

from storage.paper_trade_store import (
    save_trade_from_result, load_trades, load_complete_trades,
    load_open_trades, list_tracked_tickers, BUY_DECISIONS,
    STATUS_OPEN, STATUS_PARTIAL, STATUS_COMPLETE,
)


# ── RECORD MODE ───────────────────────────────────────────────────────────────

def _record(args) -> None:
    """
    Haalt live scoring op voor tickers en slaat BUY-signalen op.
    """
    from data.assembler import build_ticker_input
    from scoring.scoring_v1_2 import score_ticker
    from data.assembler import _find_sector

    # Bepaal tickers
    if args.ticker:
        tickers = [t.upper() for t in args.ticker]
    elif args.group:
        watchlist_path = os.path.join(_REPO_ROOT, "research", "validation_watchlist.json")
        with open(watchlist_path) as f:
            wl = json.load(f)
        tickers = []
        for g in wl.get("groups", []):
            if g["id"] == args.group:
                tickers = [t["ticker"].upper() for t in g["tickers"]]
                break
        if not tickers:
            logger.error("Groep '%s' niet gevonden in validation_watchlist.json", args.group)
            sys.exit(1)
    else:
        logger.error("Geef --ticker of --group op")
        sys.exit(1)

    logger.info("Record mode — %d tickers", len(tickers))
    recorded = 0

    for ticker in tickers:
        try:
            ticker_input, quality = build_ticker_input(ticker, force_refresh=True)
            result  = score_ticker(ticker_input)
            decision = result.decision.value

            if decision not in BUY_DECISIONS:
                logger.info("  %s — %s (geen BUY, overgeslagen)", ticker, decision)
                continue

            sector  = _find_sector(ticker)
            # Catalyst info via classifier
            cat_source = "UNKNOWN"
            try:
                from data.finnhub_client import fetch_company_news, is_available
                from data.catalyst_classifier import classify
                if is_available():
                    items      = fetch_company_news(ticker, hours=48)
                    cat_result = classify(ticker, items, sector.leaders, sector.sympathy)
                    cat_source = cat_result.catalyst_source.value
            except Exception:
                pass

            vol_ratio = (
                ticker_input.volume_today / ticker_input.avg_volume_20d
                if ticker_input.avg_volume_20d > 0 else 0.0
            )

            trade_id = save_trade_from_result(
                ticker          = ticker,
                decision        = decision,
                momentum_score  = result.momentum_score,
                skip_score      = result.skip_score,
                phase           = result.phase.value,
                sector_id       = ticker_input.sector.sector_id,
                sector_heat     = ticker_input.sector.heat,
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

            logger.info(
                "  ✅ %s — %s | score=%.1f | entry=%.2f | id=%s",
                ticker, decision, result.momentum_score,
                ticker_input.price, trade_id,
            )
            recorded += 1

        except Exception as exc:
            logger.error("  ❌ %s mislukt: %s: %s", ticker, type(exc).__name__, exc)

    print(f"\n  {recorded} BUY-signalen opgeslagen.")
    print(f"  Run later: python scripts/paper_trade_report.py evaluate")


# ── EVALUATE MODE ─────────────────────────────────────────────────────────────

def _evaluate(args) -> None:
    """
    Haalt toekomstige prijzen op voor open trades.
    """
    from storage.paper_trade_evaluator import evaluate_all_open

    tickers = [t.upper() for t in args.ticker] if args.ticker else None

    logger.info("Evaluate mode — open trades ophalen...")
    summary = evaluate_all_open(tickers=tickers)

    print(f"\n  Geëvalueerd: {summary['evaluated']}")
    print(f"  Bijgewerkt:  {summary['updated']}")
    print(f"  Overgeslagen: {summary['skipped']} (te vroeg of al compleet)")
    if summary["errors"]:
        print(f"  Fouten:      {summary['errors']}")
    print(f"\n  Run: python scripts/paper_trade_report.py report")


# ── REPORT MODE ───────────────────────────────────────────────────────────────

def _median(values: list[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    if n % 2 == 0:
        return round((s[n//2 - 1] + s[n//2]) / 2, 3)
    return round(s[n//2], 3)


def _win_rate(returns: list[float], threshold: float = 0.0) -> float:
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > threshold)
    return round(wins / len(returns) * 100, 1)


def _report(args) -> None:
    """
    Toont win rate, gemiddeld rendement en uitsplitsing per beslissing.
    """
    min_trades  = args.min_trades
    dec_filter  = args.decision.upper() if args.decision else None

    # Laad complete trades
    all_complete = load_complete_trades()
    all_trades   = load_trades()

    if dec_filter:
        all_complete = [t for t in all_complete if t.get("decision") == dec_filter]
        all_trades   = [t for t in all_trades   if t.get("decision") == dec_filter]

    open_count     = sum(1 for t in all_trades if t.get("status") == STATUS_OPEN)
    partial_count  = sum(1 for t in all_trades if t.get("status") == STATUS_PARTIAL)
    complete_count = len(all_complete)
    total          = len(all_trades)

    print()
    print("=" * 70)
    print("  PAPER TRADE RAPPORT")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)
    print(f"\n  Totaal signalen:  {total}")
    print(f"  Compleet (4/4):   {complete_count}")
    print(f"  Partieel (1-3/4): {partial_count}")
    print(f"  Open (0/4):       {open_count}")

    if complete_count < min_trades:
        print(f"\n  ⚠️  Onvoldoende complete trades voor statistieken")
        print(f"  (minimum: {min_trades}, huidig: {complete_count})")
        print(f"\n  Wacht {10 - complete_count} meer complete trades,")
        print(f"  of run: python scripts/paper_trade_report.py evaluate\n")
        _print_open_trades(all_trades)
        return

    # ── OVERALL STATISTIEKEN ──────────────────────────────────────────────────
    horizons = [
        ("1d",  "return_1d",  "1 dag"),
        ("3d",  "return_3d",  "3 dagen"),
        ("5d",  "return_5d",  "5 dagen"),
        ("10d", "return_10d", "10 dagen"),
    ]

    print(f"\n  OVERALL RESULTATEN ({complete_count} complete trades)")
    print(f"  {'HORIZON':<12} {'WIN%':>6} {'GEM%':>7} {'MED%':>7} {'BEST%':>7} {'WORST%':>8} {'N':>4}")
    print(f"  {'-'*12} {'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*4}")

    for key, ret_field, label in horizons:
        returns = [
            t[ret_field] for t in all_complete
            if t.get(ret_field) is not None
        ]
        if not returns:
            continue
        avg   = round(sum(returns) / len(returns), 2)
        med   = _median(returns)
        best  = round(max(returns), 2)
        worst = round(min(returns), 2)
        wr    = _win_rate(returns)
        print(
            f"  {label:<12} {wr:>5.1f}% {avg:>+6.2f}% {med:>+6.2f}% "
            f"{best:>+6.2f}% {worst:>+7.2f}% {len(returns):>4}"
        )

    # ── PER BESLISSING ────────────────────────────────────────────────────────
    decisions = sorted(
        set(t.get("decision", "") for t in all_complete),
        key=lambda d: ["BUY_MAX","BUY_STRONG","BUY_MODERATE","BUY_SMALL"].index(d)
                      if d in ["BUY_MAX","BUY_STRONG","BUY_MODERATE","BUY_SMALL"] else 99,
    )

    if len(decisions) > 1:
        print(f"\n  RESULTATEN PER BESLISSING (primaire horizon: 5d)")
        print(f"  {'BESLISSING':<14} {'N':>4} {'WIN%':>6} {'GEM5d%':>8} {'MED5d%':>8}")
        print(f"  {'-'*14} {'-'*4} {'-'*6} {'-'*8} {'-'*8}")

        for dec in decisions:
            dec_trades = [t for t in all_complete if t.get("decision") == dec]
            returns_5d = [t["return_5d"] for t in dec_trades if t.get("return_5d") is not None]
            if not returns_5d:
                continue
            avg = round(sum(returns_5d) / len(returns_5d), 2)
            med = _median(returns_5d)
            wr  = _win_rate(returns_5d)
            print(f"  {dec:<14} {len(dec_trades):>4} {wr:>5.1f}% {avg:>+7.2f}% {med:>+7.2f}%")

    # ── PER CATALYST TYPE ─────────────────────────────────────────────────────
    cat_groups = defaultdict(list)
    for t in all_complete:
        cat = t.get("catalyst_type", "NONE")
        ret = t.get("return_5d")
        if ret is not None:
            cat_groups[cat].append(ret)

    if len(cat_groups) > 1:
        print(f"\n  RESULTATEN PER CATALYST TYPE (5d)")
        print(f"  {'CATALYST':<12} {'N':>4} {'WIN%':>6} {'GEM%':>7}")
        print(f"  {'-'*12} {'-'*4} {'-'*6} {'-'*7}")
        for cat in ["STRONG", "MODERATE", "WEAK", "NONE"]:
            rets = cat_groups.get(cat, [])
            if not rets:
                continue
            avg = round(sum(rets) / len(rets), 2)
            wr  = _win_rate(rets)
            print(f"  {cat:<12} {len(rets):>4} {wr:>5.1f}% {avg:>+6.2f}%")

    # ── MODERATE WARNING ─────────────────────────────────────────────────────
    moderate_trades = [t for t in all_complete if t.get("catalyst_type") == "MODERATE"]
    if moderate_trades:
        mod_rets_5d = [t["return_5d"] for t in moderate_trades if t.get("return_5d") is not None]
        mod_wr = _win_rate(mod_rets_5d) if mod_rets_5d else 0.0
        if mod_wr == 0.0 or (len(mod_rets_5d) >= 2 and mod_wr < 40.0):
            print(f"\n  ⚠️  MODERATE CATALYST WAARSCHUWING")
            print(f"  Win rate MODERATE (5d): {mod_wr:.0f}% over {len(mod_rets_5d)} trades")
            print(f"  → Behandel MODERATE signalen als WATCHLIST ONLY totdat N≥10 en win rate >50%")

    # ── EXHAUSTION × CATALYST CROSSTAB ───────────────────────────────────────
    exh_cats = defaultdict(list)
    non_exh_cats = defaultdict(list)
    for t in all_complete:
        phase = t.get("phase", "")
        cat   = t.get("catalyst_type", "NONE")
        ret   = t.get("return_5d")
        if ret is None:
            continue
        if phase == "EXHAUSTION":
            exh_cats[cat].append(ret)
        else:
            non_exh_cats[cat].append(ret)

    all_exh_cats = set(exh_cats.keys()) | set(non_exh_cats.keys())
    if exh_cats and len(all_exh_cats) > 0:
        print(f"\n  EXHAUSTION × CATALYST CROSSTAB (5d)")
        print(f"  {'CATALYST':<10} {'FASE':<14} {'N':>4} {'WIN%':>6} {'GEM%':>7} {'MED%':>7}")
        print(f"  {'-'*10} {'-'*14} {'-'*4} {'-'*6} {'-'*7} {'-'*7}")
        for cat in ["STRONG", "MODERATE", "WEAK", "NONE"]:
            for phase_label, groups in [("EXHAUSTION", exh_cats), ("OTHER", non_exh_cats)]:
                rets = groups.get(cat, [])
                if not rets:
                    continue
                avg = round(sum(rets) / len(rets), 2)
                med = _median(rets)
                wr  = _win_rate(rets)
                flag = " ⚠️" if phase_label == "EXHAUSTION" and cat == "MODERATE" else ""
                print(
                    f"  {cat:<10} {phase_label:<14} {len(rets):>4} "
                    f"{wr:>5.1f}% {avg:>+6.2f}% {med:>+6.2f}%{flag}"
                )

    # ── TICKER-LEVEL DEDUPE VIEW ──────────────────────────────────────────────
    # Per ticker: alleen het EERSTE signaal (op signal_ts). Aparte stats.
    ticker_first: dict[str, dict] = {}
    for t in all_complete:
        ticker = t["ticker"]
        ts     = t.get("signal_ts", "")
        if ticker not in ticker_first or ts < ticker_first[ticker].get("signal_ts", ""):
            ticker_first[ticker] = t

    deduped = list(ticker_first.values())
    stacked_tickers = [
        tkr for tkr in set(t["ticker"] for t in all_complete)
        if sum(1 for t in all_complete if t["ticker"] == tkr) > 1
    ]

    if stacked_tickers:
        print(f"\n  TICKER-LEVEL VIEW — DEDUPE (alleen eerste signaal per ticker)")
        print(f"  Gestacked (meerdere signalen): {', '.join(sorted(stacked_tickers))}")

        deduped_rets_5d = [t["return_5d"] for t in deduped if t.get("return_5d") is not None]
        if deduped_rets_5d:
            avg_d = round(sum(deduped_rets_5d) / len(deduped_rets_5d), 2)
            med_d = _median(deduped_rets_5d)
            wr_d  = _win_rate(deduped_rets_5d)

            all_rets_5d = [t["return_5d"] for t in all_complete if t.get("return_5d") is not None]
            avg_a = round(sum(all_rets_5d) / len(all_rets_5d), 2)
            wr_a  = _win_rate(all_rets_5d)

            print(f"  {'VIEW':<20} {'N':>4} {'WIN%':>6} {'GEM5d%':>8} {'MED5d%':>8}")
            print(f"  {'-'*20} {'-'*4} {'-'*6} {'-'*8} {'-'*8}")
            print(
                f"  {'Trade-level (alle)':<20} {len(all_rets_5d):>4} "
                f"{wr_a:>5.1f}% {avg_a:>+7.2f}% —"
            )
            print(
                f"  {'Ticker-level (1e sig)':<20} {len(deduped_rets_5d):>4} "
                f"{wr_d:>5.1f}% {avg_d:>+7.2f}% {med_d:>+7.2f}%"
            )

        print(f"\n  Dedupe trades (eerste signaal per ticker, gesorteerd op 5d):")
        print(
            f"  {'TICKER':<7} {'ENTRY':>9} {'SIGNAAL':<12} "
            f"{'5d%':>6} {'10d%':>7} {'CAT':<10} {'FASE'}"
        )
        print(f"  {'-'*7} {'-'*9} {'-'*12} {'-'*6} {'-'*7} {'-'*9} {'-'*10}")
        for t in sorted(deduped, key=lambda x: x.get("return_5d") or -999, reverse=True):
            def _fmt2(k):
                v = t.get(k)
                return f"{v:+.1f}" if v is not None else "  —"
            sig_date = t.get("signal_ts", "")[:10]
            print(
                f"  {t['ticker']:<7} {t['entry_price']:>9.2f} {sig_date:<12} "
                f"{_fmt2('return_5d'):>6} {_fmt2('return_10d'):>7} "
                f"{t.get('catalyst_type','?'):<10} {t.get('phase','?')}"
            )

    # ── PER CATALYST SOURCE ───────────────────────────────────────────────────
    src_groups = defaultdict(list)
    for t in all_complete:
        src = t.get("catalyst_source", "UNKNOWN")
        ret = t.get("return_5d")
        if ret is not None:
            src_groups[src].append(ret)

    if len(src_groups) > 1:
        print(f"\n  RESULTATEN PER CATALYST SOURCE (5d)")
        print(f"  {'SOURCE':<12} {'N':>4} {'WIN%':>6} {'GEM%':>7}")
        print(f"  {'-'*12} {'-'*4} {'-'*6} {'-'*7}")
        for src in ["OWN", "SECTOR", "SYMPATHY", "NONE", "UNKNOWN"]:
            rets = src_groups.get(src, [])
            if not rets:
                continue
            avg = round(sum(rets) / len(rets), 2)
            wr  = _win_rate(rets)
            print(f"  {src:<12} {len(rets):>4} {wr:>5.1f}% {avg:>+6.2f}%")

    # ── INDIVIDUELE TRADES ────────────────────────────────────────────────────
    print(f"\n  INDIVIDUELE TRADES (compleet, gesorteerd op 5d return)")
    print(
        f"  {'TICKER':<7} {'BESLISSING':<14} {'ENTRY':>7} "
        f"{'1d%':>6} {'3d%':>6} {'5d%':>6} {'10d%':>7} {'CAT':<10} {'FASE'}"
    )
    print(f"  {'-'*7} {'-'*13} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*9} {'-'*10}")

    sorted_trades = sorted(
        all_complete,
        key=lambda t: t.get("return_5d") or -999,
        reverse=True,
    )
    for t in sorted_trades:
        def _fmt(k):
            v = t.get(k)
            return f"{v:+.1f}" if v is not None else "  —"

        print(
            f"  {t['ticker']:<7} {t['decision']:<14} {t['entry_price']:>7.2f} "
            f"{_fmt('return_1d'):>6} {_fmt('return_3d'):>6} "
            f"{_fmt('return_5d'):>6} {_fmt('return_10d'):>7} "
            f"{t.get('catalyst_type','?'):<10} {t.get('phase','?')}"
        )

    # ── OPEN TRADES ───────────────────────────────────────────────────────────
    _print_open_trades(all_trades)

    print()


def _print_open_trades(all_trades: list[dict]) -> None:
    open_trades = [t for t in all_trades if t.get("status") in (STATUS_OPEN, STATUS_PARTIAL)]
    if not open_trades:
        return

    print(f"\n  OPEN / PARTIEEL TRADES ({len(open_trades)})")
    print(f"  {'TICKER':<7} {'BESLISSING':<14} {'ENTRY':>7} {'SIGNAAL':<22} {'STATUS':<10} {'1d%':>6} {'3d%':>6} {'5d%':>6}")
    print(f"  {'-'*7} {'-'*13} {'-'*7} {'-'*21} {'-'*9} {'-'*6} {'-'*6} {'-'*6}")

    for t in sorted(open_trades, key=lambda x: x.get("signal_ts", ""), reverse=True):
        def _fmt(k):
            v = t.get(k)
            return f"{v:+.1f}" if v is not None else "  —"
        sig_ts = t.get("signal_ts", "")[:16].replace("T", " ")
        print(
            f"  {t['ticker']:<7} {t['decision']:<14} {t['entry_price']:>7.2f} "
            f"{sig_ts:<22} {t.get('status','?'):<10} "
            f"{_fmt('return_1d'):>6} {_fmt('return_3d'):>6} {_fmt('return_5d'):>6}"
        )


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Paper trading validatie — record, evaluate, report",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    # Record
    rec = sub.add_parser("record", help="Sla BUY-signalen op voor tickers")
    rec.add_argument("--ticker", nargs="+", help="Specifieke tickers")
    rec.add_argument("--group",  help="Groep uit validation_watchlist.json")

    # Evaluate
    ev = sub.add_parser("evaluate", help="Haal toekomstige prijzen op")
    ev.add_argument("--ticker", nargs="+", help="Alleen deze tickers evalueren")

    # Report
    rep = sub.add_parser("report", help="Toon statistieken")
    rep.add_argument("--min-trades", type=int, default=5,
                     help="Minimum complete trades voor statistieken (default: 5)")
    rep.add_argument("--decision", help="Filter op beslissing (bijv. BUY_MODERATE)")

    args = parser.parse_args()

    if args.mode == "record":
        _record(args)
    elif args.mode == "evaluate":
        _evaluate(args)
    elif args.mode == "report":
        _report(args)


if __name__ == "__main__":
    main()