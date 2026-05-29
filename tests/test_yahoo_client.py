"""
tests/test_yahoo_client.py
Yahoo Client Fallback Tests — v1.0

Test coverage:
    TestHistoryFallback         history() pad wordt gebruikt als fast_info faalt
    TestFastInfoSuccess         normaal pad: fast_info werkt
    TestFetchErrorLogging       exception type + message worden gelogd
    TestBothPathsFail           RuntimeError als beide paden falen
    TestGetSnapshotFallback     get_snapshot() vangt RuntimeError op en retourneert MISSING
    TestFetchFromHistoryHelper  _fetch_from_history() edge cases
"""

import pytest
import pandas as pd
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone

from schemas.ticker_snapshot import TickerSnapshot, DataConfidence


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_history_df(closes=(150.0, 148.0, 152.0), volumes=(1_000_000, 900_000, 1_100_000)):
    """Minimale DataFrame die t.history() zou retourneren."""
    import pandas as pd
    return pd.DataFrame({
        "Open":   closes,
        "High":   closes,
        "Low":    closes,
        "Close":  closes,
        "Volume": volumes,
    })


def _make_fast_info_mock(last_price=150.0, previous_close=148.0,
                         market_cap=3e12, shares_outstanding=24_400_000_000):
    fi = MagicMock()
    fi.last_price          = last_price
    fi.previous_close      = previous_close
    fi.market_cap          = market_cap
    fi.shares_outstanding  = shares_outstanding
    fi.pre_market_price    = None
    return fi


def _make_history_mock(df):
    """history() retourneert een DataFrame — via side_effect."""
    return df


# ── TestFastInfoSuccess ────────────────────────────────────────────────────────

class TestFastInfoSuccess:
    """Normaal pad: fast_info werkt — geen fallback nodig."""

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_returns_ticker_snapshot(self, mock_ticker_cls, mock_session):
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        ticker_obj = MagicMock()
        ticker_obj.fast_info = _make_fast_info_mock(last_price=152.0, previous_close=148.0)
        ticker_obj.history.return_value = _make_history_df()
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        snap = _fetch_once("NVDA")

        assert isinstance(snap, TickerSnapshot)
        assert snap.ticker == "NVDA"
        assert snap.price == 152.0

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_no_fallback_called_when_fast_info_ok(self, mock_ticker_cls, mock_session):
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        ticker_obj = MagicMock()
        ticker_obj.fast_info = _make_fast_info_mock()
        ticker_obj.history.return_value = _make_history_df()
        mock_ticker_cls.return_value = ticker_obj

        with patch("data.yahoo_client._fetch_from_history") as mock_fallback:
            from data.yahoo_client import _fetch_once
            _fetch_once("NVDA")
            mock_fallback.assert_not_called()

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_day_change_pct_calculated_correctly(self, mock_ticker_cls, mock_session):
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        ticker_obj = MagicMock()
        # price=110, prev=100 → change=+10%
        ticker_obj.fast_info = _make_fast_info_mock(last_price=110.0, previous_close=100.0)
        ticker_obj.history.return_value = _make_history_df()
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        snap = _fetch_once("TEST")
        assert snap.day_change_pct == pytest.approx(10.0, abs=0.01)


# ── TestHistoryFallback ────────────────────────────────────────────────────────

class TestHistoryFallback:
    """fast_info faalt → history-fallback wordt gebruikt."""

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_history_fallback_used_when_fast_info_fails(self, mock_ticker_cls, mock_session):
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        # Simuleer KeyError: 'currentTradingPeriod' (yfinance 0.2.36 bug)
        type(fi_mock).last_price = PropertyMock(
            side_effect=KeyError("currentTradingPeriod")
        )

        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = _make_history_df(
            closes=(145.0, 148.0, 152.0),
            volumes=(800_000, 900_000, 1_000_000),
        )
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        snap = _fetch_once("NVDA")

        assert isinstance(snap, TickerSnapshot)
        assert snap.price == 152.0           # laatste close uit history
        assert snap.prev_close == 148.0      # één rij eerder
        assert snap.volume_today == 1_000_000

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_history_fallback_price_from_last_row(self, mock_ticker_cls, mock_session):
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=ValueError("geen geldige prijs")
        )

        df = _make_history_df(
            closes=(100.0, 102.0, 104.0),
            volumes=(500_000, 600_000, 700_000),
        )
        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = df
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        snap = _fetch_once("AAPL")

        assert snap.price == 104.0
        assert snap.prev_close == 102.0

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_history_fallback_single_row_prev_equals_price(self, mock_ticker_cls, mock_session):
        """Één rij in history → prev_close == price (geen vorige dag)."""
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=RuntimeError("fast_info kapot")
        )

        df = _make_history_df(closes=(90.0,), volumes=(300_000,))
        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = df
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        snap = _fetch_once("TEST")

        # Één rij: prev_close == price
        assert snap.price == 90.0
        assert snap.prev_close == 90.0

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_history_fallback_avg_volume_calculated(self, mock_ticker_cls, mock_session):
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=KeyError("currentTradingPeriod")
        )

        # 3 dagen, gem volume = 600_000
        df = _make_history_df(
            closes=(50.0, 51.0, 52.0),
            volumes=(400_000, 600_000, 800_000),
        )
        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = df
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        snap = _fetch_once("TEST")

        assert snap.avg_volume_20d == 600_000

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_history_fallback_market_cap_none_when_fast_info_fails(
        self, mock_ticker_cls, mock_session
    ):
        """market_cap is None als fast_info faalde — history heeft dit niet."""
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=KeyError("currentTradingPeriod")
        )

        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = _make_history_df()
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        snap = _fetch_once("TEST")

        assert snap.market_cap is None
        assert snap.float_shares is None


# ── TestBothPathsFail ─────────────────────────────────────────────────────────

class TestBothPathsFail:
    """fast_info én history falen → RuntimeError uit _fetch_once."""

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_raises_runtime_error_when_both_fail(self, mock_ticker_cls, mock_session):
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=KeyError("currentTradingPeriod")
        )

        import pandas as pd
        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = pd.DataFrame()  # leeg
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        with pytest.raises(RuntimeError, match="Zowel fast_info als history"):
            _fetch_once("BROKEN")

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_both_fail_history_exception(self, mock_ticker_cls, mock_session):
        """history gooit ook een exception (niet lege df)."""
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=KeyError("currentTradingPeriod")
        )

        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.side_effect = ConnectionError("Yahoo unreachable")
        mock_ticker_cls.return_value = ticker_obj

        from data.yahoo_client import _fetch_once
        with pytest.raises(RuntimeError, match="Zowel fast_info als history"):
            _fetch_once("BROKEN")


# ── TestGetSnapshotFallback ───────────────────────────────────────────────────

class TestGetSnapshotFallback:
    """get_snapshot() mag nooit een exception gooien — altijd TickerSnapshot."""

    def test_get_snapshot_returns_missing_when_both_paths_fail(self):
        """Beide paden falen → TickerSnapshot met MISSING confidence, geen exception."""
        with patch("data.yahoo_client._fetch_once",
                   side_effect=RuntimeError("beide paden kapot")):
            with patch("data.yahoo_client.CACHE_ENABLED", False):
                from data.yahoo_client import get_snapshot
                result = get_snapshot("KAPOT")

        assert isinstance(result, TickerSnapshot)
        assert result.confidence == DataConfidence.MISSING
        assert result.price == 0.0
        assert result.error is not None
        assert "RuntimeError" in result.error

    def test_get_snapshot_error_contains_exception_type(self):
        """Het error-veld bevat het exception type (niet alleen de message)."""
        with patch("data.yahoo_client._fetch_once",
                   side_effect=KeyError("currentTradingPeriod")):
            with patch("data.yahoo_client.CACHE_ENABLED", False):
                from data.yahoo_client import get_snapshot
                result = get_snapshot("BADTICKER")

        assert result.error is not None
        assert "KeyError" in result.error

    def test_get_snapshot_never_raises(self):
        """get_snapshot() gooit nooit een exception, wat er ook misgaat."""
        with patch("data.yahoo_client._fetch_once",
                   side_effect=Exception("willekeurige crash")):
            with patch("data.yahoo_client.CACHE_ENABLED", False):
                from data.yahoo_client import get_snapshot
                try:
                    result = get_snapshot("CRASH")
                    assert isinstance(result, TickerSnapshot)
                except Exception as exc:
                    pytest.fail(f"get_snapshot gooidde een exception: {exc}")


# ── TestFetchFromHistoryHelper ────────────────────────────────────────────────

class TestFetchFromHistoryHelper:
    """Unit tests voor _fetch_from_history() direct."""

    def test_returns_dict_with_required_keys(self):
        df = _make_history_df(
            closes=(100.0, 102.0, 105.0),
            volumes=(1_000_000, 1_100_000, 1_200_000),
        )
        ticker_obj = MagicMock()
        ticker_obj.history.return_value = df

        from data.yahoo_client import _fetch_from_history
        result = _fetch_from_history("TEST", ticker_obj)

        assert result is not None
        assert "price" in result
        assert "prev_close" in result
        assert "volume_today" in result
        assert "avg_volume_20d" in result

    def test_returns_none_on_empty_dataframe(self):
        import pandas as pd
        ticker_obj = MagicMock()
        ticker_obj.history.return_value = pd.DataFrame()

        from data.yahoo_client import _fetch_from_history
        result = _fetch_from_history("EMPTY", ticker_obj)

        assert result is None

    def test_returns_none_when_history_raises(self):
        ticker_obj = MagicMock()
        ticker_obj.history.side_effect = ConnectionError("geblokkeerd")

        from data.yahoo_client import _fetch_from_history
        result = _fetch_from_history("BLOCKED", ticker_obj)

        assert result is None

    def test_avg_volume_at_least_one(self):
        """avg_volume_20d is altijd >= 1, ook bij extreem laag volume."""
        df = _make_history_df(closes=(10.0,), volumes=(0,))
        ticker_obj = MagicMock()
        ticker_obj.history.return_value = df

        from data.yahoo_client import _fetch_from_history
        result = _fetch_from_history("LOWVOL", ticker_obj)

        # Beide paden kapot als volume=0 → None, maar als DataFrame 1 rij heeft
        # met volume=0 → avg = max(0, 1) = 1
        if result is not None:
            assert result["avg_volume_20d"] >= 1


# ── TestFetchErrorLogging ─────────────────────────────────────────────────────

class TestFetchErrorLogging:
    """Exception type + message worden gelogd — niet geslikken."""

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_fast_info_error_is_logged_as_warning(self, mock_ticker_cls, mock_session, caplog):
        import logging
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=KeyError("currentTradingPeriod")
        )
        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = _make_history_df()
        mock_ticker_cls.return_value = ticker_obj

        with caplog.at_level(logging.WARNING, logger="data.yahoo_client"):
            from data.yahoo_client import _fetch_once
            _fetch_once("NVDA")

        # Minimaal één waarschuwing over fast_info
        assert any("fast_info" in r.message for r in caplog.records)

    @patch("data.yahoo_client.get_market_session")
    @patch("yfinance.Ticker")
    def test_log_contains_exception_type(self, mock_ticker_cls, mock_session, caplog):
        import logging
        from data.market_session import MarketSession
        mock_session.return_value = MarketSession.REGULAR

        fi_mock = MagicMock()
        type(fi_mock).last_price = PropertyMock(
            side_effect=KeyError("currentTradingPeriod")
        )
        ticker_obj = MagicMock()
        ticker_obj.fast_info = fi_mock
        ticker_obj.history.return_value = _make_history_df()
        mock_ticker_cls.return_value = ticker_obj

        with caplog.at_level(logging.WARNING, logger="data.yahoo_client"):
            from data.yahoo_client import _fetch_once
            _fetch_once("NVDA")

        all_messages = " ".join(r.message for r in caplog.records)
        assert "KeyError" in all_messages
