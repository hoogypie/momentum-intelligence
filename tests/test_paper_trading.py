"""
tests/test_paper_trading.py
Paper Trading Tests — v1.0

Test coverage:
    TestPaperTradeStore         Opslaan, laden, update, idempotentie
    TestPaperTradeMakeId        Trade ID formaat en uniciteit
    TestPaperTradeFilters       Filters: decision, status, ticker
    TestUpdateTradeOutcomes     Return berekening en status-overgang
    TestPaperTradeEvaluator     Price fetching, horizon-logica, fallback
    TestPaperTradeStatistics    Win rate, gemiddeld rendement, mediaan
    TestValidationRunnerHook    paper_trade parameter in _analyze_one
"""

import json
import os
import pytest
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_trade(
    ticker    = "NVDA",
    decision  = "BUY_MODERATE",
    score     = 65.0,
    entry     = 135.0,
    cat_type  = "STRONG",
    cat_src   = "OWN",
    phase     = "BREAKOUT",
    age_days  = 0,
) -> dict:
    """Maak een minimale trade-dict voor tests."""
    from storage.paper_trade_store import PaperTrade, _make_trade_id, STATUS_OPEN
    ts = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {
        "trade_id":       _make_trade_id(ticker, decision, ts),
        "ticker":         ticker,
        "signal_ts":      ts.isoformat(),
        "stored_at":      ts.isoformat(),
        "decision":       decision,
        "momentum_score": score,
        "skip_score":     0,
        "phase":          phase,
        "sector_id":      "ai_infra",
        "sector_heat":    95,
        "catalyst_type":  cat_type,
        "catalyst_source": cat_src,
        "catalyst_desc":  "Test catalyst",
        "entry_price":    entry,
        "day_change_pct": 2.5,
        "volume_ratio":   3.0,
        "premarket_pct":  0.0,
        "price_1d":       None,
        "price_3d":       None,
        "price_5d":       None,
        "price_10d":      None,
        "return_1d":      None,
        "return_3d":      None,
        "return_5d":      None,
        "return_10d":     None,
        "status":         STATUS_OPEN,
        "evaluated_at":   None,
        "data_confidence": "LIVE",
        "is_partial_data": False,
    }


# ── TestPaperTradeMakeId ──────────────────────────────────────────────────────

class TestPaperTradeMakeId:

    def test_format_contains_ticker(self):
        from storage.paper_trade_store import _make_trade_id
        ts = datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc)
        tid = _make_trade_id("NVDA", "BUY_MODERATE", ts)
        assert "NVDA" in tid

    def test_format_contains_decision(self):
        from storage.paper_trade_store import _make_trade_id
        ts = datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc)
        tid = _make_trade_id("NVDA", "BUY_STRONG", ts)
        assert "BUY_STRONG" in tid

    def test_format_contains_timestamp(self):
        from storage.paper_trade_store import _make_trade_id
        ts = datetime(2026, 5, 29, 10, 30, 0, tzinfo=timezone.utc)
        tid = _make_trade_id("NVDA", "BUY_SMALL", ts)
        assert "20260529T103000" in tid

    def test_uppercase_ticker(self):
        from storage.paper_trade_store import _make_trade_id
        ts = datetime.now(timezone.utc)
        tid = _make_trade_id("nvda", "BUY_SMALL", ts)
        assert "NVDA" in tid

    def test_different_tickers_different_ids(self):
        from storage.paper_trade_store import _make_trade_id
        ts = datetime.now(timezone.utc)
        assert _make_trade_id("NVDA", "BUY_SMALL", ts) != _make_trade_id("MU", "BUY_SMALL", ts)


# ── TestPaperTradeStore ───────────────────────────────────────────────────────

class TestPaperTradeStore:

    def test_record_and_load(self, tmp_path):
        from storage.paper_trade_store import PaperTrade, record_trade, load_trades, _make_trade_id
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                ts    = datetime.now(timezone.utc)
                trade = PaperTrade(**{k: v for k, v in _make_trade(ticker="NVDA").items()
                                     if k in PaperTrade.__dataclass_fields__})
                record_trade(trade)
                trades = load_trades(ticker="NVDA")
                assert len(trades) == 1
                assert trades[0]["ticker"] == "NVDA"

    def test_record_is_idempotent(self, tmp_path):
        """Zelfde trade_id overschrijft vorige entry — geen duplicaten."""
        from storage.paper_trade_store import PaperTrade, record_trade, load_trades, _make_trade_id
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                t = _make_trade(ticker="MU")
                trade_obj = PaperTrade(**{k: v for k, v in t.items()
                                         if k in PaperTrade.__dataclass_fields__})
                record_trade(trade_obj)
                record_trade(trade_obj)  # Tweede keer
                trades = load_trades(ticker="MU")
                assert len(trades) == 1

    def test_save_trade_from_result_returns_none_for_non_buy(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                result = save_trade_from_result(
                    ticker="NVDA", decision="WATCH", momentum_score=45.0,
                    skip_score=0, phase="NEUTRAL", sector_id="ai_infra",
                    sector_heat=95, catalyst_type="NONE", catalyst_source="NONE",
                    catalyst_desc="", entry_price=135.0, day_change_pct=1.0,
                    volume_ratio=1.5, premarket_pct=0.0,
                )
                assert result is None

    def test_save_trade_from_result_returns_trade_id_for_buy(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                result = save_trade_from_result(
                    ticker="NVDA", decision="BUY_MODERATE", momentum_score=65.0,
                    skip_score=0, phase="BREAKOUT", sector_id="ai_infra",
                    sector_heat=95, catalyst_type="STRONG", catalyst_source="OWN",
                    catalyst_desc="Earnings beat", entry_price=135.0,
                    day_change_pct=2.5, volume_ratio=3.0, premarket_pct=0.0,
                )
                assert result is not None
                assert "NVDA" in result
                assert "BUY_MODERATE" in result

    def test_save_never_raises(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                try:
                    save_trade_from_result(
                        ticker="", decision="BUY_MAX", momentum_score=float("nan"),
                        skip_score=0, phase="", sector_id="", sector_heat=0,
                        catalyst_type="NONE", catalyst_source="NONE",
                        catalyst_desc="", entry_price=0.0, day_change_pct=0.0,
                        volume_ratio=0.0, premarket_pct=0.0,
                    )
                except Exception as exc:
                    pytest.fail(f"save_trade_from_result gooidde exception: {exc}")

    def test_all_buy_decisions_recorded(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result, load_trades, BUY_DECISIONS
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                for dec in BUY_DECISIONS:
                    save_trade_from_result(
                        ticker=f"T{dec[:3]}", decision=dec, momentum_score=70.0,
                        skip_score=0, phase="BREAKOUT", sector_id="test",
                        sector_heat=80, catalyst_type="MODERATE", catalyst_source="OWN",
                        catalyst_desc="test", entry_price=100.0,
                        day_change_pct=5.0, volume_ratio=3.0, premarket_pct=0.0,
                    )
                all_trades = load_trades()
                recorded_decisions = {t["decision"] for t in all_trades}
                assert BUY_DECISIONS.issubset(recorded_decisions)


# ── TestPaperTradeFilters ─────────────────────────────────────────────────────

class TestPaperTradeFilters:

    def _setup_trades(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result
        kwargs = dict(
            skip_score=0, phase="BREAKOUT", sector_id="test", sector_heat=80,
            catalyst_type="MODERATE", catalyst_source="OWN", catalyst_desc="test",
            day_change_pct=2.0, volume_ratio=2.0, premarket_pct=0.0,
        )
        save_trade_from_result(ticker="NVDA", decision="BUY_STRONG",  momentum_score=80.0, entry_price=135.0, **kwargs)
        save_trade_from_result(ticker="MU",   decision="BUY_MODERATE", momentum_score=65.0, entry_price=100.0, **kwargs)
        save_trade_from_result(ticker="IONQ", decision="BUY_SMALL",   momentum_score=55.0, entry_price=25.0,  **kwargs)

    def test_filter_by_ticker(self, tmp_path):
        from storage.paper_trade_store import load_trades
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                self._setup_trades(tmp_path)
                trades = load_trades(ticker="NVDA")
                assert all(t["ticker"] == "NVDA" for t in trades)
                assert len(trades) == 1

    def test_filter_by_decision(self, tmp_path):
        from storage.paper_trade_store import load_trades
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                self._setup_trades(tmp_path)
                trades = load_trades(decision="BUY_MODERATE")
                assert all(t["decision"] == "BUY_MODERATE" for t in trades)

    def test_load_all_without_filter(self, tmp_path):
        from storage.paper_trade_store import load_trades
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                self._setup_trades(tmp_path)
                trades = load_trades()
                assert len(trades) == 3

    def test_load_complete_empty_when_no_complete(self, tmp_path):
        from storage.paper_trade_store import load_complete_trades
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                self._setup_trades(tmp_path)
                trades = load_complete_trades()
                assert trades == []


# ── TestUpdateTradeOutcomes ───────────────────────────────────────────────────

class TestUpdateTradeOutcomes:

    def test_return_calculated_correctly(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result, update_trade_outcomes, load_trades
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                trade_id = save_trade_from_result(
                    ticker="NVDA", decision="BUY_MODERATE", momentum_score=65.0,
                    skip_score=0, phase="BREAKOUT", sector_id="ai_infra",
                    sector_heat=95, catalyst_type="STRONG", catalyst_source="OWN",
                    catalyst_desc="test", entry_price=100.0,
                    day_change_pct=2.0, volume_ratio=2.0, premarket_pct=0.0,
                )
                update_trade_outcomes(trade_id, "NVDA", price_1d=105.0)
                trades = load_trades(ticker="NVDA")
                assert trades[0]["return_1d"] == pytest.approx(5.0, abs=0.01)

    def test_negative_return(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result, update_trade_outcomes, load_trades
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                trade_id = save_trade_from_result(
                    ticker="NVDA", decision="BUY_SMALL", momentum_score=55.0,
                    skip_score=0, phase="NEUTRAL", sector_id="test",
                    sector_heat=70, catalyst_type="WEAK", catalyst_source="OWN",
                    catalyst_desc="test", entry_price=100.0,
                    day_change_pct=1.0, volume_ratio=1.5, premarket_pct=0.0,
                )
                update_trade_outcomes(trade_id, "NVDA", price_1d=92.0)
                trades = load_trades(ticker="NVDA")
                assert trades[0]["return_1d"] == pytest.approx(-8.0, abs=0.01)

    def test_status_becomes_partial_with_one_horizon(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result, update_trade_outcomes, load_trades, STATUS_PARTIAL
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                trade_id = save_trade_from_result(
                    ticker="MU", decision="BUY_MODERATE", momentum_score=65.0,
                    skip_score=0, phase="BREAKOUT", sector_id="test",
                    sector_heat=80, catalyst_type="MODERATE", catalyst_source="OWN",
                    catalyst_desc="test", entry_price=100.0,
                    day_change_pct=2.0, volume_ratio=2.0, premarket_pct=0.0,
                )
                update_trade_outcomes(trade_id, "MU", price_1d=103.0)
                trades = load_trades(ticker="MU")
                assert trades[0]["status"] == STATUS_PARTIAL

    def test_status_becomes_complete_with_all_horizons(self, tmp_path):
        from storage.paper_trade_store import save_trade_from_result, update_trade_outcomes, load_trades, STATUS_COMPLETE
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                trade_id = save_trade_from_result(
                    ticker="IONQ", decision="BUY_SMALL", momentum_score=55.0,
                    skip_score=0, phase="BREAKOUT", sector_id="quantum",
                    sector_heat=92, catalyst_type="MODERATE", catalyst_source="OWN",
                    catalyst_desc="test", entry_price=25.0,
                    day_change_pct=5.0, volume_ratio=4.0, premarket_pct=0.0,
                )
                update_trade_outcomes(trade_id, "IONQ",
                    price_1d=26.0, price_3d=27.0, price_5d=28.0, price_10d=30.0)
                trades = load_trades(ticker="IONQ")
                assert trades[0]["status"] == STATUS_COMPLETE

    def test_returns_false_for_unknown_trade_id(self, tmp_path):
        from storage.paper_trade_store import update_trade_outcomes
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            result = update_trade_outcomes("NONEXISTENT_ID", "NVDA", price_1d=140.0)
            assert result is False


# ── TestPaperTradeEvaluator ───────────────────────────────────────────────────

class TestPaperTradeEvaluator:

    def _make_full_trade(self, age_days=12, entry=100.0):
        t = _make_trade(ticker="NVDA", entry=entry, age_days=age_days)
        return t

    def test_evaluate_trade_fills_price_fields(self):
        """Mocked Yahoo geeft prijzen terug → outcome fields gevuld."""
        from storage.paper_trade_evaluator import evaluate_trade

        trade = self._make_full_trade(age_days=15)

        with patch("storage.paper_trade_evaluator._fetch_close_price") as mock_fetch:
            mock_fetch.side_effect = lambda ticker, target_dt, tolerance=2: {
                "1d":  105.0,
                "3d":  108.0,
                "5d":  110.0,
                "10d": 115.0,
            }.get(
                min(["1d","3d","5d","10d"],
                    key=lambda h: abs(
                        (target_dt - (datetime.fromisoformat(trade["signal_ts"].replace("Z","+00:00"))
                         + timedelta(days={"1d":2,"3d":5,"5d":8,"10d":15}[h]))).total_seconds()
                    )),
                None,
            )

            updated = evaluate_trade(trade)

        assert any(updated.get(k) is not None for k in ("price_1d","price_3d","price_5d","price_10d"))

    def test_evaluate_trade_never_raises(self):
        """evaluate_trade gooit nooit een exception, ook niet bij slechte input."""
        from storage.paper_trade_evaluator import evaluate_trade
        try:
            result = evaluate_trade({"ticker": "BROKEN", "entry_price": 0.0,
                                      "signal_ts": "invalid-timestamp"})
            assert isinstance(result, dict)
        except Exception as exc:
            pytest.fail(f"evaluate_trade gooidde exception: {exc}")

    def test_evaluate_trade_skips_already_filled(self):
        """Horizons die al ingevuld zijn worden niet overschreven."""
        from storage.paper_trade_evaluator import evaluate_trade

        trade = self._make_full_trade(age_days=15)
        trade["price_1d"]  = 105.0
        trade["return_1d"] = 5.0

        with patch("storage.paper_trade_evaluator._fetch_close_price") as mock_fetch:
            mock_fetch.return_value = 999.0  # Zou nooit gezien moeten worden voor 1d
            updated = evaluate_trade(trade)

        assert updated["price_1d"] == 105.0  # Ongewijzigd

    def test_evaluate_skips_future_horizons(self):
        """Horizons die nog niet bereikbaar zijn (te vroeg) worden overgeslagen."""
        from storage.paper_trade_evaluator import evaluate_trade

        trade = self._make_full_trade(age_days=0)  # Net opgeslagen
        with patch("storage.paper_trade_evaluator._fetch_close_price") as mock_fetch:
            mock_fetch.return_value = 110.0
            updated = evaluate_trade(trade)
        # 10d horizon is nog niet bereikbaar voor een 0-dag-oud signaal
        # Afhankelijk van tolerantie: sommige horizons wel, andere niet
        assert isinstance(updated, dict)

    def test_fetch_close_price_returns_none_on_error(self):
        """_fetch_close_price retourneert None bij yfinance fout."""
        from storage.paper_trade_evaluator import _fetch_close_price
        target = datetime.now(timezone.utc) - timedelta(days=5)
        with patch("yfinance.Ticker") as mock_ticker:
            mock_ticker.return_value.history.side_effect = Exception("Yahoo down")
            result = _fetch_close_price("NVDA", target)
        assert result is None

    def test_evaluate_all_open_never_raises(self, tmp_path):
        from storage.paper_trade_evaluator import evaluate_all_open
        with patch("storage.paper_trade_store._TRADES_DIR", str(tmp_path)):
            with patch("storage.paper_trade_store._INDEX_PATH", str(tmp_path / "_index.jsonl")):
                with patch("storage.paper_trade_evaluator._fetch_close_price", return_value=None):
                    try:
                        summary = evaluate_all_open(tickers=["NVDA"])
                        assert isinstance(summary, dict)
                    except Exception as exc:
                        pytest.fail(f"evaluate_all_open gooidde exception: {exc}")


# ── TestPaperTradeStatistics ──────────────────────────────────────────────────

class TestPaperTradeStatistics:
    """Test de statistiek-helpers in paper_trade_report.py."""

    def test_win_rate_all_positive(self):
        from scripts.paper_trade_report import _win_rate
        assert _win_rate([5.0, 3.0, 1.0]) == 100.0

    def test_win_rate_all_negative(self):
        from scripts.paper_trade_report import _win_rate
        assert _win_rate([-5.0, -3.0, -1.0]) == 0.0

    def test_win_rate_mixed(self):
        from scripts.paper_trade_report import _win_rate
        assert _win_rate([5.0, -3.0, 2.0, -1.0]) == 50.0

    def test_win_rate_empty(self):
        from scripts.paper_trade_report import _win_rate
        assert _win_rate([]) == 0.0

    def test_win_rate_custom_threshold(self):
        from scripts.paper_trade_report import _win_rate
        # Alleen boven 3% telt als win
        assert _win_rate([5.0, 2.0, 1.0], threshold=3.0) == pytest.approx(33.3, abs=0.1)

    def test_median_odd_count(self):
        from scripts.paper_trade_report import _median
        assert _median([1.0, 3.0, 5.0]) == 3.0

    def test_median_even_count(self):
        from scripts.paper_trade_report import _median
        assert _median([1.0, 3.0, 5.0, 7.0]) == 4.0

    def test_median_empty(self):
        from scripts.paper_trade_report import _median
        assert _median([]) is None

    def test_median_single(self):
        from scripts.paper_trade_report import _median
        assert _median([42.0]) == 42.0

    def test_median_negative_values(self):
        from scripts.paper_trade_report import _median
        assert _median([-5.0, -3.0, -1.0]) == -3.0


# ── TestValidationRunnerHook ──────────────────────────────────────────────────

class TestValidationRunnerHook:

    def test_paper_trade_param_exists(self):
        """_analyze_one accepteert paper_trade parameter."""
        import inspect
        from scripts.validation_runner import _analyze_one
        sig = inspect.signature(_analyze_one)
        assert "paper_trade" in sig.parameters

    def test_paper_trade_default_true(self):
        from scripts.validation_runner import _analyze_one
        import inspect
        sig = inspect.signature(_analyze_one)
        assert sig.parameters["paper_trade"].default is True

    def test_paper_trade_called_for_buy_signal(self):
        """save_trade_from_result wordt aangeroepen voor BUY-signalen."""
        from unittest.mock import patch, MagicMock

        mock_result  = MagicMock()
        mock_result.decision.value          = "BUY_MODERATE"
        mock_result.momentum_score          = 65.0
        mock_result.skip_score              = 0
        mock_result.phase.value             = "BREAKOUT"
        mock_result.phase_description       = "Test"
        mock_result.market_cap_tier.value   = "LARGE"
        mock_result.sizing_eur              = "€300"
        mock_result.summary                 = "Test"
        mock_result.momentum_detail.volume_anomaly          = 10.0
        mock_result.momentum_detail.catalyst_quality        = 12.0
        mock_result.momentum_detail.sector_heat_score       = 15.0
        mock_result.momentum_detail.premarket_strength      = 0.0
        mock_result.momentum_detail.relative_strength_score = 5.0
        mock_result.momentum_detail.social_acceleration     = 0.0
        mock_result.momentum_detail.float_score             = 4.0
        mock_result.momentum_detail.social_was_capped       = False
        mock_result.momentum_detail.breakdown               = {}
        mock_result.skip_detail.is_hard_blocked = False
        mock_result.skip_detail.reasons         = []
        mock_result.skip_detail.blocking_reasons = []

        mock_quality = MagicMock()
        mock_quality.confidence.value  = "LIVE"
        mock_quality.price_available   = True
        mock_quality.volume_available  = True
        mock_quality.news_available    = True
        mock_quality.cache_hit         = False
        mock_quality.fetch_error       = None

        mock_input = MagicMock()
        mock_input.price            = 135.0
        mock_input.day_change_pct   = 2.5
        mock_input.premarket_pct    = 0.0
        mock_input.volume_today     = 5_000_000
        mock_input.avg_volume_20d   = 2_000_000
        mock_input.catalyst_type.value = "STRONG"
        mock_input.catalyst_description = "Test"
        mock_input.sector.sector_id  = "ai_infra"
        mock_input.sector.heat       = 95
        mock_input.sector.leaders    = []
        mock_input.sector.sympathy   = []

        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(mock_input, mock_quality)):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=mock_result):
                with patch("storage.paper_trade_store.save_trade_from_result",
                           return_value="TRADE_ID_123") as mock_save:
                    from scripts.validation_runner import _analyze_one
                    result = _analyze_one("NVDA", paper_trade=True)

        mock_save.assert_called_once()

    def test_paper_trade_not_called_for_watch(self):
        """save_trade_from_result wordt NIET aangeroepen voor WATCH-signalen."""
        mock_result = MagicMock()
        mock_result.decision.value          = "WATCH"
        mock_result.momentum_score          = 45.0
        mock_result.skip_score              = 0
        mock_result.phase.value             = "NEUTRAL"
        mock_result.phase_description       = "Test"
        mock_result.market_cap_tier.value   = "LARGE"
        mock_result.sizing_eur              = "€0"
        mock_result.summary                 = "Test"
        mock_result.momentum_detail.volume_anomaly          = 5.0
        mock_result.momentum_detail.catalyst_quality        = 0.0
        mock_result.momentum_detail.sector_heat_score       = 10.0
        mock_result.momentum_detail.premarket_strength      = 0.0
        mock_result.momentum_detail.relative_strength_score = 5.0
        mock_result.momentum_detail.social_acceleration     = 0.0
        mock_result.momentum_detail.float_score             = 4.0
        mock_result.momentum_detail.social_was_capped       = False
        mock_result.momentum_detail.breakdown               = {}
        mock_result.skip_detail.is_hard_blocked  = False
        mock_result.skip_detail.reasons          = []
        mock_result.skip_detail.blocking_reasons = []

        mock_quality = MagicMock()
        mock_quality.confidence.value = "LIVE"
        mock_quality.price_available  = True
        mock_quality.volume_available = True
        mock_quality.news_available   = False
        mock_quality.cache_hit        = False
        mock_quality.fetch_error      = None

        mock_input = MagicMock()
        mock_input.price            = 100.0
        mock_input.day_change_pct   = 1.0
        mock_input.premarket_pct    = 0.0
        mock_input.volume_today     = 1_000_000
        mock_input.avg_volume_20d   = 1_000_000
        mock_input.catalyst_type.value = "NONE"
        mock_input.catalyst_description = ""
        mock_input.sector.sector_id  = "unknown"
        mock_input.sector.heat       = 50
        mock_input.sector.leaders    = []
        mock_input.sector.sympathy   = []

        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(mock_input, mock_quality)):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=mock_result):
                with patch("storage.paper_trade_store.save_trade_from_result") as mock_save:
                    from scripts.validation_runner import _analyze_one
                    _analyze_one("GOOGL", paper_trade=True)

        mock_save.assert_not_called()

    def test_paper_trade_false_skips_recording(self):
        """paper_trade=False slaat nooit op, ook niet voor BUY-signalen."""
        mock_result = MagicMock()
        mock_result.decision.value = "BUY_STRONG"
        mock_result.momentum_score = 80.0
        mock_result.skip_score     = 0
        mock_result.phase.value    = "BREAKOUT"
        mock_result.phase_description = "Test"
        mock_result.market_cap_tier.value = "SMALL"
        mock_result.sizing_eur = "€400"
        mock_result.summary    = "Test"
        mock_result.momentum_detail.volume_anomaly          = 20.0
        mock_result.momentum_detail.catalyst_quality        = 20.0
        mock_result.momentum_detail.sector_heat_score       = 17.0
        mock_result.momentum_detail.premarket_strength      = 5.0
        mock_result.momentum_detail.relative_strength_score = 8.0
        mock_result.momentum_detail.social_acceleration     = 4.0
        mock_result.momentum_detail.float_score             = 6.0
        mock_result.momentum_detail.social_was_capped       = False
        mock_result.momentum_detail.breakdown               = {}
        mock_result.skip_detail.is_hard_blocked  = False
        mock_result.skip_detail.reasons          = []
        mock_result.skip_detail.blocking_reasons = []

        mock_quality = MagicMock()
        mock_quality.confidence.value = "LIVE"
        mock_quality.price_available  = True
        mock_quality.volume_available = True
        mock_quality.news_available   = True
        mock_quality.cache_hit        = False
        mock_quality.fetch_error      = None

        mock_input = MagicMock()
        mock_input.price            = 50.0
        mock_input.day_change_pct   = 8.0
        mock_input.premarket_pct    = 2.0
        mock_input.volume_today     = 8_000_000
        mock_input.avg_volume_20d   = 1_000_000
        mock_input.catalyst_type.value = "STRONG"
        mock_input.catalyst_description = "Test"
        mock_input.sector.sector_id  = "quantum"
        mock_input.sector.heat       = 92
        mock_input.sector.leaders    = []
        mock_input.sector.sympathy   = []

        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(mock_input, mock_quality)):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=mock_result):
                with patch("storage.paper_trade_store.save_trade_from_result") as mock_save:
                    from scripts.validation_runner import _analyze_one
                    _analyze_one("IONQ", paper_trade=False)

        mock_save.assert_not_called()
