"""
tests/test_backend.py
Backend endpoint tests — v2.0

Alle tests gebruiken mocks voor data ophalen.
Geen netwerk vereist — draait in elke omgeving.

Wat getest wordt:
    TestHealthEndpoint      /health response structuur
    TestAnalyzeEndpoint     /analyze/{ticker} happy path + errors
    TestSerializer          _serialize() helper met enums + dataclasses
    TestAssemblerLogic      catalyst classifier + relative strength
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.app import app, _serialize
from data.yahoo_client import QuoteData
from data.news_client import NewsItem
from scoring.scoring_v1_2 import (
    Decision, Phase, MarketCapTier,
    CatalystType, RelativeStrength, SectorConfig,
)

client = TestClient(app)


# ── HEALTH ENDPOINT ───────────────────────────────────────────────────────────

class TestHealthEndpoint:

    def test_health_returns_200(self):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_status_is_ok(self):
        r = client.get("/health")
        assert r.json()["status"] == "ok"

    def test_health_has_version(self):
        r = client.get("/health")
        assert "version" in r.json()

    def test_health_has_engine_field(self):
        r = client.get("/health")
        assert r.json()["engine"] == "scoring_v1_2"

    def test_health_has_timestamp(self):
        r = client.get("/health")
        assert "timestamp" in r.json()

    def test_health_has_data_sources(self):
        r = client.get("/health")
        assert "data_sources" in r.json()

    def test_health_has_limitations(self):
        """Transparantie over v2.0 beperkingen."""
        r = client.get("/health")
        limitations = r.json()["limitations"]
        assert isinstance(limitations, list)
        assert len(limitations) > 0


# ── ANALYZE ENDPOINT ──────────────────────────────────────────────────────────

def _mock_quote(ticker="TEST", price=50.0, day_change_pct=6.0,
                premarket_pct=10.0, volume_today=3_000_000,
                avg_volume_20d=500_000, market_cap=1_000_000_000,
                float_shares=40_000_000, error=None):
    return QuoteData(
        ticker=ticker, price=price, prev_close=price - 2,
        day_change_pct=day_change_pct, premarket_price=None,
        premarket_pct=premarket_pct, volume_today=volume_today,
        avg_volume_20d=avg_volume_20d, market_cap=market_cap,
        float_shares=float_shares, error=error,
    )


class TestAnalyzeEndpoint:

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_returns_200(self, mock_spy, mock_news, mock_quote):
        mock_quote.return_value = _mock_quote()
        r = client.get("/analyze/TEST")
        assert r.status_code == 200

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_has_required_fields(self, mock_spy, mock_news, mock_quote):
        mock_quote.return_value = _mock_quote()
        r = client.get("/analyze/TEST")
        d = r.json()
        for field in ["ticker", "decision", "momentum_score", "skip_score",
                      "phase", "market_cap_tier", "sizing_eur",
                      "data_quality", "analyzed_at"]:
            assert field in d, f"Veld '{field}' ontbreekt in response"

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_decision_is_valid_enum(self, mock_spy, mock_news, mock_quote):
        mock_quote.return_value = _mock_quote()
        r = client.get("/analyze/TEST")
        valid = {d.value for d in Decision}
        assert r.json()["decision"] in valid

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_momentum_score_in_range(self, mock_spy, mock_news, mock_quote):
        mock_quote.return_value = _mock_quote()
        r = client.get("/analyze/TEST")
        score = r.json()["momentum_score"]
        assert 0 <= score <= 100

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_data_quality_present(self, mock_spy, mock_news, mock_quote):
        mock_quote.return_value = _mock_quote()
        r = client.get("/analyze/TEST")
        dq = r.json()["data_quality"]
        assert "price_available"     in dq
        assert "float_available"     in dq
        assert "premarket_available" in dq
        assert "news_available"      in dq
        assert "social_available"    in dq
        assert "sec_check_automated" in dq

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_no_news_results_in_none_catalyst(self, mock_spy, mock_news, mock_quote):
        """Lege nieuwslijst → catalyst=NONE → score engine gebruikt 'Geen catalyst' label."""
        mock_quote.return_value = _mock_quote(volume_today=800_000)
        r = client.get("/analyze/TEST")
        d = r.json()
        breakdown = d["momentum_detail"]["breakdown"]
        catalyst_key = next(k for k in breakdown if "Catalyst" in k)
        # Engine label voor CatalystType.NONE = "Geen catalyst (48u)"
        assert "Geen catalyst" in breakdown[catalyst_key]

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_ticker_normalized_to_uppercase(self, mock_spy, mock_news, mock_quote):
        mock_quote.return_value = _mock_quote(ticker="TEST")
        r = client.get("/analyze/test")
        assert r.json()["ticker"] == "TEST"

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_blocked_on_error(self, mock_spy, mock_news, mock_quote):
        """Ophaalfout geeft 422 terug met duidelijke foutmelding."""
        mock_quote.return_value = _mock_quote(price=0.0, error="HTTP 404")
        r = client.get("/analyze/FAKE")
        assert r.status_code == 422
        assert "error" in r.json()["detail"]

    def test_analyze_invalid_ticker_returns_400(self):
        """Ticker met cijfers of tekens → 400."""
        r = client.get("/analyze/123INVALID!")
        assert r.status_code == 400

    def test_analyze_empty_ticker_returns_400(self):
        r = client.get("/analyze/ ")
        assert r.status_code in (400, 404, 422)

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_high_volume_scores_higher(self, mock_spy, mock_news, mock_quote):
        """10x volume scoort hoger dan 1.2x volume."""
        mock_quote.return_value = _mock_quote(
            volume_today=5_000_000, avg_volume_20d=500_000  # 10x
        )
        r_high = client.get("/analyze/TEST")

        mock_quote.return_value = _mock_quote(
            volume_today=600_000, avg_volume_20d=500_000   # 1.2x
        )
        r_low = client.get("/analyze/TEST")

        assert r_high.json()["momentum_score"] > r_low.json()["momentum_score"]

    @patch("data.assembler.get_quote")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.5)
    def test_analyze_day_over_40_returns_skip(self, mock_spy, mock_news, mock_quote):
        """Dag +42% moet SKIP teruggeven (Skip Score ≥ 50)."""
        mock_quote.return_value = _mock_quote(
            day_change_pct=42.0, premarket_pct=35.0,
            volume_today=5_000_000,
        )
        r = client.get("/analyze/TEST")
        assert r.json()["decision"] == "SKIP"
        assert r.json()["skip_score"] >= 50


# ── SERIALIZER TESTS ──────────────────────────────────────────────────────────

class TestSerializer:
    """_serialize() moet enums, dataclasses en geneste structuren correct omzetten."""

    def test_enum_serialized_to_value(self):
        assert _serialize(Decision.BUY_MAX) == "BUY_MAX"
        assert _serialize(Phase.FRENZY) == "FRENZY"
        assert _serialize(MarketCapTier.MICRO) == "MICRO"

    def test_dict_serialized_recursively(self):
        d = {"decision": Decision.SKIP, "score": 42.0}
        result = _serialize(d)
        assert result["decision"] == "SKIP"
        assert result["score"] == 42.0

    def test_list_serialized_recursively(self):
        lst = [Decision.BUY_SMALL, Decision.BLOCKED]
        result = _serialize(lst)
        assert result == ["BUY_SMALL", "BLOCKED"]

    def test_primitives_pass_through(self):
        assert _serialize(42) == 42
        assert _serialize("hello") == "hello"
        assert _serialize(True) is True
        assert _serialize(None) is None

    def test_nested_enum_in_dict(self):
        d = {"outer": {"inner": Phase.ACCUMULATION}}
        result = _serialize(d)
        assert result["outer"]["inner"] == "ACCUMULATION"


# ── ASSEMBLER LOGIC TESTS ─────────────────────────────────────────────────────

class TestAssemblerLogic:
    """Test classify_catalyst en classify_relative_strength geïsoleerd."""

    def test_catalyst_none_on_empty_news(self):
        from data.assembler import _classify_catalyst
        cat, _ = _classify_catalyst([])
        assert cat == CatalystType.NONE

    def test_catalyst_strong_on_earnings_beat(self):
        from data.assembler import _classify_catalyst
        news = [NewsItem("Company beats estimates by 24%", "Reuters", "2026-05-28", None)]
        cat, desc = _classify_catalyst(news)
        assert cat == CatalystType.STRONG

    def test_catalyst_strong_on_government_contract(self):
        from data.assembler import _classify_catalyst
        news = [NewsItem("DoD contract awarded to company", "WSJ", "2026-05-28", None)]
        cat, _ = _classify_catalyst(news)
        assert cat == CatalystType.STRONG

    def test_catalyst_moderate_on_upgrade(self):
        from data.assembler import _classify_catalyst
        news = [NewsItem("Analyst upgrade to Outperform", "Barrons", "2026-05-28", None)]
        cat, _ = _classify_catalyst(news)
        assert cat == CatalystType.MODERATE

    def test_catalyst_weak_on_vague_announcement(self):
        from data.assembler import _classify_catalyst
        news = [NewsItem("Company explores strategic options", "PR", "2026-05-28", None)]
        cat, _ = _classify_catalyst(news)
        assert cat == CatalystType.WEAK

    def test_rs_strong_positive_stock_up_market_down(self):
        from data.assembler import _classify_relative_strength
        rs = _classify_relative_strength(stock_pct=3.0, spy_pct=-1.0)
        assert rs == RelativeStrength.STRONG_POSITIVE

    def test_rs_moderate_positive_stock_outperforms(self):
        from data.assembler import _classify_relative_strength
        rs = _classify_relative_strength(stock_pct=5.0, spy_pct=1.0)
        assert rs == RelativeStrength.MODERATE_POSITIVE

    def test_rs_neutral_similar_to_market(self):
        from data.assembler import _classify_relative_strength
        rs = _classify_relative_strength(stock_pct=1.0, spy_pct=0.8)
        assert rs == RelativeStrength.NEUTRAL

    def test_rs_underperforming(self):
        from data.assembler import _classify_relative_strength
        rs = _classify_relative_strength(stock_pct=-1.0, spy_pct=2.0)
        assert rs == RelativeStrength.UNDERPERFORMING

    def test_sector_lookup_known_ticker(self):
        from data.assembler import _find_sector
        sector = _find_sector("NVDA")
        assert sector.sector_id != "unknown"
        assert sector.heat > 0

    def test_sector_lookup_unknown_ticker_returns_default(self):
        from data.assembler import _find_sector
        sector = _find_sector("XYZABC123UNKNOWN")
        assert sector.sector_id == "unknown"
        assert sector.heat == 50  # neutraal
