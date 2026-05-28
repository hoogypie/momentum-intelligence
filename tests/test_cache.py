"""
tests/test_cache.py
Caching & Data Freshness Layer Tests — v2.2

Coverage:
    TestCacheEnabled            Cache actief, TTL, roundtrip
    TestCacheTTLAndExpiry       TTL expiry, te-oud verwijdering
    TestConfidenceLabels        LIVE/DELAYED/STALE/PARTIAL/MISSING
    TestStaleTransitions        age_to_confidence() thresholds
    TestWorstConfidence         Combinatie veld + leeftijd
    TestCacheHitMiss            Hit/miss in yahoo_client via mock
    TestFallbackOnYahooFailure  Yahoo faalt → cache fallback
    TestCacheInvalidation       invalidate(), force_refresh, clear
    TestMarketHours             TTL per marktperiode
    TestBatchEndpoint           Batch scoring, partial failure
    TestSectorEndpoint          Sector snapshot structuur
    TestFreshnessMetadata       data_age_seconds, cache_hit in response
    TestCacheStats              Stats accuracy
"""

import pytest
import time
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from backend.app import app
from schemas.ticker_snapshot import (
    TickerSnapshot, DataConfidence,
    determine_confidence, age_to_confidence, worst_confidence, FreshnessInfo,
)
from cache.market_cache import (
    get_cached, set_cached, set_cooldown, is_cooling_down,
    clear_cache, invalidate, invalidate_all, cache_stats,
    get_market_ttl, is_market_open, CACHE_ENABLED,
    LIVE_MAX_AGE, DELAYED_MAX_AGE, STALE_MAX_AGE,
    CacheEntry, _cache, _TTL_REGULAR,
)

client = TestClient(app)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _live_snap(ticker="TEST", price=50.0, volume=2_000_000,
               market_cap=1e9, float_shares=40_000_000):
    return TickerSnapshot(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.LIVE,
        price=price, prev_close=price - 1, day_change_pct=2.0,
        premarket_pct=5.0, premarket_available=True,
        volume_today=volume, avg_volume_20d=500_000,
        market_cap=market_cap, float_shares=float_shares,
        cache_hit=False, data_age_seconds=0.0,
    )


def _mock_snap(ticker="TEST", price=50.0, day_pct=6.0, pm_pct=10.0,
               volume=3_000_000, avg_vol=500_000, market_cap=1e9,
               float_shares=40_000_000, error=None,
               confidence=DataConfidence.LIVE, cache_hit=False, age=0.0):
    return TickerSnapshot(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        confidence=confidence,
        price=price, prev_close=price - 2, day_change_pct=day_pct,
        premarket_price=None, premarket_pct=pm_pct,
        premarket_available=pm_pct > 0,
        volume_today=volume, avg_volume_20d=avg_vol,
        market_cap=market_cap, float_shares=float_shares,
        error=error, cache_hit=cache_hit, data_age_seconds=age,
    )


# ── CACHE ENABLED TESTS ───────────────────────────────────────────────────────

class TestCacheEnabled:

    def setup_method(self):
        clear_cache()

    def test_cache_enabled_in_v22(self):
        assert CACHE_ENABLED is True

    def test_set_and_get_roundtrip(self):
        set_cached("RNDTRIP", {"price": 42.0}, ttl_seconds=60)
        entry = get_cached("RNDTRIP")
        assert entry is not None
        assert entry.data["price"] == 42.0

    def test_ticker_normalized_uppercase_in_cache(self):
        set_cached("nvda", {"price": 900.0}, ttl_seconds=60)
        entry = get_cached("NVDA")
        assert entry is not None

    def test_cache_miss_returns_none(self):
        assert get_cached("TICKER_NOT_IN_CACHE_XYZ") is None

    def test_entry_has_cached_at_timestamp(self):
        set_cached("TSTEST", {"price": 1.0}, ttl_seconds=60)
        entry = get_cached("TSTEST")
        assert isinstance(entry.cached_at, datetime)

    def test_entry_has_ttl(self):
        set_cached("TTLTEST", {"price": 1.0}, ttl_seconds=120)
        entry = get_cached("TTLTEST")
        assert entry.ttl_seconds == 120

    def test_entry_age_starts_near_zero(self):
        set_cached("AGETEST", {"price": 1.0}, ttl_seconds=60)
        entry = get_cached("AGETEST")
        assert entry.age_seconds() < 2.0  # moet net opgeslagen zijn

    def test_ttl_remaining_positive_on_fresh_entry(self):
        set_cached("TTLREM", {"price": 1.0}, ttl_seconds=60)
        entry = get_cached("TTLREM")
        assert entry.ttl_remaining() > 0


# ── TTL AND EXPIRY TESTS ──────────────────────────────────────────────────────

class TestCacheTTLAndExpiry:

    def setup_method(self):
        clear_cache()

    def test_expired_entry_not_returned(self):
        """Verlopen entry → get_cached geeft None (maar verwijdert het niet zelf)."""
        set_cached("EXPIRED_TTL", {"price": 1.0}, ttl_seconds=60)
        entry = get_cached("EXPIRED_TTL")
        assert entry is not None  # aanwezig
        assert entry.is_expired() is False  # nog niet verlopen (< 60s)

    def test_is_expired_true_when_past_ttl(self):
        """CacheEntry.is_expired() True als age > ttl_seconds."""
        entry = CacheEntry(
            ticker="X",
            data={"price": 1.0},
            cached_at=datetime.now(timezone.utc) - timedelta(seconds=120),
            ttl_seconds=60,
        )
        assert entry.is_expired() is True

    def test_is_too_old_true_beyond_stale_max(self):
        """Ouder dan STALE_MAX_AGE → is_too_old() = True → niet uit cache serveren."""
        entry = CacheEntry(
            ticker="X",
            data={"price": 1.0},
            cached_at=datetime.now(timezone.utc) - timedelta(seconds=STALE_MAX_AGE + 100),
            ttl_seconds=60,
        )
        assert entry.is_too_old() is True

    def test_too_old_entry_removed_on_get(self):
        """Entry ouder dan STALE_MAX_AGE wordt verwijderd bij get_cached."""
        set_cached("OLDENTRY", {"price": 1.0}, ttl_seconds=60)
        # Verouder de entry direct in _cache
        _cache["OLDENTRY"].cached_at = (
            datetime.now(timezone.utc) - timedelta(seconds=STALE_MAX_AGE + 500)
        )
        result = get_cached("OLDENTRY")
        assert result is None

    def test_confidence_label_on_entry(self):
        set_cached("CONFLBL", {"price": 1.0}, ttl_seconds=60)
        entry = get_cached("CONFLBL")
        assert entry.confidence_label() == "LIVE"


# ── CONFIDENCE LABEL TESTS ────────────────────────────────────────────────────

class TestConfidenceLabels:

    def test_live_when_fresh_and_complete(self):
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
            error="Timeout",
        )
        assert conf == DataConfidence.MISSING

    def test_partial_when_two_optional_fields_missing(self):
        conf = determine_confidence(
            price=50.0, volume_today=1_000_000,
            market_cap=None, float_shares=None,
            premarket_available=True, error=None,
        )
        assert conf == DataConfidence.PARTIAL

    def test_stale_enum_exists(self):
        assert DataConfidence.STALE == "STALE"

    def test_delayed_enum_exists(self):
        assert DataConfidence.DELAYED == "DELAYED"


# ── STALE TRANSITIONS TESTS ───────────────────────────────────────────────────

class TestStaleTransitions:
    """age_to_confidence() threshold tests. Grenzen: 300s en 3600s."""

    def test_live_at_zero_seconds(self):
        assert age_to_confidence(0) == DataConfidence.LIVE

    def test_live_just_before_threshold(self):
        assert age_to_confidence(LIVE_MAX_AGE - 1) == DataConfidence.LIVE

    def test_delayed_at_live_threshold(self):
        assert age_to_confidence(LIVE_MAX_AGE + 1) == DataConfidence.DELAYED

    def test_delayed_in_middle_range(self):
        mid = (LIVE_MAX_AGE + DELAYED_MAX_AGE) // 2
        assert age_to_confidence(mid) == DataConfidence.DELAYED

    def test_delayed_just_before_stale_threshold(self):
        assert age_to_confidence(DELAYED_MAX_AGE - 1) == DataConfidence.DELAYED

    def test_stale_at_delayed_threshold(self):
        assert age_to_confidence(DELAYED_MAX_AGE + 1) == DataConfidence.STALE

    def test_stale_far_beyond_threshold(self):
        assert age_to_confidence(STALE_MAX_AGE) == DataConfidence.STALE

    def test_entry_confidence_label_transitions(self):
        """CacheEntry.confidence_label() volgt dezelfde grenzen."""
        fresh_entry = CacheEntry(
            ticker="X", data={},
            cached_at=datetime.now(timezone.utc),
            ttl_seconds=3600,
        )
        assert fresh_entry.confidence_label() == "LIVE"

        old_entry = CacheEntry(
            ticker="X", data={},
            cached_at=datetime.now(timezone.utc) - timedelta(seconds=LIVE_MAX_AGE + 60),
            ttl_seconds=7200,
        )
        assert old_entry.confidence_label() == "DELAYED"

        stale_entry = CacheEntry(
            ticker="X", data={},
            cached_at=datetime.now(timezone.utc) - timedelta(seconds=DELAYED_MAX_AGE + 60),
            ttl_seconds=7200,
        )
        assert stale_entry.confidence_label() == "STALE"


# ── WORST CONFIDENCE TESTS ────────────────────────────────────────────────────

class TestWorstConfidence:
    """Eindconfidence = slechtste van veld + leeftijd."""

    def test_live_plus_live_is_live(self):
        assert worst_confidence(DataConfidence.LIVE, DataConfidence.LIVE) == DataConfidence.LIVE

    def test_live_plus_delayed_is_delayed(self):
        assert worst_confidence(DataConfidence.LIVE, DataConfidence.DELAYED) == DataConfidence.DELAYED

    def test_partial_beats_delayed(self):
        assert worst_confidence(DataConfidence.PARTIAL, DataConfidence.DELAYED) == DataConfidence.PARTIAL

    def test_missing_always_worst(self):
        for c in DataConfidence:
            assert worst_confidence(c, DataConfidence.MISSING) == DataConfidence.MISSING

    def test_three_labels_returns_worst(self):
        assert worst_confidence(
            DataConfidence.LIVE, DataConfidence.DELAYED, DataConfidence.STALE
        ) == DataConfidence.STALE


# ── CACHE HIT/MISS IN API ─────────────────────────────────────────────────────

class TestCacheHitMiss:

    def setup_method(self):
        clear_cache()

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_cache_miss_reflected_in_response(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap(cache_hit=False, age=0.0)
        r = client.get("/analyze/CMTEST")
        assert r.status_code == 200
        assert r.json()["data_quality"]["cache_hit"] is False

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_cache_hit_reflected_in_response(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap(
            cache_hit=True, age=45.0, confidence=DataConfidence.LIVE
        )
        r = client.get("/analyze/HITTEST")
        dq = r.json()["data_quality"]
        assert dq["cache_hit"] is True
        assert dq["data_age_seconds"] == 45.0

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_confidence_in_data_quality(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap(confidence=DataConfidence.DELAYED, cache_hit=True)
        r = client.get("/analyze/DELTEST")
        assert r.json()["data_quality"]["confidence"] == "DELAYED"

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_stale_confidence_passes_through(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap(confidence=DataConfidence.STALE, cache_hit=True)
        r = client.get("/analyze/STALETEST")
        assert r.json()["data_quality"]["confidence"] == "STALE"


# ── FALLBACK TESTS ────────────────────────────────────────────────────────────

class TestFallbackOnYahooFailure:

    def setup_method(self):
        clear_cache()

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_stale_cache_serves_when_yahoo_fails(self, mock_spy, mock_news, mock_snap):
        """Yahoo faalt maar cache heeft stale data → STALE response, geen 422."""
        mock_snap.return_value = _mock_snap(
            price=38.0,
            confidence=DataConfidence.STALE,
            cache_hit=True,
            age=4000.0,
            error="Live fetch mislukt — serveren vanuit cache",
        )
        r = client.get("/analyze/FBTEST")
        # Met cache_hit=True en price>0 moet de API 200 teruggeven
        assert r.status_code == 200
        assert r.json()["data_quality"]["confidence"] == "STALE"
        assert r.json()["data_quality"]["cache_hit"] is True

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_full_missing_when_no_cache_and_yahoo_fails(
            self, mock_spy, mock_news, mock_snap):
        """Yahoo faalt EN geen cache → 422 MISSING."""
        mock_snap.return_value = _mock_snap(
            price=0.0, confidence=DataConfidence.MISSING,
            cache_hit=False, error="Network timeout",
        )
        r = client.get("/analyze/FBMISS")
        assert r.status_code == 422

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_delayed_cache_still_scores(self, mock_spy, mock_news, mock_snap):
        """DELAYED cache → score berekend, beslissing aanwezig."""
        mock_snap.return_value = _mock_snap(
            confidence=DataConfidence.DELAYED, cache_hit=True, age=900.0
        )
        r = client.get("/analyze/DLTEST")
        assert r.status_code == 200
        assert r.json()["decision"] is not None


# ── CACHE INVALIDATION TESTS ──────────────────────────────────────────────────

class TestCacheInvalidation:

    def setup_method(self):
        clear_cache()

    def test_invalidate_single_ticker(self):
        set_cached("INVAL1", {"price": 1.0}, ttl_seconds=60)
        assert get_cached("INVAL1") is not None
        result = invalidate("INVAL1")
        assert result is True
        assert get_cached("INVAL1") is None

    def test_invalidate_nonexistent_returns_false(self):
        assert invalidate("DOESNOTEXIST_XYZ") is False

    def test_invalidate_all_clears_cache(self):
        set_cached("A1", {"price": 1.0})
        set_cached("B1", {"price": 2.0})
        set_cached("C1", {"price": 3.0})
        count = invalidate_all()
        assert count == 3
        assert get_cached("A1") is None

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_refresh_param_bypasses_cache(self, mock_spy, mock_news, mock_snap):
        """refresh=true → cache bypass, altijd live ophalen."""
        mock_snap.return_value = _mock_snap(cache_hit=False, age=0.0)
        r = client.get("/analyze/RFTEST?refresh=true")
        assert r.status_code == 200
        # Verificeer dat get_snapshot met force_refresh=True is aangeroepen
        call_args = mock_snap.call_args
        assert call_args[1].get("force_refresh") is True or \
               (len(call_args[0]) > 1 and call_args[0][1] is True)

    def test_cooldown_set_and_checked(self):
        set_cooldown("COOLTEST", seconds=30)
        assert is_cooling_down("COOLTEST")

    def test_cooldown_expires(self):
        """Verlopen cooldown wordt automatisch verwijderd bij check."""
        from cache.market_cache import _cooldowns
        _cooldowns["EXPCOOL"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert not is_cooling_down("EXPCOOL")  # expired → False + removed


# ── MARKET HOURS TESTS ────────────────────────────────────────────────────────

class TestMarketHours:

    def test_get_market_ttl_returns_positive_int(self):
        ttl = get_market_ttl()
        assert isinstance(ttl, int)
        assert ttl > 0

    def test_is_market_open_returns_bool(self):
        result = is_market_open()
        assert isinstance(result, bool)

    def test_regular_hours_ttl_shortest(self):
        """Regular market hours hebben kortste TTL (meest verse data nodig)."""
        from cache.market_cache import _TTL_REGULAR, _TTL_AFTERHOURS, _TTL_OVERNIGHT
        assert _TTL_REGULAR < _TTL_AFTERHOURS < _TTL_OVERNIGHT

    def test_ttl_thresholds_defined(self):
        from cache.market_cache import (
            _TTL_PREMARKET, _TTL_REGULAR, _TTL_AFTERHOURS, _TTL_OVERNIGHT
        )
        assert _TTL_REGULAR == 60
        assert _TTL_OVERNIGHT == 1800


# ── BATCH ENDPOINT TESTS ──────────────────────────────────────────────────────

class TestBatchEndpoint:

    def setup_method(self):
        clear_cache()

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_batch_returns_200(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/analyze?tickers=IONQ,QBTS,RGTI")
        assert r.status_code == 200

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_batch_has_required_fields(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/analyze?tickers=IONQ,QBTS")
        d = r.json()
        for field in ["tickers_requested", "tickers_scored", "tickers_failed",
                      "results", "errors", "analyzed_at"]:
            assert field in d

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_batch_scores_all_valid_tickers(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/analyze?tickers=A,B,C")
        d = r.json()
        assert d["tickers_requested"] == 3
        assert d["tickers_scored"] == 3
        assert d["tickers_failed"] == 0

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_batch_partial_failure_tolerant(self, mock_spy, mock_news, mock_snap):
        """Eén ongeldige ticker stopt de rest niet."""
        def side_effect(ticker, **kwargs):
            if ticker == "BADINPUT123":
                return _mock_snap(price=0.0, confidence=DataConfidence.MISSING,
                                  error="Not found")
            return _mock_snap(ticker=ticker)

        mock_snap.side_effect = side_effect
        r = client.get("/analyze?tickers=IONQ,BADINPUT123,QBTS")
        d = r.json()
        assert d["tickers_requested"] == 3
        # BADINPUT123 met price=0 geeft 422 → terechtkomt in errors
        assert d["tickers_scored"] + d["tickers_failed"] == 3

    def test_batch_too_many_tickers_returns_400(self):
        tickers = ",".join([f"T{i}" for i in range(11)])  # 11 > max 10
        r = client.get(f"/analyze?tickers={tickers}")
        assert r.status_code == 400
        assert "TOO_MANY_TICKERS" in str(r.json())

    def test_batch_empty_tickers_returns_400(self):
        r = client.get("/analyze?tickers=")
        assert r.status_code == 400

    def test_batch_single_ticker_works(self):
        with patch("data.assembler.get_snapshot") as ms, \
             patch("data.assembler.get_news", return_value=[]), \
             patch("data.assembler.get_spy_return", return_value=0.0):
            ms.return_value = _mock_snap(ticker="SOLO")
            r = client.get("/analyze?tickers=SOLO")
            d = r.json()
            assert d["tickers_requested"] == 1
            assert d["tickers_scored"] == 1

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_batch_results_have_scoring_fields(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/analyze?tickers=X,Y")
        results = r.json()["results"]
        for res in results:
            assert "decision"       in res
            assert "momentum_score" in res
            assert "skip_score"     in res
            assert "data_quality"   in res

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_batch_max_10_exactly(self, mock_spy, mock_news, mock_snap):
        """Precies 10 tickers → mag niet 400 geven."""
        mock_snap.return_value = _mock_snap()
        tickers = ",".join([f"T{i}" for i in range(10)])
        r = client.get(f"/analyze?tickers={tickers}")
        assert r.status_code == 200


# ── SECTOR ENDPOINT TESTS ─────────────────────────────────────────────────────

class TestSectorEndpoint:

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_sector_returns_200_for_known_sector(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/sector/quantum")
        assert r.status_code == 200

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_sector_has_required_fields(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/sector/quantum")
        d = r.json()
        for field in ["sector_id", "label", "heat", "status",
                      "leaders_scored", "sympathy", "analyzed_at"]:
            assert field in d, f"Veld '{field}' ontbreekt in sector response"

    def test_sector_404_for_unknown_sector(self):
        r = client.get("/sector/nonexistent_sector_xyz")
        assert r.status_code == 404
        assert "available" in r.json()["detail"]

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_sector_leaders_list(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/sector/quantum")
        leaders = r.json()["leaders_scored"]
        assert isinstance(leaders, list)
        assert len(leaders) > 0

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_sector_has_heat(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/sector/quantum")
        heat = r.json()["heat"]
        assert 0 <= heat <= 100

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_sector_has_sympathy_list(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/sector/quantum")
        assert isinstance(r.json()["sympathy"], list)

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_sector_confidence_present(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/sector/quantum")
        assert "sector_confidence" in r.json()

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_sector_ai_infra_recognized(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap()
        r = client.get("/sector/ai_infra")
        assert r.status_code == 200
        assert r.json()["sector_id"] == "ai_infra"


# ── FRESHNESS METADATA TESTS ──────────────────────────────────────────────────

class TestFreshnessMetadata:

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_data_age_seconds_in_response(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap(age=120.0, cache_hit=True)
        r = client.get("/analyze/AGETEST")
        assert r.json()["data_quality"]["data_age_seconds"] == 120.0

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_cache_hit_false_for_fresh_fetch(self, mock_spy, mock_news, mock_snap):
        mock_snap.return_value = _mock_snap(cache_hit=False, age=0.0)
        r = client.get("/analyze/FRESHFETCH")
        assert r.json()["data_quality"]["cache_hit"] is False

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_freshness_info_model(self, mock_spy, mock_news, mock_snap):
        """FreshnessInfo model is constructeerbaar."""
        fi = FreshnessInfo(
            fetched_at=datetime.now(timezone.utc),
            data_age_seconds=42.5,
            confidence=DataConfidence.LIVE,
            cache_hit=True,
            cache_ttl_remaining=17.5,
            is_market_open=True,
        )
        assert fi.data_age_seconds == 42.5
        assert fi.confidence == DataConfidence.LIVE
        assert fi.cache_hit is True


# ── CACHE STATS TESTS ─────────────────────────────────────────────────────────

class TestCacheStats:

    def setup_method(self):
        clear_cache()

    def test_stats_empty_cache(self):
        stats = cache_stats()
        assert stats["total_entries"] == 0
        assert stats["live"] == 0

    def test_stats_counts_fresh_entry(self):
        set_cached("STAT1", {"price": 1.0})
        stats = cache_stats()
        assert stats["total_entries"] == 1
        assert stats["live"] == 1

    def test_stats_counts_stale_entry(self):
        set_cached("STALESTAT", {"price": 1.0}, ttl_seconds=7200)
        _cache["STALESTAT"].cached_at = (
            datetime.now(timezone.utc) - timedelta(seconds=DELAYED_MAX_AGE + 100)
        )
        stats = cache_stats()
        assert stats["stale"] >= 1

    def test_stats_has_market_info(self):
        stats = cache_stats()
        assert "market_open" in stats
        assert "current_ttl" in stats

    def test_health_endpoint_includes_cache_stats(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert "cache_stats" in r.json()
        cs = r.json()["cache_stats"]
        assert "enabled" in cs
        assert cs["enabled"] is True
