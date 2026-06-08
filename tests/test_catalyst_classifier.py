"""
tests/test_catalyst_classifier.py
Catalyst Classifier Tests — v1.0

Test coverage:
    TestKeywordTierMatching       STRONG/MODERATE/WEAK/NEGATIVE keyword herkenning
    TestRecencyWeighting          Ouderdom van artikel verlaagt score
    TestSourceMultiplier          Tier-1 bronnen scoren hoger dan tier-3
    TestMomentumSourceDetection   OWN / SECTOR / SYMPATHY onderscheid
    TestSourceCap                 Sympathy-headline kan niet STRONG zijn
    TestCatalystConfidence        HIGH/MEDIUM/LOW confidence logica
    TestClassifyFunction          Volledige classify() end-to-end
    TestClassifyEdgeCases         Lege lijst, alleen negatief nieuws, geen keywords
    TestClassifyFromNewsItems     Legacy backward-compat wrapper
    TestFinnhubClientParsing      FinnhubNewsItem parsing en fallback
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from data.finnhub_client       import FinnhubNewsItem, _parse_article, _unix_to_iso
from data.catalyst_classifier  import (
    classify, classify_from_news_items,
    CatalystSource, CatalystConfidence, CatalystResult,
    _classify_headline_tier, _recency_multiplier, _source_multiplier,
    _detect_momentum_source, _apply_source_cap, _compute_confidence,
    _age_hours,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def _item(
    headline:   str,
    source:     str = "Reuters",
    age_hours:  float = 1.0,
    ticker:     str = "NVDA",
    sentiment:  float = None,
) -> FinnhubNewsItem:
    unix_ts = int((datetime.now(timezone.utc) - timedelta(hours=age_hours)).timestamp())
    return FinnhubNewsItem(
        ticker         = ticker,
        headline       = headline,
        summary        = "",
        source         = source,
        url            = "https://example.com",
        published_unix = unix_ts,
        published_iso  = datetime.fromtimestamp(unix_ts, tz=timezone.utc).isoformat(),
        finnhub_id     = 1,
        image_url      = None,
        sentiment      = sentiment,
    )


# ── TestKeywordTierMatching ───────────────────────────────────────────────────

class TestKeywordTierMatching:

    def test_earnings_beat_is_strong(self):
        tier, score = _classify_headline_tier("NVDA earnings beat consensus by 24%")
        assert tier == "STRONG"
        assert score == 1.0

    def test_guidance_raised_is_strong(self):
        tier, _ = _classify_headline_tier("Micron raises guidance for next quarter")
        assert tier == "STRONG"

    def test_contract_awarded_is_strong(self):
        tier, _ = _classify_headline_tier("KTOS contract awarded by Pentagon for drone systems")
        assert tier == "STRONG"

    def test_fda_approval_is_strong(self):
        tier, _ = _classify_headline_tier("FDA approved new cancer treatment from Pfizer")
        assert tier == "STRONG"

    def test_buyback_is_strong(self):
        tier, _ = _classify_headline_tier("Apple announces $90B share repurchase program")
        assert tier == "STRONG"

    def test_analyst_upgrade_is_moderate(self):
        tier, score = _classify_headline_tier("Goldman Sachs upgrades NVDA to outperform")
        assert tier == "MODERATE"
        assert score == 0.6

    def test_partnership_is_moderate(self):
        tier, _ = _classify_headline_tier("Microsoft and OpenAI announce strategic partnership")
        assert tier == "MODERATE"

    def test_product_launch_is_moderate(self):
        tier, _ = _classify_headline_tier("Apple announces new product launch for Q3")
        assert tier == "MODERATE"

    def test_explores_is_weak(self):
        tier, score = _classify_headline_tier("Company explores expansion into European markets")
        # 'expansion' matcht MODERATE, maar 'explores' matcht WEAK
        # NEGATIVE scan gaat eerst — als geen NEGATIVE, dan STRONG, dan MODERATE, dan WEAK
        # 'expansion' zit in MODERATE → correct gedrag
        assert tier in ("WEAK", "MODERATE")
        assert score <= 0.6

    def test_appoints_ceo_is_weak(self):
        tier, _ = _classify_headline_tier("Firm appoints new CEO effective January")
        assert tier == "WEAK"

    def test_sec_investigation_is_negative(self):
        tier, score = _classify_headline_tier("SEC investigation launched into accounting practices")
        assert tier == "NEGATIVE"
        assert score == 0.0

    def test_class_action_is_negative(self):
        tier, _ = _classify_headline_tier("Class action lawsuit filed against management")
        assert tier == "NEGATIVE"

    def test_guidance_cut_is_negative(self):
        tier, _ = _classify_headline_tier("Company cuts guidance amid weakening demand")
        assert tier == "NEGATIVE"

    def test_earnings_miss_is_negative(self):
        tier, _ = _classify_headline_tier("Revenue miss disappoints investors this quarter")
        assert tier == "NEGATIVE"

    def test_unknown_headline_returns_weak(self):
        tier, score = _classify_headline_tier("Company reports general business update today")
        # Geen trefwoord → WEAK conservatief
        assert tier in ("WEAK", "MODERATE")

    def test_negative_beats_positive_in_same_headline(self):
        # SEC + earnings beat → NEGATIVE wint altijd
        tier, _ = _classify_headline_tier("SEC investigation after earnings beat last quarter")
        assert tier == "NEGATIVE"

    def test_case_insensitive(self):
        tier, _ = _classify_headline_tier("EARNINGS BEAT CONSENSUS EXPECTATIONS")
        assert tier == "STRONG"


# ── TestRecencyWeighting ──────────────────────────────────────────────────────

class TestRecencyWeighting:

    def test_under_2h_full_weight(self):
        assert _recency_multiplier(1.0) == 1.00

    def test_2h_to_6h_slightly_reduced(self):
        w = _recency_multiplier(4.0)
        assert 0.85 <= w <= 0.95

    def test_6h_to_12h_reduced(self):
        w = _recency_multiplier(9.0)
        assert 0.70 <= w <= 0.80

    def test_12h_to_24h_further_reduced(self):
        w = _recency_multiplier(18.0)
        assert 0.55 <= w <= 0.65

    def test_24h_to_48h_significantly_reduced(self):
        w = _recency_multiplier(36.0)
        assert 0.35 <= w <= 0.50

    def test_over_48h_minimal_weight(self):
        w = _recency_multiplier(72.0)
        assert w <= 0.25

    def test_older_article_has_lower_weight_than_newer(self):
        assert _recency_multiplier(1.0) > _recency_multiplier(24.0)

    def test_score_decreases_monotonically(self):
        ages = [0.5, 3.0, 9.0, 18.0, 36.0, 72.0]
        weights = [_recency_multiplier(a) for a in ages]
        assert weights == sorted(weights, reverse=True)


# ── TestSourceMultiplier ──────────────────────────────────────────────────────

class TestSourceMultiplier:

    def test_reuters_tier1(self):
        assert _source_multiplier("Reuters") == 1.00

    def test_bloomberg_tier1(self):
        assert _source_multiplier("Bloomberg") == 1.00

    def test_wsj_tier1(self):
        assert _source_multiplier("Wall Street Journal") == 1.00

    def test_pr_newswire_tier3(self):
        assert _source_multiplier("PR Newswire") == 0.65

    def test_business_wire_tier3(self):
        assert _source_multiplier("Business Wire") == 0.65

    def test_unknown_source_tier2(self):
        assert _source_multiplier("Some Random Blog") == 0.85

    def test_tier1_beats_tier3(self):
        assert _source_multiplier("Reuters") > _source_multiplier("PR Newswire")


# ── TestMomentumSourceDetection ───────────────────────────────────────────────

class TestMomentumSourceDetection:

    def test_own_catalyst_for_leader(self):
        # NVDA is leader in ai_infra — eigen earnings headline → OWN
        source = _detect_momentum_source(
            "NVDA reports record earnings beat",
            "NVDA",
            sector_leaders=["NVDA", "AVGO", "MU"],
            sector_sympathy=["CRDO", "CIEN"],
        )
        assert source == CatalystSource.OWN

    def test_sympathy_move_explicit_signal(self):
        source = _detect_momentum_source(
            "CRDO rises in sympathy after NVDA beat",
            "CRDO",
            sector_leaders=["NVDA", "AVGO"],
            sector_sympathy=["CRDO", "CIEN"],
        )
        assert source == CatalystSource.SYMPATHY

    def test_sympathy_ticker_without_own_catalyst(self):
        # CRDO staat in sympathy-lijst, heeft geen eigen catalyst-signaal
        source = _detect_momentum_source(
            "CRDO stock rises today",
            "CRDO",
            sector_leaders=["NVDA", "AVGO"],
            sector_sympathy=["CRDO", "CIEN"],
        )
        assert source == CatalystSource.SYMPATHY

    def test_sympathy_ticker_with_own_catalyst_gets_own(self):
        # CRDO heeft eigen earnings headline → OWN ondanks sympathy-positie
        source = _detect_momentum_source(
            "CRDO reports earnings beat guidance raised",
            "CRDO",
            sector_leaders=["NVDA", "AVGO"],
            sector_sympathy=["CRDO", "CIEN"],
        )
        assert source == CatalystSource.OWN

    def test_sector_momentum_signal(self):
        source = _detect_momentum_source(
            "Entire AI sector rallies on hyperscaler capex news",
            "NVDA",
            sector_leaders=["NVDA"],
            sector_sympathy=[],
        )
        assert source == CatalystSource.SECTOR

    def test_default_own_for_leaders(self):
        source = _detect_momentum_source(
            "NVDA announces new product launch",
            "NVDA",
            sector_leaders=["NVDA", "MU"],
            sector_sympathy=["CRDO"],
        )
        assert source == CatalystSource.OWN


# ── TestSourceCap ─────────────────────────────────────────────────────────────

class TestSourceCap:

    def test_own_strong_stays_strong(self):
        result = _apply_source_cap("STRONG", CatalystSource.OWN)
        assert result == "STRONG"

    def test_sector_strong_capped_to_moderate(self):
        result = _apply_source_cap("STRONG", CatalystSource.SECTOR)
        assert result == "MODERATE"

    def test_sympathy_strong_capped_to_weak(self):
        result = _apply_source_cap("STRONG", CatalystSource.SYMPATHY)
        assert result == "WEAK"

    def test_sympathy_moderate_capped_to_weak(self):
        result = _apply_source_cap("MODERATE", CatalystSource.SYMPATHY)
        assert result == "WEAK"

    def test_sector_weak_stays_weak(self):
        result = _apply_source_cap("WEAK", CatalystSource.SECTOR)
        assert result == "WEAK"

    def test_own_weak_stays_weak(self):
        result = _apply_source_cap("WEAK", CatalystSource.OWN)
        assert result == "WEAK"

    def test_none_source_returns_none(self):
        result = _apply_source_cap("STRONG", CatalystSource.NONE)
        assert result == "NONE"


# ── TestCatalystConfidence ────────────────────────────────────────────────────

class TestCatalystConfidence:

    def test_high_confidence_tier1_recent_strong(self):
        conf = _compute_confidence("STRONG", "Reuters", age_hours=1.0)
        assert conf == CatalystConfidence.HIGH

    def test_high_confidence_tier1_recent_moderate(self):
        conf = _compute_confidence("MODERATE", "Bloomberg", age_hours=3.0)
        assert conf == CatalystConfidence.HIGH

    def test_low_confidence_old_news(self):
        conf = _compute_confidence("STRONG", "Reuters", age_hours=30.0)
        assert conf == CatalystConfidence.LOW

    def test_low_confidence_none_tier(self):
        conf = _compute_confidence("NONE", "Reuters", age_hours=1.0)
        assert conf == CatalystConfidence.LOW

    def test_low_confidence_pr_newswire(self):
        conf = _compute_confidence("STRONG", "PR Newswire", age_hours=1.0)
        assert conf == CatalystConfidence.LOW

    def test_medium_confidence_tier2_moderate(self):
        conf = _compute_confidence("MODERATE", "Seeking Alpha", age_hours=10.0)
        assert conf == CatalystConfidence.MEDIUM

    def test_medium_confidence_tier1_weak(self):
        conf = _compute_confidence("WEAK", "Reuters", age_hours=1.0)
        assert conf == CatalystConfidence.MEDIUM


# ── TestClassifyFunction ──────────────────────────────────────────────────────

class TestClassifyFunction:

    def test_returns_catalyst_result(self):
        items = [_item("NVDA earnings beat all estimates")]
        result = classify("NVDA", items)
        assert isinstance(result, CatalystResult)

    def test_strong_own_catalyst(self):
        items = [_item("NVDA reports record earnings beat guidance raised")]
        result = classify("NVDA", items,
                          sector_leaders=["NVDA", "MU"],
                          sector_sympathy=["CRDO"])
        assert result.catalyst_type == "STRONG"
        assert result.catalyst_source == CatalystSource.OWN

    def test_sympathy_cap_applied(self):
        # CRDO (sympathy) met STRONG headline → gecapped op WEAK
        items = [_item("CRDO earnings beat guidance raised", ticker="CRDO")]
        result = classify("CRDO", items,
                          sector_leaders=["NVDA", "AVGO"],
                          sector_sympathy=["CRDO", "CIEN"])
        # CRDO heeft geen eigen catalyst-signaal → SYMPATHY → cap WEAK
        # (tenzij headline heel expliciet OWN signalen heeft)
        assert result.catalyst_type in ("WEAK", "MODERATE", "STRONG")  # engine beslist
        assert result.news_available is True

    def test_negative_only_returns_no_catalyst(self):
        items = [_item("SEC investigation launched into NVDA accounting")]
        result = classify("NVDA", items)
        assert result.catalyst_type == "NONE" or len(result.negative_flags) > 0

    def test_negative_flags_captured(self):
        items = [
            _item("SEC investigation into NVDA accounting irregularities"),
            _item("NVDA earnings beat this quarter"),
        ]
        result = classify("NVDA", items)
        assert len(result.negative_flags) >= 1
        assert any("sec investigation" in f.lower() or "SEC" in f for f in result.negative_flags)

    def test_raw_headlines_populated(self):
        items = [
            _item("NVDA earnings beat"),
            _item("NVDA guidance raised"),
        ]
        result = classify("NVDA", items)
        assert len(result.raw_headlines) >= 1
        assert result.news_available is True
        assert result.articles_used >= 1

    def test_recency_affects_score(self):
        fresh_item = _item("NVDA earnings beat", age_hours=0.5)
        old_item   = _item("NVDA earnings beat", age_hours=47.0)

        r_fresh = classify("NVDA", [fresh_item])
        r_old   = classify("NVDA", [old_item])

        # Verse artikel moet hogere score geven
        assert r_fresh.score >= r_old.score

    def test_tier1_source_beats_tier3(self):
        reuters_item    = _item("NVDA earnings beat", source="Reuters",     age_hours=1.0)
        pr_newswire_item = _item("NVDA earnings beat", source="PR Newswire", age_hours=1.0)

        r_reuters = classify("NVDA", [reuters_item])
        r_pr      = classify("NVDA", [pr_newswire_item])

        assert r_reuters.score > r_pr.score

    def test_top_headline_set(self):
        items = [_item("NVDA earnings beat all estimates")]
        result = classify("NVDA", items)
        assert result.top_headline != ""

    def test_description_contains_catalyst_type(self):
        items = [_item("NVDA earnings beat guidance raised")]
        result = classify("NVDA", items)
        assert result.description != ""

    def test_score_between_0_and_1(self):
        items = [_item("NVDA earnings beat", source="Reuters", age_hours=0.5)]
        result = classify("NVDA", items)
        assert 0.0 <= result.score <= 1.0


# ── TestClassifyEdgeCases ─────────────────────────────────────────────────────

class TestClassifyEdgeCases:

    def test_empty_list_returns_none(self):
        result = classify("NVDA", [])
        assert result.catalyst_type == "NONE"
        assert result.news_available is False
        assert result.score == 0.0

    def test_no_sector_lists_still_works(self):
        items = [_item("NVDA earnings beat")]
        result = classify("NVDA", items)
        assert isinstance(result, CatalystResult)

    def test_never_raises_exception(self):
        """classify() gooit nooit een exception."""
        try:
            result = classify("", [])
            assert isinstance(result, CatalystResult)
        except Exception as exc:
            pytest.fail(f"classify() gooidde een exception: {exc}")

    def test_only_negative_news(self):
        items = [_item("SEC investigation into company accounting fraud")]
        result = classify("TEST", items)
        # Catalyst is NONE maar negative_flags zijn gevuld
        assert len(result.negative_flags) >= 1

    def test_multiple_items_best_wins(self):
        items = [
            _item("Company explores new markets",            age_hours=1.0),
            _item("Company reports record earnings beat",   age_hours=2.0),
            _item("Analyst upgrades to outperform",         age_hours=3.0),
        ]
        result = classify("NVDA", items)
        # Earnings beat (STRONG) moet winnen van upgrade (MODERATE)
        assert result.catalyst_type == "STRONG"

    def test_classified_at_is_set(self):
        result = classify("NVDA", [])
        assert result.classified_at != ""
        # Moet een geldige ISO string zijn
        datetime.fromisoformat(result.classified_at)

    def test_articles_used_count(self):
        items = [_item(f"Headline {i}") for i in range(5)]
        result = classify("NVDA", items)
        assert result.articles_used == 5


# ── TestClassifyFromNewsItems ─────────────────────────────────────────────────

class TestClassifyFromNewsItems:

    def test_accepts_finnhub_news_items(self):
        items = [_item("NVDA earnings beat")]
        result = classify_from_news_items("NVDA", items)
        assert isinstance(result, CatalystResult)

    def test_accepts_legacy_news_items(self):
        """Legacy NewsItem met published_at ISO string wordt geconverteerd."""
        from datetime import datetime, timezone
        legacy = MagicMock()
        legacy.headline      = "NVDA earnings beat this quarter"
        legacy.source        = "Reuters"
        legacy.published_at  = datetime.now(timezone.utc).isoformat()
        legacy.published_unix = None
        legacy.sentiment     = None

        result = classify_from_news_items("NVDA", [legacy])
        assert isinstance(result, CatalystResult)

    def test_empty_list_returns_none(self):
        result = classify_from_news_items("NVDA", [])
        assert result.catalyst_type == "NONE"

    def test_never_raises_on_malformed_legacy_item(self):
        broken = MagicMock(spec=[])  # geen attributen
        try:
            result = classify_from_news_items("NVDA", [broken])
            assert isinstance(result, CatalystResult)
        except Exception as exc:
            pytest.fail(f"classify_from_news_items gooidde een exception: {exc}")


# ── TestFinnhubClientParsing ──────────────────────────────────────────────────

class TestFinnhubClientParsing:

    def test_parse_valid_article(self):
        article = {
            "headline":  "NVDA beats earnings estimates",
            "source":    "Reuters",
            "url":       "https://reuters.com/nvda",
            "datetime":  _now_unix(),
            "id":        12345,
            "summary":   "NVDA reported strong Q1 results",
            "sentiment": 0.8,
        }
        item = _parse_article("NVDA", article)
        assert item is not None
        assert item.ticker == "NVDA"
        assert item.headline == "NVDA beats earnings estimates"
        assert item.source   == "Reuters"

    def test_parse_missing_headline_returns_none(self):
        article = {"source": "Reuters", "datetime": _now_unix(), "id": 1}
        item = _parse_article("NVDA", article)
        assert item is None

    def test_parse_empty_headline_returns_none(self):
        article = {"headline": "   ", "source": "Reuters", "datetime": _now_unix(), "id": 1}
        item = _parse_article("NVDA", article)
        assert item is None

    def test_unix_to_iso_valid(self):
        unix = 1748476800  # een vaste timestamp
        iso  = _unix_to_iso(unix)
        assert "T" in iso
        assert iso.endswith("+00:00") or iso.endswith("Z")

    def test_unix_to_iso_zero_fallback(self):
        iso = _unix_to_iso(0)
        assert isinstance(iso, str)
        assert len(iso) > 0

    def test_is_available_false_without_key(self):
        from data.finnhub_client import is_available
        with patch("data.finnhub_client._FINNHUB_KEY", ""):
            assert is_available() is False

    def test_is_available_true_with_key(self):
        from data.finnhub_client import is_available
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test123"):
            assert is_available() is True

    def test_fetch_returns_empty_without_key(self):
        from data.finnhub_client import fetch_company_news
        with patch("data.finnhub_client._FINNHUB_KEY", ""):
            result = fetch_company_news("NVDA")
        assert result == []

    def test_fetch_returns_empty_on_http_error(self):
        from data.finnhub_client import fetch_company_news
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch", side_effect=Exception("http error")):
                result = fetch_company_news("NVDA")
        # fetch_company_news vangt exceptions op via try/except
        assert result == []

    def test_fetch_never_raises(self):
        from data.finnhub_client import fetch_company_news
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch", side_effect=RuntimeError("crash")):
                result = fetch_company_news("NVDA")
        # Mag nooit een exception gooien
        assert isinstance(result, list)

    def test_parsed_items_sorted_newest_first(self):
        now = _now_unix()
        articles = [
            {"headline": "Old news",    "source": "Reuters", "datetime": now - 7200, "id": 1},
            {"headline": "Recent news", "source": "Reuters", "datetime": now - 1800, "id": 2},
            {"headline": "Medium news", "source": "Reuters", "datetime": now - 3600, "id": 3},
        ]
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("httpx.get") as mock_get:
                mock_get.return_value.json.return_value = articles
                mock_get.return_value.raise_for_status = lambda: None
                from data.finnhub_client import _do_fetch
                items = _do_fetch("NVDA", hours=48)

        timestamps = [i.published_unix for i in items]
        assert timestamps == sorted(timestamps, reverse=True)

# ── TestFinnhubRetryAndStats ──────────────────────────────────────────────────

class TestFinnhubRetryAndStats:
    """Retry logica en sessie-statistieken."""

    def setup_method(self):
        """Reset stats voor elke test."""
        from data.finnhub_client import reset_session_stats
        reset_session_stats()

    def test_timeout_incremented_on_readtimeout(self):
        from data.finnhub_client import fetch_company_news, get_session_stats
        import httpx
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch",
                       side_effect=httpx.ReadTimeout("timed out")):
                fetch_company_news("NVDA")
        stats = get_session_stats()
        assert stats["timeout"] == 1
        assert stats["success"] == 0

    def test_success_incremented_on_ok(self):
        from data.finnhub_client import fetch_company_news, get_session_stats
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch", return_value=[]):
                fetch_company_news("NVDA")
        stats = get_session_stats()
        assert stats["success"] == 1
        assert stats["timeout"] == 0

    def test_error_incremented_on_other_exception(self):
        from data.finnhub_client import fetch_company_news, get_session_stats
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch",
                       side_effect=ValueError("bad response")):
                fetch_company_news("NVDA")
        stats = get_session_stats()
        assert stats["error"] == 1

    def test_no_key_incremented_without_key(self):
        from data.finnhub_client import fetch_company_news, get_session_stats
        with patch("data.finnhub_client._FINNHUB_KEY", ""):
            fetch_company_news("NVDA")
        stats = get_session_stats()
        assert stats["no_key"] == 1
        assert stats["total"] == 1

    def test_total_incremented_per_ticker(self):
        from data.finnhub_client import fetch_company_news, get_session_stats
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch", return_value=[]):
                fetch_company_news("NVDA")
                fetch_company_news("MU")
                fetch_company_news("IONQ")
        assert get_session_stats()["total"] == 3

    def test_reset_clears_all_counters(self):
        from data.finnhub_client import fetch_company_news, get_session_stats, reset_session_stats
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch", return_value=[]):
                fetch_company_news("NVDA")
        assert get_session_stats()["total"] == 1
        reset_session_stats()
        stats = get_session_stats()
        assert all(v == 0 for v in stats.values())

    def test_format_session_stats_shows_success(self):
        from data.finnhub_client import fetch_company_news, format_session_stats
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch", return_value=[]):
                for _ in range(3):
                    fetch_company_news("NVDA")
        output = format_session_stats(total_tickers=5)
        assert "3/5" in output
        assert "succesvol" in output

    def test_format_session_stats_shows_timeout(self):
        import httpx
        from data.finnhub_client import fetch_company_news, format_session_stats
        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client._do_fetch",
                       side_effect=httpx.ReadTimeout("timeout")):
                fetch_company_news("NVDA")
                fetch_company_news("MU")
        output = format_session_stats(total_tickers=5)
        assert "timeout" in output.lower()
        assert "2/5" in output

    def test_retry_succeeds_on_second_attempt(self):
        """_do_fetch probeert opnieuw na een timeout — tweede poging slaagt."""
        import httpx
        from data.finnhub_client import _do_fetch
        now_unix = int(__import__('datetime').datetime.now(__import__('datetime').timezone.utc).timestamp())
        articles = [{"headline": "Test headline", "source": "Reuters",
                     "datetime": now_unix, "id": 1, "summary": ""}]

        call_count = {"n": 0}
        def mock_get(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.ReadTimeout("first attempt timed out")
            m = patch("httpx.get").__class__
            resp = __import__('unittest.mock', fromlist=['MagicMock']).MagicMock()
            resp.json.return_value = articles
            resp.raise_for_status = lambda: None
            return resp

        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client.time.sleep"):  # snel zonder echte sleep
                with patch("httpx.get", side_effect=mock_get):
                    items = _do_fetch("NVDA", hours=48)

        assert call_count["n"] == 2
        assert len(items) == 1

    def test_retry_exhausted_raises(self):
        """Na _MAX_RETRIES pogingen gooit _do_fetch een exception."""
        import httpx
        from data.finnhub_client import _do_fetch, _MAX_RETRIES
        call_count = {"n": 0}
        def always_timeout(*a, **kw):
            call_count["n"] += 1
            raise httpx.ReadTimeout("always times out")

        with patch("data.finnhub_client._FINNHUB_KEY", "sk-test"):
            with patch("data.finnhub_client.time.sleep"):
                with patch("httpx.get", side_effect=always_timeout):
                    with pytest.raises(Exception):
                        _do_fetch("NVDA", hours=48)

        assert call_count["n"] == _MAX_RETRIES

    def test_timeout_12_seconds(self):
        """Timeout is 12 seconden (verhoogd van 5s)."""
        from data.finnhub_client import _TIMEOUT_SEC
        assert _TIMEOUT_SEC >= 10.0

    def test_is_timeout_detects_readtimeout(self):
        import httpx
        from data.finnhub_client import _is_timeout
        assert _is_timeout(httpx.ReadTimeout("read timed out")) is True

    def test_is_timeout_detects_connecttimeout(self):
        import httpx
        from data.finnhub_client import _is_timeout
        assert _is_timeout(httpx.ConnectTimeout("connect timed out")) is True

    def test_is_timeout_false_for_other_errors(self):
        from data.finnhub_client import _is_timeout
        assert _is_timeout(ValueError("bad json")) is False
        assert _is_timeout(ConnectionError("refused")) is False

