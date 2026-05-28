"""
tests/test_history.py
Historical Memory Layer Tests — v2.5

Coverage:
    TestSnapshotStore       Write/read, retention, versioning, persistence
    TestSignalDecay         Age categories, multipliers, decision downgrade
    TestSignalTracker       Phase transitions, catalyst timeline, trend
    TestSectorHistory       Save/load, heat trend, heating up detection
    TestHistoryReplay       Signal evolution, sector evolution, window
    TestHistoryEndpoints    API endpoints voor history
    TestStorageIntegration  End-to-end: analyze → opgeslagen → opgehaald
"""

import os
import json
import pytest
import shutil
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from backend.app import app
from schemas.ticker_snapshot import TickerSnapshot, DataConfidence

client = TestClient(app)

# ── TEST STORAGE ISOLATIE ─────────────────────────────────────────────────────
# Tests schrijven naar een tijdelijke directory om conflicten te vermijden.

_TEST_STORAGE = tempfile.mkdtemp(prefix="mi_test_storage_")
_TEST_TICKERS = os.path.join(_TEST_STORAGE, "tickers")
_TEST_SECTORS = os.path.join(_TEST_STORAGE, "sectors")


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    """Redirect alle storage operaties naar een tmp directory per test."""
    tickers_dir = tmp_path / "tickers"
    sectors_dir = tmp_path / "sectors"
    tickers_dir.mkdir()
    sectors_dir.mkdir()

    monkeypatch.setattr(
        "storage.snapshot_store._TICKERS_DIR", str(tickers_dir)
    )
    monkeypatch.setattr(
        "storage.signal_tracker._TICKERS_DIR", str(tickers_dir)
    )
    monkeypatch.setattr(
        "storage.sector_history._SECTORS_DIR", str(sectors_dir)
    )
    yield tmp_path


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _make_snap_dict(
    ticker="TEST", decision="BUY_MODERATE", score=62.0, phase="BREAKOUT",
    confidence="LIVE", day_pct=6.0, vol_ratio=3.2, price=50.0,
    timestamp=None,
) -> dict:
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    return {
        "version_id":          f"TEST_{ts[:10].replace('-','')}",
        "ticker":              ticker.upper(),
        "timestamp":           ts,
        "decision":            decision,
        "momentum_score":      score,
        "skip_score":          0,
        "phase":               phase,
        "confidence":          confidence,
        "cache_hit":           False,
        "data_age_seconds":    0.0,
        "retries_used":        0,
        "catalyst_type":       "STRONG",
        "catalyst_description": "Q2 earnings beat",
        "day_change_pct":      day_pct,
        "volume_ratio":        vol_ratio,
        "sector_heat":         92,
        "sector_id":           "quantum",
        "market_session":      "REGULAR",
        "price":               price,
        "premarket_pct":       0.0,
        "stored_at":           ts,
    }


def _mock_snapshot(ticker="TEST", price=50.0):
    return TickerSnapshot(
        ticker=ticker, timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.LIVE,
        price=price, prev_close=price - 2, day_change_pct=6.0,
        premarket_pct=0.0, premarket_available=False,
        volume_today=3_000_000, avg_volume_20d=500_000,
        market_cap=1e9, float_shares=40_000_000,
        cache_hit=False, data_age_seconds=0.0,
    )


# ── SNAPSHOT STORE TESTS ──────────────────────────────────────────────────────

class TestSnapshotStore:

    def test_save_and_load_roundtrip(self):
        from storage.snapshot_store import save_snapshot_dict, load_snapshots
        snap = _make_snap_dict("IONQ", score=75.0)
        save_snapshot_dict("IONQ", snap)
        loaded = load_snapshots("IONQ", limit=5)
        assert len(loaded) == 1
        assert loaded[0]["momentum_score"] == 75.0

    def test_multiple_saves_accumulate(self):
        from storage.snapshot_store import save_snapshot_dict, load_snapshots
        for i in range(5):
            snap = _make_snap_dict("MULTI", score=float(50 + i))
            save_snapshot_dict("MULTI", snap)
        loaded = load_snapshots("MULTI", limit=10)
        assert len(loaded) == 5

    def test_load_returns_newest_first(self):
        from storage.snapshot_store import save_snapshot_dict, load_snapshots
        now = datetime.now(timezone.utc)
        older = _make_snap_dict("ORDER", score=40.0,
                                timestamp=(now - timedelta(hours=2)).isoformat())
        newer = _make_snap_dict("ORDER", score=80.0,
                                timestamp=now.isoformat())
        save_snapshot_dict("ORDER", older)
        save_snapshot_dict("ORDER", newer)
        loaded = load_snapshots("ORDER", limit=5)
        assert loaded[0]["momentum_score"] == 80.0  # Nieuwste eerst

    def test_retention_limit_enforced(self):
        from storage.snapshot_store import (
            save_snapshot_dict, load_snapshots, MAX_SNAPSHOTS_PER_TICKER
        )
        import storage.snapshot_store as ss
        original = ss.MAX_SNAPSHOTS_PER_TICKER
        ss.MAX_SNAPSHOTS_PER_TICKER = 5

        for i in range(8):
            save_snapshot_dict("RETTEST", _make_snap_dict("RETTEST", score=float(i)))

        loaded = load_snapshots("RETTEST", limit=100)
        assert len(loaded) <= 5
        ss.MAX_SNAPSHOTS_PER_TICKER = original

    def test_load_latest_returns_most_recent(self):
        from storage.snapshot_store import save_snapshot_dict, load_latest
        now = datetime.now(timezone.utc)
        save_snapshot_dict("LAT", _make_snap_dict("LAT", score=30.0,
                           timestamp=(now - timedelta(hours=1)).isoformat()))
        save_snapshot_dict("LAT", _make_snap_dict("LAT", score=70.0,
                           timestamp=now.isoformat()))
        latest = load_latest("LAT")
        assert latest is not None
        assert latest["momentum_score"] == 70.0

    def test_load_latest_none_when_no_data(self):
        from storage.snapshot_store import load_latest
        assert load_latest("NONEXISTENT_TICKER_XYZ") is None

    def test_load_since_filters_by_time(self):
        from storage.snapshot_store import save_snapshot_dict, load_since
        now = datetime.now(timezone.utc)
        old  = _make_snap_dict("SINCE", score=10.0,
                               timestamp=(now - timedelta(hours=30)).isoformat())
        new  = _make_snap_dict("SINCE", score=90.0,
                               timestamp=(now - timedelta(hours=1)).isoformat())
        save_snapshot_dict("SINCE", old)
        save_snapshot_dict("SINCE", new)
        result = load_since("SINCE", hours=24)
        assert len(result) == 1
        assert result[0]["momentum_score"] == 90.0

    def test_version_id_unique(self):
        """Elke save krijgt een uniek version_id."""
        from storage.snapshot_store import save_snapshot_dict, load_snapshots
        import time
        for i in range(3):
            save_snapshot_dict("UNIQ", _make_snap_dict("UNIQ", score=float(i)))
            time.sleep(0.01)
        loaded = load_snapshots("UNIQ", limit=10)
        ids = [s["version_id"] for s in loaded]
        assert len(set(ids)) == 3  # Alle IDs uniek

    def test_count_snapshots(self):
        from storage.snapshot_store import save_snapshot_dict, count_snapshots
        for i in range(3):
            save_snapshot_dict("CNT", _make_snap_dict("CNT"))
        assert count_snapshots("CNT") == 3

    def test_count_zero_for_unknown_ticker(self):
        from storage.snapshot_store import count_snapshots
        assert count_snapshots("UNKNOWNTICKER999") == 0

    def test_list_tracked_tickers(self):
        from storage.snapshot_store import save_snapshot_dict, list_tracked_tickers
        save_snapshot_dict("TICKA", _make_snap_dict("TICKA"))
        save_snapshot_dict("TICKB", _make_snap_dict("TICKB"))
        tracked = list_tracked_tickers()
        assert "TICKA" in tracked
        assert "TICKB" in tracked

    def test_delete_ticker_history(self):
        from storage.snapshot_store import (
            save_snapshot_dict, delete_ticker_history, load_snapshots
        )
        save_snapshot_dict("DELME", _make_snap_dict("DELME"))
        assert len(load_snapshots("DELME")) == 1
        result = delete_ticker_history("DELME")
        assert result is True
        assert len(load_snapshots("DELME")) == 0

    def test_delete_nonexistent_returns_false(self):
        from storage.snapshot_store import delete_ticker_history
        assert delete_ticker_history("DOESNOTEXIST_XYZ") is False

    def test_persistence_across_instances(self):
        """Data is aaneengesloten leesbaar na tweede save operatie."""
        from storage.snapshot_store import save_snapshot_dict, load_snapshots
        snap1 = _make_snap_dict("PERSIST", score=88.0)
        snap2 = _make_snap_dict("PERSIST", score=90.0)
        save_snapshot_dict("PERSIST", snap1)
        save_snapshot_dict("PERSIST", snap2)
        # Tweede load moet beide teruggeven
        loaded = load_snapshots("PERSIST", limit=10)
        assert len(loaded) == 2
        scores = {s["momentum_score"] for s in loaded}
        assert 88.0 in scores
        assert 90.0 in scores


# ── SIGNAL DECAY TESTS ────────────────────────────────────────────────────────

class TestSignalDecay:

    def test_fresh_signal_no_decay(self):
        from storage.signal_decay import apply_decay, SignalAge
        now = datetime.now(timezone.utc).isoformat()
        result = apply_decay("BUY_STRONG", 80.0, now)
        assert result.signal_age == SignalAge.FRESH
        assert result.decay_applied == 1.0
        assert result.effective_score == 80.0
        assert result.effective_decision == "BUY_STRONG"

    def test_aging_signal_decay(self):
        from storage.signal_decay import apply_decay, SignalAge
        ts = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        result = apply_decay("BUY_MAX", 90.0, ts)
        assert result.signal_age == SignalAge.AGING
        assert result.decay_applied == 0.85
        assert result.effective_score == round(90.0 * 0.85, 1)
        assert result.effective_decision == "BUY_MAX"

    def test_stale_signal_decay_and_downgrade(self):
        from storage.signal_decay import apply_decay, SignalAge
        ts = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat()
        result = apply_decay("BUY_MAX", 90.0, ts)
        assert result.signal_age == SignalAge.STALE
        assert result.decay_applied == 0.65
        # BUY_MAX → BUY_STRONG (1 stap lager)
        assert result.effective_decision == "BUY_STRONG"

    def test_old_signal_max_watch(self):
        from storage.signal_decay import apply_decay, SignalAge
        ts = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        result = apply_decay("BUY_MAX", 90.0, ts)
        assert result.signal_age == SignalAge.OLD
        assert result.decay_applied == 0.40
        assert result.effective_decision == "WATCH"
        assert result.is_actionable is False

    def test_expired_always_skip(self):
        from storage.signal_decay import apply_decay, SignalAge
        ts = (datetime.now(timezone.utc) - timedelta(hours=60)).isoformat()
        result = apply_decay("BUY_MAX", 90.0, ts)
        assert result.signal_age == SignalAge.EXPIRED
        assert result.decay_applied == 0.0
        assert result.effective_score == 0.0
        assert result.effective_decision == "SKIP"
        assert result.is_actionable is False

    def test_blocked_decision_never_changes(self):
        from storage.signal_decay import apply_decay
        ts_old = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        result = apply_decay("BLOCKED", 0.0, ts_old)
        assert result.effective_decision == "BLOCKED"

    def test_skip_decision_never_changes(self):
        from storage.signal_decay import apply_decay
        ts_old = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        result = apply_decay("SKIP", 0.0, ts_old)
        assert result.effective_decision == "SKIP"

    def test_frenzy_extra_decay(self):
        from storage.signal_decay import apply_decay
        ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        # Zonder FRENZY
        normal  = apply_decay("BUY_STRONG", 80.0, ts, phase="NEUTRAL")
        # Met FRENZY
        frenzy  = apply_decay("BUY_STRONG", 80.0, ts, phase="FRENZY")
        assert frenzy.effective_score < normal.effective_score

    def test_apply_decay_to_snapshot(self):
        from storage.signal_decay import apply_decay_to_snapshot
        snap = _make_snap_dict("TEST", score=70.0,
                               timestamp=datetime.now(timezone.utc).isoformat())
        result = apply_decay_to_snapshot(snap)
        assert result.original_score == 70.0
        assert result.signal_age.value == "FRESH"

    def test_get_signal_age_boundaries(self):
        from storage.signal_decay import get_signal_age, SignalAge
        assert get_signal_age(datetime.now(timezone.utc).isoformat())[0] == SignalAge.FRESH
        t2h = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=1)).isoformat()
        assert get_signal_age(t2h)[0] == SignalAge.AGING
        t9h = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
        assert get_signal_age(t9h)[0] == SignalAge.STALE
        t25h = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        assert get_signal_age(t25h)[0] == SignalAge.OLD
        t49h = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        assert get_signal_age(t49h)[0] == SignalAge.EXPIRED

    def test_fresh_signal_is_actionable(self):
        from storage.signal_decay import apply_decay
        now = datetime.now(timezone.utc).isoformat()
        result = apply_decay("BUY_MODERATE", 65.0, now)
        assert result.is_actionable is True

    def test_watch_is_not_actionable(self):
        from storage.signal_decay import apply_decay
        ts = (datetime.now(timezone.utc) - timedelta(hours=36)).isoformat()
        result = apply_decay("BUY_MODERATE", 55.0, ts)
        assert result.is_actionable is False


# ── SIGNAL TRACKER TESTS ──────────────────────────────────────────────────────

class TestSignalTracker:

    def test_record_transition_on_phase_change(self):
        from storage.signal_tracker import (
            record_transition_if_changed, get_transitions
        )
        old_snap = _make_snap_dict("TRTEST", phase="NEUTRAL")
        result = record_transition_if_changed(
            ticker="TRTEST", new_phase="BREAKOUT",
            momentum_score=72.0, decision="BUY_STRONG",
            version_id="v001",
            snapshots=[_make_snap_dict("TRTEST", phase="BREAKOUT"), old_snap],
        )
        assert result is not None
        assert result.from_phase == "NEUTRAL"
        assert result.to_phase   == "BREAKOUT"

    def test_no_transition_same_phase(self):
        from storage.signal_tracker import record_transition_if_changed
        same_phase_snaps = [
            _make_snap_dict("SAME", phase="BREAKOUT"),
            _make_snap_dict("SAME", phase="BREAKOUT"),
        ]
        result = record_transition_if_changed(
            ticker="SAME", new_phase="BREAKOUT",
            momentum_score=70.0, decision="BUY_MODERATE",
            version_id="v001", snapshots=same_phase_snaps,
        )
        assert result is None

    def test_catalyst_timeline_tracks_change(self):
        from storage.signal_tracker import (
            record_catalyst_if_changed, get_catalyst_timeline
        )
        # Eerdere snapshot heeft catalyst_type "NONE"
        prev_snap = _make_snap_dict("CTEST")
        prev_snap["catalyst_type"] = "NONE"
        current_snap = _make_snap_dict("CTEST")
        current_snap["catalyst_type"] = "STRONG"
        # snapshots[0] = huidige, snapshots[1] = vorige
        result = record_catalyst_if_changed(
            ticker="CTEST", catalyst_type="STRONG",
            catalyst_desc="Q2 earnings beat",
            version_id="v001",
            snapshots=[current_snap, prev_snap],  # huidig, dan vorige
        )
        assert result is not None
        assert result.catalyst_type == "STRONG"
        assert result.previous_type == "NONE"

    def test_no_catalyst_event_same_type(self):
        from storage.signal_tracker import record_catalyst_if_changed
        # Beide snapshots: STRONG → geen verandering
        prev_snap = _make_snap_dict("CSAME")
        prev_snap["catalyst_type"] = "STRONG"
        curr_snap = _make_snap_dict("CSAME")
        curr_snap["catalyst_type"] = "STRONG"
        result = record_catalyst_if_changed(
            ticker="CSAME", catalyst_type="STRONG",
            catalyst_desc="still strong", version_id="v001",
            snapshots=[curr_snap, prev_snap],
        )
        assert result is None

    def test_momentum_trend_improving(self):
        from storage.signal_tracker import calculate_momentum_trend
        snaps = [
            _make_snap_dict(score=80.0),
            _make_snap_dict(score=78.0),
            _make_snap_dict(score=75.0),
            _make_snap_dict(score=60.0),
            _make_snap_dict(score=55.0),
            _make_snap_dict(score=52.0),
        ]
        assert calculate_momentum_trend(snaps) == "IMPROVING"

    def test_momentum_trend_deteriorating(self):
        from storage.signal_tracker import calculate_momentum_trend
        snaps = [
            _make_snap_dict(score=50.0),
            _make_snap_dict(score=52.0),
            _make_snap_dict(score=55.0),
            _make_snap_dict(score=75.0),
            _make_snap_dict(score=78.0),
            _make_snap_dict(score=80.0),
        ]
        assert calculate_momentum_trend(snaps) == "DETERIORATING"

    def test_momentum_trend_stable(self):
        from storage.signal_tracker import calculate_momentum_trend
        snaps = [
            _make_snap_dict(score=65.0),
            _make_snap_dict(score=64.0),
            _make_snap_dict(score=66.0),
            _make_snap_dict(score=63.0),
            _make_snap_dict(score=65.0),
            _make_snap_dict(score=64.0),
        ]
        assert calculate_momentum_trend(snaps) == "STABLE"

    def test_insufficient_data_trend(self):
        from storage.signal_tracker import calculate_momentum_trend
        assert calculate_momentum_trend([]) == "INSUFFICIENT_DATA"
        assert calculate_momentum_trend([_make_snap_dict()]) == "INSUFFICIENT_DATA"

    def test_decision_distribution(self):
        from storage.signal_tracker import get_decision_distribution
        snaps = [
            _make_snap_dict(decision="BUY_STRONG"),
            _make_snap_dict(decision="BUY_STRONG"),
            _make_snap_dict(decision="BUY_MODERATE"),
            _make_snap_dict(decision="WATCH"),
        ]
        dist = get_decision_distribution(snaps)
        assert dist["BUY_STRONG"] == 2
        assert dist["BUY_MODERATE"] == 1
        assert dist["WATCH"] == 1


# ── SECTOR HISTORY TESTS ──────────────────────────────────────────────────────

class TestSectorHistory:

    def test_save_and_load_sector(self):
        from storage.sector_history import save_sector_snapshot, load_sector_history
        save_sector_snapshot(
            sector_id="quantum", heat=92, avg_momentum=65.0,
            avg_skip=5.0, leader_decisions={"IONQ": "BUY_MODERATE", "QBTS": "WATCH"},
        )
        history = load_sector_history("quantum", limit=5)
        assert len(history) == 1
        assert history[0]["heat"] == 92
        assert history[0]["avg_momentum"] == 65.0

    def test_multiple_sector_snapshots(self):
        from storage.sector_history import save_sector_snapshot, load_sector_history
        for heat in [75, 80, 88, 92]:
            save_sector_snapshot(
                sector_id="quantum", heat=heat, avg_momentum=60.0,
                avg_skip=0.0, leader_decisions={},
            )
        history = load_sector_history("quantum", limit=10)
        assert len(history) == 4

    def test_heat_trend_newest_first(self):
        from storage.sector_history import save_sector_snapshot, get_heat_trend
        for heat in [75, 80, 85, 90]:
            save_sector_snapshot("qttest", heat=heat, avg_momentum=60.0,
                                 avg_skip=0.0, leader_decisions={})
        trend = get_heat_trend("qttest", limit=4)
        assert trend[0] == 90   # nieuwste eerst
        assert trend[-1] == 75

    def test_is_heating_up_true(self):
        from storage.sector_history import save_sector_snapshot, is_sector_heating_up
        # Oud → nieuw: 50, 60, 70, 80 (stijgend)
        for heat in [50, 60, 70, 80]:
            save_sector_snapshot("heat_up", heat=heat, avg_momentum=60.0,
                                 avg_skip=0.0, leader_decisions={})
        assert is_sector_heating_up("heat_up", window=2) is True

    def test_is_heating_up_false(self):
        from storage.sector_history import save_sector_snapshot, is_sector_heating_up
        # Oud → nieuw: 80, 70, 60, 50 (dalend)
        for heat in [80, 70, 60, 50]:
            save_sector_snapshot("heat_dn", heat=heat, avg_momentum=60.0,
                                 avg_skip=0.0, leader_decisions={})
        assert is_sector_heating_up("heat_dn", window=2) is False

    def test_empty_sector_returns_false(self):
        from storage.sector_history import is_sector_heating_up
        assert is_sector_heating_up("nosuchsector_xyz") is False

    def test_momentum_trend_sector(self):
        from storage.sector_history import save_sector_snapshot, get_momentum_trend
        for mom in [55.0, 60.0, 65.0]:
            save_sector_snapshot("momtest", heat=80, avg_momentum=mom,
                                 avg_skip=0.0, leader_decisions={})
        trend = get_momentum_trend("momtest", limit=3)
        assert len(trend) == 3
        assert trend[0] == 65.0  # nieuwste


# ── HISTORY REPLAY TESTS ──────────────────────────────────────────────────────

class TestHistoryReplay:

    def test_signal_evolution_no_history(self):
        from storage.history_replay import get_signal_evolution
        result = get_signal_evolution("TICKER_NODATA_XYZ", hours=24)
        assert result["snapshot_count"] == 0
        assert result["ticker"] == "TICKER_NODATA_XYZ"

    def test_signal_evolution_with_snapshots(self):
        from storage.snapshot_store import save_snapshot_dict
        from storage.history_replay import get_signal_evolution
        for i in range(3):
            save_snapshot_dict("EVTEST", _make_snap_dict("EVTEST", score=60.0 + i))
        result = get_signal_evolution("EVTEST", hours=24)
        assert result["snapshot_count"] == 3
        assert "effective_signals" in result
        assert "momentum_trend" in result
        assert "summary" in result

    def test_effective_signals_have_decay_fields(self):
        from storage.snapshot_store import save_snapshot_dict
        from storage.history_replay import get_signal_evolution
        save_snapshot_dict("DECTEST", _make_snap_dict("DECTEST", score=70.0))
        result = get_signal_evolution("DECTEST", hours=24)
        if result["effective_signals"]:
            sig = result["effective_signals"][0]
            assert "effective_decision" in sig
            assert "signal_age"         in sig
            assert "decay_applied"      in sig
            assert "is_actionable"      in sig

    def test_sector_evolution_no_history(self):
        from storage.history_replay import get_sector_evolution
        result = get_sector_evolution("nosuchsector_xyz")
        assert result["snapshot_count"] == 0
        assert result["is_heating_up"] is False

    def test_momentum_window_no_history(self):
        from storage.history_replay import get_momentum_window
        result = get_momentum_window("NOHISTTICKER_XYZ")
        assert result["window_open"] is False
        assert "Geen historische data" in result["reason"]

    def test_momentum_window_fresh_signal_open(self):
        from storage.snapshot_store import save_snapshot_dict
        from storage.history_replay import get_momentum_window
        save_snapshot_dict("WOPEN", _make_snap_dict("WOPEN", decision="BUY_MODERATE",
                                                     score=65.0))
        result = get_momentum_window("WOPEN", hours=6)
        assert result["window_open"] is True
        assert result["signal_age"] == "FRESH"

    def test_momentum_window_expired_signal_closed(self):
        from storage.snapshot_store import save_snapshot_dict
        from storage.history_replay import get_momentum_window
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=55)).isoformat()
        save_snapshot_dict("WOLD", _make_snap_dict("WOLD", decision="BUY_STRONG",
                                                    score=80.0, timestamp=old_ts))
        result = get_momentum_window("WOLD", hours=6)
        assert result["window_open"] is False
        assert result["signal_age"] == "EXPIRED"

    def test_sector_evolution_with_data(self):
        from storage.sector_history import save_sector_snapshot
        from storage.history_replay import get_sector_evolution
        for heat in [80, 85, 90]:
            save_sector_snapshot("quantumtest", heat=heat, avg_momentum=65.0,
                                 avg_skip=0.0, leader_decisions={"IONQ": "BUY"})
        result = get_sector_evolution("quantumtest")
        assert result["snapshot_count"] == 3
        assert len(result["heat_trend"]) == 3


# ── HISTORY API ENDPOINTS TESTS ───────────────────────────────────────────────

class TestHistoryEndpoints:

    def test_history_endpoint_404_when_no_data(self):
        r = client.get("/history/NOTRACKEDTICKER")
        assert r.status_code == 404
        d = r.json()["detail"]
        assert d["error"] == "NO_HISTORY"
        assert "NOTRACKEDTICKER" in d["message"]

    def test_history_window_no_data_returns_200(self):
        """Window endpoint geeft 200 ook zonder data (closed window)."""
        r = client.get("/history/NOTRACKEDTICKER/window")
        assert r.status_code == 200
        assert r.json()["window_open"] is False

    def test_history_transitions_no_data_returns_200(self):
        r = client.get("/history/NOTRACKEDTICKER/transitions")
        assert r.status_code == 200
        assert "phase_transitions" in r.json()

    def test_sector_trend_endpoint_returns_200(self):
        r = client.get("/sector/quantum/trend")
        assert r.status_code == 200
        assert "is_heating_up" in r.json()
        assert "heat_trend" in r.json()

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_analyze_persists_snapshot(
            self, mock_social, mock_spy, mock_news, mock_snap):
        """Na /analyze moet de snapshot in history staan."""
        from data.social_client import SocialData
        mock_snap.return_value = _mock_snapshot()
        mock_social.return_value = SocialData("TEST", 0, 1, 0.0, "p", False, None)

        r = client.get("/analyze/HISTTEST")
        assert r.status_code == 200

        # Nu moet /history/HISTTEST data hebben
        r2 = client.get("/history/HISTTEST")
        assert r2.status_code == 200
        assert r2.json()["snapshot_count"] >= 1

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_analyze_with_persist_false_no_storage(
            self, mock_social, mock_spy, mock_news, mock_snap):
        """persist=false → geen opslag in history."""
        from data.social_client import SocialData
        mock_snap.return_value = _mock_snapshot()
        mock_social.return_value = SocialData("TEST", 0, 1, 0.0, "p", False, None)

        r = client.get("/analyze/NOPERSIST?persist=false")
        assert r.status_code == 200

        r2 = client.get("/history/NOPERSIST")
        assert r2.status_code == 404  # Geen data opgeslagen

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_history_endpoint_after_multiple_analyses(
            self, mock_social, mock_spy, mock_news, mock_snap):
        from data.social_client import SocialData
        mock_snap.return_value = _mock_snapshot()
        mock_social.return_value = SocialData("TEST", 0, 1, 0.0, "p", False, None)

        for _ in range(3):
            client.get("/analyze/MULTITEST")

        r = client.get("/history/MULTITEST")
        assert r.status_code == 200
        assert r.json()["snapshot_count"] == 3

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    @patch("data.assembler.get_social_data")
    def test_history_has_effective_signals(
            self, mock_social, mock_spy, mock_news, mock_snap):
        from data.social_client import SocialData
        mock_snap.return_value = _mock_snapshot()
        mock_social.return_value = SocialData("TEST", 0, 1, 0.0, "p", False, None)

        client.get("/analyze/EFFTEST")
        r = client.get("/history/EFFTEST")

        assert r.status_code == 200
        d = r.json()
        assert "effective_signals" in d
        assert len(d["effective_signals"]) >= 1
        sig = d["effective_signals"][0]
        assert "effective_decision" in sig
        assert "is_actionable" in sig

    def test_cache_stats_includes_history(self):
        r = client.get("/cache/stats")
        assert r.status_code == 200
        assert "history" in r.json()
        assert "tracked_tickers" in r.json()["history"]

    def test_history_tag_in_openapi(self):
        schema = client.get("/openapi.json").json()
        tag_names = [t["name"] for t in schema.get("tags", [])]
        assert "history" in tag_names


# ── STORAGE INTEGRATION TESTS ─────────────────────────────────────────────────

class TestStorageIntegration:

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=-1.0)
    @patch("data.assembler.get_social_data")
    def test_full_pipeline_analyze_to_history(
            self, mock_social, mock_spy, mock_news, mock_snap):
        """Volledig end-to-end: analyze → opgeslagen → history endpoint → decay."""
        from data.social_client import SocialData
        # Hoog volume voor BUY beslissing
        snap = TickerSnapshot(
            ticker="PIPELINE", timestamp=datetime.now(timezone.utc),
            confidence=DataConfidence.LIVE,
            price=55.0, prev_close=50.0, day_change_pct=10.0,
            premarket_pct=12.0, premarket_available=True,
            volume_today=8_000_000, avg_volume_20d=500_000,  # 16x volume
            market_cap=2e8, float_shares=8_000_000,  # micro cap
            cache_hit=False, data_age_seconds=0.0,
        )
        mock_snap.return_value = snap
        mock_social.return_value = SocialData("PIPELINE", 0, 1, 0.0, "p", False, None)

        # 1. Score
        r1 = client.get("/analyze/PIPELINE")
        assert r1.status_code == 200
        original_decision = r1.json()["decision"]
        assert original_decision not in ("SKIP", "BLOCKED")

        # 2. History
        r2 = client.get("/history/PIPELINE")
        assert r2.status_code == 200
        d = r2.json()
        assert d["snapshot_count"] == 1

        # 3. Effective signal (fresh → geen decay)
        sig = d["effective_signals"][0]
        assert sig["effective_decision"] == original_decision
        assert sig["signal_age"] == "FRESH"
        assert sig["is_actionable"] is True

        # 4. Window open
        r3 = client.get("/history/PIPELINE/window")
        assert r3.status_code == 200
        assert r3.json()["window_open"] is True

    def test_snapshot_store_never_raises(self):
        """save_snapshot_dict crasht nooit, ook bij ongeldige data."""
        from storage.snapshot_store import save_snapshot_dict
        try:
            save_snapshot_dict("CRASH", {})        # Leeg dict
            save_snapshot_dict("CRASH", {"x": 1})  # Minimale data
        except Exception as e:
            pytest.fail(f"save_snapshot_dict gooide exception: {e}")

    def test_signal_decay_never_raises(self):
        """apply_decay crasht nooit bij ongeldige invoer."""
        from storage.signal_decay import apply_decay
        try:
            apply_decay("UNKNOWN_DECISION", -99.0, "invalid_timestamp")
            apply_decay("", 0.0, "")
        except Exception as e:
            pytest.fail(f"apply_decay gooide exception: {e}")
