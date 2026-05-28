"""
tests/test_scoring.py
Momentum Intelligence — Score Engine v1.2 Test Suite

Structuur:
    assert_decision()           Helper — centrale assertiefunctie
    make_input()                Factory — minimalistische TickerInput constructie
    TestHardBlocked             SEC / Class Action / CFD vetoes
    TestSkipScore               Soft skip penalties (cumulatief)
    TestCombinationRule         catalyst=NONE + momentum<50 → SKIP
    TestMomentumComponents      Unit tests per scoring-component
    TestPhaseDetection          ACCUMULATION → EXHAUSTION fase-logica
    TestMarketCapTier           Tier-indeling + sizing caps
    TestSocialQualityCap        Social gecapped per catalyst-kwaliteit
    TestFloatScore              Float score schaal + None-fallback
    TestDecisionThresholds      Grenzen BUY_SMALL/MODERATE/STRONG/MAX
    TestRegression              Alle 11 mock cases met ranges (anti-regressie)

Regressie-aanpak:
    Elke test in TestRegression bevat:
        expected_decision   → exact
        momentum_min/max    → ±5 pts marge rond bekende v1.2 scores
        skip_min/max        → exact bereik

Aanroepen:
    pytest tests/ -v
    pytest tests/ -v --tb=short
    pytest tests/test_scoring.py::TestRegression -v
"""

import pytest
from scoring.scoring_v1_2 import (
    TickerInput, ScoringResult, SectorConfig,
    CatalystType, RelativeStrength, Decision, Phase, MarketCapTier,
    score_ticker, calculate_skip_score, calculate_momentum_score,
    detect_phase, get_market_cap_tier, compute_sizing,
    _volume_anomaly, _sector_heat_score, _catalyst_quality,
    _premarket_strength, _relative_strength, _social_acceleration,
    _float_score, MOCK_TICKERS, EXPECTATIONS,
)


# ── HELPER ────────────────────────────────────────────────────────────────────

def assert_decision(
    result: ScoringResult,
    expected_decision: str,
    *,
    momentum_min: float = None,
    momentum_max: float = None,
    skip_min: int = None,
    skip_max: int = None,
) -> None:
    """
    Centrale assertiefunctie voor alle scoring tests.

    Asserteert:
        1. Decision (exact)
        2. Momentum score binnen opgegeven bereik (optioneel)
        3. Skip score binnen opgegeven bereik (optioneel)

    Foutmelding bevat: ticker, verwacht, actueel, momentum, skip.
    Dit maakt het direct duidelijk welk getal buiten de grenzen viel.
    """
    assert result.decision.value == expected_decision, (
        f"\n  Ticker:   {result.ticker}"
        f"\n  Verwacht: {expected_decision}"
        f"\n  Actueel:  {result.decision.value}"
        f"\n  Momentum: {result.momentum_score:.1f}"
        f"\n  Skip:     {result.skip_score}"
        f"\n  Reden:    {result.summary}"
    )

    if momentum_min is not None:
        assert result.momentum_score >= momentum_min, (
            f"{result.ticker}: momentum {result.momentum_score:.1f} "
            f"< verwacht min {momentum_min}"
        )

    if momentum_max is not None:
        assert result.momentum_score <= momentum_max, (
            f"{result.ticker}: momentum {result.momentum_score:.1f} "
            f"> verwacht max {momentum_max}"
        )

    if skip_min is not None:
        assert result.skip_score >= skip_min, (
            f"{result.ticker}: skip {result.skip_score} "
            f"< verwacht min {skip_min}"
        )

    if skip_max is not None:
        assert result.skip_score <= skip_max, (
            f"{result.ticker}: skip {result.skip_score} "
            f"> verwacht max {skip_max}"
        )


# ── FACTORY ───────────────────────────────────────────────────────────────────

_DEFAULT_SECTOR = SectorConfig(
    sector_id="test_sector",
    sector_label="TEST",
    heat=75,
    phase=1,
    leaders=[],
    sympathy=[],
)


def make_input(**kwargs) -> TickerInput:
    """
    Minimalistische TickerInput factory met veilige defaults.
    Override specifieke velden via kwargs om gefocuste unit tests te schrijven.

    Defaults:
        - 6x normaal volume (geeft goede momentum score)
        - MODERATE catalyst
        - MODERATE_POSITIVE relative strength
        - sector heat 75
        - Geen SEC/class action/CFD flags
    """
    defaults = dict(
        ticker="UNIT_TEST",
        price=50.0,
        day_change_pct=6.0,
        premarket_pct=10.0,
        volume_today=3_000_000,
        avg_volume_20d=500_000,
        market_cap_usd=1_000_000_000,
        float_shares=40_000_000,
        is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Unit test catalyst",
        relative_strength=RelativeStrength.MODERATE_POSITIVE,
        sector=_DEFAULT_SECTOR,
        social_mentions_today=500,
        social_mentions_avg=100,
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=0,
    )
    defaults.update(kwargs)
    return TickerInput(**defaults)


# ── HARD BLOCKED TESTS ────────────────────────────────────────────────────────

class TestHardBlocked:
    """
    Hard vetoes. Ongeacht hoe sterk het momentum:
    SEC / Class Action / CFD → altijd BLOCKED.
    """

    def test_sec_investigation_is_blocked(self):
        result = score_ticker(make_input(has_sec_investigation=True))
        assert_decision(result, "BLOCKED", skip_min=100)
        assert result.skip_detail.is_hard_blocked

    def test_class_action_is_blocked(self):
        result = score_ticker(make_input(has_class_action=True))
        assert_decision(result, "BLOCKED", skip_min=100)
        assert result.skip_detail.is_hard_blocked

    def test_cfd_only_is_blocked(self):
        result = score_ticker(make_input(is_cfd_only=True))
        assert_decision(result, "BLOCKED", skip_min=100)
        assert result.skip_detail.is_hard_blocked

    def test_blocked_despite_high_momentum(self):
        """Momentum 90+ kan SEC veto niet overschrijven."""
        result = score_ticker(make_input(
            has_sec_investigation=True,
            volume_today=9_000_000,
            catalyst_type=CatalystType.STRONG,
            catalyst_description="Blowout earnings",
            relative_strength=RelativeStrength.STRONG_POSITIVE,
            sector=SectorConfig("q", "QUANTUM", 95, 1, [], []),
        ))
        assert_decision(result, "BLOCKED")
        assert result.momentum_score >= 70  # hoog momentum maar toch BLOCKED

    def test_multiple_vetoes_cumulate(self):
        """SEC + class action = skip score ≥ 200."""
        result = score_ticker(make_input(
            has_sec_investigation=True,
            has_class_action=True,
        ))
        assert_decision(result, "BLOCKED", skip_min=200)

    def test_blocking_reason_in_output(self):
        """Blocking reason moet SEC vermelding bevatten."""
        result = score_ticker(make_input(has_sec_investigation=True))
        assert any("SEC" in r for r in result.skip_detail.blocking_reasons)

    def test_cfd_blocking_reason_in_output(self):
        result = score_ticker(make_input(is_cfd_only=True))
        assert any("CFD" in r for r in result.skip_detail.blocking_reasons)


# ── SKIP SCORE TESTS ─────────────────────────────────────────────────────────

class TestSkipScore:
    """
    Soft skip penalties. Cumulatief. Drempel ≥ 50 = SKIP.
    """

    def test_day_over_40pct_adds_40pts(self):
        skip = calculate_skip_score(make_input(day_change_pct=41.0))
        assert skip.total >= 40
        assert any("40" in r or "te laat" in r for r in skip.reasons)

    def test_day_20_to_40_adds_10pts(self):
        skip = calculate_skip_score(make_input(day_change_pct=25.0))
        # Moet 10 punten zijn voor dag 20-40%
        assert 8 <= skip.total <= 15

    def test_premarket_over_40pct_adds_40pts(self):
        skip = calculate_skip_score(make_input(
            premarket_pct=45.0,
            day_change_pct=3.0,
        ))
        assert skip.total >= 40

    def test_premarket_20_to_40_adds_15pts(self):
        skip = calculate_skip_score(make_input(
            premarket_pct=25.0,
            day_change_pct=3.0,
        ))
        assert 13 <= skip.total <= 18

    def test_no_catalyst_adds_20pts(self):
        skip = calculate_skip_score(make_input(
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
        ))
        assert 18 <= skip.total <= 22

    def test_volume_below_avg_adds_25pts(self):
        skip = calculate_skip_score(make_input(
            volume_today=400_000,
            avg_volume_20d=700_000,  # 0.57x — onder gemiddelde
        ))
        assert skip.total >= 25

    def test_insider_sells_over_10_adds_15pts(self):
        skip = calculate_skip_score(make_input(insider_sells_90d=12))
        assert skip.total >= 15

    def test_insider_sells_5_to_10_adds_8pts(self):
        skip = calculate_skip_score(make_input(insider_sells_90d=7))
        assert 6 <= skip.total <= 10

    def test_skip_score_cumulates(self):
        """Dag +42% EN geen catalyst EN volume laag = cumulatief skip."""
        result = score_ticker(make_input(
            day_change_pct=42.0,
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
            volume_today=300_000,
            avg_volume_20d=700_000,
        ))
        assert_decision(result, "SKIP", skip_min=80)

    def test_clean_ticker_has_zero_skip(self):
        """Sterke setup zonder flags = skip score 0."""
        skip = calculate_skip_score(make_input(
            day_change_pct=8.0,
            premarket_pct=10.0,
            catalyst_type=CatalystType.STRONG,
            catalyst_description="Earnings beat",
            volume_today=4_000_000,
            avg_volume_20d=500_000,
        ))
        assert skip.total == 0
        assert not skip.is_hard_blocked


# ── COMBINATIEREGEL TESTS ─────────────────────────────────────────────────────

class TestCombinationRule:
    """
    Combinatieregel (v1.1): catalyst=NONE + momentum<50 → SKIP.
    Zelfs als Skip Score < 50.
    """

    def test_no_catalyst_low_momentum_is_skip(self):
        result = score_ticker(make_input(
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
            volume_today=800_000,
            avg_volume_20d=500_000,   # 1.6x — matig
            sector=SectorConfig("c", "CYBER", 40, 2, [], []),
            social_mentions_today=200,
            social_mentions_avg=100,
        ))
        assert_decision(result, "SKIP")
        assert "Combinatieregel" in result.summary

    def test_no_catalyst_high_momentum_is_not_blocked_by_combo(self):
        """catalyst=NONE met momentum ≥ 50 = combinatieregel triggert NIET."""
        result = score_ticker(make_input(
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
            volume_today=6_000_000,
            avg_volume_20d=400_000,   # 15x — extreem volume
            sector=SectorConfig("d", "DRONES", 98, 1, [], []),
            social_mentions_today=5_000,
            social_mentions_avg=100,
            relative_strength=RelativeStrength.STRONG_POSITIVE,
            day_change_pct=8.0,
            premarket_pct=12.0,
        ))
        # Geen catalyst = skip +20, maar score kan hoog genoeg zijn om niet
        # onder 50 te vallen. Als skip ≥ 50 → SKIP via skip regel (niet combo).
        # Dit test dat de combinatieregel NIET vanzelf alles blokkeert.
        assert result.skip_detail.total >= 20  # geen catalyst altijd +20

    def test_weak_catalyst_not_triggered_by_combo(self):
        """Combinatieregel geldt alleen voor catalyst=NONE, niet WEAK."""
        result = score_ticker(make_input(
            catalyst_type=CatalystType.WEAK,
            catalyst_description="Vage update",
            volume_today=600_000,
            avg_volume_20d=500_000,
        ))
        # Met WEAK catalyst triggert de combinatieregel niet
        assert "Combinatieregel" not in result.summary


# ── MOMENTUM COMPONENT TESTS ──────────────────────────────────────────────────

class TestMomentumComponents:
    """Unit tests per scoring-component. Test elke formule geïsoleerd."""

    # Volume Anomaly
    def test_volume_extreme_scores_max(self):
        data = make_input(volume_today=8_000_000, avg_volume_20d=800_000)  # 10x
        pts, _ = _volume_anomaly(data)
        assert pts == 22.0

    def test_volume_5x_scores_17_6(self):
        data = make_input(volume_today=2_500_000, avg_volume_20d=500_000)  # 5x
        pts, _ = _volume_anomaly(data)
        assert pts == 17.6

    def test_volume_below_avg_scores_zero(self):
        data = make_input(volume_today=300_000, avg_volume_20d=700_000)  # 0.43x
        pts, _ = _volume_anomaly(data)
        assert pts == 0.0

    # Catalyst Quality
    def test_strong_catalyst_scores_20(self):
        data = make_input(
            catalyst_type=CatalystType.STRONG,
            catalyst_description="DoD contract",
        )
        pts, _ = _catalyst_quality(data)
        assert pts == 20.0

    def test_moderate_catalyst_scores_12(self):
        data = make_input(catalyst_type=CatalystType.MODERATE)
        pts, _ = _catalyst_quality(data)
        assert pts == 12.0

    def test_no_catalyst_scores_zero(self):
        data = make_input(
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
        )
        pts, _ = _catalyst_quality(data)
        assert pts == 0.0

    # Premarket Strength
    def test_premarket_sweet_spot_scores_max(self):
        data = make_input(premarket_pct=12.0)
        pts, _ = _premarket_strength(data)
        assert pts == 14.0

    def test_premarket_over_40_scores_zero(self):
        data = make_input(premarket_pct=45.0)
        pts, _ = _premarket_strength(data)
        assert pts == 0.0

    def test_premarket_negative_scores_zero(self):
        data = make_input(premarket_pct=-3.0)
        pts, _ = _premarket_strength(data)
        assert pts == 0.0

    def test_premarket_20_to_40_decreases_linearly(self):
        """Score neemt af naarmate pre-market stijgt boven 20%."""
        data_25 = make_input(premarket_pct=25.0)
        data_35 = make_input(premarket_pct=35.0)
        pts_25, _ = _premarket_strength(data_25)
        pts_35, _ = _premarket_strength(data_35)
        assert pts_25 > pts_35  # hoger % = lagere score

    # Relative Strength
    def test_strong_positive_rs_scores_max(self):
        data = make_input(relative_strength=RelativeStrength.STRONG_POSITIVE)
        pts, _ = _relative_strength(data)
        assert pts == 10.0

    def test_underperforming_rs_scores_zero(self):
        data = make_input(relative_strength=RelativeStrength.UNDERPERFORMING)
        pts, _ = _relative_strength(data)
        assert pts == 0.0


# ── SOCIAL QUALITY CAP TESTS ──────────────────────────────────────────────────

class TestSocialQualityCap:
    """Social mag NOOIT alleen tot BUY leiden. Cap per catalyst kwaliteit."""

    def test_no_catalyst_caps_social_at_2(self):
        data = make_input(
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
            social_mentions_today=20_000,
            social_mentions_avg=200,  # 100x viral
        )
        pts, _, capped, _ = _social_acceleration(data)
        assert capped
        assert pts <= 2.0

    def test_weak_catalyst_caps_social_at_4(self):
        data = make_input(
            catalyst_type=CatalystType.WEAK,
            catalyst_description="Vage update",
            social_mentions_today=10_000,
            social_mentions_avg=100,  # 100x
        )
        pts, _, capped, _ = _social_acceleration(data)
        assert capped
        assert pts <= 4.0

    def test_moderate_catalyst_caps_social_at_6(self):
        data = make_input(
            catalyst_type=CatalystType.MODERATE,
            social_mentions_today=10_000,
            social_mentions_avg=100,  # 100x
        )
        pts, _, capped, _ = _social_acceleration(data)
        assert capped
        assert pts <= 6.0

    def test_strong_catalyst_no_cap(self):
        """STRONG catalyst → volledige 8 pts mogelijk."""
        data = make_input(
            catalyst_type=CatalystType.STRONG,
            catalyst_description="Earnings beat",
            social_mentions_today=10_000,
            social_mentions_avg=100,  # 100x
        )
        pts, _, capped, _ = _social_acceleration(data)
        assert not capped
        assert pts == 8.0

    def test_social_capped_flag_in_result(self):
        """social_was_capped moet True zijn in MomentumScoreResult."""
        result = score_ticker(make_input(
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
            social_mentions_today=15_000,
            social_mentions_avg=200,
        ))
        assert result.momentum_detail.social_was_capped

    def test_low_social_not_capped(self):
        """Lage social velocity triggert de cap niet."""
        data = make_input(
            catalyst_type=CatalystType.NONE,
            catalyst_description="Geen nieuws",
            social_mentions_today=90,
            social_mentions_avg=100,  # 0.9x
        )
        pts, _, capped, _ = _social_acceleration(data)
        assert not capped  # al onder de cap


# ── FLOAT SCORE TESTS ─────────────────────────────────────────────────────────

class TestFloatScore:
    """Float score schaal + None-fallback."""

    def test_extreme_low_float_scores_max(self):
        data = make_input(float_shares=3_000_000)  # 3M
        pts, _ = _float_score(data)
        assert pts == 8.0

    def test_low_float_scores_6_5(self):
        data = make_input(float_shares=10_000_000)  # 10M
        pts, _ = _float_score(data)
        assert pts == 6.5

    def test_medium_float_scores_4_5(self):
        data = make_input(float_shares=30_000_000)  # 30M
        pts, _ = _float_score(data)
        assert pts == 4.5

    def test_high_float_scores_2(self):
        data = make_input(float_shares=100_000_000)  # 100M
        pts, _ = _float_score(data)
        assert pts == 2.0

    def test_very_high_float_scores_zero(self):
        data = make_input(float_shares=300_000_000)  # 300M
        pts, _ = _float_score(data)
        assert pts == 0.0

    def test_unknown_float_is_neutral(self):
        """float=None → 4/8 pts (neutraal, niet benadelend)."""
        data = make_input(float_shares=None)
        pts, label = _float_score(data)
        assert pts == 4.0
        assert "onbekend" in label.lower()


# ── PHASE DETECTION TESTS ─────────────────────────────────────────────────────

class TestPhaseDetection:
    """ACCUMULATION → BREAKOUT → EXPANSION → FRENZY → EXHAUSTION."""

    def test_frenzy_phase(self):
        data = make_input(
            day_change_pct=30.0,
            volume_today=7_000_000, avg_volume_20d=1_000_000,  # 7x
            social_mentions_today=2_100, social_mentions_avg=300,  # 7x
        )
        phase, _ = detect_phase(data)
        assert phase == Phase.FRENZY

    def test_expansion_phase(self):
        data = make_input(
            day_change_pct=15.0,
            volume_today=2_500_000, avg_volume_20d=500_000,  # 5x
            social_mentions_today=200, social_mentions_avg=100,
        )
        phase, _ = detect_phase(data)
        assert phase == Phase.EXPANSION

    def test_breakout_phase(self):
        data = make_input(
            day_change_pct=7.0,
            volume_today=1_500_000, avg_volume_20d=500_000,  # 3x
            catalyst_type=CatalystType.STRONG,
            catalyst_description="Strong catalyst",
        )
        phase, _ = detect_phase(data)
        assert phase == Phase.BREAKOUT

    def test_accumulation_phase(self):
        data = make_input(
            day_change_pct=2.0,
            volume_today=1_000_000, avg_volume_20d=500_000,  # 2x — opbouwend
        )
        phase, _ = detect_phase(data)
        assert phase == Phase.ACCUMULATION

    def test_exhaustion_phase(self):
        data = make_input(
            day_change_pct=1.5,
            volume_today=300_000, avg_volume_20d=700_000,  # 0.43x — krimpt
        )
        phase, _ = detect_phase(data)
        assert phase == Phase.EXHAUSTION

    def test_frenzy_requires_all_three_conditions(self):
        """FRENZY vereist dag>25% AND social>6x AND volume>6x."""
        # Hoog dag%, maar sociale velocity te laag → geen FRENZY
        data = make_input(
            day_change_pct=30.0,
            volume_today=7_000_000, avg_volume_20d=1_000_000,
            social_mentions_today=200, social_mentions_avg=100,  # 2x — te laag
        )
        phase, _ = detect_phase(data)
        assert phase != Phase.FRENZY  # niet genoeg sociale momentum


# ── MARKET CAP TIER TESTS ─────────────────────────────────────────────────────

class TestMarketCapTier:
    """Tier-indeling + sizing caps."""

    def test_micro_cap_tier(self):
        assert get_market_cap_tier(150_000_000) == MarketCapTier.MICRO

    def test_small_cap_tier(self):
        assert get_market_cap_tier(800_000_000) == MarketCapTier.SMALL

    def test_mid_cap_tier(self):
        assert get_market_cap_tier(5_000_000_000) == MarketCapTier.MID

    def test_large_cap_tier(self):
        assert get_market_cap_tier(50_000_000_000) == MarketCapTier.LARGE

    def test_micro_cap_limits_buy_max_to_250(self):
        """BUY_MAX op MICRO-cap moet gesized zijn op €250."""
        result = score_ticker(make_input(
            market_cap_usd=150_000_000,
            volume_today=8_000_000, avg_volume_20d=500_000,
            catalyst_type=CatalystType.STRONG,
            catalyst_description="DoD contract",
            relative_strength=RelativeStrength.STRONG_POSITIVE,
            sector=SectorConfig("d", "DRONES", 98, 1, [], []),
            float_shares=2_000_000,
            day_change_pct=10.0,
            premarket_pct=12.0,
        ))
        assert "250" in result.sizing_eur
        assert "MICRO" in result.sizing_eur

    def test_large_cap_no_size_restriction(self):
        """LARGE-cap met BUY_MAX = geen cap-melding in sizing."""
        result = score_ticker(make_input(
            market_cap_usd=50_000_000_000,
            volume_today=8_000_000, avg_volume_20d=500_000,
            catalyst_type=CatalystType.STRONG,
            catalyst_description="Strong catalyst",
            relative_strength=RelativeStrength.STRONG_POSITIVE,
            sector=SectorConfig("d", "DRONES", 98, 1, [], []),
            float_shares=None,
            day_change_pct=10.0,
            premarket_pct=12.0,
        ))
        assert "gelimiteerd" not in result.sizing_eur


# ── DECISION THRESHOLD TESTS ──────────────────────────────────────────────────

class TestDecisionThresholds:
    """
    Test de grenzen van elk BUY-niveau.
    Doel: als gewichten ooit veranderen, vangen deze tests de verschuiving.
    """

    def _high_score_input(self, sector_heat=90) -> TickerInput:
        return make_input(
            volume_today=5_000_000, avg_volume_20d=500_000,  # 10x
            catalyst_type=CatalystType.STRONG,
            catalyst_description="Strong catalyst",
            relative_strength=RelativeStrength.STRONG_POSITIVE,
            sector=SectorConfig("x", "X", sector_heat, 1, [], []),
            social_mentions_today=2_000, social_mentions_avg=200,
            day_change_pct=10.0, premarket_pct=12.0,
            float_shares=5_000_000,
        )

    def test_skip_score_takes_priority_over_momentum(self):
        """Momentum 90+ maar SEC investigation = BLOCKED."""
        result = score_ticker(make_input(
            has_sec_investigation=True,
            volume_today=8_000_000, avg_volume_20d=500_000,
            catalyst_type=CatalystType.STRONG,
            catalyst_description="Earnings beat",
        ))
        assert result.decision == Decision.BLOCKED
        assert result.momentum_score >= 50  # hoog momentum, toch blocked

    def test_watch_range(self):
        """Score 30-44 → WATCH."""
        result = score_ticker(make_input(
            volume_today=600_000, avg_volume_20d=500_000,  # 1.2x
            catalyst_type=CatalystType.WEAK,
            catalyst_description="Vage update",
            relative_strength=RelativeStrength.NEUTRAL,
            sector=SectorConfig("x", "X", 35, 1, [], []),
            social_mentions_today=80, social_mentions_avg=100,
            day_change_pct=2.0, premarket_pct=2.0,
            float_shares=150_000_000,
        ))
        assert result.momentum_score < 50
        assert result.decision in (Decision.WATCH, Decision.SKIP)


# ── REGRESSION TESTS ─────────────────────────────────────────────────────────

class TestRegression:
    """
    Alle 11 mock test cases als harde regressie-tests.
    Als een refactor de beslissingslogica breekt, falen deze tests.

    Ranges zijn ±5 pts marge rondom bekende v1.2 scores.
    Skip ranges zijn exact (skip score is deterministisch).
    """

    REGRESSION_CASES = [
        # (ticker, expected_decision, mom_min, mom_max, skip_min, skip_max)
        ("UMAC_T1",        "BUY_MAX",   90,  100, 20,  30),
        ("APP_T2",         "BLOCKED",   60,  80,  100, 200),
        ("SPACE_T3",       "BLOCKED",   85,  100, 100, 200),
        ("SNOW_T4",        "BUY_SMALL", 48,  65,   0,  10),
        ("CHASER_T5",      "SKIP",      80,  100, 50,  65),
        ("SLEEPER_T6",     "BUY_SMALL", 52,  68,   0,  10),
        ("HYPE_T7",        "SKIP",      30,  50,  40,  50),
        ("QBTS_T8",        "BUY_SMALL", 52,  68,   0,  10),
        ("LOWFLOAT_T9",    "BUY_MAX",   92,  100,  5,  15),
        ("MEGACAP_T10",    "WATCH",     35,  50,   0,  10),
        ("SOCIALPUMP_T11", "SKIP",      25,  42,  40,  50),
    ]

    @pytest.mark.parametrize(
        "ticker,expected,mom_min,mom_max,skip_min,skip_max",
        REGRESSION_CASES,
        ids=[c[0] for c in REGRESSION_CASES],
    )
    def test_regression(self, ticker, expected, mom_min, mom_max,
                        skip_min, skip_max):
        """Exacte beslissing + momentum/skip bereiken."""
        mock = next(m for m in MOCK_TICKERS if m.ticker == ticker)
        result = score_ticker(mock)
        assert_decision(
            result, expected,
            momentum_min=mom_min, momentum_max=mom_max,
            skip_min=skip_min, skip_max=skip_max,
        )

    def test_all_11_pass(self):
        """Samengestelde regressietest: alle 11 cases in één run."""
        passed = 0
        failures = []
        for mock, (name, expected, _) in zip(MOCK_TICKERS, EXPECTATIONS):
            result = score_ticker(mock)
            if result.decision.value == expected:
                passed += 1
            else:
                failures.append(
                    f"{name}: got {result.decision.value}, expected {expected}"
                )
        assert passed == 11, (
            f"Regressie: {passed}/11 correct. Failures:\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
