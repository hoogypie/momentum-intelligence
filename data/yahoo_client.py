"""
data/yahoo_client.py
Yahoo Finance data client — v2.0

Haalt op: prijs, volume, market cap, pre-market, 20-daags gemiddeld volume.
Bron: yfinance (unofficial Yahoo Finance API, gratis, geen key vereist).

Ontwerpkeuzes:
    - Alle velden hebben graceful fallbacks — nooit een crash op None
    - float_shares: yfinance geeft shares_outstanding (niet exact float, maar
      beste benadering zonder betaalde feed). Zie FM-003 in KNOWN_FAILURE_MODES.
    - pre_market_pct: alleen beschikbaar buiten reguliere handelsuren.
      Geeft 0.0 terug als pre-market niet actief is.
    - avg_volume_20d: berekend uit 1-maand history.
    - SPY return: voor relative strength berekening in assembler.py

Fase 5: vervangen door Polygon.io voor betere kwaliteit + WebSocket support.
"""

from dataclasses import dataclass
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class QuoteData:
    """Ruwe marktdata van Yahoo Finance. Alle velden nullable."""
    ticker: str
    price: float
    prev_close: float
    day_change_pct: float
    premarket_price: Optional[float]
    premarket_pct: float             # 0.0 als niet beschikbaar
    volume_today: int
    avg_volume_20d: int
    market_cap: Optional[float]
    float_shares: Optional[int]      # Benadering via shares_outstanding
    error: Optional[str] = None      # Ingevuld bij ophaalfout


def get_quote(ticker: str) -> QuoteData:
    """
    Haalt marktdata op voor één ticker via yfinance.

    Errors:
        - Onbekende ticker → QuoteData met error ingevuld, veilige defaults
        - Rate limit → idem
        - Netwerk timeout → idem

    Returns altijd een QuoteData object, nooit een exception.
    """
    try:
        import yfinance as yf
        import pandas as pd

        t = yf.Ticker(ticker)

        # Snelle info (minder API calls dan .info)
        fi = t.fast_info

        price = _safe(fi, "last_price", 0.0)
        prev_close = _safe(fi, "previous_close", price)

        day_change_pct = (
            ((price - prev_close) / prev_close * 100)
            if prev_close and prev_close > 0
            else 0.0
        )

        # Pre-market — alleen buiten markturen beschikbaar
        pm_price = _safe(fi, "pre_market_price", None)
        if pm_price and prev_close and prev_close > 0:
            premarket_pct = (pm_price - prev_close) / prev_close * 100
        else:
            premarket_pct = 0.0

        # Volume — huidig + 20-daags gemiddelde
        hist_1m = t.history(period="1mo", auto_adjust=True)
        hist_2d = t.history(period="2d",  auto_adjust=True)

        if not hist_1m.empty:
            avg_volume_20d = int(hist_1m["Volume"].mean())
            volume_today   = int(hist_1m["Volume"].iloc[-1])
        elif not hist_2d.empty:
            avg_volume_20d = int(hist_2d["Volume"].mean())
            volume_today   = int(hist_2d["Volume"].iloc[-1])
        else:
            avg_volume_20d = 1
            volume_today   = 0

        market_cap   = _safe(fi, "market_cap", None)
        shares_out   = _safe(fi, "shares_outstanding", None)
        float_shares = int(shares_out) if shares_out else None

        return QuoteData(
            ticker=ticker.upper(),
            price=round(float(price), 2),
            prev_close=round(float(prev_close), 2),
            day_change_pct=round(day_change_pct, 2),
            premarket_price=round(float(pm_price), 2) if pm_price else None,
            premarket_pct=round(premarket_pct, 2),
            volume_today=volume_today,
            avg_volume_20d=max(avg_volume_20d, 1),
            market_cap=float(market_cap) if market_cap else None,
            float_shares=float_shares,
        )

    except Exception as exc:
        logger.warning(f"yahoo_client: fout bij ophalen {ticker}: {exc}")
        return QuoteData(
            ticker=ticker.upper(),
            price=0.0, prev_close=0.0, day_change_pct=0.0,
            premarket_price=None, premarket_pct=0.0,
            volume_today=0, avg_volume_20d=1,
            market_cap=None, float_shares=None,
            error=str(exc),
        )


def get_spy_return() -> float:
    """
    Dagsrendement van SPY voor relative strength berekening.
    Geeft 0.0 terug bij ophaalfout.
    """
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        fi  = spy.fast_info
        price      = _safe(fi, "last_price",      0.0)
        prev_close = _safe(fi, "previous_close",  price)
        if prev_close and prev_close > 0:
            return round((price - prev_close) / prev_close * 100, 2)
    except Exception as exc:
        logger.warning(f"yahoo_client: SPY ophalen mislukt: {exc}")
    return 0.0


def _safe(obj, attr: str, default):
    """Haal attribuut op zonder crash bij None of missing."""
    try:
        val = getattr(obj, attr, default)
        return val if val is not None else default
    except Exception:
        return default
