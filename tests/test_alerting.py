"""
tests/test_alerting.py
Alerting & Watchlist Layer Tests — v2.9

Coverage:
    TestAlertStore          Save/load, severity filter, index
    TestCooldownManager     Set/check/expire, per-trigger isolation
    TestWatchlistManager    CRUD, validation, builtin protection
    TestAlertEngine         Each trigger type, threshold crossing
    TestDuplicateSuppression Cooldown prevents double alerts
    TestSeverityTransitions  Severity logic per trigger/decision
    TestEvalAwareAlerts      Historical context injection
    TestAlertEndpoints       API endpoints
    TestWatchlistEndpoints   Watchlist API
"""

import json, os, time, pytest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from backend.app import app
from schemas.ticker_snapshot import TickerSnapshot, DataConfidence

client = TestClient(app)


# ── TEST ISOLATIE ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    # Storage
    tickers_dir = tmp_path / "tickers"; tickers_dir.mkdir()
    evals_dir   = tmp_path / "evals";   evals_dir.mkdir()
    alerts_dir  = tmp_path / "alerts";  alerts_dir.mkdir()
    sectors_dir = tmp_path / "sectors"; sectors_dir.mkdir()

    monkeypatch.setattr("storage.snapshot_store._TICKERS_DIR",  str(tickers_dir))
    monkeypatch.setattr("storage.signal_tracker._TICKERS_DIR",  str(tickers_dir))
    monkeypatch.setattr("storage.sector_history._SECTORS_DIR",  str(sectors_dir))
    monkeypatch.setattr("storage.evaluation_store._EVALS_DIR",  str(evals_dir))
    monkeypatch.setattr("alerting.alert_store._ALERTS_DIR",      str(alerts_dir))

    # Watchlists — gebruik echte bestanden maar met custom dir voor custom lists
    custom_dir = tmp_path / "custom"; custom_dir.mkdir()
    monkeypatch.setattr("alerting.watchlist_manager._CUSTOM_DIR", str(custom_dir))

    # Research dirs
    for d in ["observations", "replay_notes", "signal_reviews"]:
        (tmp_path / d).mkdir()
    monkeypatch.setattr("research.observation_store._OBS_DIR",     str(tmp_path / "observations"))
    monkeypatch.setattr("research.observation_store._NOTES_DIR",   str(tmp_path / "replay_notes"))
    monkeypatch.setattr("research.observation_store._REVIEWS_DIR", str(tmp_path / "signal_reviews"))
    monkeypatch.setattr("research.evaluation_report._REVIEWS_DIR", str(tmp_path / "signal_reviews"))

    # Reset cooldowns
    from alerting.cooldown_manager import clear_all_cooldowns
    clear_all_cooldowns()

    yield tmp_path


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _make_snap(ticker="T", score=65.0, decision="BUY_MODERATE", phase="BREAKOUT",
               vol_ratio=3.2, confidence="LIVE", catalyst="STRONG", price=50.0):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "ticker": ticker, "timestamp": now,
        "version_id": f"V_{ticker}_{now[:19].replace(':','').replace('-','')}",
        "decision": decision, "momentum_score": score, "skip_score": 0,
        "phase": phase, "confidence": confidence, "cache_hit": False,
        "data_age_seconds": 0.0, "retries_used": 0,
        "catalyst_type": catalyst, "catalyst_description": "test",
        "day_change_pct": 6.0, "volume_ratio": vol_ratio,
        "sector_heat": 85, "sector_id": "quantum",
        "market_session": "REGULAR", "price": price,
        "premarket_pct": 0.0, "stored_at": now,
    }


def _save(ticker, **kwargs):
    from storage.snapshot_store import save_snapshot_dict
    save_snapshot_dict(ticker, _make_snap(ticker, **kwargs))


def _make_alert(ticker="T", severity="WATCH", trigger="momentum_threshold"):
    from alerting.alert_store import Alert, make_alert_id
    return Alert(
        alert_id=make_alert_id(ticker, trigger),
        ticker=ticker, severity=severity,
        trigger_type=trigger,
        title=f"{ticker}: test alert",
        message="Test message",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── ALERT STORE TESTS ─────────────────────────────────────────────────────────

class TestAlertStore:

    def test_save_and_load_alert(self):
        from alerting.alert_store import save_alert, load_alerts
        a = _make_alert("NVDA", "HIGH")
        save_alert(a)
        loaded = load_alerts(ticker="NVDA")
        assert len(loaded) == 1
        assert loaded[0]["severity"] == "HIGH"

    def test_load_all_alerts_via_index(self):
        from alerting.alert_store import save_alert, load_alerts
        save_alert(_make_alert("A", "INFO"))
        save_alert(_make_alert("B", "HIGH"))
        all_alerts = load_alerts(limit=10)
        assert len(all_alerts) == 2

    def test_load_by_minimum_severity(self):
        from alerting.alert_store import save_alert, load_alerts
        save_alert(_make_alert("STEST", "INFO",     "phase_transition"))
        save_alert(_make_alert("STEST", "WATCH",    "volume_anomaly"))
        save_alert(_make_alert("STEST", "HIGH",     "momentum_threshold"))
        save_alert(_make_alert("STEST", "CRITICAL", "buy_max_signal"))
        result = load_alerts(ticker="STEST", severity="HIGH")
        assert all(a["severity"] in ("HIGH", "CRITICAL") for a in result)
        assert len(result) == 2

    def test_suppressed_alerts_not_saved(self):
        from alerting.alert_store import save_alert, load_alerts, Alert, make_alert_id
        a = Alert(
            alert_id=make_alert_id("SUP", "test"),
            ticker="SUP", severity="INFO",
            trigger_type="test", title="Test", message="Test",
            timestamp=datetime.now(timezone.utc).isoformat(),
            suppressed=True,
        )
        save_alert(a)
        loaded = load_alerts(ticker="SUP")
        assert len(loaded) == 0

    def test_count_alerts_by_severity(self):
        from alerting.alert_store import save_alert, count_alerts_by_severity
        save_alert(_make_alert("CNT", "INFO"))
        save_alert(_make_alert("CNT", "HIGH"))
        save_alert(_make_alert("CNT", "HIGH"))
        counts = count_alerts_by_severity("CNT")
        assert counts["INFO"] == 1
        assert counts["HIGH"] == 2

    def test_list_alerted_tickers(self):
        from alerting.alert_store import save_alert, list_alerted_tickers
        save_alert(_make_alert("TICK_A"))
        save_alert(_make_alert("TICK_B"))
        tickers = list_alerted_tickers()
        assert "TICK_A" in tickers
        assert "TICK_B" in tickers

    def test_severity_rank_ordering(self):
        from alerting.alert_store import severity_rank
        assert severity_rank("INFO")     < severity_rank("WATCH")
        assert severity_rank("WATCH")    < severity_rank("HIGH")
        assert severity_rank("HIGH")     < severity_rank("CRITICAL")


# ── COOLDOWN TESTS ────────────────────────────────────────────────────────────

class TestCooldownManager:

    def setup_method(self):
        from alerting.cooldown_manager import clear_all_cooldowns
        clear_all_cooldowns()

    def test_no_cooldown_by_default(self):
        from alerting.cooldown_manager import is_suppressed
        assert is_suppressed("NVDA", "momentum_threshold", "HIGH") is False

    def test_suppressed_after_set(self):
        from alerting.cooldown_manager import is_suppressed, set_cooldown
        set_cooldown("NVDA", "momentum_threshold", "HIGH")
        assert is_suppressed("NVDA", "momentum_threshold", "HIGH") is True

    def test_different_trigger_not_suppressed(self):
        from alerting.cooldown_manager import is_suppressed, set_cooldown
        set_cooldown("NVDA", "momentum_threshold", "HIGH")
        # Andere trigger type is niet gesupprimeerd
        assert is_suppressed("NVDA", "phase_transition", "HIGH") is False

    def test_different_ticker_not_suppressed(self):
        from alerting.cooldown_manager import is_suppressed, set_cooldown
        set_cooldown("NVDA", "volume_anomaly", "WATCH")
        assert is_suppressed("AAPL", "volume_anomaly", "WATCH") is False

    def test_cooldown_expires(self):
        from alerting.cooldown_manager import is_suppressed, set_cooldown, _cooldowns
        set_cooldown("EXPTEST", "test_trigger", "INFO", override_minutes=0.001)
        # Vervals de cooldown direct
        _cooldowns[list(_cooldowns.keys())[-1]] = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        assert is_suppressed("EXPTEST", "test_trigger", "INFO") is False

    def test_clear_cooldown_for_ticker(self):
        from alerting.cooldown_manager import is_suppressed, set_cooldown, clear_cooldown
        set_cooldown("CLRTEST", "momentum_threshold", "HIGH")
        set_cooldown("CLRTEST", "phase_transition",   "WATCH")
        cleared = clear_cooldown("CLRTEST")
        assert cleared == 2
        assert is_suppressed("CLRTEST", "momentum_threshold", "HIGH") is False

    def test_clear_all_cooldowns(self):
        from alerting.cooldown_manager import set_cooldown, clear_all_cooldowns, get_active_cooldowns
        set_cooldown("A", "t1", "INFO")
        set_cooldown("B", "t1", "INFO")
        count = clear_all_cooldowns()
        assert count == 2
        assert len(get_active_cooldowns()) == 0

    def test_cooldown_stats(self):
        from alerting.cooldown_manager import set_cooldown, cooldown_stats
        set_cooldown("STAT", "trigger_x", "WATCH")
        stats = cooldown_stats()
        assert stats["active_cooldowns"] >= 1


# ── WATCHLIST MANAGER TESTS ───────────────────────────────────────────────────

class TestWatchlistManager:

    def test_list_builtin_watchlists(self):
        from alerting.watchlist_manager import list_watchlists
        wls = list_watchlists()
        names = [w["name"] for w in wls]
        assert "core" in names
        assert "momentum" in names
        assert "sector_rotation" in names

    def test_load_core_watchlist(self):
        from alerting.watchlist_manager import load_watchlist
        wl = load_watchlist("core")
        assert wl is not None
        assert len(wl["tickers"]) > 0

    def test_load_nonexistent_returns_none(self):
        from alerting.watchlist_manager import load_watchlist
        assert load_watchlist("nonexistent_xyz") is None

    def test_create_custom_watchlist(self):
        from alerting.watchlist_manager import create_watchlist, load_watchlist
        wl = create_watchlist("mylist", "Test", ["IONQ", "QBTS"])
        assert wl["name"] == "mylist"
        assert "IONQ" in wl["tickers"]
        # Herlaadbaar
        loaded = load_watchlist("mylist")
        assert loaded is not None

    def test_create_duplicate_raises(self):
        from alerting.watchlist_manager import create_watchlist
        create_watchlist("duptest", "First")
        with pytest.raises(ValueError, match="bestaat al"):
            create_watchlist("duptest", "Second")

    def test_invalid_name_raises(self):
        from alerting.watchlist_manager import create_watchlist
        with pytest.raises(ValueError):
            create_watchlist("INVALID NAME!", "Test")

    def test_add_ticker(self):
        from alerting.watchlist_manager import create_watchlist, add_ticker
        create_watchlist("addtest", "Test")
        wl = add_ticker("addtest", "nvda")
        assert "NVDA" in wl["tickers"]

    def test_add_duplicate_ticker_no_duplication(self):
        from alerting.watchlist_manager import create_watchlist, add_ticker
        create_watchlist("duptickertest", "Test", ["NVDA"])
        wl = add_ticker("duptickertest", "NVDA")
        assert wl["tickers"].count("NVDA") == 1

    def test_remove_ticker(self):
        from alerting.watchlist_manager import create_watchlist, remove_ticker
        create_watchlist("remtest", "Test", ["NVDA", "AAPL"])
        wl = remove_ticker("remtest", "NVDA")
        assert "NVDA" not in wl["tickers"]
        assert "AAPL" in wl["tickers"]

    def test_add_to_nonexistent_raises(self):
        from alerting.watchlist_manager import add_ticker
        with pytest.raises(ValueError):
            add_ticker("nonexistent_xyz", "NVDA")

    def test_invalid_ticker_rejected(self):
        from alerting.watchlist_manager import create_watchlist, add_ticker
        create_watchlist("invtickertest", "Test")
        with pytest.raises(ValueError):
            add_ticker("invtickertest", "123INVALID!")

    def test_cannot_delete_builtin(self):
        from alerting.watchlist_manager import delete_watchlist
        with pytest.raises(ValueError, match="Ingebouwde"):
            delete_watchlist("core")

    def test_delete_custom_watchlist(self):
        from alerting.watchlist_manager import create_watchlist, delete_watchlist, load_watchlist
        create_watchlist("deleteme", "Test")
        result = delete_watchlist("deleteme")
        assert result is True
        assert load_watchlist("deleteme") is None

    def test_get_all_watchlist_tickers(self):
        from alerting.watchlist_manager import get_all_watchlist_tickers
        tickers = get_all_watchlist_tickers()
        assert len(tickers) > 0
        assert all(t == t.upper() for t in tickers)

    def test_get_ticker_watchlists(self):
        from alerting.watchlist_manager import get_ticker_watchlists
        # NVDA is in core watchlist
        wls = get_ticker_watchlists("NVDA")
        assert "core" in wls


# ── ALERT ENGINE TESTS ────────────────────────────────────────────────────────

class TestAlertEngine:

    def test_momentum_threshold_crossing_watch_to_buy(self):
        from alerting.alert_engine import check_momentum_threshold
        old = _make_snap("T", score=40.0, decision="WATCH")
        new = _make_snap("T", score=68.0, decision="BUY_MODERATE")
        alerts = check_momentum_threshold("T", old, new)
        assert len(alerts) >= 1
        assert any(a.trigger_type == "momentum_threshold" for a in alerts)

    def test_momentum_threshold_no_change(self):
        from alerting.alert_engine import check_momentum_threshold
        old = new = _make_snap("T", score=65.0, decision="BUY_MODERATE")
        alerts = check_momentum_threshold("T", old, new)
        assert len(alerts) == 0

    def test_buy_max_triggers_critical(self):
        from alerting.alert_engine import check_momentum_threshold
        old = _make_snap("T", score=74.0, decision="BUY_STRONG")
        new = _make_snap("T", score=91.0, decision="BUY_MAX")
        alerts = check_momentum_threshold("T", old, new)
        severities = [a.severity for a in alerts]
        assert "CRITICAL" in severities

    def test_phase_transition_detected(self):
        from alerting.alert_engine import check_phase_transition
        old = _make_snap("T", phase="NEUTRAL")
        new = _make_snap("T", phase="BREAKOUT")
        alerts = check_phase_transition("T", old, new)
        assert len(alerts) == 1
        assert alerts[0].trigger_type == "phase_transition"

    def test_no_phase_alert_same_phase(self):
        from alerting.alert_engine import check_phase_transition
        old = new = _make_snap("T", phase="BREAKOUT")
        alerts = check_phase_transition("T", old, new)
        assert len(alerts) == 0

    def test_frenzy_phase_is_critical(self):
        from alerting.alert_engine import check_phase_transition
        old = _make_snap("T", phase="EXPANSION")
        new = _make_snap("T", phase="FRENZY")
        alerts = check_phase_transition("T", old, new)
        assert alerts[0].severity == "CRITICAL"

    def test_volume_anomaly_above_threshold(self):
        from alerting.alert_engine import check_volume_anomaly
        snap = _make_snap("T", vol_ratio=4.5)
        alerts = check_volume_anomaly("T", snap, threshold=3.0)
        assert len(alerts) == 1
        assert alerts[0].trigger_type == "volume_anomaly"

    def test_volume_anomaly_below_threshold(self):
        from alerting.alert_engine import check_volume_anomaly
        snap = _make_snap("T", vol_ratio=2.0)
        alerts = check_volume_anomaly("T", snap, threshold=3.0)
        assert len(alerts) == 0

    def test_confidence_downgrade_detected(self):
        from alerting.alert_engine import check_confidence_downgrade
        old = _make_snap("T", confidence="LIVE")
        new = _make_snap("T", confidence="STALE")
        alerts = check_confidence_downgrade("T", old, new)
        assert len(alerts) == 1
        assert alerts[0].trigger_type == "confidence_downgrade"

    def test_confidence_upgrade_no_alert(self):
        from alerting.alert_engine import check_confidence_downgrade
        old = _make_snap("T", confidence="STALE")
        new = _make_snap("T", confidence="LIVE")
        alerts = check_confidence_downgrade("T", old, new)
        assert len(alerts) == 0

    def test_score_drop_triggers_alert(self):
        from alerting.alert_engine import check_score_drop
        old = _make_snap("T", score=80.0)
        new = _make_snap("T", score=55.0)  # -25 pts
        alerts = check_score_drop("T", old, new, threshold=15.0)
        assert len(alerts) == 1
        assert alerts[0].trigger_type == "score_drop"

    def test_small_score_drop_no_alert(self):
        from alerting.alert_engine import check_score_drop
        old = _make_snap("T", score=70.0)
        new = _make_snap("T", score=62.0)  # -8 pts (< 15)
        alerts = check_score_drop("T", old, new, threshold=15.0)
        assert len(alerts) == 0

    def test_scan_ticker_insufficient_snapshots(self):
        """< 2 snapshots → geen alerts."""
        from alerting.alert_engine import scan_ticker
        _save("ONESNAPONLY")
        alerts = scan_ticker("ONESNAPONLY")
        assert alerts == []

    def test_scan_ticker_with_two_snapshots(self):
        """2 snapshots → wordt vergeleken."""
        from alerting.alert_engine import scan_ticker
        _save("TWOSNAPTEST", score=40.0, decision="WATCH", phase="NEUTRAL")
        time.sleep(0.01)
        _save("TWOSNAPTEST", score=75.0, decision="BUY_STRONG", phase="BREAKOUT")
        alerts = scan_ticker("TWOSNAPTEST")
        # Moet alerts genereren (threshold + phase transition)
        assert len(alerts) >= 1


# ── DUPLICATE SUPPRESSION TESTS ───────────────────────────────────────────────

class TestDuplicateSuppression:

    def setup_method(self):
        from alerting.cooldown_manager import clear_all_cooldowns
        clear_all_cooldowns()

    def test_same_alert_suppressed_within_cooldown(self):
        from alerting.alert_engine import check_momentum_threshold
        old = _make_snap("DUP", score=40.0, decision="WATCH")
        new = _make_snap("DUP", score=65.0, decision="BUY_MODERATE")

        # Eerste keer: alert fired
        alerts1 = check_momentum_threshold("DUP", old, new)
        assert len(alerts1) >= 1

        # Tweede keer: gesupprimeerd door cooldown
        alerts2 = check_momentum_threshold("DUP", old, new)
        assert len(alerts2) == 0

    def test_different_trigger_not_suppressed(self):
        """Verschillende trigger types hebben eigen cooldown."""
        from alerting.alert_engine import (
            check_momentum_threshold, check_phase_transition
        )
        old_snap = _make_snap("DIF", score=40.0, decision="WATCH", phase="NEUTRAL")
        new_snap = _make_snap("DIF", score=65.0, decision="BUY_MODERATE", phase="BREAKOUT")

        check_momentum_threshold("DIF", old_snap, new_snap)
        # Phase transition heeft eigen cooldown
        phase_alerts = check_phase_transition("DIF", old_snap, new_snap)
        assert len(phase_alerts) >= 1

    def test_clear_cooldown_re_enables_alert(self):
        from alerting.alert_engine import check_momentum_threshold
        from alerting.cooldown_manager import clear_cooldown
        old = _make_snap("CLR", score=40.0, decision="WATCH")
        new = _make_snap("CLR", score=65.0, decision="BUY_MODERATE")

        check_momentum_threshold("CLR", old, new)
        clear_cooldown("CLR")  # Wis cooldown

        alerts = check_momentum_threshold("CLR", old, new)
        assert len(alerts) >= 1


# ── SEVERITY TRANSITION TESTS ─────────────────────────────────────────────────

class TestSeverityTransitions:

    def test_watch_decision_gives_info_severity(self):
        from alerting.alert_engine import check_momentum_threshold
        old = _make_snap("SEV", score=28.0, decision="SKIP")
        new = _make_snap("SEV", score=33.0, decision="WATCH")
        alerts = [a for a in check_momentum_threshold("SEV", old, new)
                  if a.trigger_type == "momentum_threshold"]
        if alerts:
            assert alerts[0].severity == "INFO"

    def test_buy_max_gives_critical_severity(self):
        from alerting.alert_engine import check_momentum_threshold
        old = _make_snap("SEVCRIT", score=74.0, decision="BUY_STRONG")
        new = _make_snap("SEVCRIT", score=92.0, decision="BUY_MAX")
        alerts = check_momentum_threshold("SEVCRIT", old, new)
        severities = [a.severity for a in alerts]
        assert "CRITICAL" in severities

    def test_volume_extremes_critical(self):
        from alerting.alert_engine import check_volume_anomaly
        snap = _make_snap("VOLEXT", vol_ratio=8.5)
        alerts = check_volume_anomaly("VOLEXT", snap, threshold=3.0)
        assert alerts[0].severity == "CRITICAL"


# ── EVALUATION-AWARE ALERTS TESTS ─────────────────────────────────────────────

class TestEvalAwareAlerts:

    def test_no_eval_insight_without_data(self):
        """Geen evaluatiedata → geen eval_insight alert."""
        from alerting.alert_engine import check_evaluation_insight
        snap = _make_snap("NOEVAL")
        alerts = check_evaluation_insight("NOEVAL", snap)
        assert alerts == []

    def test_no_eval_insight_for_skip(self):
        """SKIP beslissing → geen eval_insight."""
        from alerting.alert_engine import check_evaluation_insight
        snap = _make_snap("SKIPEVL", decision="SKIP")
        alerts = check_evaluation_insight("SKIPEVL", snap)
        assert alerts == []

    def test_eval_insight_needs_minimum_graded(self):
        """Minder dan 5 grades → geen eval_insight."""
        from alerting.alert_engine import check_evaluation_insight
        from storage.evaluation_store import save_outcome, SignalOutcome
        # Sla slechts 2 grades op
        for i in range(2):
            o = SignalOutcome(
                version_id=f"V_{i}", ticker="EVALTEST",
                timestamp=datetime.now(timezone.utc).isoformat(),
                decision="BUY_MODERATE", momentum_score=70.0,
                phase="BREAKOUT", catalyst_type="STRONG",
                sector_id="quantum", entry_price=50.0,
                return_1d=5.0, grade="SUCCESS", grade_basis="1d",
                graded_at=datetime.now(timezone.utc).isoformat(),
                evaluated_at=datetime.now(timezone.utc).isoformat(),
            )
            save_outcome(o)
        snap = _make_snap("EVALTEST")
        alerts = check_evaluation_insight("EVALTEST", snap)
        assert alerts == []


# ── ALERT API ENDPOINTS ───────────────────────────────────────────────────────

class TestAlertEndpoints:

    def test_get_alerts_empty(self):
        r = client.get("/alerts")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_get_alerts_with_data(self):
        from alerting.alert_store import save_alert
        save_alert(_make_alert("ENDTEST", "HIGH"))
        r = client.get("/alerts?ticker=ENDTEST")
        assert r.status_code == 200
        assert r.json()["count"] == 1

    def test_get_alerts_severity_filter(self):
        from alerting.alert_store import save_alert
        save_alert(_make_alert("SEVFILT", "INFO"))
        save_alert(_make_alert("SEVFILT", "CRITICAL"))
        r = client.get("/alerts?ticker=SEVFILT&severity=HIGH")
        d = r.json()
        assert all(a["severity"] in ("HIGH", "CRITICAL") for a in d["alerts"])

    def test_scan_alerts_endpoint(self):
        r = client.post("/alerts/scan")
        assert r.status_code == 200
        d = r.json()
        assert "tickers_scanned" in d
        assert "total_alerts_fired" in d

    def test_scan_ticker_endpoint_invalid(self):
        r = client.post("/alerts/scan/123INVALID")
        assert r.status_code == 400

    def test_scan_ticker_endpoint_valid(self):
        r = client.post("/alerts/scan/NVDA")
        assert r.status_code == 200
        d = r.json()
        assert d["ticker"] == "NVDA"
        assert "alerts_fired" in d

    def test_alerts_in_openapi(self):
        schema = client.get("/openapi.json").json()
        paths  = schema["paths"]
        assert "/alerts" in paths
        assert "/alerts/scan" in paths


# ── WATCHLIST API ENDPOINTS ───────────────────────────────────────────────────

class TestWatchlistEndpoints:

    def test_get_watchlists(self):
        r = client.get("/watchlists")
        assert r.status_code == 200
        d = r.json()
        assert d["count"] >= 3  # core + momentum + sector_rotation
        names = [w["name"] for w in d["watchlists"]]
        assert "core" in names

    def test_get_specific_watchlist(self):
        r = client.get("/watchlists/core")
        assert r.status_code == 200
        assert r.json()["name"] == "core"

    def test_get_nonexistent_watchlist_404(self):
        r = client.get("/watchlists/doesnotexist_xyz")
        assert r.status_code == 404

    def test_create_custom_watchlist(self):
        r = client.post("/watchlists?name=mytest&description=Test&tickers=IONQ,QBTS")
        assert r.status_code == 200
        d = r.json()
        assert d["created"] is True
        assert "IONQ" in d["watchlist"]["tickers"]

    def test_create_invalid_name(self):
        r = client.post("/watchlists?name=INVALID NAME!")
        assert r.status_code == 400

    def test_add_ticker_to_watchlist(self):
        client.post("/watchlists?name=addtest&description=Test")
        r = client.post("/watchlists/addtest/add?ticker=NVDA")
        assert r.status_code == 200
        assert "NVDA" in r.json()["watchlist"]["tickers"]

    def test_remove_ticker_from_watchlist(self):
        client.post("/watchlists?name=remtest&description=Test&tickers=NVDA,AAPL")
        r = client.post("/watchlists/remtest/remove?ticker=NVDA")
        assert r.status_code == 200
        assert "NVDA" not in r.json()["watchlist"]["tickers"]

    def test_add_to_nonexistent_watchlist(self):
        r = client.post("/watchlists/doesnotexist_xyz/add?ticker=NVDA")
        assert r.status_code == 400

    def test_watchlists_in_openapi(self):
        schema = client.get("/openapi.json").json()
        paths  = schema["paths"]
        assert "/watchlists" in paths
        assert "/watchlists/{name}" in paths
        assert "/watchlists/{name}/add" in paths
        assert "/watchlists/{name}/remove" in paths
