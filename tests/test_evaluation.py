"""
tests/test_evaluation.py
Signal Evaluation Layer Tests — v2.7

Coverage:
    TestEvaluationStore       Save/load/delete outcomes
    TestGradeLogic            Grade correctheid per grade type
    TestFuturePriceLookup     Tijdshorizon matching + edge cases
    TestSignalEvaluator       evaluate_snapshot, evaluate_ticker
    TestSignalStatistics      compute_signal_statistics breakdown
    TestMissingDataHandling   Geen toekomstige data → PENDING
    TestEvaluationReport      Markdown + JSON export
    TestEvaluationEndpoints   API tests voor /evaluation/*
    TestDecayEvaluation       Welke signalen verliezen snel kracht?
    TestEdgeCases             Corrupte snapshots, nul-prijs, etc.
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from dataclasses import asdict
from fastapi.testclient import TestClient

from backend.app import app
from schemas.ticker_snapshot import TickerSnapshot, DataConfidence

client = TestClient(app)


# ── TEST ISOLATIE ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    tickers_dir   = tmp_path / "tickers"
    sectors_dir   = tmp_path / "sectors"
    evals_dir     = tmp_path / "evaluations"
    tickers_dir.mkdir(); sectors_dir.mkdir(); evals_dir.mkdir()

    monkeypatch.setattr("storage.snapshot_store._TICKERS_DIR",   str(tickers_dir))
    monkeypatch.setattr("storage.signal_tracker._TICKERS_DIR",   str(tickers_dir))
    monkeypatch.setattr("storage.sector_history._SECTORS_DIR",   str(sectors_dir))
    monkeypatch.setattr("storage.evaluation_store._EVALS_DIR",   str(evals_dir))

    reviews_dir = tmp_path / "signal_reviews"
    reviews_dir.mkdir()
    monkeypatch.setattr("research.evaluation_report._REVIEWS_DIR", str(reviews_dir))

    # Patch ook observation_store dirs
    obs_dir   = tmp_path / "observations"
    notes_dir = tmp_path / "replay_notes"
    obs_dir.mkdir(); notes_dir.mkdir()
    monkeypatch.setattr("research.observation_store._OBS_DIR",   str(obs_dir))
    monkeypatch.setattr("research.observation_store._NOTES_DIR", str(notes_dir))

    yield tmp_path


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _make_snapshot(
    ticker="TEST", score=70.0, decision="BUY_MODERATE", phase="BREAKOUT",
    price=50.0, catalyst="STRONG", ts=None,
) -> dict:
    now = ts or datetime.now(timezone.utc).isoformat()
    return {
        "version_id": f"V_{now[:19].replace(':','').replace('-','').replace('T','_')}_{ticker}",
        "ticker": ticker, "timestamp": now,
        "decision": decision, "momentum_score": score, "skip_score": 0,
        "phase": phase, "confidence": "LIVE", "cache_hit": False,
        "data_age_seconds": 0.0, "retries_used": 0,
        "catalyst_type": catalyst, "catalyst_description": "test",
        "day_change_pct": 6.0, "volume_ratio": 3.0,
        "sector_heat": 85, "sector_id": "quantum",
        "market_session": "REGULAR", "price": price,
        "premarket_pct": 0.0, "stored_at": now,
    }


def _save_snap(ticker, score=65.0, decision="BUY_MODERATE", price=50.0,
               phase="BREAKOUT", catalyst="STRONG", ts=None):
    from storage.snapshot_store import save_snapshot_dict
    snap = _make_snapshot(ticker, score, decision, phase, price, catalyst, ts)
    save_snapshot_dict(ticker, snap)
    return snap


def _make_outcome(ticker="TEST", decision="BUY_MODERATE", score=70.0,
                  phase="BREAKOUT", entry_price=50.0,
                  return_1d=5.0, grade="SUCCESS"):
    from storage.evaluation_store import SignalOutcome
    now = datetime.now(timezone.utc).isoformat()
    return SignalOutcome(
        version_id=f"V_{ticker}_{now[:10]}",
        ticker=ticker, timestamp=now,
        decision=decision, momentum_score=score,
        phase=phase, catalyst_type="STRONG",
        sector_id="quantum", entry_price=entry_price,
        return_1d=return_1d,
        price_1d=entry_price * (1 + return_1d / 100) if return_1d else None,
        grade=grade, grade_basis="1d",
        graded_at=now, evaluated_at=now,
    )


def _mock_snapshot(ticker="TEST", price=50.0, volume=3_000_000):
    return TickerSnapshot(
        ticker=ticker, timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.LIVE,
        price=price, prev_close=price - 2, day_change_pct=6.0,
        premarket_pct=0.0, premarket_available=False,
        volume_today=volume, avg_volume_20d=500_000,
        market_cap=1e9, float_shares=40_000_000,
        cache_hit=False, data_age_seconds=0.0,
    )


# ── EVALUATION STORE TESTS ────────────────────────────────────────────────────

class TestEvaluationStore:

    def test_save_and_load_outcome(self):
        from storage.evaluation_store import save_outcome, load_outcomes
        o = _make_outcome("NVDA", grade="SUCCESS")
        save_outcome(o)
        loaded = load_outcomes("NVDA")
        assert len(loaded) == 1
        assert loaded[0]["grade"] == "SUCCESS"

    def test_save_overrides_same_version_id(self):
        """Zelfde version_id → overschrijft vorige entry."""
        from storage.evaluation_store import (
            save_outcome, load_outcomes, SignalOutcome
        )
        now = datetime.now(timezone.utc).isoformat()
        vid = "V_OVERRIDE_TEST"

        o1 = _make_outcome("OVR", grade="PENDING")
        o1.version_id = vid
        save_outcome(o1)

        o2 = _make_outcome("OVR", grade="SUCCESS")
        o2.version_id = vid
        save_outcome(o2)

        loaded = load_outcomes("OVR")
        assert len(loaded) == 1   # Geen duplicaten
        assert loaded[0]["grade"] == "SUCCESS"

    def test_load_graded_excludes_pending(self):
        from storage.evaluation_store import save_outcome, load_graded_outcomes
        o_pending = _make_outcome("GRDTEST", grade="PENDING")
        o_success = _make_outcome("GRDTEST", grade="SUCCESS")
        o_pending.version_id = "V_PENDING"
        o_success.version_id = "V_SUCCESS"
        save_outcome(o_pending)
        save_outcome(o_success)
        graded = load_graded_outcomes("GRDTEST")
        assert len(graded) == 1
        assert graded[0]["grade"] == "SUCCESS"

    def test_load_outcome_by_version(self):
        from storage.evaluation_store import save_outcome, load_outcome_by_version
        o = _make_outcome("VTEST", grade="FAILED")
        o.version_id = "V_SPECIFIC"
        save_outcome(o)
        found = load_outcome_by_version("VTEST", "V_SPECIFIC")
        assert found is not None
        assert found["grade"] == "FAILED"

    def test_list_evaluated_tickers(self):
        from storage.evaluation_store import save_outcome, list_evaluated_tickers
        save_outcome(_make_outcome("TICKER_A"))
        save_outcome(_make_outcome("TICKER_B"))
        tickers = list_evaluated_tickers()
        assert "TICKER_A" in tickers
        assert "TICKER_B" in tickers

    def test_delete_outcomes(self):
        from storage.evaluation_store import save_outcome, delete_outcomes, load_outcomes
        save_outcome(_make_outcome("DELTEST"))
        assert len(load_outcomes("DELTEST")) == 1
        result = delete_outcomes("DELTEST")
        assert result is True
        assert len(load_outcomes("DELTEST")) == 0

    def test_delete_nonexistent_returns_false(self):
        from storage.evaluation_store import delete_outcomes
        assert delete_outcomes("DOESNOTEXIST_EVAL") is False


# ── GRADE LOGIC TESTS ─────────────────────────────────────────────────────────

class TestGradeLogic:

    def test_buy_signal_success(self):
        from storage.signal_evaluator import grade_signal
        grade, basis = grade_signal("BUY_MODERATE", None, None, 5.0, None)
        assert grade == "SUCCESS"
        assert basis == "1d"

    def test_buy_signal_failed(self):
        from storage.signal_evaluator import grade_signal
        grade, basis = grade_signal("BUY_STRONG", None, None, -4.0, None)
        assert grade == "FAILED"

    def test_buy_signal_neutral(self):
        from storage.signal_evaluator import grade_signal
        grade, _ = grade_signal("BUY_SMALL", None, None, 1.5, None)
        assert grade == "NEUTRAL"

    def test_skip_signal_success_when_price_fell(self):
        """SKIP/BLOCKED is SUCCESS als prijs daalde."""
        from storage.signal_evaluator import grade_signal
        grade, basis = grade_signal("SKIP", None, None, -3.5, None)
        assert grade == "SUCCESS"

    def test_skip_signal_failed_when_price_rose(self):
        from storage.signal_evaluator import grade_signal
        grade, _ = grade_signal("BLOCKED", None, None, 4.0, None)
        assert grade == "FAILED"

    def test_watch_always_neutral(self):
        from storage.signal_evaluator import grade_signal
        for ret in [-10.0, 0.0, 10.0]:
            grade, basis = grade_signal("WATCH", None, None, ret, None)
            assert grade == "NEUTRAL"
            assert basis is None

    def test_pending_when_no_future_data(self):
        from storage.signal_evaluator import grade_signal
        grade, basis = grade_signal("BUY_MAX", None, None, None, None)
        assert grade == "PENDING"
        assert basis is None

    def test_fallback_to_4h_if_no_1d(self):
        """Als er geen 1d data is maar wel 4h, gebruik 4h."""
        from storage.signal_evaluator import grade_signal
        grade, basis = grade_signal("BUY_MODERATE", None, 4.5, None, None)
        assert grade == "SUCCESS"
        assert basis == "4h"

    def test_fallback_to_1h_if_no_1d_or_4h(self):
        from storage.signal_evaluator import grade_signal
        grade, basis = grade_signal("BUY_MODERATE", 5.0, None, None, None)
        assert grade == "SUCCESS"
        assert basis == "1h"

    def test_buy_max_success_threshold(self):
        """BUY_MAX is ook SUCCESS bij ≥ 3%."""
        from storage.signal_evaluator import grade_signal
        grade, _ = grade_signal("BUY_MAX", None, None, 3.0, None)
        assert grade == "SUCCESS"

    def test_buy_small_at_threshold(self):
        from storage.signal_evaluator import grade_signal
        grade, _ = grade_signal("BUY_SMALL", None, None, 3.0, None)
        assert grade == "SUCCESS"

    def test_failed_threshold_boundary(self):
        from storage.signal_evaluator import grade_signal
        grade, _ = grade_signal("BUY_MODERATE", None, None, -3.0, None)
        assert grade == "FAILED"


# ── FUTURE PRICE LOOKUP TESTS ─────────────────────────────────────────────────

class TestFuturePriceLookup:

    def test_finds_price_within_1d_window(self):
        from storage.signal_evaluator import _find_future_price
        now  = datetime.now(timezone.utc)
        snap_future = {
            "timestamp": (now + timedelta(hours=24)).isoformat(),
            "price": 55.0,
        }
        price = _find_future_price([snap_future], now, "1d")
        assert price == 55.0

    def test_no_price_outside_window(self):
        """Snapshot buiten tolerantievenster → None."""
        from storage.signal_evaluator import _find_future_price
        now = datetime.now(timezone.utc)
        snap_too_far = {
            "timestamp": (now + timedelta(hours=30)).isoformat(),
            "price": 55.0,
        }
        price = _find_future_price([snap_too_far], now, "1d")
        assert price is None  # 30u valt buiten 20-28u venster

    def test_finds_closest_price_in_window(self):
        """Meerdere snapshots in window → dichtste wins."""
        from storage.signal_evaluator import _find_future_price
        now = datetime.now(timezone.utc)
        snaps = [
            {"timestamp": (now + timedelta(hours=23)).isoformat(), "price": 52.0},
            {"timestamp": (now + timedelta(hours=24)).isoformat(), "price": 54.0},  # Dichtstbij
            {"timestamp": (now + timedelta(hours=25)).isoformat(), "price": 56.0},
        ]
        price = _find_future_price(snaps, now, "1d")
        assert price == 54.0

    def test_zero_price_skipped(self):
        from storage.signal_evaluator import _find_future_price
        now = datetime.now(timezone.utc)
        snaps = [
            {"timestamp": (now + timedelta(hours=24)).isoformat(), "price": 0.0},
            {"timestamp": (now + timedelta(hours=23.5)).isoformat(), "price": 55.0},
        ]
        price = _find_future_price(snaps, now, "1d")
        assert price == 55.0

    def test_1h_window(self):
        from storage.signal_evaluator import _find_future_price
        now = datetime.now(timezone.utc)
        snap = {"timestamp": (now + timedelta(hours=1)).isoformat(), "price": 51.0}
        assert _find_future_price([snap], now, "1h") == 51.0

    def test_4h_window(self):
        from storage.signal_evaluator import _find_future_price
        now = datetime.now(timezone.utc)
        snap = {"timestamp": (now + timedelta(hours=4)).isoformat(), "price": 52.0}
        assert _find_future_price([snap], now, "4h") == 52.0


# ── SIGNAL EVALUATOR TESTS ────────────────────────────────────────────────────

class TestSignalEvaluator:

    def test_evaluate_snapshot_pending_no_future(self):
        """Geen toekomstige snapshots → PENDING."""
        from storage.signal_evaluator import evaluate_snapshot
        now  = datetime.now(timezone.utc).isoformat()
        snap = _make_snapshot("EVAL1", price=50.0, ts=now)
        # Alleen het signaal zelf als "all_snaps", geen toekomstige
        outcome = evaluate_snapshot(snap, [snap])
        assert outcome.grade == "PENDING"

    def test_evaluate_snapshot_success(self):
        """Prijs +5% na 1d → SUCCESS voor BUY signaal."""
        from storage.signal_evaluator import evaluate_snapshot
        now     = datetime.now(timezone.utc)
        snap_t0 = _make_snapshot("EVAL2", price=50.0, decision="BUY_MODERATE",
                                 ts=now.isoformat())
        snap_t1d = _make_snapshot("EVAL2", price=52.5,  # +5%
                                  ts=(now + timedelta(hours=24)).isoformat())
        outcome = evaluate_snapshot(snap_t0, [snap_t1d, snap_t0])
        assert outcome.grade == "SUCCESS"
        assert outcome.return_1d is not None
        assert outcome.return_1d > 0

    def test_evaluate_snapshot_failed(self):
        """Prijs -5% na 1d → FAILED voor BUY signaal."""
        from storage.signal_evaluator import evaluate_snapshot
        now     = datetime.now(timezone.utc)
        snap_t0 = _make_snapshot("EVAL3", price=50.0, decision="BUY_STRONG",
                                 ts=now.isoformat())
        snap_t1d = _make_snapshot("EVAL3", price=47.5,  # -5%
                                  ts=(now + timedelta(hours=24)).isoformat())
        outcome = evaluate_snapshot(snap_t0, [snap_t1d, snap_t0])
        assert outcome.grade == "FAILED"
        assert outcome.return_1d < 0

    def test_evaluate_snapshot_stores_all_horizons(self):
        from storage.signal_evaluator import evaluate_snapshot
        now = datetime.now(timezone.utc)
        snap_t0 = _make_snapshot("HZTEST", price=50.0, ts=now.isoformat())
        future_snaps = [
            _make_snapshot("HZTEST", price=50.5,
                           ts=(now + timedelta(hours=1)).isoformat()),
            _make_snapshot("HZTEST", price=51.0,
                           ts=(now + timedelta(hours=4)).isoformat()),
            _make_snapshot("HZTEST", price=52.0,
                           ts=(now + timedelta(hours=24)).isoformat()),
            _make_snapshot("HZTEST", price=53.0,
                           ts=(now + timedelta(hours=72)).isoformat()),
        ]
        outcome = evaluate_snapshot(snap_t0, future_snaps + [snap_t0])
        assert outcome.price_1h  is not None
        assert outcome.price_4h  is not None
        assert outcome.price_1d  is not None
        assert outcome.price_3d  is not None

    def test_evaluate_ticker_no_snapshots(self):
        from storage.signal_evaluator import evaluate_ticker
        result = evaluate_ticker("NOSNAPS_XYZ")
        assert result["evaluated"] == 0
        assert result["pending"] == 0

    def test_evaluate_ticker_all_pending(self):
        """Slechts één snapshot per ticker → geen toekomstige data → alles PENDING."""
        from storage.signal_evaluator import evaluate_ticker
        _save_snap("ALLPEND", score=70.0, price=50.0)
        result = evaluate_ticker("ALLPEND")
        assert result["pending"] >= 1

    def test_evaluate_ticker_produces_outcomes_with_future_data(self):
        from storage.signal_evaluator import evaluate_ticker
        from storage.snapshot_store import save_snapshot_dict
        now = datetime.now(timezone.utc)

        for i, (score, price, hours) in enumerate([
            (70.0, 50.0, 0),    # Signal
            (72.0, 52.5, 24),   # +5% future
        ]):
            save_snapshot_dict("FUTDATA", _make_snapshot(
                "FUTDATA", score=score, price=price,
                ts=(now - timedelta(hours=hours)).isoformat(),
            ))

        result = evaluate_ticker("FUTDATA")
        graded = [o for o in result["outcomes"] if o["grade"] != "PENDING"]
        assert len(graded) >= 1


# ── SIGNAL STATISTICS TESTS ───────────────────────────────────────────────────

class TestSignalStatistics:

    def _seed_outcomes(self, ticker, grades_and_phases):
        from storage.evaluation_store import save_outcome, SignalOutcome
        for i, (grade, phase, decision, return_1d) in enumerate(grades_and_phases):
            o = SignalOutcome(
                version_id=f"V_{ticker}_{i}",
                ticker=ticker,
                timestamp=datetime.now(timezone.utc).isoformat(),
                decision=decision, momentum_score=70.0,
                phase=phase, catalyst_type="STRONG",
                sector_id="quantum", entry_price=50.0,
                return_1d=return_1d,
                price_1d=50.0 * (1 + return_1d / 100),
                grade=grade, grade_basis="1d",
                graded_at=datetime.now(timezone.utc).isoformat(),
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            )
            save_outcome(o)

    def test_success_rate_calculation(self):
        from storage.signal_evaluator import compute_signal_statistics
        self._seed_outcomes("STATS", [
            ("SUCCESS", "BREAKOUT", "BUY_MODERATE", 5.0),
            ("SUCCESS", "BREAKOUT", "BUY_MODERATE", 4.0),
            ("FAILED",  "NEUTRAL",  "BUY_SMALL",   -4.0),
            ("NEUTRAL", "EXPANSION","BUY_MODERATE",  1.0),
        ])
        stats = compute_signal_statistics("STATS")
        assert stats["success_rate"] == pytest.approx(0.5, abs=0.01)
        assert stats["total_graded"] == 4

    def test_by_phase_breakdown(self):
        from storage.signal_evaluator import compute_signal_statistics
        self._seed_outcomes("BYPHASE", [
            ("SUCCESS", "BREAKOUT", "BUY_MODERATE", 5.0),
            ("SUCCESS", "BREAKOUT", "BUY_STRONG",   6.0),
            ("FAILED",  "NEUTRAL",  "BUY_SMALL",   -4.0),
        ])
        stats = compute_signal_statistics("BYPHASE")
        assert "BREAKOUT" in stats["by_phase"]
        assert stats["by_phase"]["BREAKOUT"]["success"] == 2
        assert stats["by_phase"]["BREAKOUT"]["total"]   == 2

    def test_avg_score_success_higher_than_failed(self):
        """Succesvolle signalen hebben gemiddeld hogere scores."""
        from storage.evaluation_store import save_outcome, SignalOutcome
        from storage.signal_evaluator import compute_signal_statistics

        for i, (score, grade) in enumerate([
            (85.0, "SUCCESS"), (82.0, "SUCCESS"), (80.0, "SUCCESS"),
            (45.0, "FAILED"),  (40.0, "FAILED"),
        ]):
            o = SignalOutcome(
                version_id=f"V_SCORE_{i}",
                ticker="SCOREDIFF",
                timestamp=datetime.now(timezone.utc).isoformat(),
                decision="BUY_MODERATE", momentum_score=score,
                phase="BREAKOUT", catalyst_type="STRONG",
                sector_id="quantum", entry_price=50.0,
                return_1d=5.0 if grade == "SUCCESS" else -4.0,
                grade=grade, grade_basis="1d",
                graded_at=datetime.now(timezone.utc).isoformat(),
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            )
            save_outcome(o)

        stats = compute_signal_statistics("SCOREDIFF")
        avg_s = stats["avg_score_success"]
        avg_f = stats["avg_score_failed"]
        assert avg_s is not None and avg_f is not None
        assert avg_s > avg_f

    def test_no_outcomes_returns_message(self):
        from storage.signal_evaluator import compute_signal_statistics
        stats = compute_signal_statistics("NOEVALS_XYZ")
        assert stats["total_graded"] == 0
        assert "message" in stats

    def test_best_and_worst_signal(self):
        from storage.signal_evaluator import compute_signal_statistics
        self._seed_outcomes("BESTWORST", [
            ("SUCCESS", "EXPANSION", "BUY_MAX",     12.0),  # Best
            ("SUCCESS", "BREAKOUT",  "BUY_MODERATE", 4.0),
            ("FAILED",  "NEUTRAL",   "BUY_SMALL",   -8.0),  # Worst
        ])
        stats = compute_signal_statistics("BESTWORST")
        assert stats["best_signal"]["return_1d"]  == 12.0
        assert stats["worst_signal"]["return_1d"] == -8.0


# ── MISSING DATA HANDLING TESTS ───────────────────────────────────────────────

class TestMissingDataHandling:

    def test_zero_entry_price_handled(self):
        from storage.signal_evaluator import evaluate_snapshot
        now  = datetime.now(timezone.utc).isoformat()
        snap = _make_snapshot("ZPRICE", price=0.0, ts=now)
        outcome = evaluate_snapshot(snap, [snap])
        assert outcome.grade == "PENDING"

    def test_missing_timestamp_handled(self):
        from storage.signal_evaluator import evaluate_snapshot
        snap = _make_snapshot("NOTS")
        snap["timestamp"] = ""  # Leeg timestamp
        outcome = evaluate_snapshot(snap, [snap])
        assert outcome.grade == "PENDING"

    def test_corrupted_future_snapshot_skipped(self):
        """Snapshots met ongeldig timestamp worden overgeslagen."""
        from storage.signal_evaluator import _find_future_price
        now  = datetime.now(timezone.utc)
        snaps = [
            {"timestamp": "INVALID_TS",                            "price": 55.0},
            {"timestamp": (now + timedelta(hours=24)).isoformat(), "price": 52.0},
        ]
        price = _find_future_price(snaps, now, "1d")
        assert price == 52.0  # Geldige snapshot gebruikt

    def test_no_future_data_all_pending(self):
        from storage.signal_evaluator import evaluate_ticker
        now = datetime.now(timezone.utc)
        # Sla alleen één snapshot op met een prijs
        from storage.snapshot_store import save_snapshot_dict
        save_snapshot_dict("PEND_ONLY", _make_snapshot("PEND_ONLY", price=50.0,
                                                        ts=now.isoformat()))
        result = evaluate_ticker("PEND_ONLY")
        # Geen toekomstige data → PENDING
        assert all(o["grade"] == "PENDING" for o in result["outcomes"])


# ── EVALUATION REPORT TESTS ───────────────────────────────────────────────────

class TestEvaluationReport:

    def test_ticker_report_markdown_generated(self):
        from research.evaluation_report import ticker_evaluation_report
        stats = {
            "total_graded": 5, "success_rate": 0.6,
            "success_count": 3, "failed_count": 1, "neutral_count": 1,
            "avg_score_success": 75.0, "avg_score_failed": 45.0,
            "avg_return_1d": 3.5,
            "best_signal":  {"decision": "BUY_STRONG", "momentum_score": 80.0,
                             "return_1d": 8.0, "timestamp": "2026-05-28T10:00:00"},
            "worst_signal": {"decision": "BUY_SMALL", "momentum_score": 42.0,
                             "return_1d": -4.5, "timestamp": "2026-05-28T14:00:00"},
            "by_phase": {"BREAKOUT": {"success": 2, "total": 3, "success_rate": 0.67}},
            "by_catalyst": {"STRONG": {"success": 3, "total": 4, "success_rate": 0.75}},
        }
        md = ticker_evaluation_report("IONQ", stats, [])
        assert "IONQ" in md
        assert "60.0%" in md
        assert "BREAKOUT" in md

    def test_global_report_markdown_generated(self):
        from research.evaluation_report import global_summary_report
        stats = {
            "tickers_evaluated": 5, "total_graded": 50,
            "success_count": 30, "failed_count": 10, "neutral_count": 10,
            "success_rate": 0.6, "by_phase": {}, "by_decision": {},
        }
        md = global_summary_report(stats)
        assert "60.0%" in md
        assert "50" in md

    def test_export_evaluation_json_produces_file(self):
        from research.evaluation_report import export_evaluation_json, _REVIEWS_DIR
        stats    = {"total_graded": 3, "success_rate": 0.67}
        outcomes = [{"grade": "SUCCESS", "return_1d": 5.0}]
        path     = export_evaluation_json("NVDA", stats, outcomes)
        assert os.path.exists(path)
        with open(path) as f:
            d = json.load(f)
        assert d["ticker"] == "NVDA"
        assert d["statistics"]["total_graded"] == 3

    def test_export_markdown_report_produces_file(self):
        from research.evaluation_report import export_markdown_report
        stats = {"total_graded": 2, "success_rate": 0.5, "success_count": 1,
                 "failed_count": 1, "neutral_count": 0, "avg_score_success": None,
                 "avg_score_failed": None, "avg_return_1d": None,
                 "best_signal": None, "worst_signal": None,
                 "by_phase": {}, "by_catalyst": {}}
        path = export_markdown_report("QBTS", stats, [])
        assert os.path.exists(path)
        assert path.endswith(".md")


# ── EVALUATION API ENDPOINT TESTS ─────────────────────────────────────────────

class TestEvaluationEndpoints:

    def test_run_evaluation_404_no_snapshots(self):
        r = client.post("/evaluation/run/NOSNAPS")
        assert r.status_code == 404

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_run_evaluation_after_analyze(
            self, mock_social, mock_spy, mock_news, mock_snap):
        from data.social_client import SocialData
        mock_snap.return_value = _mock_snapshot("EVTEST", price=50.0)
        mock_social.return_value = SocialData("EVTEST", 0, 1, 0.0, "p", False, None)

        # Eerst analyze uitvoeren
        r1 = client.get("/analyze/EVTEST")
        assert r1.status_code == 200

        # Dan evaluation triggeren
        r2 = client.post("/evaluation/run/EVTEST")
        assert r2.status_code == 200
        d = r2.json()
        assert "evaluated" in d
        assert "pending" in d
        assert d["evaluated"] + d["pending"] >= 1

    def test_get_ticker_evaluation_404_no_evals(self):
        r = client.get("/evaluation/ticker/NOEVALS")
        assert r.status_code == 404

    def test_get_ticker_evaluation_404_no_evals_for_valid_ticker(self):
        r = client.get("/evaluation/ticker/VALIDDTICKER")
        assert r.status_code == 404

    def test_get_session_evaluation_invalid_date(self):
        r = client.get("/evaluation/session/not-a-date")
        assert r.status_code == 400

    def test_get_session_evaluation_valid_date(self):
        r = client.get("/evaluation/session/2026-05-28")
        assert r.status_code == 200
        d = r.json()
        assert "date" in d
        assert "tickers_with_evaluations" in d

    def test_get_top_signals_best(self):
        r = client.get("/evaluation/top-signals?best=true&n=5")
        assert r.status_code == 200
        d = r.json()
        assert d["type"] == "BEST"
        assert "signals" in d

    def test_get_top_signals_worst(self):
        r = client.get("/evaluation/top-signals?best=false&n=5")
        assert r.status_code == 200
        d = r.json()
        assert d["type"] == "WORST"

    def test_get_evaluation_stats(self):
        r = client.get("/evaluation/stats")
        assert r.status_code == 200
        assert "total_graded" in r.json()

    def test_evaluation_endpoints_in_openapi(self):
        schema = client.get("/openapi.json").json()
        paths  = schema["paths"]
        assert "/evaluation/run/{ticker}" in paths
        assert "/evaluation/ticker/{ticker}" in paths
        assert "/evaluation/top-signals" in paths
        assert "/evaluation/stats" in paths


# ── DECAY EVALUATION TESTS ────────────────────────────────────────────────────

class TestDecayEvaluation:

    def test_fresh_signal_no_decay_applied(self):
        """Vers signaal (< 2u) heeft decay multiplier 1.0."""
        from storage.signal_decay import apply_decay
        ts  = datetime.now(timezone.utc).isoformat()
        result = apply_decay("BUY_STRONG", 80.0, ts)
        assert result.decay_applied == 1.0
        assert result.effective_score == 80.0

    def test_stale_signal_score_reduced(self):
        """STALE signaal heeft lagere effectieve score."""
        from storage.signal_decay import apply_decay
        ts = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        result = apply_decay("BUY_STRONG", 80.0, ts)
        assert result.effective_score < 80.0
        assert result.decay_applied == 0.65

    def test_expired_signal_score_zero(self):
        """EXPIRED signaal heeft effectieve score 0."""
        from storage.signal_decay import apply_decay
        ts = (datetime.now(timezone.utc) - timedelta(hours=55)).isoformat()
        result = apply_decay("BUY_MAX", 90.0, ts)
        assert result.effective_score == 0.0
        assert result.effective_decision == "SKIP"

    def test_aging_strong_frenzy_decays_faster(self):
        """FRENZY fase veroudert sneller dan NEUTRAL fase."""
        from storage.signal_decay import apply_decay
        ts = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        normal = apply_decay("BUY_STRONG", 80.0, ts, phase="NEUTRAL")
        frenzy = apply_decay("BUY_STRONG", 80.0, ts, phase="FRENZY")
        assert frenzy.effective_score < normal.effective_score


# ── EDGE CASES ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_evaluate_snapshot_with_invalid_future_ts(self):
        """Ongeldig timestamp in toekomstige snapshot → crash niet."""
        from storage.signal_evaluator import evaluate_snapshot
        now  = datetime.now(timezone.utc).isoformat()
        snap = _make_snapshot("EDGE1", price=50.0, ts=now)
        bad_future = {"timestamp": "INVALID", "price": 55.0}
        outcome = evaluate_snapshot(snap, [bad_future, snap])
        assert outcome is not None

    def test_calc_return_zero_entry_price(self):
        from storage.signal_evaluator import _calc_return
        assert _calc_return(0.0, 55.0) is None

    def test_calc_return_none_future(self):
        from storage.signal_evaluator import _calc_return
        assert _calc_return(50.0, None) is None

    def test_grade_signal_unknown_decision(self):
        """Onbekende beslissing → PENDING."""
        from storage.signal_evaluator import grade_signal
        grade, _ = grade_signal("UNKNOWN_DECISION", None, None, 5.0, None)
        assert grade == "PENDING"

    def test_evaluate_ticker_empty_snapshots_no_crash(self):
        from storage.signal_evaluator import evaluate_ticker
        result = evaluate_ticker("EMPTY_TICKER_XYZ")
        assert result["outcomes"] == []
