"""
tests/test_signals.py
Signal Quality Tests — v2.4

Coverage:
    TestMarketSession           Sessie detectie per tijdstip
    TestNewsClient              Keyword classificatie + confidence scoring
    TestFinnhubIntegration      Finnhub key check + mocked response
    TestNegativeSignals         Negatieve headline detectie
    TestNewsConfidence          Source tier + recency scoring
    TestSocialClient            Placeholder + architectuur
    TestSectorIntelligence      Dynamic heat berekening
    TestRelativeStrengthV24     Verbeterde RS drempels
    TestMarketSessionInSnapshot market_session veld in TickerSnapshot
    TestAssemblerV24            Assembler met alle v2.4 intelligentie
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from backend.app import app
from schemas.ticker_snapshot import TickerSnapshot, DataConfidence
from cache.market_cache import clear_cache, set_cached

client = TestClient(app)


def _snap(ticker="TEST", price=50.0, day_pct=5.0, session="REGULAR",
          confidence=DataConfidence.LIVE, cache_hit=False):
    return TickerSnapshot(
        ticker=ticker, timestamp=datetime.now(timezone.utc),
        confidence=confidence, price=price, prev_close=price - 2,
        day_change_pct=day_pct, premarket_pct=0.0, premarket_available=False,
        volume_today=2_000_000, avg_volume_20d=500_000,
        market_cap=1e9, float_shares=40_000_000,
        market_session=session, cache_hit=cache_hit, data_age_seconds=0.0,
    )


# ── MARKET SESSION TESTS ──────────────────────────────────────────────────────

class TestMarketSession:

    def test_regular_hours_detection(self):
        from data.market_session import get_market_session, MarketSession
        # 14:00 UTC = 09:00 ET (pre-market)
        # 15:00 UTC = 10:00 ET (regular)
        dt = datetime(2026, 5, 28, 15, 0, tzinfo=timezone.utc)  # Wednesday
        assert get_market_session(dt) == MarketSession.REGULAR

    def test_premarket_detection(self):
        from data.market_session import get_market_session, MarketSession
        # 10:00 UTC = 05:00 ET (pre-market)
        dt = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
        assert get_market_session(dt) == MarketSession.PREMARKET

    def test_afterhours_detection(self):
        from data.market_session import get_market_session, MarketSession
        # 22:00 UTC = 17:00 ET (after hours)
        dt = datetime(2026, 5, 28, 22, 0, tzinfo=timezone.utc)
        assert get_market_session(dt) == MarketSession.AFTERHOURS

    def test_overnight_detection(self):
        from data.market_session import get_market_session, MarketSession
        # 03:00 UTC = 22:00 ET (closed)
        dt = datetime(2026, 5, 28, 3, 0, tzinfo=timezone.utc)
        assert get_market_session(dt) == MarketSession.CLOSED

    def test_weekend_is_closed(self):
        from data.market_session import get_market_session, MarketSession
        # 2026-05-30 is Saturday
        dt = datetime(2026, 5, 30, 15, 0, tzinfo=timezone.utc)
        assert get_market_session(dt) == MarketSession.CLOSED

    def test_sunday_is_closed(self):
        from data.market_session import get_market_session, MarketSession
        dt = datetime(2026, 5, 31, 15, 0, tzinfo=timezone.utc)
        assert get_market_session(dt) == MarketSession.CLOSED

    def test_is_regular_hours_helper(self):
        from data.market_session import is_regular_hours
        dt_regular = datetime(2026, 5, 28, 15, 0, tzinfo=timezone.utc)
        dt_closed  = datetime(2026, 5, 28, 3,  0, tzinfo=timezone.utc)
        assert is_regular_hours(dt_regular) is True
        assert is_regular_hours(dt_closed)  is False

    def test_is_premarket_helper(self):
        from data.market_session import is_premarket
        dt_pre = datetime(2026, 5, 28, 10, 0, tzinfo=timezone.utc)
        assert is_premarket(dt_pre) is True

    def test_session_descriptions_all_defined(self):
        from data.market_session import SESSION_DESCRIPTIONS, MarketSession
        for session in MarketSession:
            assert session in SESSION_DESCRIPTIONS

    def test_session_enum_values(self):
        from data.market_session import MarketSession
        assert MarketSession.REGULAR    == "REGULAR"
        assert MarketSession.PREMARKET  == "PREMARKET"
        assert MarketSession.AFTERHOURS == "AFTERHOURS"
        assert MarketSession.CLOSED     == "CLOSED"


# ── NEWS CLIENT KEYWORD TESTS ─────────────────────────────────────────────────

class TestNewsClient:

    def test_classify_strong_earnings_beat(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Q2 earnings beat estimates by 18%", "Reuters", "2026-05-28T10:00:00Z", None)]
        cat, desc, _ = classify_catalyst_from_headlines(news)
        assert cat == "STRONG"

    def test_classify_strong_contract(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("DoD contract awarded worth $250M", "WSJ", "2026-05-28T10:00:00Z", None)]
        cat, _, _ = classify_catalyst_from_headlines(news)
        assert cat == "STRONG"

    def test_classify_strong_guidance_raise(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Company raised guidance above consensus", "Bloomberg", "2026-05-28T10:00:00Z", None)]
        cat, _, _ = classify_catalyst_from_headlines(news)
        assert cat == "STRONG"

    def test_classify_moderate_upgrade(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Goldman upgrades to Buy rating", "Barrons", "2026-05-28T10:00:00Z", None)]
        cat, _, _ = classify_catalyst_from_headlines(news)
        assert cat == "MODERATE"

    def test_classify_moderate_partnership(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Strategic partnership announced with Microsoft", "CNBC", "2026-05-28T10:00:00Z", None)]
        cat, _, _ = classify_catalyst_from_headlines(news)
        assert cat == "MODERATE"

    def test_classify_weak_explores(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Company explores potential strategic options", "PR Newswire", "2026-05-28T10:00:00Z", None)]
        cat, _, _ = classify_catalyst_from_headlines(news)
        assert cat == "WEAK"

    def test_classify_none_on_empty(self):
        from data.news_client import classify_catalyst_from_headlines
        cat, desc, flags = classify_catalyst_from_headlines([])
        assert cat == "NONE"
        assert flags == []

    def test_strong_overrides_moderate(self):
        """Sterkste catalyst wint bij meerdere artikelen."""
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [
            NewsItem("Company explores options", "PR", "2026-05-28T10:00:00Z", None),
            NewsItem("Q2 earnings beat by 20%", "Reuters", "2026-05-28T09:00:00Z", None),
        ]
        cat, _, _ = classify_catalyst_from_headlines(news)
        assert cat == "STRONG"

    def test_returns_description(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Record revenue quarter announced", "Bloomberg", "2026-05-28T10:00:00Z", None)]
        _, desc, _ = classify_catalyst_from_headlines(news)
        assert len(desc) > 0


# ── NEGATIVE SIGNALS TESTS ────────────────────────────────────────────────────

class TestNegativeSignals:

    def test_sec_investigation_detected(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("SEC investigation into accounting practices", "Reuters", "2026-05-28T10:00:00Z", None)]
        _, _, neg_flags = classify_catalyst_from_headlines(news)
        assert len(neg_flags) > 0
        assert any("sec investigation" in f.lower() for f in neg_flags)

    def test_class_action_detected(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Class action lawsuit filed against company", "WSJ", "2026-05-28T10:00:00Z", None)]
        _, _, neg_flags = classify_catalyst_from_headlines(news)
        assert len(neg_flags) > 0

    def test_guidance_cut_detected(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Company cuts guidance below analyst expectations", "CNBC", "2026-05-28T10:00:00Z", None)]
        _, _, neg_flags = classify_catalyst_from_headlines(news)
        assert len(neg_flags) > 0

    def test_clean_news_no_flags(self):
        from data.news_client import classify_catalyst_from_headlines, NewsItem
        news = [NewsItem("Company reports Q2 earnings beat", "Reuters", "2026-05-28T10:00:00Z", None)]
        _, _, neg_flags = classify_catalyst_from_headlines(news)
        assert neg_flags == []


# ── NEWS CONFIDENCE TESTS ─────────────────────────────────────────────────────

class TestNewsConfidence:

    def test_high_confidence_tier1_source_recent(self):
        from data.news_client import _news_confidence, NewsConfidence
        now_iso = datetime.now(timezone.utc).isoformat()
        conf = _news_confidence("Reuters", now_iso, "STRONG")
        assert conf == NewsConfidence.HIGH

    def test_low_confidence_old_news(self):
        from data.news_client import _news_confidence, NewsConfidence
        old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
        conf = _news_confidence("PR Newswire", old, "NONE")
        assert conf == NewsConfidence.LOW

    def test_medium_confidence_default(self):
        from data.news_client import _news_confidence, NewsConfidence
        ts = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        conf = _news_confidence("Seeking Alpha", ts, "MODERATE")
        assert conf == NewsConfidence.MEDIUM

    def test_source_tier_reuters_is_1(self):
        from data.news_client import _source_tier
        assert _source_tier("Reuters") == 1

    def test_source_tier_pr_newswire_is_3(self):
        from data.news_client import _source_tier
        assert _source_tier("PR Newswire") == 3

    def test_source_tier_unknown_is_2(self):
        from data.news_client import _source_tier
        assert _source_tier("Random Blog XYZ") == 2

    def test_finnhub_key_check(self):
        """Zonder key geeft get_news() altijd lege lijst."""
        from data.news_client import get_news
        with patch.dict("os.environ", {"FINNHUB_API_KEY": ""}):
            # Reload module om env var te gebruiken
            import importlib
            import data.news_client as nc
            original_key = nc._FINNHUB_KEY
            nc._FINNHUB_KEY = ""  # Force empty
            result = nc.get_news("NVDA")
            nc._FINNHUB_KEY = original_key  # Restore
            assert result == []


# ── FINNHUB INTEGRATION TESTS (MOCKED) ───────────────────────────────────────

class TestFinnhubIntegration:

    def test_finnhub_response_parsed_correctly(self):
        """Mocked Finnhub response wordt correct geparsd."""
        from data.news_client import _fetch_finnhub_news
        mock_response = [
            {
                "headline": "Company beats Q2 earnings by 24%",
                "source": "Reuters",
                "datetime": int(datetime.now(timezone.utc).timestamp()),
                "sentiment": 0.85,
            },
            {
                "headline": "Analyst upgrades to Buy",
                "source": "Barrons",
                "datetime": int((datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()),
                "sentiment": 0.4,
            },
        ]

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            import data.news_client as nc
            original_key = nc._FINNHUB_KEY
            nc._FINNHUB_KEY = "test_key"

            result = _fetch_finnhub_news("NVDA", hours=48)
            nc._FINNHUB_KEY = original_key

            assert len(result) == 2
            assert result[0].headline == "Company beats Q2 earnings by 24%"
            assert result[0].source == "Reuters"
            assert result[0].sentiment == 0.85

    def test_finnhub_sorted_by_recency(self):
        """Artikelen gesorteerd op publicatiedatum (nieuwste eerst)."""
        from data.news_client import _fetch_finnhub_news
        now = datetime.now(timezone.utc)
        mock_response = [
            {"headline": "Old news", "source": "PR",
             "datetime": int((now - timedelta(hours=24)).timestamp()), "sentiment": None},
            {"headline": "Recent news", "source": "Reuters",
             "datetime": int((now - timedelta(hours=1)).timestamp()), "sentiment": None},
        ]

        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            import data.news_client as nc
            nc._FINNHUB_KEY = "test_key"
            result = _fetch_finnhub_news("TEST", hours=48)
            nc._FINNHUB_KEY = ""

            assert result[0].headline == "Recent news"

    def test_finnhub_http_error_returns_empty(self):
        """HTTP error → lege lijst, geen crash."""
        from data.news_client import _fetch_finnhub_news
        with patch("httpx.get", side_effect=Exception("Connection refused")):
            import data.news_client as nc
            nc._FINNHUB_KEY = "test_key"
            result = _fetch_finnhub_news("TEST", hours=48)
            nc._FINNHUB_KEY = ""
            assert result == []

    def test_finnhub_empty_list_ok(self):
        """Finnhub geeft lege lijst → lege NewsItem lijst."""
        from data.news_client import _fetch_finnhub_news
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = []
            mock_resp.raise_for_status.return_value = None
            mock_get.return_value = mock_resp

            import data.news_client as nc
            nc._FINNHUB_KEY = "test_key"
            result = _fetch_finnhub_news("TEST", hours=48)
            nc._FINNHUB_KEY = ""
            assert result == []


# ── SOCIAL CLIENT TESTS ───────────────────────────────────────────────────────

class TestSocialClient:

    def test_get_social_data_returns_social_data(self):
        from data.social_client import get_social_data, SocialData
        result = get_social_data("NVDA")
        assert isinstance(result, SocialData)

    def test_social_data_not_available(self):
        from data.social_client import get_social_data
        result = get_social_data("TEST")
        assert result.available is False

    def test_social_data_has_safe_defaults(self):
        """Mentions avg ≥ 1 om deling door nul te voorkomen."""
        from data.social_client import get_social_data
        result = get_social_data("TEST")
        assert result.mentions_avg >= 1

    def test_social_data_never_raises(self):
        """Moet altijd SocialData teruggeven."""
        from data.social_client import get_social_data
        try:
            result = get_social_data("ANYTICKERXYZ")
            assert result is not None
        except Exception:
            pytest.fail("get_social_data gooide een exception")

    def test_is_social_available_false(self):
        from data.social_client import is_social_available
        assert is_social_available() is False

    def test_social_placeholder_note(self):
        from data.social_client import get_social_data
        result = get_social_data("TEST")
        assert result.note is not None
        assert len(result.note) > 0


# ── SECTOR INTELLIGENCE TESTS ─────────────────────────────────────────────────

class TestSectorIntelligence:

    def test_no_cache_returns_static_heat(self):
        from data.sector_intelligence import get_dynamic_sector_heat
        result = get_dynamic_sector_heat(
            sector_id="test", static_heat=75,
            leaders=["IONQ", "QBTS"],
            cache_fn=lambda t: None,  # Geen cache
        )
        assert result == 75

    def test_no_leaders_returns_static(self):
        from data.sector_intelligence import get_dynamic_sector_heat
        result = get_dynamic_sector_heat(
            sector_id="test", static_heat=60,
            leaders=[],
            cache_fn=lambda t: None,
        )
        assert result == 60

    def test_dynamic_heat_blended_with_cache_data(self):
        """Met cache data: dynamische heat blend met statische heat."""
        from data.sector_intelligence import get_dynamic_sector_heat
        from cache.market_cache import CacheEntry

        def mock_cache(ticker):
            return CacheEntry(
                ticker=ticker,
                data={
                    "price": 50.0, "day_change_pct": 8.0,
                    "volume_today": 5_000_000, "avg_volume_20d": 500_000,
                },
                cached_at=datetime.now(timezone.utc),
                ttl_seconds=60,
            )

        result = get_dynamic_sector_heat(
            sector_id="quantum", static_heat=70,
            leaders=["IONQ", "QBTS"],
            cache_fn=mock_cache,
        )
        assert 0 <= result <= 100
        assert result != 70  # Moet dynamisch zijn

    def test_heat_clamped_to_0_100(self):
        from data.sector_intelligence import get_dynamic_sector_heat
        from cache.market_cache import CacheEntry

        def extreme_cache(ticker):
            return CacheEntry(
                ticker=ticker,
                data={"day_change_pct": 50.0, "volume_today": 100_000_000,
                      "avg_volume_20d": 100_000},
                cached_at=datetime.now(timezone.utc),
                ttl_seconds=60,
            )

        result = get_dynamic_sector_heat(
            sector_id="test", static_heat=90,
            leaders=["X"], cache_fn=extreme_cache,
        )
        assert 0 <= result <= 100

    def test_enrich_sector_config(self):
        from data.sector_intelligence import enrich_sector_config
        sector_data = {
            "id": "quantum", "label": "QUANTUM", "heat": 80,
            "leaders": ["IONQ"], "sympathy": ["QBTS"],
        }
        enriched = enrich_sector_config(sector_data, cache_fn=lambda t: None)
        assert "heat" in enriched
        assert "heat_source" in enriched
        assert enriched["heat_source"] in ("dynamic", "static")


# ── RELATIVE STRENGTH V2.4 TESTS ──────────────────────────────────────────────

class TestRelativeStrengthV24:

    def test_strong_positive_stock_up_market_down(self):
        from data.assembler import _classify_relative_strength
        from scoring.scoring_v1_2 import RelativeStrength
        rs = _classify_relative_strength(stock_pct=2.0, spy_pct=-1.0)
        assert rs == RelativeStrength.STRONG_POSITIVE

    def test_strong_positive_large_outperformance(self):
        from data.assembler import _classify_relative_strength
        from scoring.scoring_v1_2 import RelativeStrength
        rs = _classify_relative_strength(stock_pct=5.0, spy_pct=0.5)
        assert rs == RelativeStrength.STRONG_POSITIVE  # diff=4.5 > 2.5

    def test_moderate_positive_moderate_outperformance(self):
        from data.assembler import _classify_relative_strength
        from scoring.scoring_v1_2 import RelativeStrength
        rs = _classify_relative_strength(stock_pct=2.5, spy_pct=1.0)
        assert rs == RelativeStrength.MODERATE_POSITIVE  # diff=1.5 > 1.0

    def test_neutral_similar_performance(self):
        from data.assembler import _classify_relative_strength
        from scoring.scoring_v1_2 import RelativeStrength
        rs = _classify_relative_strength(stock_pct=1.2, spy_pct=1.0)
        assert rs == RelativeStrength.NEUTRAL  # diff=0.2

    def test_underperforming(self):
        from data.assembler import _classify_relative_strength
        from scoring.scoring_v1_2 import RelativeStrength
        rs = _classify_relative_strength(stock_pct=-1.0, spy_pct=2.0)
        assert rs == RelativeStrength.UNDERPERFORMING  # diff=-3.0


# ── MARKET SESSION IN SNAPSHOT ────────────────────────────────────────────────

class TestMarketSessionInSnapshot:

    def test_snapshot_has_market_session_field(self):
        s = _snap(session="REGULAR")
        assert s.market_session == "REGULAR"

    def test_snapshot_market_session_optional(self):
        """market_session is optional — backward compat."""
        s = TickerSnapshot(
            ticker="TEST", timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.LIVE,
            price=50.0, prev_close=49.0, day_change_pct=2.0,
            premarket_pct=0.0, premarket_available=False,
            volume_today=1_000_000, avg_volume_20d=500_000,
        )
        assert s.market_session is None  # Geen crash

    def test_all_session_values_valid(self):
        from data.market_session import MarketSession
        for session in MarketSession:
            s = _snap(session=session.value)
            assert s.market_session == session.value


# ── ASSEMBLER V2.4 TESTS ──────────────────────────────────────────────────────

class TestAssemblerV24:

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_assembler_uses_social_client(
            self, mock_social, mock_spy, mock_news, mock_snap):
        from data.social_client import SocialData
        mock_snap.return_value = _snap()
        mock_social.return_value = SocialData(
            ticker="TEST", mentions_today=500, mentions_avg=100,
            velocity=5.0, platform="stocktwits", available=True, note=None,
        )
        from data.assembler import build_ticker_input
        inp, quality = build_ticker_input("TEST")
        assert inp.social_mentions_today == 500
        assert inp.social_mentions_avg == 100

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news")
    @patch("data.assembler.get_spy_return", return_value=-1.0)
    @patch("data.assembler.get_social_data")
    def test_strong_catalyst_from_news(
            self, mock_social, mock_spy, mock_news, mock_snap):
        from data.news_client import NewsItem
        from data.social_client import SocialData
        from scoring.scoring_v1_2 import CatalystType

        mock_snap.return_value = _snap(day_pct=8.0)
        mock_news.return_value = [
            NewsItem("Q2 earnings beat by 22%", "Reuters",
                     datetime.now(timezone.utc).isoformat(), 0.9)
        ]
        mock_social.return_value = SocialData("TEST", 0, 1, 0.0, "p", False, None)

        from data.assembler import build_ticker_input
        inp, _ = build_ticker_input("TEST")
        assert inp.catalyst_type == CatalystType.STRONG

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_sec_check_automated_flag(
            self, mock_social, mock_spy, mock_news, mock_snap):
        from data.social_client import SocialData
        mock_snap.return_value = _snap()
        mock_social.return_value = SocialData("TEST", 0, 1, 0.0, "p", False, None)

        from data.assembler import build_ticker_input
        import os
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "test123"}):
            _, quality = build_ticker_input("TEST")
            assert quality.sec_check_automated is True

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_sec_check_not_automated_without_key(
            self, mock_social, mock_spy, mock_news, mock_snap):
        from data.social_client import SocialData
        mock_snap.return_value = _snap()
        mock_social.return_value = SocialData("TEST", 0, 1, 0.0, "p", False, None)

        from data.assembler import build_ticker_input
        import os
        with patch.dict(os.environ, {"FINNHUB_API_KEY": ""}):
            _, quality = build_ticker_input("TEST")
            assert quality.sec_check_automated is False
