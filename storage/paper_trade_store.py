"""
storage/paper_trade_store.py
Paper Trade Store — v1.0

Slaat BUY_SMALL, BUY_MODERATE en BUY_STRONG signalen op op het moment
dat de engine ze genereert. Koppelt later aan werkelijke marktprijzen
via paper_trade_evaluator.py om rendement te meten.

Formaat: JSON Lines — storage/data/paper_trades/{TICKER}.jsonl
Eén bestand per ticker, append-only.
Eén globale index: storage/data/paper_trades/_index.jsonl

Design keuzes:
    - Gescheiden van snapshot_store — paper trades zijn signaal-specifiek
    - Geen automatische prijs-lookup bij opslag — alleen de entry-context
    - Prijzen worden later ingevuld door paper_trade_evaluator.py
    - Idempotent: zelfde trade_id overschrijft vorige entry
    - Trade ID: {YYYYMMDDTHHMMSS}_{TICKER}_{DECISION}
"""

import json
import os
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_STORAGE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "storage", "data",
)
_TRADES_DIR  = os.path.join(_STORAGE_ROOT, "paper_trades")
_INDEX_PATH  = os.path.join(_TRADES_DIR, "_index.jsonl")

# Beslissingen die in aanmerking komen voor paper trading
BUY_DECISIONS = {"BUY_SMALL", "BUY_MODERATE", "BUY_STRONG", "BUY_MAX"}

# Status waarden
STATUS_OPEN     = "OPEN"       # Nog geen outcomes ingevuld
STATUS_PARTIAL  = "PARTIAL"    # Sommige horizons ingevuld
STATUS_COMPLETE = "COMPLETE"   # Alle gevraagde horizons ingevuld


@dataclass
class PaperTrade:
    """
    Eén paper trade: een BUY-signaal met entry-context en latere uitkomsten.
    """
    # Identiteit
    trade_id:       str          # {YYYYMMDDTHHMMSS}_{TICKER}_{DECISION}
    ticker:         str
    signal_ts:      str          # ISO UTC — moment van het signaal
    stored_at:      str          # ISO UTC — moment van opslag

    # Engine output op het moment van het signaal
    decision:       str          # BUY_SMALL / BUY_MODERATE / BUY_STRONG / BUY_MAX
    momentum_score: float
    skip_score:     int
    phase:          str
    sector_id:      str
    sector_heat:    int

    # Catalyst context
    catalyst_type:  str          # STRONG / MODERATE / WEAK / NONE
    catalyst_source: str         # OWN / SECTOR / SYMPATHY / NONE
    catalyst_desc:  str

    # Marktcontext bij signaal
    entry_price:    float
    day_change_pct: float
    volume_ratio:   float        # volume_today / avg_volume_20d
    premarket_pct:  float

    # Outcomes — ingevuld door paper_trade_evaluator.py
    price_1d:       Optional[float] = None
    price_3d:       Optional[float] = None
    price_5d:       Optional[float] = None
    price_10d:      Optional[float] = None

    return_1d:      Optional[float] = None   # %
    return_3d:      Optional[float] = None
    return_5d:      Optional[float] = None
    return_10d:     Optional[float] = None

    # Status
    status:         str          = STATUS_OPEN
    evaluated_at:   Optional[str] = None

    # Kwaliteitsvlaggen
    data_confidence: str         = "UNKNOWN"
    is_partial_data: bool        = False     # True als data PARTIAL was bij signaal


def _make_trade_id(ticker: str, decision: str, ts: datetime) -> str:
    return f"{ts.strftime('%Y%m%dT%H%M%S')}_{ticker.upper()}_{decision}"


def _trade_path(ticker: str) -> str:
    os.makedirs(_TRADES_DIR, exist_ok=True)
    return os.path.join(_TRADES_DIR, f"{ticker.upper()}.jsonl")


def _append_jsonl(path: str, obj: dict) -> None:
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, default=str) + "\n")
    except Exception as exc:
        logger.warning("paper_trade_store: schrijven mislukt naar %s: %s", path, exc)


def _rewrite_jsonl(path: str, entries: list[dict]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, default=str) + "\n")
    except Exception as exc:
        logger.warning("paper_trade_store: herschrijven mislukt naar %s: %s", path, exc)


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    result = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    result.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as exc:
        logger.warning("paper_trade_store: lezen mislukt van %s: %s", path, exc)
    return result


# ── WRITE ─────────────────────────────────────────────────────────────────────

def record_trade(trade: PaperTrade) -> None:
    """
    Slaat een paper trade op. Overschrijft vorige entry met dezelfde trade_id.
    Voegt ook toe aan de globale index.
    """
    ticker = trade.ticker.upper()
    path   = _trade_path(ticker)

    # Idempotent: vervang bestaande entry met dezelfde trade_id
    existing = [
        e for e in _read_jsonl(path)
        if e.get("trade_id") != trade.trade_id
    ]
    existing.append(asdict(trade))
    _rewrite_jsonl(path, existing)

    # Index bijwerken (alleen als nieuw)
    index = _read_jsonl(_INDEX_PATH)
    existing_ids = {e.get("trade_id") for e in index}
    if trade.trade_id not in existing_ids:
        _append_jsonl(_INDEX_PATH, {
            "trade_id":   trade.trade_id,
            "ticker":     ticker,
            "decision":   trade.decision,
            "signal_ts":  trade.signal_ts,
            "stored_at":  trade.stored_at,
        })

    logger.info(
        "paper_trade_store: %s %s @ %.2f opgeslagen (id=%s)",
        ticker, trade.decision, trade.entry_price, trade.trade_id,
    )


def save_trade_from_result(
    ticker:          str,
    decision:        str,
    momentum_score:  float,
    skip_score:      int,
    phase:           str,
    sector_id:       str,
    sector_heat:     int,
    catalyst_type:   str,
    catalyst_source: str,
    catalyst_desc:   str,
    entry_price:     float,
    day_change_pct:  float,
    volume_ratio:    float,
    premarket_pct:   float,
    data_confidence: str  = "UNKNOWN",
    is_partial_data: bool = False,
    signal_ts:       Optional[datetime] = None,
) -> Optional[str]:
    """
    Convenience wrapper: maakt PaperTrade aan en slaat op.
    Retourneert trade_id, of None als beslissing niet BUY is.
    Gooit nooit een exception.
    """
    if decision not in BUY_DECISIONS:
        return None

    try:
        now = signal_ts or datetime.now(timezone.utc)
        trade = PaperTrade(
            trade_id        = _make_trade_id(ticker, decision, now),
            ticker          = ticker.upper(),
            signal_ts       = now.isoformat(),
            stored_at       = datetime.now(timezone.utc).isoformat(),
            decision        = decision,
            momentum_score  = round(momentum_score, 2),
            skip_score      = skip_score,
            phase           = phase,
            sector_id       = sector_id,
            sector_heat     = sector_heat,
            catalyst_type   = catalyst_type,
            catalyst_source = catalyst_source,
            catalyst_desc   = catalyst_desc[:120],
            entry_price     = round(entry_price, 4),
            day_change_pct  = round(day_change_pct, 2),
            volume_ratio    = round(volume_ratio, 2),
            premarket_pct   = round(premarket_pct, 2),
            data_confidence = data_confidence,
            is_partial_data = is_partial_data,
        )
        record_trade(trade)
        return trade.trade_id
    except Exception as exc:
        logger.warning(
            "paper_trade_store: opslaan mislukt voor %s: %s: %s",
            ticker, type(exc).__name__, exc,
        )
        return None


# ── READ ──────────────────────────────────────────────────────────────────────

def load_trades(
    ticker:      Optional[str] = None,
    decision:    Optional[str] = None,
    status:      Optional[str] = None,
    limit:       int           = 500,
) -> list[dict]:
    """
    Laadt paper trades. Filters: ticker, decision, status.
    Zonder ticker: alle trades via de index.
    Gesorteerd: nieuwste eerst.
    """
    if ticker:
        trades = _read_jsonl(_trade_path(ticker))
    else:
        # Alle tickers via index
        index  = _read_jsonl(_INDEX_PATH)
        tickers = list({e["ticker"] for e in index if "ticker" in e})
        trades  = []
        for t in tickers:
            trades.extend(_read_jsonl(_trade_path(t)))

    if decision:
        trades = [t for t in trades if t.get("decision") == decision]
    if status:
        trades = [t for t in trades if t.get("status") == status]

    trades.sort(key=lambda x: x.get("signal_ts", ""), reverse=True)
    return trades[:limit]


def load_open_trades(ticker: Optional[str] = None) -> list[dict]:
    """Trades die nog geen outcome hebben (OPEN of PARTIAL)."""
    return [
        t for t in load_trades(ticker=ticker)
        if t.get("status") in (STATUS_OPEN, STATUS_PARTIAL)
    ]


def load_complete_trades(ticker: Optional[str] = None) -> list[dict]:
    """Trades met volledige outcome data."""
    return [
        t for t in load_trades(ticker=ticker)
        if t.get("status") == STATUS_COMPLETE
    ]


def update_trade_outcomes(
    trade_id:   str,
    ticker:     str,
    price_1d:   Optional[float] = None,
    price_3d:   Optional[float] = None,
    price_5d:   Optional[float] = None,
    price_10d:  Optional[float] = None,
) -> bool:
    """
    Vult prijs- en rendement-velden in voor een bestaande trade.
    Berekent returns en bepaalt status.
    Retourneert True als de trade gevonden en bijgewerkt is.
    """
    path   = _trade_path(ticker)
    trades = _read_jsonl(path)

    updated = False
    for trade in trades:
        if trade.get("trade_id") != trade_id:
            continue

        entry = trade.get("entry_price", 0.0)

        def _ret(p: Optional[float]) -> Optional[float]:
            if p and entry and entry > 0:
                return round((p - entry) / entry * 100, 3)
            return None

        if price_1d  is not None: trade["price_1d"]  = price_1d;  trade["return_1d"]  = _ret(price_1d)
        if price_3d  is not None: trade["price_3d"]  = price_3d;  trade["return_3d"]  = _ret(price_3d)
        if price_5d  is not None: trade["price_5d"]  = price_5d;  trade["return_5d"]  = _ret(price_5d)
        if price_10d is not None: trade["price_10d"] = price_10d; trade["return_10d"] = _ret(price_10d)

        # Status bepalen
        filled   = sum(1 for k in ("return_1d","return_3d","return_5d","return_10d") if trade.get(k) is not None)
        trade["status"]       = STATUS_COMPLETE if filled == 4 else (STATUS_PARTIAL if filled > 0 else STATUS_OPEN)
        trade["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        updated = True
        break

    if updated:
        _rewrite_jsonl(path, trades)
    return updated


def list_tracked_tickers() -> list[str]:
    """Alle tickers met minstens één paper trade."""
    try:
        os.makedirs(_TRADES_DIR, exist_ok=True)
        return [
            f.replace(".jsonl", "")
            for f in os.listdir(_TRADES_DIR)
            if f.endswith(".jsonl") and not f.startswith("_")
        ]
    except Exception:
        return []


def delete_trades(ticker: str) -> bool:
    """Verwijdert alle trades voor een ticker."""
    path = _trade_path(ticker)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
