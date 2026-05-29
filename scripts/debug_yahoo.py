"""
scripts/debug_yahoo.py
Standalone Yahoo Finance debug script — v1.0

Gebruik:
    python3 scripts/debug_yahoo.py
    python3 scripts/debug_yahoo.py IONQ MSFT

Wat dit test:
    1. fast_info (last_price, previous_close, market_cap, shares_outstanding)
    2. history(period="5d")
    3. history-gebaseerde prijs/volume derivatie (fallback pad)
    4. Toont volledige traceback bij elke fout
    5. Geeft eindvonnis: WERKT / WERKT GEDEELTELIJK / KAPOT

Geen project-imports nodig — draait puur op yfinance + stdlib.
"""

import sys
import traceback
import importlib.metadata

# ── versie-info ───────────────────────────────────────────────────────────────
try:
    yf_version = importlib.metadata.version("yfinance")
except Exception:
    yf_version = "onbekend"

print(f"yfinance versie: {yf_version}")
print(f"Python: {sys.version.split()[0]}")
print("=" * 60)

# ── tickers bepalen ───────────────────────────────────────────────────────────
tickers = sys.argv[1:] if len(sys.argv) > 1 else ["NVDA", "IONQ"]

try:
    import yfinance as yf
except ImportError:
    print("FOUT: yfinance niet geïnstalleerd. Run: pip install yfinance")
    sys.exit(1)


def _safe_attr(obj, attr, default=None):
    """Haal attribuut op zonder te crashen."""
    try:
        val = getattr(obj, attr, default)
        return val
    except Exception:
        return default


def test_ticker(ticker: str) -> dict:
    """
    Test alle yfinance data-paden voor één ticker.
    Retourneert dict met resultaten en status per pad.
    """
    result = {
        "ticker": ticker,
        "fast_info_ok": False,
        "history_ok": False,
        "price": None,
        "prev_close": None,
        "volume": None,
        "market_cap": None,
        "errors": [],
    }

    t = yf.Ticker(ticker)

    # ── PAD 1: fast_info ──────────────────────────────────────────────────────
    print(f"\n{'─'*40}")
    print(f"[{ticker}] PAD 1 — fast_info")
    try:
        fi = t.fast_info

        # last_price triggert intern _get_1y_prices() → kwetsbaar pad
        price = _safe_attr(fi, "last_price", None)
        if price is None:
            raise ValueError("fast_info.last_price retourneerde None")

        prev_close    = _safe_attr(fi, "previous_close", price)
        market_cap    = _safe_attr(fi, "market_cap", None)
        shares_out    = _safe_attr(fi, "shares_outstanding", None)
        premarket_p   = _safe_attr(fi, "pre_market_price", None)

        print(f"  last_price:         {price}")
        print(f"  previous_close:     {prev_close}")
        print(f"  market_cap:         {market_cap}")
        print(f"  shares_outstanding: {shares_out}")
        print(f"  pre_market_price:   {premarket_p}")

        result["fast_info_ok"] = True
        result["price"]        = price
        result["prev_close"]   = prev_close
        result["market_cap"]   = market_cap

        print(f"  ✅ fast_info WERKT")

    except Exception as exc:
        err_summary = f"{type(exc).__name__}: {exc}"
        result["errors"].append(f"fast_info — {err_summary}")
        print(f"  ❌ fast_info MISLUKT: {err_summary}")
        print(f"  Volledige traceback:")
        traceback.print_exc()

    # ── PAD 2: history(period="5d") ───────────────────────────────────────────
    print(f"\n[{ticker}] PAD 2 — history(period='5d')")
    try:
        # Verse Ticker-instantie om cache-effecten te vermijden
        t2   = yf.Ticker(ticker)
        hist = t2.history(period="5d", auto_adjust=True)

        print(f"  Rijen terug: {len(hist)}")
        print(f"  Kolommen:    {list(hist.columns)}")

        if hist.empty:
            raise ValueError(
                "history() retourneerde lege DataFrame — "
                "ticker ongeldig, delisted, of Yahoo geblokkeerd"
            )

        last_close  = float(hist["Close"].iloc[-1])
        last_volume = int(hist["Volume"].iloc[-1])

        # prev_close = slotkoers van de dag ervóór (als die beschikbaar is)
        prev_close_h = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else last_close

        print(f"  Laatste close:  {last_close:.2f}")
        print(f"  Vorige close:   {prev_close_h:.2f}")
        print(f"  Laatste volume: {last_volume:,}")
        print(f"  Gem. volume:    {int(hist['Volume'].mean()):,}")
        print(f"\n  Laatste 3 rijen:")
        print(hist.tail(3).to_string())

        result["history_ok"] = True
        result["volume"]     = last_volume

        # Als fast_info faalde, vul dan prijs in vanuit history
        if result["price"] is None:
            result["price"]      = last_close
            result["prev_close"] = prev_close_h
            print(f"\n  ℹ️  Prijs afgeleid uit history (fast_info was kapot)")

        print(f"\n  ✅ history WERKT")

    except Exception as exc:
        err_summary = f"{type(exc).__name__}: {exc}"
        result["errors"].append(f"history — {err_summary}")
        print(f"  ❌ history MISLUKT: {err_summary}")
        print(f"  Volledige traceback:")
        traceback.print_exc()

    # ── PAD 3: info (optioneel — langzaam) ───────────────────────────────────
    print(f"\n[{ticker}] PAD 3 — info (beschikbaarheidscheck)")
    try:
        t3     = yf.Ticker(ticker)
        info   = t3.info
        keys   = list(info.keys()) if isinstance(info, dict) else []
        sample = {k: info[k] for k in list(keys)[:5]}
        print(f"  info beschikbaar: {len(keys)} velden")
        print(f"  Eerste 5 velden:  {sample}")
        print(f"  ✅ info beschikbaar")
    except Exception as exc:
        err_summary = f"{type(exc).__name__}: {exc}"
        result["errors"].append(f"info — {err_summary}")
        print(f"  ⚠️  info MISLUKT (niet kritiek): {err_summary}")

    return result


# ── Hoofd-loop ────────────────────────────────────────────────────────────────
all_results = []
for ticker in tickers:
    res = test_ticker(ticker.upper())
    all_results.append(res)

# ── Eindvonnis ────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("EINDVONNIS")
print("=" * 60)

for res in all_results:
    ticker = res["ticker"]
    if res["fast_info_ok"] and res["history_ok"]:
        status = "✅ VOLLEDIG WERKEND"
    elif res["fast_info_ok"] or res["history_ok"]:
        status = "⚠️  GEDEELTELIJK (één pad werkt)"
    else:
        status = "❌ BEIDE PADEN KAPOT — API returns 422"

    print(f"\n  {ticker}: {status}")
    print(f"    fast_info:  {'OK' if res['fast_info_ok'] else 'KAPOT'}")
    print(f"    history:    {'OK' if res['history_ok'] else 'KAPOT'}")
    if res["price"] is not None:
        print(f"    Prijs:      {res['price']:.2f}")
    if res["errors"]:
        print(f"    Fouten ({len(res['errors'])}):")
        for err in res["errors"]:
            print(f"      • {err}")

print()
print("CONCLUSIE:")
all_ok         = all(r["fast_info_ok"] and r["history_ok"] for r in all_results)
partial        = any(r["fast_info_ok"] or r["history_ok"] for r in all_results)
fast_info_fail = any(not r["fast_info_ok"] for r in all_results)

if all_ok:
    print("  Alle paden werken — backend zou moeten werken.")
elif partial and fast_info_fail:
    print("  fast_info kapot, history werkt → fallback in yahoo_client.py actief.")
    print("  Waarschijnlijke oorzaak: yfinance 0.2.36 bug (KeyError currentTradingPeriod).")
    print("  Fix: upgrade naar yfinance>=0.2.54 OF gebruik history-fallback (al gebouwd).")
else:
    print("  Beide paden kapot → Yahoo Finance is geblokkeerd of unreachable.")
    print("  Check: netwerktoegang, VPN, of Yahoo rate-limiting (429/403).")
    print("  Bevestig met: curl -I https://query1.finance.yahoo.com/v8/finance/chart/NVDA")
