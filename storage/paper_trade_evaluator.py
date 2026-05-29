"""
storage/paper_trade_evaluator.py
Paper Trade Evaluator — v1.0

Haalt toekomstige marktprijzen op voor open paper trades en vult de
outcome-velden in. Gebruikt Yahoo Finance via yfinance.history().

Horizons:
    1d  → slotkoers 1 handeldag na signaal
    3d  → slotkoers 3 handelsdagen na signaal
    5d  → slotkoers 5 handelsdagen na signaal
    10d → slotkoers 10 handelsdagen na signaal

"Handelsdagen" = kalender-offset via yfinance history.
Tolerantievenster: ± 1 kalenderdag per horizon.

Design keuzes:
    - Werkt op opgeslagen trades — geen live scoring nodig
    - yfinance history(period) als bron — zelfde als Yahoo client
    - Graceful fallback: ontbrekende prijzen blijven None (PARTIAL status)
    - Nooit een exception — elke fout wordt gelogd en overgeslagen
    - Idempotent: reeds gevulde horizons worden NIET overschreven
"""

import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Handelsdagen horizons als kalender-dagen (benadering)
# Gebruik 1.4× factor voor weekenden (5 handelsdagen ≈ 7 kalenderdagen)
_HORIZON_CALENDAR_DAYS = {
    "1d":  2,    # +2 kalenderdagen (buffer voor weekenden)
    "3d":  5,
    "5d":  8,
    "10d": 15,
}

# Tolerantievenster: hoeveel extra kalenderdagen we accepteren
_TOLERANCE_DAYS = 2


def _fetch_close_price(
    ticker:     str,
    target_dt:  datetime,
    tolerance:  int = _TOLERANCE_DAYS,
) -> Optional[float]:
    """
    Haalt de slotkoers op die het dichtst bij target_dt ligt.
    Zoekt binnen [target_dt - tolerance, target_dt + tolerance].

    Returns:
        Slotkoers als float, of None als niet gevonden.
    """
    try:
        import yfinance as yf
        t     = yf.Ticker(ticker)
        start = (target_dt - timedelta(days=tolerance)).strftime("%Y-%m-%d")
        end   = (target_dt + timedelta(days=tolerance + 1)).strftime("%Y-%m-%d")

        hist = t.history(start=start, end=end, auto_adjust=True)
        if hist is None or hist.empty:
            return None

        # Zoek de rij die het dichtst bij target_dt ligt
        target_ts = target_dt.timestamp()
        best_price = None
        best_diff  = float("inf")

        for idx in hist.index:
            try:
                row_ts = idx.timestamp()
                diff   = abs(row_ts - target_ts)
                if diff < best_diff:
                    best_diff  = diff
                    best_price = float(hist.loc[idx, "Close"])
            except Exception:
                continue

        return round(best_price, 4) if best_price else None

    except Exception as exc:
        logger.debug(
            "paper_trade_evaluator: prijs-fetch mislukt voor %s @ %s: %s",
            ticker, target_dt.strftime("%Y-%m-%d"), exc,
        )
        return None


def evaluate_trade(trade: dict) -> dict:
    """
    Haalt ontbrekende outcome-prijzen op voor één trade.

    Args:
        trade: dict van een PaperTrade (uit paper_trade_store)

    Returns:
        Bijgewerkt trade-dict. Origineel niet gewijzigd.
    Gooit nooit een exception.
    """
    trade = dict(trade)  # kopie

    try:
        ticker    = trade["ticker"]
        signal_ts = datetime.fromisoformat(
            trade["signal_ts"].replace("Z", "+00:00")
        )
        entry     = trade.get("entry_price", 0.0)

        if not entry or entry <= 0:
            logger.debug("paper_trade_evaluator: geen entry price voor %s", ticker)
            return trade

        now = datetime.now(timezone.utc)

        for horizon, cal_days in _HORIZON_CALENDAR_DAYS.items():
            price_key  = f"price_{horizon}"
            return_key = f"return_{horizon}"

            # Sla over als al ingevuld
            if trade.get(price_key) is not None:
                continue

            target_dt = signal_ts + timedelta(days=cal_days)

            # Nog niet genoeg tijd verstreken — sla over
            if now < target_dt - timedelta(days=_TOLERANCE_DAYS):
                logger.debug(
                    "paper_trade_evaluator: %s %s nog niet bereikbaar (target=%s)",
                    ticker, horizon, target_dt.strftime("%Y-%m-%d"),
                )
                continue

            price = _fetch_close_price(ticker, target_dt)
            if price is not None:
                trade[price_key]  = price
                trade[return_key] = round((price - entry) / entry * 100, 3)
                logger.debug(
                    "paper_trade_evaluator: %s %s = %.2f (%.2f%%)",
                    ticker, horizon, price, trade[return_key],
                )

        # Status herberekenen
        filled = sum(
            1 for k in ("return_1d", "return_3d", "return_5d", "return_10d")
            if trade.get(k) is not None
        )
        from storage.paper_trade_store import STATUS_OPEN, STATUS_PARTIAL, STATUS_COMPLETE
        if filled == 4:
            trade["status"] = STATUS_COMPLETE
        elif filled > 0:
            trade["status"] = STATUS_PARTIAL

        if filled > 0:
            trade["evaluated_at"] = datetime.now(timezone.utc).isoformat()

    except Exception as exc:
        logger.warning(
            "paper_trade_evaluator: evaluate_trade mislukt voor %s: %s: %s",
            trade.get("ticker", "?"), type(exc).__name__, exc,
        )

    return trade


def evaluate_all_open(tickers: Optional[list[str]] = None) -> dict:
    """
    Evalueert alle open trades. Slaat updates op via paper_trade_store.

    Args:
        tickers: Optionele filter. Zonder: alle tracked tickers.

    Returns:
        Samenvatting: {evaluated, updated, skipped, errors}
    Gooit nooit een exception.
    """
    from storage.paper_trade_store import (
        load_open_trades, list_tracked_tickers, update_trade_outcomes,
    )

    if tickers is None:
        tickers = list_tracked_tickers()

    summary = {"evaluated": 0, "updated": 0, "skipped": 0, "errors": 0}

    for ticker in tickers:
        open_trades = load_open_trades(ticker=ticker)
        for trade in open_trades:
            try:
                summary["evaluated"] += 1
                updated = evaluate_trade(trade)

                # Sla alleen op als er iets veranderd is
                changed = any(
                    updated.get(k) != trade.get(k)
                    for k in ("price_1d", "price_3d", "price_5d", "price_10d")
                )
                if changed:
                    update_trade_outcomes(
                        trade_id  = updated["trade_id"],
                        ticker    = updated["ticker"],
                        price_1d  = updated.get("price_1d"),
                        price_3d  = updated.get("price_3d"),
                        price_5d  = updated.get("price_5d"),
                        price_10d = updated.get("price_10d"),
                    )
                    summary["updated"] += 1
                    logger.info(
                        "paper_trade_evaluator: %s/%s bijgewerkt (status=%s)",
                        updated["ticker"], updated["trade_id"][-20:], updated.get("status"),
                    )
                else:
                    summary["skipped"] += 1

            except Exception as exc:
                summary["errors"] += 1
                logger.warning(
                    "paper_trade_evaluator: fout bij %s: %s: %s",
                    trade.get("ticker"), type(exc).__name__, exc,
                )

    return summary
