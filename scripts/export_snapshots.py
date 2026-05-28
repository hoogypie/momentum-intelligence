#!/usr/bin/env python3
"""
scripts/export_snapshots.py
CLI Export Tool — v2.6

Exporteert historische snapshots, replay summaries en sector history
naar JSON of Markdown bestanden.

Gebruik:
    python3 scripts/export_snapshots.py ticker NVDA
    python3 scripts/export_snapshots.py ticker NVDA --review
    python3 scripts/export_snapshots.py sector quantum
    python3 scripts/export_snapshots.py session 2026-05-28
    python3 scripts/export_snapshots.py all-tickers
    python3 scripts/export_snapshots.py list

Output gaat naar research/signal_reviews/ (ticker)
                   research/replay_notes/  (session/sector)
"""

import sys
import json
import argparse
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def cmd_ticker(args) -> int:
    from storage.replay_engine       import replay_ticker
    from research.observation_store  import save_replay_note, save_signal_review

    ticker  = args.ticker.upper()
    print(f"Exporteren: {ticker} (limit={args.limit}) …")

    data = replay_ticker(ticker, limit=args.limit)

    if data["snapshot_count"] == 0:
        print(f"  Geen snapshots gevonden voor {ticker}.")
        print(f"  Gebruik: curl http://localhost:8000/analyze/{ticker}")
        return 1

    if args.review:
        path = save_signal_review(ticker, data)
        print(f"  Signal review opgeslagen: {path}")
    else:
        path = save_replay_note(ticker, data)
        print(f"  Replay note opgeslagen: {path}")

    print(f"  Snapshots: {data['snapshot_count']}")
    print(f"  Trend: {data['momentum_trend']}")
    if data.get("summary", {}).get("current"):
        c = data["summary"]["current"]
        print(f"  Huidig: {c.get('decision')} (score {c.get('momentum_score'):.1f})")
    return 0


def cmd_sector(args) -> int:
    from storage.replay_engine      import replay_sector
    from research.observation_store import save_replay_note

    sector  = args.sector.lower()
    print(f"Exporteren sector: {sector} …")

    data = replay_sector(sector, limit=args.limit)

    if data["snapshot_count"] == 0:
        print(f"  Geen sector snapshots voor '{sector}'.")
        print(f"  Gebruik: curl http://localhost:8000/sector/{sector}")
        return 1

    path = save_replay_note(f"SECTOR_{sector.upper()}", data)
    print(f"  Sector replay opgeslagen: {path}")
    print(f"  Snapshots: {data['snapshot_count']}")
    if data.get("heat_trend"):
        print(f"  Huidige heat: {data['heat_trend'][0]}")
    return 0


def cmd_session(args) -> int:
    from storage.replay_engine      import replay_session
    from research.observation_store import save_replay_note

    date_str = args.date
    print(f"Exporteren sessie: {date_str} …")

    data = replay_session(date_str)

    if "error" in data:
        print(f"  Fout: {data['message']}")
        return 1

    if data["total_snapshots"] == 0:
        print(f"  Geen activiteit gevonden op {date_str}.")
        return 1

    path = save_replay_note(f"SESSION_{date_str}", data)
    print(f"  Sessie replay opgeslagen: {path}")
    print(f"  Tickers actief: {data['tickers_active']}")
    print(f"  Totaal snapshots: {data['total_snapshots']}")
    if data.get("best_ticker"):
        print(f"  Beste ticker: {data['best_ticker']} (score {data.get('best_score', '?'):.1f})")
    return 0


def cmd_all_tickers(args) -> int:
    from storage.timeline import get_all_tracked_summaries

    print("Overzicht van alle getrackte tickers …")
    summaries = get_all_tracked_summaries()

    if not summaries:
        print("  Geen tickers getrackt. Start met: curl http://localhost:8000/analyze/NVDA")
        return 1

    for s in summaries:
        if not s.get("tracked"):
            continue
        decision = s.get("current_decision", "?")
        score    = s.get("current_score") or 0
        trend    = s.get("score_range", {}).get("avg", 0)
        print(
            f"  {s['ticker']:<10} {decision:<15} "
            f"score={score:.0f}  avg={trend:.0f}  "
            f"n={s['snapshot_count']}"
        )
    return 0


def cmd_list(args) -> int:
    from research.observation_store import (
        list_replay_notes, list_signal_reviews, list_observations
    )

    notes    = list_replay_notes()
    reviews  = list_signal_reviews()
    obs      = list_observations()

    print(f"\nReplay notes ({len(notes)}):")
    for n in notes[:10]:
        print(f"  {n}")

    print(f"\nSignal reviews ({len(reviews)}):")
    for r in reviews[:10]:
        print(f"  {r}")

    print(f"\nHandmatige observaties ({len(obs)}):")
    for o in obs:
        print(f"  {o}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Momentum Intelligence — Export Tool"
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ticker
    pt = sub.add_parser("ticker", help="Exporteer ticker snapshots")
    pt.add_argument("ticker")
    pt.add_argument("--limit",  type=int, default=100)
    pt.add_argument("--review", action="store_true",
                    help="Exporteer als signal review (JSON + Markdown)")

    # sector
    ps = sub.add_parser("sector", help="Exporteer sector history")
    ps.add_argument("sector")
    ps.add_argument("--limit", type=int, default=50)

    # session
    pses = sub.add_parser("session", help="Exporteer sessie (YYYY-MM-DD)")
    pses.add_argument("date")

    # all-tickers
    sub.add_parser("all-tickers", help="Overzicht alle getrackte tickers")

    # list
    sub.add_parser("list", help="Overzicht van alle exports")

    args = p.parse_args()

    dispatch = {
        "ticker":      cmd_ticker,
        "sector":      cmd_sector,
        "session":     cmd_session,
        "all-tickers": cmd_all_tickers,
        "list":        cmd_list,
    }

    fn = dispatch.get(args.command)
    if fn:
        return fn(args)
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
