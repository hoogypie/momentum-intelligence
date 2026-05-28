"""
tests/test_replay.py
Replay & Observation Tests — v2.6

Coverage:
    TestSnapshotDiff       Diff correctheid, geen verandering, direction
    TestDiffSeries         Reeks diffs, significante filter
    TestTimeline           first_seen, last_updated, strongest, confidence
    TestReplayEngine       Ticker replay, sector replay, session replay
    TestSessionReplay      Date filtering, ongeldige datum, leeg resultaat
    TestObservationStore   Save/list replay notes, signal reviews, templates
    TestExportScript       CLI export importeerbaar + argumenten
    TestReplayEndpoints    API endpoints voor /replay/*
    TestCorruptedSnapshots Foutieve JSON-regels worden overgeslagen
"""

import os
import json
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from backend.app import app
from schemas.ticker_snapshot import TickerSnapshot, DataConfidence

client = TestClient(app)


# ── TEST ISOLATIE ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    """Redirect storage naar tmp directory, research naar tmp dir."""
    tickers_dir = tmp_path / "tickers"
    sectors_dir = tmp_path / "sectors"
    tickers_dir.mkdir()
    sectors_dir.mkdir()

    monkeypatch.setattr("storage.snapshot_store._TICKERS_DIR", str(tickers_dir))
    monkeypatch.setattr("storage.signal_tracker._TICKERS_DIR", str(tickers_dir))
    monkeypatch.setattr("storage.sector_history._SECTORS_DIR", str(sectors_dir))

    # Research dirs
    obs_dir     = tmp_path / "observations"
    notes_dir   = tmp_path / "replay_notes"
    reviews_dir = tmp_path / "signal_reviews"
    obs_dir.mkdir(); notes_dir.mkdir(); reviews_dir.mkdir()

    monkeypatch.setattr("research.observation_store._OBS_DIR",     str(obs_dir))
    monkeypatch.setattr("research.observation_store._NOTES_DIR",   str(notes_dir))
    monkeypatch.setattr("research.observation_store._REVIEWS_DIR", str(reviews_dir))

    yield tmp_path


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _save_snap(ticker, score=65.0, decision="BUY_MODERATE", phase="BREAKOUT",
               confidence="LIVE", catalyst="STRONG", day_pct=6.0, price=50.0,
               ts=None):
    from storage.snapshot_store import save_snapshot_dict
    now = ts or datetime.now(timezone.utc).isoformat()
    save_snapshot_dict(ticker, {
        "ticker": ticker, "timestamp": now,
        "decision": decision, "momentum_score": score, "skip_score": 0,
        "phase": phase, "confidence": confidence,
        "cache_hit": False, "data_age_seconds": 0.0, "retries_used": 0,
        "catalyst_type": catalyst, "catalyst_description": "test",
        "day_change_pct": day_pct, "volume_ratio": 3.0,
        "sector_heat": 85, "sector_id": "quantum",
        "market_session": "REGULAR", "price": price,
        "premarket_pct": 0.0, "stored_at": now,
    })


def _mock_snap(ticker="TEST", price=50.0, volume=3_000_000):
    return TickerSnapshot(
        ticker=ticker, timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.LIVE,
        price=price, prev_close=price - 2, day_change_pct=6.0,
        premarket_pct=0.0, premarket_available=False,
        volume_today=volume, avg_volume_20d=500_000,
        market_cap=1e9, float_shares=40_000_000,
        cache_hit=False, data_age_seconds=0.0,
    )


# ── SNAPSHOT DIFF TESTS ───────────────────────────────────────────────────────

class TestSnapshotDiff:

    def test_basic_diff_score_delta(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                 "momentum_score": 55.0, "decision": "WATCH", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:00:00Z",
                 "momentum_score": 70.0, "decision": "BUY_MODERATE", "phase": "BREAKOUT",
                 "confidence": "LIVE", "catalyst_type": "STRONG"}
        diff = diff_snapshots(older, newer)
        assert diff.score_delta == 15.0
        assert diff.decision_changed is True
        assert diff.phase_changed is True
        assert diff.catalyst_changed is True

    def test_diff_decision_improved(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                 "momentum_score": 55.0, "decision": "WATCH", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:00:00Z",
                 "momentum_score": 75.0, "decision": "BUY_STRONG", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        diff = diff_snapshots(older, newer)
        assert diff.decision_improved is True

    def test_diff_decision_deteriorated(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                 "momentum_score": 80.0, "decision": "BUY_STRONG", "phase": "EXPANSION",
                 "confidence": "LIVE", "catalyst_type": "STRONG"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T12:00:00Z",
                 "momentum_score": 45.0, "decision": "WATCH", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        diff = diff_snapshots(older, newer)
        assert diff.decision_improved is False
        assert diff.score_delta < 0

    def test_diff_no_change(self):
        from storage.snapshot_diff import diff_snapshots
        snap = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                "momentum_score": 65.0, "decision": "BUY_MODERATE", "phase": "BREAKOUT",
                "confidence": "LIVE", "catalyst_type": "STRONG"}
        diff = diff_snapshots(snap, snap)
        assert diff.score_delta == 0.0
        assert diff.decision_changed is False
        assert diff.phase_changed is False

    def test_diff_elapsed_minutes(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00+00:00",
                 "momentum_score": 55.0, "decision": "WATCH", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:30:00+00:00",
                 "momentum_score": 70.0, "decision": "BUY_MODERATE", "phase": "BREAKOUT",
                 "confidence": "LIVE", "catalyst_type": "STRONG"}
        diff = diff_snapshots(older, newer)
        assert diff.elapsed_minutes == 90.0

    def test_diff_confidence_improved(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                 "momentum_score": 60.0, "decision": "BUY_MODERATE", "phase": "BREAKOUT",
                 "confidence": "STALE", "catalyst_type": "NONE"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:00:00Z",
                 "momentum_score": 62.0, "decision": "BUY_MODERATE", "phase": "BREAKOUT",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        diff = diff_snapshots(older, newer)
        assert diff.confidence_improved is True

    def test_diff_is_significant_on_decision_change(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                 "momentum_score": 55.0, "decision": "WATCH", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:00:00Z",
                 "momentum_score": 57.0, "decision": "BUY_SMALL", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        diff = diff_snapshots(older, newer)
        assert diff.is_significant is True

    def test_diff_not_significant_small_score_change(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                 "momentum_score": 65.0, "decision": "BUY_MODERATE", "phase": "BREAKOUT",
                 "confidence": "LIVE", "catalyst_type": "STRONG"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:00:00Z",
                 "momentum_score": 67.0, "decision": "BUY_MODERATE", "phase": "BREAKOUT",
                 "confidence": "LIVE", "catalyst_type": "STRONG"}
        diff = diff_snapshots(older, newer)
        assert diff.is_significant is False

    def test_diff_has_summary(self):
        from storage.snapshot_diff import diff_snapshots
        older = {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
                 "momentum_score": 55.0, "decision": "WATCH", "phase": "NEUTRAL",
                 "confidence": "LIVE", "catalyst_type": "NONE"}
        newer = {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:00:00Z",
                 "momentum_score": 75.0, "decision": "BUY_STRONG", "phase": "BREAKOUT",
                 "confidence": "LIVE", "catalyst_type": "STRONG"}
        diff = diff_snapshots(older, newer)
        assert len(diff.summary) > 0


# ── DIFF SERIES TESTS ─────────────────────────────────────────────────────────

class TestDiffSeries:

    def test_diff_series_produces_n_minus_1_diffs(self):
        from storage.snapshot_diff import diff_series
        snaps = [
            {"ticker": "T", "version_id": f"v{i}", "momentum_score": float(50 + i),
             "decision": "BUY_MODERATE", "phase": "BREAKOUT", "confidence": "LIVE",
             "catalyst_type": "STRONG", "timestamp": f"2026-05-28T1{i}:00:00Z"}
            for i in range(4)
        ]
        diffs = diff_series(snaps)
        assert len(diffs) == 3  # 4 snapshots → 3 diffs

    def test_diff_series_empty_on_single_snapshot(self):
        from storage.snapshot_diff import diff_series
        assert diff_series([{"ticker": "T"}]) == []

    def test_diff_series_empty_on_no_snapshots(self):
        from storage.snapshot_diff import diff_series
        assert diff_series([]) == []

    def test_find_significant_changes_filters(self):
        from storage.snapshot_diff import diff_series, find_significant_changes
        snaps = [
            {"ticker": "T", "version_id": "v3", "timestamp": "2026-05-28T12:00:00Z",
             "momentum_score": 80.0, "decision": "BUY_STRONG", "phase": "EXPANSION",
             "confidence": "LIVE", "catalyst_type": "STRONG"},
            {"ticker": "T", "version_id": "v2", "timestamp": "2026-05-28T11:30:00Z",
             "momentum_score": 79.0, "decision": "BUY_STRONG", "phase": "EXPANSION",
             "confidence": "LIVE", "catalyst_type": "STRONG"},
            {"ticker": "T", "version_id": "v1", "timestamp": "2026-05-28T10:00:00Z",
             "momentum_score": 52.0, "decision": "WATCH", "phase": "NEUTRAL",
             "confidence": "LIVE", "catalyst_type": "NONE"},
        ]
        diffs = diff_series(snaps)
        sig   = find_significant_changes(diffs)
        assert len(sig) == 1   # Alleen v1→v2 is significant (WATCH→BUY_STRONG + 27 pts)

    def test_diff_series_with_stored_snapshots(self):
        """Test met echte opgeslagen snapshots."""
        from storage.snapshot_diff import diff_series
        from storage.snapshot_store import load_snapshots
        now = datetime.now(timezone.utc)
        _save_snap("DIFFTEST", score=50.0, decision="WATCH",
                   ts=(now - timedelta(hours=2)).isoformat())
        _save_snap("DIFFTEST", score=70.0, decision="BUY_MODERATE",
                   ts=now.isoformat())
        snaps = load_snapshots("DIFFTEST", limit=10)
        diffs = diff_series(snaps)
        assert len(diffs) == 1
        assert diffs[0].decision_changed is True


# ── TIMELINE TESTS ────────────────────────────────────────────────────────────

class TestTimeline:

    def test_first_seen_oldest_snapshot(self):
        from storage.timeline import first_seen
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=5)).isoformat()
        _save_snap("TLFIRST", score=40.0, ts=old)
        _save_snap("TLFIRST", score=70.0, ts=now.isoformat())
        result = first_seen("TLFIRST")
        assert result is not None
        assert result["momentum_score"] == 40.0

    def test_last_updated_newest_snapshot(self):
        from storage.timeline import last_updated
        now = datetime.now(timezone.utc)
        old = (now - timedelta(hours=5)).isoformat()
        _save_snap("TLLAST", score=40.0, ts=old)
        _save_snap("TLLAST", score=80.0, ts=now.isoformat())
        result = last_updated("TLLAST")
        assert result["momentum_score"] == 80.0

    def test_strongest_signal_max_score(self):
        from storage.timeline import strongest_signal
        _save_snap("TLSTRONG", score=45.0, decision="WATCH")
        _save_snap("TLSTRONG", score=88.0, decision="BUY_MAX")
        _save_snap("TLSTRONG", score=62.0, decision="BUY_MODERATE")
        result = strongest_signal("TLSTRONG")
        assert result["momentum_score"] == 88.0

    def test_weakest_signal(self):
        from storage.timeline import weakest_signal
        _save_snap("TLWEAK", score=88.0, decision="BUY_MAX")
        _save_snap("TLWEAK", score=42.0, decision="WATCH")
        result = weakest_signal("TLWEAK")
        assert result["momentum_score"] == 42.0

    def test_confidence_history_deduplicates(self):
        from storage.timeline import confidence_history
        now = datetime.now(timezone.utc)
        for i in range(3):
            _save_snap("TLCONF", score=60.0, confidence="LIVE",
                       ts=(now - timedelta(minutes=i)).isoformat())
        _save_snap("TLCONF", score=55.0, confidence="DELAYED",
                   ts=(now - timedelta(hours=1)).isoformat())
        history = confidence_history("TLCONF")
        # LIVE × 3 → 1 entry, DELAYED × 1 → 1 entry = 2 totaal
        assert len(history) == 2

    def test_score_timeline_chronological(self):
        from storage.timeline import score_timeline
        now = datetime.now(timezone.utc)
        _save_snap("TLTL", score=50.0, ts=(now - timedelta(hours=2)).isoformat())
        _save_snap("TLTL", score=70.0, ts=(now - timedelta(hours=1)).isoformat())
        _save_snap("TLTL", score=80.0, ts=now.isoformat())
        timeline = score_timeline("TLTL")
        # Chronologisch: oudste eerst
        assert timeline[0]["score"] == 50.0
        assert timeline[-1]["score"] == 80.0

    def test_phase_history_deduplicates(self):
        from storage.timeline import phase_history
        now = datetime.now(timezone.utc)
        _save_snap("TLPHASE", score=60.0, phase="BREAKOUT",
                   ts=(now - timedelta(hours=3)).isoformat())
        _save_snap("TLPHASE", score=62.0, phase="BREAKOUT",
                   ts=(now - timedelta(hours=2)).isoformat())
        _save_snap("TLPHASE", score=75.0, phase="EXPANSION",
                   ts=now.isoformat())
        phases = phase_history("TLPHASE")
        assert len(phases) == 2  # BREAKOUT, EXPANSION

    def test_ticker_summary_all_fields(self):
        from storage.timeline import get_ticker_summary
        _save_snap("TLSUMM", score=70.0)
        summary = get_ticker_summary("TLSUMM")
        for key in ["ticker", "snapshot_count", "first_seen", "last_updated",
                    "current_decision", "strongest_signal", "score_range",
                    "days_tracked", "tracked"]:
            assert key in summary

    def test_ticker_summary_untracked(self):
        from storage.timeline import get_ticker_summary
        summary = get_ticker_summary("NOTTRACKED_XYZ")
        assert summary["tracked"] is False
        assert summary["snapshot_count"] == 0


# ── REPLAY ENGINE TESTS ───────────────────────────────────────────────────────

class TestReplayEngine:

    def test_replay_ticker_no_data(self):
        from storage.replay_engine import replay_ticker
        result = replay_ticker("NOTEXIST_XYZ")
        assert result["snapshot_count"] == 0

    def test_replay_ticker_with_snapshots(self):
        from storage.replay_engine import replay_ticker
        for i in range(4):
            _save_snap("RVTEST", score=60.0 + i, decision="BUY_MODERATE")
        result = replay_ticker("RVTEST", limit=10)
        assert result["snapshot_count"] == 4
        assert "diffs" in result
        assert "score_timeline" in result
        assert "effective_signals" in result

    def test_replay_ticker_has_3_diffs_for_4_snaps(self):
        from storage.replay_engine import replay_ticker
        now = datetime.now(timezone.utc)
        for i in range(4):
            _save_snap("RVDIFF", score=float(50 + i * 5),
                       ts=(now - timedelta(hours=3 - i)).isoformat())
        result = replay_ticker("RVDIFF", limit=10)
        assert len(result["diffs"]) == 3

    def test_replay_sector_no_data(self):
        from storage.replay_engine import replay_sector
        result = replay_sector("nonexistent_sector_xyz")
        assert result["snapshot_count"] == 0

    def test_replay_sector_with_history(self):
        from storage.sector_history   import save_sector_snapshot
        from storage.replay_engine    import replay_sector
        save_sector_snapshot("quantum", heat=92, avg_momentum=65.0,
                             avg_skip=0.0, leader_decisions={"IONQ": "BUY"})
        result = replay_sector("quantum")
        assert result["snapshot_count"] == 1
        assert result["heat_trend"] == [92]

    def test_replay_sector_heat_delta(self):
        from storage.sector_history import save_sector_snapshot
        from storage.replay_engine  import replay_sector
        for heat in [75, 80, 85, 90]:
            save_sector_snapshot("quantum", heat=heat, avg_momentum=60.0,
                                 avg_skip=0.0, leader_decisions={})
        result = replay_sector("quantum")
        # heat_delta = recente (90) - oudste (75) = +15
        assert result["heat_delta"] == 15

    def test_replay_ticker_hours_filter(self):
        from storage.replay_engine import replay_ticker
        now = datetime.now(timezone.utc)
        _save_snap("RVHOUR", score=40.0, ts=(now - timedelta(hours=30)).isoformat())
        _save_snap("RVHOUR", score=70.0, ts=now.isoformat())
        result = replay_ticker("RVHOUR", hours=6)
        assert result["snapshot_count"] == 1  # Alleen recente


# ── SESSION REPLAY TESTS ──────────────────────────────────────────────────────

class TestSessionReplay:

    def test_session_invalid_date(self):
        from storage.replay_engine import replay_session
        result = replay_session("not-a-date")
        assert "error" in result
        assert result["error"] == "INVALID_DATE"

    def test_session_empty_result(self):
        from storage.replay_engine import replay_session
        result = replay_session("2020-01-01")  # Datum ver in het verleden
        assert result["total_snapshots"] == 0

    def test_session_finds_today_snapshots(self):
        from storage.replay_engine import replay_session
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _save_snap("SESSTEST")
        result = replay_session(today)
        assert result["total_snapshots"] >= 1
        assert "SESSTEST" in result["session_by_ticker"]

    def test_session_excludes_other_dates(self):
        from storage.replay_engine import replay_session
        now  = datetime.now(timezone.utc)
        yesterday = (now - timedelta(days=2)).isoformat()
        _save_snap("SESSEXCL", ts=yesterday)
        today = now.strftime("%Y-%m-%d")
        result = replay_session(today)
        assert "SESSEXCL" not in result.get("session_by_ticker", {})

    def test_session_has_best_ticker(self):
        from storage.replay_engine import replay_session
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _save_snap("BESTTICKER", score=90.0, decision="BUY_MAX")
        _save_snap("WORSTTICKER", score=30.0, decision="WATCH")
        result = replay_session(today)
        assert result["best_ticker"] == "BESTTICKER"

    def test_session_decisions_seen(self):
        from storage.replay_engine import replay_session
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _save_snap("DEC_A", score=70.0, decision="BUY_MODERATE")
        _save_snap("DEC_B", score=55.0, decision="WATCH")
        result = replay_session(today)
        assert "BUY_MODERATE" in result["decisions_seen"]
        assert "WATCH" in result["decisions_seen"]


# ── CORRUPTED SNAPSHOT TESTS ──────────────────────────────────────────────────

class TestCorruptedSnapshots:

    def test_corrupted_json_line_skipped(self):
        """Ongeldige JSON-regels worden stil overgeslagen."""
        from storage.snapshot_store import load_snapshots, _ticker_path
        from storage.snapshot_store import _TICKERS_DIR
        path = os.path.join(_TICKERS_DIR, "CORRUPT.jsonl")
        with open(path, "w") as f:
            # Eén geldige + twee ongeldige regels
            f.write('{"ticker":"CORRUPT","version_id":"v1","timestamp":"2026-05-28T10:00:00Z","momentum_score":65.0,"decision":"BUY_MODERATE","phase":"BREAKOUT","confidence":"LIVE"}\n')
            f.write('INVALID JSON LINE HERE\n')
            f.write('{"broken": [without closing bracket\n')

        loaded = load_snapshots("CORRUPT", limit=10)
        assert len(loaded) == 1
        assert loaded[0]["decision"] == "BUY_MODERATE"

    def test_empty_lines_skipped(self):
        from storage.snapshot_store import load_snapshots, _TICKERS_DIR
        path = os.path.join(_TICKERS_DIR, "EMPTY.jsonl")
        with open(path, "w") as f:
            f.write("\n\n\n")
            f.write('{"ticker":"EMPTY","version_id":"v1","timestamp":"2026-05-28T10:00:00Z","momentum_score":50.0,"decision":"WATCH","phase":"NEUTRAL","confidence":"LIVE"}\n')

        loaded = load_snapshots("EMPTY", limit=10)
        assert len(loaded) == 1

    def test_all_corrupted_returns_empty(self):
        from storage.snapshot_store import load_snapshots, _TICKERS_DIR
        path = os.path.join(_TICKERS_DIR, "ALLBAD.jsonl")
        with open(path, "w") as f:
            f.write("not json\nalso not json\n{broken}\n")

        loaded = load_snapshots("ALLBAD", limit=10)
        assert loaded == []

    def test_replay_ticker_handles_corrupted_gracefully(self):
        """Replay crasht niet bij corrupte data."""
        from storage.replay_engine  import replay_ticker
        from storage.snapshot_store import _TICKERS_DIR
        path = os.path.join(_TICKERS_DIR, "BADREPLAY.jsonl")
        with open(path, "w") as f:
            f.write("INVALID\n")
            f.write('{"ticker":"BADREPLAY","version_id":"v1","timestamp":"2026-05-28T10:00:00Z","momentum_score":60.0,"decision":"BUY_MODERATE","phase":"BREAKOUT","confidence":"LIVE"}\n')
        try:
            result = replay_ticker("BADREPLAY")
            # Moet tenminste 1 geldig snapshot vinden
            assert result["snapshot_count"] >= 1
        except Exception as e:
            pytest.fail(f"replay_ticker crashte bij corrupte data: {e}")


# ── OBSERVATION STORE TESTS ───────────────────────────────────────────────────

class TestObservationStore:

    def test_save_and_list_replay_note(self):
        from research.observation_store import save_replay_note, list_replay_notes
        data = {"snapshot_count": 5, "momentum_trend": "IMPROVING"}
        save_replay_note("IONQ", data)
        notes = list_replay_notes("IONQ")
        assert len(notes) >= 1
        assert any("IONQ" in n for n in notes)

    def test_save_signal_review_produces_files(self):
        from research.observation_store import (
            save_signal_review, list_signal_reviews, _REVIEWS_DIR
        )
        data = {
            "snapshot_count": 10, "momentum_trend": "IMPROVING",
            "significant_changes": [],
            "summary": {"current": {"decision": "BUY_MODERATE",
                                    "momentum_score": 72.0,
                                    "phase": "BREAKOUT",
                                    "confidence": "LIVE"},
                        "strongest_ever": {"score": 72.0, "decision": "BUY_MODERATE"}},
        }
        path = save_signal_review("NVDA", data, summary="Test review")
        assert os.path.exists(path)
        # Markdown ook aangemaakt
        md_path = path.replace(".json", ".md")
        assert os.path.exists(md_path)

    def test_create_observation_template(self):
        from research.observation_store import (
            create_observation_template, list_observations
        )
        path = create_observation_template("QBTS")
        assert os.path.exists(path)
        content = open(path).read()
        assert "QBTS" in content
        obs = list_observations()
        assert any("QBTS" in f for f in obs)

    def test_observation_template_not_overwritten(self):
        from research.observation_store import create_observation_template
        path = create_observation_template("NOOP")
        # Schrijf custom inhoud
        with open(path, "w") as f:
            f.write("CUSTOM CONTENT")
        # Tweede aanroep mag niet overschrijven
        create_observation_template("NOOP")
        assert open(path).read() == "CUSTOM CONTENT"

    def test_list_replay_notes_filtered_by_ticker(self):
        from research.observation_store import save_replay_note, list_replay_notes
        save_replay_note("NVDA",  {"x": 1})
        save_replay_note("IONQ",  {"x": 2})
        notes = list_replay_notes("NVDA")
        assert all("NVDA" in n for n in notes)
        assert not any("IONQ" in n for n in notes)

    def test_save_loads_valid_json(self):
        from research.observation_store import save_replay_note, _NOTES_DIR
        data = {"snapshot_count": 5, "diffs": [], "summary": {"x": 1}}
        path = save_replay_note("LOADTEST", data)
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["ticker"] == "LOADTEST"
        assert loaded["snapshot_count"] == 5


# ── REPLAY API ENDPOINTS ──────────────────────────────────────────────────────

class TestReplayEndpoints:

    def test_replay_ticker_404_when_no_data(self):
        r = client.get("/replay/ticker/NOTTRACKED")
        assert r.status_code == 404

    def test_replay_ticker_with_data(self):
        _save_snap("APITEST", score=72.0, decision="BUY_MODERATE")
        r = client.get("/replay/ticker/APITEST")
        assert r.status_code == 200
        d = r.json()
        assert d["snapshot_count"] == 1
        assert "diffs" in d
        assert "score_timeline" in d

    def test_replay_ticker_invalid_ticker(self):
        r = client.get("/replay/ticker/123INVALID")
        assert r.status_code == 400

    def test_replay_sector_no_data(self):
        r = client.get("/replay/sector/quantum")
        assert r.status_code == 200
        assert r.json()["snapshot_count"] == 0

    def test_replay_sector_with_data(self):
        from storage.sector_history import save_sector_snapshot
        save_sector_snapshot("quantum", heat=92, avg_momentum=65.0,
                             avg_skip=0.0, leader_decisions={})
        r = client.get("/replay/sector/quantum")
        assert r.status_code == 200
        assert r.json()["snapshot_count"] == 1
        assert "heat_trend" in r.json()

    def test_replay_session_invalid_date(self):
        r = client.get("/replay/session/not-a-date")
        assert r.status_code == 400

    def test_replay_session_empty_day(self):
        r = client.get("/replay/session/2020-01-01")
        assert r.status_code == 200
        assert r.json()["total_snapshots"] == 0

    def test_replay_session_today(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        _save_snap("SSNTEST", score=65.0)
        r = client.get(f"/replay/session/{today}")
        assert r.status_code == 200
        assert r.json()["tickers_active"] >= 1

    def test_replay_summary_endpoint(self):
        _save_snap("SUMM_A", score=70.0)
        _save_snap("SUMM_B", score=55.0)
        r = client.get("/replay/summary")
        assert r.status_code == 200
        d = r.json()
        assert "ticker_count" in d
        assert d["ticker_count"] >= 2

    def test_replay_ticker_diff_endpoint(self):
        now = datetime.now(timezone.utc)
        _save_snap("DIFFAPI", score=55.0, decision="WATCH",
                   ts=(now - timedelta(hours=1)).isoformat())
        _save_snap("DIFFAPI", score=75.0, decision="BUY_MODERATE",
                   ts=now.isoformat())
        r = client.get("/replay/ticker/DIFFAPI/diff")
        assert r.status_code == 200
        d = r.json()
        assert d["diff_count"] == 1
        assert len(d["diffs"]) == 1

    def test_replay_ticker_diff_significant_filter(self):
        now = datetime.now(timezone.utc)
        _save_snap("SIGDIFF", score=62.0, decision="BUY_MODERATE", phase="BREAKOUT",
                   ts=(now - timedelta(hours=2)).isoformat())
        _save_snap("SIGDIFF", score=63.0, decision="BUY_MODERATE", phase="BREAKOUT",
                   ts=(now - timedelta(hours=1)).isoformat())
        _save_snap("SIGDIFF", score=80.0, decision="BUY_STRONG", phase="EXPANSION",
                   ts=now.isoformat())
        r = client.get("/replay/ticker/SIGDIFF/diff?significant=true")
        assert r.status_code == 200
        d = r.json()
        # Alleen de significant change (BREAKOUT→EXPANSION + BUY_MODERATE→BUY_STRONG)
        assert d["diff_count"] == 1

    def test_replay_endpoints_in_openapi(self):
        schema = client.get("/openapi.json").json()
        paths = schema["paths"]
        assert "/replay/ticker/{ticker}" in paths
        assert "/replay/sector/{sector}" in paths
        assert "/replay/session/{date}" in paths
        assert "/replay/summary" in paths


# ── EXPORT SCRIPT TESTS ───────────────────────────────────────────────────────

class TestExportScript:

    def test_export_script_importable(self):
        import scripts.export_snapshots as es
        assert hasattr(es, "main")
        assert hasattr(es, "cmd_ticker")
        assert hasattr(es, "cmd_sector")
        assert hasattr(es, "cmd_session")

    def test_export_script_commands_exist(self):
        import scripts.export_snapshots as es
        for cmd in ("cmd_ticker", "cmd_sector", "cmd_session",
                    "cmd_all_tickers", "cmd_list"):
            assert hasattr(es, cmd), f"cmd {cmd} ontbreekt"
