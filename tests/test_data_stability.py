"""
tests/test_data_stability.py
Data Stability Layer Tests — v2.1

Test coverage:
    TestDataConfidence          DataConfidence labels per veldsituatie
    TestTickerSnapshotSchema    Pydantic validatie + edge cases
    TestScoringResponseSchema   Schema structuur + veldvalidatie
    TestApiErrorSchema          Foutresponse formaten
    TestMissingFieldHandling    Engine scoort ook zonder complete data
    TestRetryBehavior           Retry + backoff logica
    TestRateLimitHandling       429/rate-limit detectie
    TestCacheArchitecture       Cache prep: disabled, stats, cooldown
    TestSectorStateSchema       SectorState validatie
    TestAssemblerMissingData    Assembler graceful bij ontbrekende velden
"""

import pytest
import time
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone

from schemas.ticker_snapshot import (
    TickerSnapshot, DataConfidence, determine_confidence
)
from schemas.scoring_response import DataQuality, ScoringResponse
from schemas.api_error import (
    ApiError, ErrorCode, invalid_ticker, ticker_not_found,
    rate_limited, fetch_error
)
from schemas.sector_state import SectorState
from cache.market_cache import (
    get_cached, set_cached, set_cooldown, is_cooling_down,
    clear_cache, cache_stats, CACHE_ENABLED
)
from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _snapshot(price=50.0, market_cap=1e9, float_shares=40_000_000,
              premarket_available=True, error=None, volume=3_000_000):
    conf = determine_confidence(
        price=price, volume_today=volume, market_cap=market_cap,
        float_shares=float_shares, premarket_available=premarket_available,
        error=error,
    )
    return TickerSnapshot(
        ticker="TEST",
        timestamp=datetime.now(timezone.utc),
        confidence=conf,
        price=price,
        prev_close=price - 1,
        day_change_pct=2.0,
        premarket_pct=5.0 if premarket_available else 0.0,
        premarket_available=premarket_available,
        volume_today=volume,
        avg_volume_20d=500_000,
        market_cap=market_cap,
        float_shares=float_shares,
        error=error,
    )


# ── DATA CONFIDENCE TESTS ─────────────────────────────────────────────────────

class TestDataConfidence:

    def test_live_when_all_fields_present(self):
        conf = determine_confidence(
            price=50.0, volume_today=1_000_000, market_cap=1e9,
            float_shares=40_000_000, premarket_available=True, error=None,
        )
        assert conf == DataConfidence.LIVE

    def test_missing_when_price_zero(self):
        conf = determine_confidence(
            price=0.0, volume_today=0, market_cap=None,
            float_shares=None, premarket_available=False, error=None,
        )
        assert conf == DataConfidence.MISSING

    def test_missing_when_error_present(self):
        conf = determine_confidence(
            price=50.0, volume_today=1_000_000, market_cap=1e9,
            float_shares=40_000_000, premarket_available=True,
            error="Connection timeout",
        )
        assert conf == DataConfidence.MISSING

    def test_partial_when_float_and_market_cap_missing(self):
        conf = determine_confidence(
            price=50.0, volume_today=1_000_000, market_cap=None,
            float_shares=None, premarket_available=False, error=None,
        )
        assert conf == DataConfidence.PARTIAL

    def test_live_when_only_premarket_missing(self):
        """Alleen ontbrekende premarket = LIVE (slechts 1 optioneel veld)."""
        conf = determine_confidence(
            price=50.0, volume_today=1_000_000, market_cap=1e9,
            float_shares=40_000_000, premarket_available=False, error=None,
        )
        assert conf == DataConfidence.LIVE

    def test_partial_requires_two_or_more_missing(self):
        """PARTIAL pas bij ≥ 2 ontbrekende optionele velden."""
        conf_one_missing = determine_confidence(
            price=50.0, volume_today=1_000_000, market_cap=None,
            float_shares=40_000_000, premarket_available=True, error=None,
        )
        conf_two_missing = determine_confidence(
            price=50.0, volume_today=1_000_000, market_cap=None,
            float_shares=None, premarket_available=True, error=None,
        )
        assert conf_one_missing == DataConfidence.LIVE
        assert conf_two_missing == DataConfidence.PARTIAL


# ── TICKER SNAPSHOT SCHEMA TESTS ─────────────────────────────────────────────

class TestTickerSnapshotSchema:

    def test_ticker_normalized_to_uppercase(self):
        snap = _snapshot()
        snap2 = snap.model_copy(update={"ticker": "nvda"})
        # Validator werkt bij constructie
        s = TickerSnapshot(
            ticker="nvda",
            timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.LIVE,
            price=50.0, prev_close=49.0, day_change_pct=2.0,
            premarket_pct=0.0, premarket_available=False,
            volume_today=1_000_000, avg_volume_20d=500_000,
        )
        assert s.ticker == "NVDA"

    def test_negative_price_clamped_to_zero(self):
        s = TickerSnapshot(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.MISSING,
            price=-5.0, prev_close=-1.0, day_change_pct=0.0,
            premarket_pct=0.0, premarket_available=False,
            volume_today=0, avg_volume_20d=1,
        )
        assert s.price == 0.0
        assert s.prev_close == 0.0

    def test_negative_volume_clamped_to_zero(self):
        s = TickerSnapshot(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.MISSING,
            price=50.0, prev_close=49.0, day_change_pct=0.0,
            premarket_pct=0.0, premarket_available=False,
            volume_today=-100, avg_volume_20d=-200,
        )
        assert s.volume_today == 0
        assert s.avg_volume_20d == 0

    def test_invalid_float_shares_becomes_none(self):
        s = TickerSnapshot(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.PARTIAL,
            price=50.0, prev_close=49.0, day_change_pct=0.0,
            premarket_pct=0.0, premarket_available=False,
            volume_today=1_000_000, avg_volume_20d=500_000,
            float_shares=-500_000,  # ongeldig
        )
        assert s.float_shares is None

    def test_invalid_market_cap_becomes_none(self):
        s = TickerSnapshot(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.PARTIAL,
            price=50.0, prev_close=49.0, day_change_pct=0.0,
            premarket_pct=0.0, premarket_available=False,
            volume_today=1_000_000, avg_volume_20d=500_000,
            market_cap=-1_000.0,  # ongeldig
        )
        assert s.market_cap is None

    def test_has_timestamp(self):
        s = _snapshot()
        assert s.timestamp is not None
        assert isinstance(s.timestamp, datetime)

    def test_has_confidence_label(self):
        s = _snapshot()
        assert s.confidence in DataConfidence.__members__.values()


# ── SCORING RESPONSE SCHEMA TESTS ─────────────────────────────────────────────

class TestScoringResponseSchema:

    def test_data_quality_has_confidence(self):
        dq = DataQuality(
            price_available=True, volume_available=True,
            float_available=True, premarket_available=False,
            news_available=False, social_available=False,
            sec_check_automated=False, confidence=DataConfidence.LIVE,
        )
        assert dq.confidence == DataConfidence.LIVE

    def test_data_quality_optional_error_is_none_by_default(self):
        dq = DataQuality(
            price_available=True, volume_available=True,
            float_available=True, premarket_available=True,
            news_available=False, social_available=False,
            sec_check_automated=False, confidence=DataConfidence.LIVE,
        )
        assert dq.fetch_error is None

    def test_data_quality_retries_default_zero(self):
        dq = DataQuality(
            price_available=True, volume_available=True,
            float_available=True, premarket_available=True,
            news_available=False, social_available=False,
            sec_check_automated=False, confidence=DataConfidence.LIVE,
        )
        assert dq.retries_used == 0


# ── API ERROR SCHEMA TESTS ────────────────────────────────────────────────────

class TestApiErrorSchema:

    def test_invalid_ticker_error(self):
        err = invalid_ticker("123BAD!")
        assert err.error == ErrorCode.INVALID_TICKER
        assert "123BAD!" in err.message

    def test_ticker_not_found_error(self):
        err = ticker_not_found("XYZNOTREAL")
        assert err.error == ErrorCode.TICKER_NOT_FOUND
        assert "XYZNOTREAL" in err.message

    def test_rate_limited_error(self):
        err = rate_limited("NVDA")
        assert err.error == ErrorCode.RATE_LIMITED
        assert err.hint is not None

    def test_fetch_error(self):
        err = fetch_error("AAPL", "Connection refused")
        assert err.error == ErrorCode.FETCH_ERROR
        assert "AAPL" in err.message

    def test_api_error_is_serializable(self):
        err = invalid_ticker("BAD")
        d = err.model_dump()
        assert "error" in d
        assert "message" in d
        assert isinstance(d["error"], str)  # Enum → str


# ── MISSING FIELD HANDLING ────────────────────────────────────────────────────

class TestMissingFieldHandling:
    """Engine moet altijd scoren, ook bij ontbrekende velden."""

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_missing_market_cap_doesnt_crash(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _snapshot(market_cap=None)
        r = client.get("/analyze/TEST")
        assert r.status_code == 200
        assert r.json()["decision"] is not None

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_missing_float_doesnt_crash(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _snapshot(float_shares=None)
        r = client.get("/analyze/TEST")
        assert r.status_code == 200
        assert r.json()["data_quality"]["float_available"] is False

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_missing_premarket_defaults_to_zero(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _snapshot(premarket_available=False)
        r = client.get("/analyze/TEST")
        assert r.status_code == 200
        assert r.json()["data_quality"]["premarket_available"] is False

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_zero_volume_falls_back_to_avg(self, mock_spy, mock_news, mock_snap):
        """volume_today=0 → assembler gebruikt avg_volume als proxy."""
        mock_snap.return_value = _snapshot(volume=0)
        r = client.get("/analyze/TEST")
        assert r.status_code == 200  # geen crash

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_partial_data_still_returns_score(self, mock_spy, mock_news, mock_snap):
        """PARTIAL confidence → score teruggeven, geen 422."""
        mock_snap.return_value = _snapshot(
            market_cap=None, float_shares=None,
            premarket_available=False,
        )
        r = client.get("/analyze/TEST")
        assert r.status_code == 200
        assert 0 <= r.json()["momentum_score"] <= 100

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_missing_float_uses_neutral_score(self, mock_spy, mock_news, mock_snap):
        """float=None → float_score = 4.0 (neutraal, niet 0)."""
        mock_snap.return_value = _snapshot(float_shares=None)
        r = client.get("/analyze/TEST")
        breakdown = r.json()["momentum_detail"]["breakdown"]
        float_key = next(k for k in breakdown if "Float" in k)
        assert "onbekend" in breakdown[float_key].lower()


# ── RETRY BEHAVIOR TESTS ──────────────────────────────────────────────────────

class TestRetryBehavior:
    """Retry + exponential backoff in yahoo_client."""

    def setup_method(self):
        from cache.market_cache import clear_cache
        clear_cache()

    def test_successful_first_try_no_retry(self):
        from data.yahoo_client import _MAX_RETRIES, _BACKOFF_SECS
        assert _MAX_RETRIES == 3
        assert _BACKOFF_SECS[0] == 0.0

    def test_backoff_increases_per_attempt(self):
        from data.yahoo_client import _BACKOFF_SECS
        for i in range(len(_BACKOFF_SECS) - 1):
            assert _BACKOFF_SECS[i] <= _BACKOFF_SECS[i + 1]

    def test_retries_used_zero_on_success(self):
        with patch("data.yahoo_client._fetch_once") as mock_fetch:
            snap = TickerSnapshot(
                ticker="TEST",
                timestamp=datetime.now(timezone.utc),
                confidence=DataConfidence.LIVE,
                price=50.0, prev_close=49.0, day_change_pct=2.0,
                premarket_pct=0.0, premarket_available=False,
                volume_today=1_000_000, avg_volume_20d=500_000,
                retries_used=0,
            )
            mock_fetch.return_value = snap
            from data.yahoo_client import get_snapshot
            result = get_snapshot("TEST_FRESH")
            assert result.retries_used == 0

    def test_missing_snapshot_when_no_cache_and_all_fail(self):
        """Na 3 mislukte pogingen én geen cache → MISSING snapshot."""
        from cache.market_cache import clear_cache, invalidate
        invalidate("NOCACHE_TICKER")  # Verzeker lege cache voor deze ticker
        with patch("data.yahoo_client._fetch_once",
                   side_effect=Exception("Network error")):
            with patch("time.sleep"):
                from data.yahoo_client import get_snapshot
                result = get_snapshot("NOCACHE_TICKER")
                assert result.confidence == DataConfidence.MISSING
                assert result.error is not None
                assert result.price == 0.0

    def test_stale_cache_returned_when_live_fails(self):
        """Als live faalt maar cache beschikbaar → stale snapshot terug."""
        from cache.market_cache import set_cached
        set_cached("STALEFB", {"price": 42.0, "volume_today": 1_000_000,
                               "avg_volume_20d": 500_000, "prev_close": 41.0,
                               "day_change_pct": 2.4, "premarket_pct": 0.0,
                               "premarket_available": False,
                               "market_cap": 1e9, "float_shares": None},
                   ttl_seconds=1)
        with patch("data.yahoo_client._fetch_once",
                   side_effect=Exception("Network error")):
            with patch("time.sleep"):
                from data.yahoo_client import get_snapshot
                result = get_snapshot("STALEFB")
                # Cache fallback levert data — niet MISSING
                assert result.price > 0
                assert result.cache_hit is True

    def test_never_raises_exception(self):
        from cache.market_cache import invalidate
        invalidate("EXTEST")
        with patch("data.yahoo_client._fetch_once",
                   side_effect=RuntimeError("Fatal error")):
            with patch("time.sleep"):
                from data.yahoo_client import get_snapshot
                try:
                    result = get_snapshot("EXTEST")
                    assert isinstance(result, TickerSnapshot)
                except Exception:
                    pytest.fail("get_snapshot gooidde een exception — dit mag niet")


# ── RATE LIMIT HANDLING TESTS ─────────────────────────────────────────────────

class TestRateLimitHandling:

    def test_rate_limit_detected_on_429_string(self):
        from data.yahoo_client import _is_rate_limited
        assert _is_rate_limited(Exception("HTTP 429: Too Many Requests"))
        assert _is_rate_limited(Exception("rate limit exceeded"))

    def test_rate_limit_not_triggered_on_generic_error(self):
        from data.yahoo_client import _is_rate_limited
        assert not _is_rate_limited(Exception("Connection refused"))
        assert not _is_rate_limited(Exception("Ticker not found"))

    def test_auth_error_detected_on_403(self):
        from data.yahoo_client import _is_auth_error
        assert _is_auth_error(Exception("HTTP Error 403: Forbidden"))
        assert _is_auth_error(Exception("403 forbidden"))

    def test_rate_limit_skips_remaining_retries(self):
        """Bij rate limit: stop na eerste detectie."""
        from cache.market_cache import invalidate
        invalidate("RATELIMITEDTEST")
        call_count = 0

        def raise_rate_limit(ticker):
            nonlocal call_count
            call_count += 1
            raise Exception("429 too many requests")

        with patch("data.yahoo_client._fetch_once", side_effect=raise_rate_limit):
            with patch("time.sleep"):
                from data.yahoo_client import get_snapshot
                result = get_snapshot("RATELIMITEDTEST")
                assert call_count == 1  # slechts één poging bij rate limit
                assert result.confidence == DataConfidence.MISSING

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_api_returns_429_on_rate_limit(self, mock_spy, mock_news, mock_snap):
        """Rate limit in fetch_error → 429 response van API."""
        from schemas.ticker_snapshot import DataConfidence
        snap = TickerSnapshot(
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.MISSING,
            price=0.0, prev_close=0.0, day_change_pct=0.0,
            premarket_pct=0.0, premarket_available=False,
            volume_today=0, avg_volume_20d=1,
            error="HTTP 429: rate limit exceeded",
        )
        mock_snap.return_value = snap
        r = client.get("/analyze/TEST")
        assert r.status_code == 429
        assert r.json()["detail"]["error"] == "RATE_LIMITED"


# ── CACHE ARCHITECTURE TESTS ──────────────────────────────────────────────────

class TestCacheArchitecture:
    """Cache is ACTIEF in v2.2 — architectuur gevalideerd."""

    def setup_method(self):
        clear_cache()

    def test_cache_enabled_in_v22(self):
        """Cache is geactiveerd in v2.2."""
        assert CACHE_ENABLED is True

    def test_get_cached_returns_none_when_miss(self):
        """Cache miss → None."""
        result = get_cached("NOTCACHED_TICKER")
        assert result is None

    def test_set_and_get_roundtrip(self):
        """Cache roundtrip: opslaan → ophalen."""
        set_cached("ROUNDTRIP", {"price": 50.0}, ttl_seconds=60)
        result = get_cached("ROUNDTRIP")
        assert result is not None
        assert result.data["price"] == 50.0

    def test_set_cooldown_and_check(self):
        """Cooldown registratie werkt."""
        set_cooldown("NVDA", seconds=60)
        assert is_cooling_down("NVDA")

    def test_no_cooldown_by_default(self):
        assert not is_cooling_down("RANDOMTICKER_NOCOOLDOWN")

    def test_cache_stats_returns_dict(self):
        stats = cache_stats()
        assert "enabled" in stats
        assert "total_entries" in stats
        assert stats["enabled"] is True

    def test_clear_cache_single_ticker(self):
        set_cached("CLEARSINGLE", {"price": 10.0})
        clear_cache("CLEARSINGLE")
        assert get_cached("CLEARSINGLE") is None

    def test_clear_cache_all(self):
        set_cached("A", {"price": 1.0})
        set_cached("B", {"price": 2.0})
        clear_cache()
        assert get_cached("A") is None
        assert get_cached("B") is None


# ── SECTOR STATE SCHEMA TESTS ─────────────────────────────────────────────────

class TestSectorStateSchema:

    def test_valid_sector_state(self):
        s = SectorState(
            sector_id="quantum",
            label="QUANTUM",
            heat=92,
            status="HOT",
            phase=3,
            leaders=["IONQ", "QBTS"],
            sympathy=["RGTI"],
        )
        assert s.heat == 92
        assert "IONQ" in s.leaders

    def test_heat_clamped_to_0_100(self):
        s = SectorState(
            sector_id="test", label="TEST",
            heat=150,   # over max
            status="HOT", phase=1,
        )
        assert s.heat == 100

    def test_heat_clamped_min_zero(self):
        s = SectorState(
            sector_id="test", label="TEST",
            heat=-10,   # onder min
            status="DORMANT", phase=1,
        )
        assert s.heat == 0

    def test_tickers_normalized_uppercase(self):
        s = SectorState(
            sector_id="test", label="TEST",
            heat=75, status="HOT", phase=1,
            leaders=["nvda", "avgo"],
            sympathy=["crdo"],
        )
        assert "NVDA" in s.leaders
        assert "CRDO" in s.sympathy

    def test_is_stale_returns_false_in_v21(self):
        """is_stale altijd False in v2.1 (timestamp parsing in v2.2)."""
        s = SectorState(
            sector_id="test", label="TEST",
            heat=75, status="HOT", phase=1,
        )
        assert s.is_stale() is False


# ── ASSEMBLER MISSING DATA TESTS ──────────────────────────────────────────────

class TestAssemblerMissingData:

    def test_safe_market_cap_default_when_none(self):
        from data.assembler import _safe_market_cap
        snap = _snapshot(market_cap=None)
        result = _safe_market_cap(snap)
        assert result == 1_000_000_000  # $1B default

    def test_safe_market_cap_uses_real_value(self):
        from data.assembler import _safe_market_cap
        snap = _snapshot(market_cap=5_000_000_000)
        result = _safe_market_cap(snap)
        assert result == 5_000_000_000

    def test_safe_volume_fallback_to_avg(self):
        from data.assembler import _safe_volume
        snap = _snapshot(volume=0)
        vol, avg = _safe_volume(snap)
        # volume=0 → vol en avg zijn allebei avg_volume_20d
        assert vol == avg
        assert vol > 0

    def test_safe_volume_uses_real_volume(self):
        from data.assembler import _safe_volume
        snap = _snapshot(volume=2_000_000)
        vol, avg = _safe_volume(snap)
        assert vol == 2_000_000
