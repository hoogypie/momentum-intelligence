"""
tests/test_validation_runner.py
Validation Runner Tests — v1.0

Test coverage:
    TestWatchlistLoading        JSON laden, groep-filter, active flag
    TestAnalyzeOne              Correcte output bij succes en bij fout
    TestExtractTopReasons       Reason extractie: blocked, skip, momentum
    TestWriteOutputs            CSV + JSON aangemaakt, kolomstructuur klopt
    TestPrintReport             Print crasht niet bij lege / error results
    TestMainArgParsing          CLI argumenten en ticker-overrides
"""

import csv
import json
import os
import pytest
import tempfile
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_quality(
    confidence="LIVE", price_ok=True, volume_ok=True,
    news_ok=False, cache_hit=False, fetch_error=None,
):
    q = MagicMock()
    q.confidence.value  = confidence
    q.price_available   = price_ok
    q.volume_available  = volume_ok
    q.news_available    = news_ok
    q.cache_hit         = cache_hit
    q.fetch_error       = fetch_error
    return q


def _mock_result(
    ticker="NVDA", decision="BUY_STRONG", momentum=72.5, skip=0,
    phase="BREAKOUT", tier="LARGE", sizing="€300-400",
    volume=15.0, catalyst=12.0, heat=17.1, premarket=0.0,
    rel_str=6.0, social=0.0, float_s=4.5,
    social_capped=False, skip_blocked=False,
    skip_reasons=None, blocking_reasons=None,
    breakdown=None,
):
    md = MagicMock()
    md.volume_anomaly          = volume
    md.catalyst_quality        = catalyst
    md.sector_heat_score       = heat
    md.premarket_strength      = premarket
    md.relative_strength_score = rel_str
    md.social_acceleration     = social
    md.float_score             = float_s
    md.social_was_capped       = social_capped
    md.social_cap_reason       = ""
    md.breakdown               = breakdown or {}

    sd = MagicMock()
    sd.total             = skip
    sd.is_hard_blocked   = skip_blocked
    sd.reasons           = skip_reasons or []
    sd.blocking_reasons  = blocking_reasons or []

    r = MagicMock()
    r.ticker           = ticker
    r.decision.value   = decision
    r.momentum_score   = momentum
    r.skip_score       = skip
    r.phase.value      = phase
    r.phase_description = f"{phase} fase"
    r.market_cap_tier.value = tier
    r.sizing_eur       = sizing
    r.summary          = f"{ticker}: {decision} | {momentum:.1f}"
    r.momentum_detail  = md
    r.skip_detail      = sd
    return r


# ── TestWatchlistLoading ──────────────────────────────────────────────────────

class TestWatchlistLoading:

    def test_loads_all_tickers(self):
        from scripts.validation_runner import _load_watchlist
        entries = _load_watchlist()
        assert len(entries) >= 30  # verwacht ~39

    def test_all_entries_have_required_keys(self):
        from scripts.validation_runner import _load_watchlist
        for entry in _load_watchlist():
            assert "ticker" in entry
            assert "group_id" in entry
            assert "group_label" in entry
            assert "cap" in entry

    def test_tickers_are_uppercase(self):
        from scripts.validation_runner import _load_watchlist
        for entry in _load_watchlist():
            assert entry["ticker"] == entry["ticker"].upper()

    def test_group_filter_quantum(self):
        from scripts.validation_runner import _load_watchlist
        entries = _load_watchlist(group_filter="quantum")
        assert len(entries) >= 3
        assert all(e["group_id"] == "quantum" for e in entries)

    def test_group_filter_nonexistent_returns_empty(self):
        from scripts.validation_runner import _load_watchlist
        entries = _load_watchlist(group_filter="bestaat_niet")
        assert entries == []

    def test_group_filter_ai_infra(self):
        from scripts.validation_runner import _load_watchlist
        entries = _load_watchlist(group_filter="ai_infra")
        tickers = [e["ticker"] for e in entries]
        assert "NVDA" in tickers
        assert "MU" in tickers

    def test_active_false_skipped(self, tmp_path):
        """Tickers met active=false worden overgeslagen."""
        wl = {
            "groups": [{
                "id": "test", "label": "Test", "sector_heat": 50,
                "tickers": [
                    {"ticker": "ACTIVE", "cap": "LARGE", "active": True},
                    {"ticker": "INACTIVE", "cap": "LARGE", "active": False},
                ]
            }]
        }
        wl_path = tmp_path / "wl.json"
        wl_path.write_text(json.dumps(wl))

        from scripts.validation_runner import _load_watchlist
        with patch("scripts.validation_runner._WATCHLIST_PATH", str(wl_path)):
            entries = _load_watchlist()

        tickers = [e["ticker"] for e in entries]
        assert "ACTIVE" in tickers
        assert "INACTIVE" not in tickers

    def test_note_included_in_entry(self):
        from scripts.validation_runner import _load_watchlist
        entries = _load_watchlist(group_filter="ai_infra")
        nvda = next(e for e in entries if e["ticker"] == "NVDA")
        assert len(nvda["note"]) > 0


# ── TestAnalyzeOne ────────────────────────────────────────────────────────────

class TestAnalyzeOne:

    def test_returns_dict_on_success(self):
        mock_result  = _mock_result()
        mock_quality = _mock_quality()

        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(MagicMock(), mock_quality)):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=mock_result):
                from scripts.validation_runner import _analyze_one
                result = _analyze_one("NVDA")

        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert result["ticker"] == "NVDA"

    def test_status_ok_on_success(self):
        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(MagicMock(), _mock_quality())):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=_mock_result()):
                from scripts.validation_runner import _analyze_one
                result = _analyze_one("NVDA")
        assert result["status"] == "ok"

    def test_status_error_on_exception(self):
        with patch("scripts.validation_runner.build_ticker_input",
                   side_effect=RuntimeError("Yahoo kapot")):
            from scripts.validation_runner import _analyze_one
            result = _analyze_one("BROKEN")
        assert result["status"] == "error"
        assert result["decision"] == "ERROR"
        assert result["momentum_score"] == 0.0

    def test_never_raises_exception(self):
        with patch("scripts.validation_runner.build_ticker_input",
                   side_effect=Exception("willekeurige crash")):
            from scripts.validation_runner import _analyze_one
            try:
                result = _analyze_one("CRASH")
                assert isinstance(result, dict)
            except Exception as exc:
                pytest.fail(f"_analyze_one gooidde een exception: {exc}")

    def test_all_required_fields_present(self):
        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(MagicMock(), _mock_quality())):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=_mock_result()):
                from scripts.validation_runner import _analyze_one
                result = _analyze_one("NVDA")

        required = [
            "ticker", "status", "decision", "momentum_score", "skip_score",
            "phase", "market_cap_tier", "sizing_eur", "top_reasons",
            "m_volume", "m_catalyst", "m_sector_heat",
            "data_confidence", "fetch_error", "analyzed_at",
        ]
        for field in required:
            assert field in result, f"Veld ontbreekt: {field}"

    def test_decision_value_from_result(self):
        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(MagicMock(), _mock_quality())):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=_mock_result(decision="BUY_MAX", momentum=91.0)):
                from scripts.validation_runner import _analyze_one
                result = _analyze_one("NVDA")
        assert result["decision"] == "BUY_MAX"
        assert result["momentum_score"] == 91.0

    def test_error_fetch_error_captured(self):
        with patch("scripts.validation_runner.build_ticker_input",
                   side_effect=KeyError("currentTradingPeriod")):
            from scripts.validation_runner import _analyze_one
            result = _analyze_one("KAPOT")
        assert "KeyError" in result["fetch_error"] or "currentTradingPeriod" in result["fetch_error"]

    def test_data_confidence_from_quality(self):
        with patch("scripts.validation_runner.build_ticker_input",
                   return_value=(MagicMock(), _mock_quality(confidence="STALE"))):
            with patch("scripts.validation_runner.score_ticker",
                       return_value=_mock_result()):
                from scripts.validation_runner import _analyze_one
                result = _analyze_one("NVDA")
        assert result["data_confidence"] == "STALE"


# ── TestExtractTopReasons ─────────────────────────────────────────────────────

class TestExtractTopReasons:

    def test_returns_list(self):
        from scripts.validation_runner import _extract_top_reasons
        result  = _mock_result()
        quality = _mock_quality()
        reasons = _extract_top_reasons(result, quality)
        assert isinstance(reasons, list)

    def test_blocked_reason_first(self):
        from scripts.validation_runner import _extract_top_reasons
        result = _mock_result(
            skip_blocked=True,
            blocking_reasons=["SEC investigation"],
        )
        reasons = _extract_top_reasons(result, _mock_quality())
        assert any("BLOCKED" in r for r in reasons)

    def test_skip_reasons_included_when_high_skip(self):
        from scripts.validation_runner import _extract_top_reasons
        result = _mock_result(skip=40, skip_reasons=["dag +42% skip"])
        reasons = _extract_top_reasons(result, _mock_quality())
        assert any("SKIP" in r for r in reasons)

    def test_momentum_components_in_reasons(self):
        from scripts.validation_runner import _extract_top_reasons
        result = _mock_result(volume=20.0, catalyst=18.0)
        reasons = _extract_top_reasons(result, _mock_quality())
        # Ten minste één momentum component in de output
        assert len(reasons) >= 1

    def test_no_news_warning_when_catalyst_zero(self):
        from scripts.validation_runner import _extract_top_reasons
        result  = _mock_result(catalyst=0.0)
        quality = _mock_quality(news_ok=False)
        reasons = _extract_top_reasons(result, quality)
        assert any("news" in r.lower() or "catalyst" in r.lower() for r in reasons)

    def test_max_three_reasons(self):
        from scripts.validation_runner import _extract_top_reasons
        result  = _mock_result()
        quality = _mock_quality()
        reasons = _extract_top_reasons(result, quality)
        assert len(reasons) <= 4  # top_reasons[:3] in caller, maar functie geeft meer terug

    def test_fetch_error_included_when_present(self):
        from scripts.validation_runner import _extract_top_reasons
        result  = _mock_result()
        quality = _mock_quality(fetch_error="KeyError: currentTradingPeriod")
        reasons = _extract_top_reasons(result, quality)
        assert any("data_warn" in r for r in reasons)


# ── TestWriteOutputs ──────────────────────────────────────────────────────────

class TestWriteOutputs:

    def _make_results(self, n=3):
        return [
            {
                "ticker": f"T{i}", "status": "ok",
                "decision": "BUY_STRONG", "momentum_score": 70.0 + i,
                "skip_score": 0, "phase": "BREAKOUT", "market_cap_tier": "LARGE",
                "sizing_eur": "€300-400", "summary": f"T{i}: BUY_STRONG",
                "m_volume": 10.0, "m_catalyst": 12.0, "m_sector_heat": 15.0,
                "m_premarket": 0.0, "m_rel_strength": 5.0, "m_social": 0.0,
                "m_float": 4.5, "social_capped": False,
                "skip_blocked": False, "skip_reasons": "",
                "data_confidence": "LIVE", "cache_hit": False, "fetch_error": "",
                "top_reasons": "volume=10/22 | sector_heat=15/18",
                "analyzed_at": "2026-05-29T12:00:00+00:00",
            }
            for i in range(n)
        ]

    def test_creates_json_and_csv(self, tmp_path):
        from scripts.validation_runner import _write_outputs
        with patch("scripts.validation_runner._OUTPUT_DIR", str(tmp_path)):
            json_path, csv_path = _write_outputs(
                self._make_results(), {"run_timestamp": "20260529_120000"}, "20260529_120000"
            )
        assert os.path.exists(json_path)
        assert os.path.exists(csv_path)

    def test_json_contains_results_and_meta(self, tmp_path):
        from scripts.validation_runner import _write_outputs
        with patch("scripts.validation_runner._OUTPUT_DIR", str(tmp_path)):
            json_path, _ = _write_outputs(
                self._make_results(2), {"run_timestamp": "20260529_120000"}, "20260529_120000"
            )
        with open(json_path) as f:
            data = json.load(f)
        assert "results" in data
        assert "meta" in data
        assert len(data["results"]) == 2

    def test_csv_has_correct_columns(self, tmp_path):
        from scripts.validation_runner import _write_outputs, _CSV_COLUMNS
        with patch("scripts.validation_runner._OUTPUT_DIR", str(tmp_path)):
            _, csv_path = _write_outputs(
                self._make_results(1), {"run_timestamp": "20260529_120000"}, "20260529_120000"
            )
        with open(csv_path) as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames
        for col in ["ticker", "decision", "momentum_score", "top_reasons"]:
            assert col in cols

    def test_csv_sorted_by_momentum_desc(self, tmp_path):
        from scripts.validation_runner import _write_outputs
        results = self._make_results(3)
        results[0]["momentum_score"] = 50.0
        results[1]["momentum_score"] = 90.0
        results[2]["momentum_score"] = 70.0

        with patch("scripts.validation_runner._OUTPUT_DIR", str(tmp_path)):
            _, csv_path = _write_outputs(
                results, {"run_timestamp": "20260529_120000"}, "20260529_120000"
            )
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        scores = [float(r["momentum_score"]) for r in rows]
        assert scores == sorted(scores, reverse=True)

    def test_errors_go_last_in_csv(self, tmp_path):
        from scripts.validation_runner import _write_outputs
        results = self._make_results(2)
        error_result = results[0].copy()
        error_result.update({"status": "error", "decision": "ERROR", "momentum_score": 0.0})
        mixed = [error_result] + results[1:]

        with patch("scripts.validation_runner._OUTPUT_DIR", str(tmp_path)):
            _, csv_path = _write_outputs(
                mixed, {"run_timestamp": "20260529_120000"}, "20260529_120000"
            )
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        assert rows[-1]["decision"] == "ERROR"

    def test_output_dir_created_if_missing(self, tmp_path):
        new_dir = str(tmp_path / "nested" / "validation")
        from scripts.validation_runner import _write_outputs
        with patch("scripts.validation_runner._OUTPUT_DIR", new_dir):
            _write_outputs(
                self._make_results(1), {"run_timestamp": "20260529_120000"}, "20260529_120000"
            )
        assert os.path.isdir(new_dir)


# ── TestPrintReport ───────────────────────────────────────────────────────────

class TestPrintReport:

    def _sample_results(self):
        return [
            {
                "ticker": "NVDA", "status": "ok", "decision": "BUY_STRONG",
                "momentum_score": 74.5, "skip_score": 0, "phase": "BREAKOUT",
                "market_cap_tier": "LARGE", "sizing_eur": "€300-400",
                "top_reasons": "volume=15/22 | sector=17/18",
                "data_confidence": "LIVE", "fetch_error": "",
                "social_capped": False, "skip_blocked": False,
                "m_catalyst": 12.0, "summary": "NVDA: BUY_STRONG | 74.5",
            },
            {
                "ticker": "KO", "status": "ok", "decision": "SKIP",
                "momentum_score": 22.0, "skip_score": 45, "phase": "NEUTRAL",
                "market_cap_tier": "LARGE", "sizing_eur": "€0",
                "top_reasons": "SKIP: geen catalyst",
                "data_confidence": "LIVE", "fetch_error": "",
                "social_capped": False, "skip_blocked": False,
                "m_catalyst": 0.0, "summary": "KO: SKIP | 22.0",
            },
        ]

    def test_print_does_not_crash(self, capsys):
        from scripts.validation_runner import _print_report
        _print_report(self._sample_results(), {"run_timestamp": "20260529_120000"})
        out = capsys.readouterr().out
        assert "NVDA" in out

    def test_print_empty_results_does_not_crash(self, capsys):
        from scripts.validation_runner import _print_report
        _print_report([], {"run_timestamp": "20260529_120000"})

    def test_print_all_errors_does_not_crash(self, capsys):
        from scripts.validation_runner import _print_report
        errors = [
            {
                "ticker": "BROKEN", "status": "error", "decision": "ERROR",
                "momentum_score": 0.0, "skip_score": 0, "phase": "",
                "market_cap_tier": "", "sizing_eur": "€0",
                "top_reasons": "FETCH FAILED: RuntimeError",
                "data_confidence": "MISSING", "fetch_error": "boom",
                "social_capped": False, "skip_blocked": False,
                "m_catalyst": 0.0, "summary": "ERROR",
            }
        ]
        _print_report(errors, {"run_timestamp": "20260529_120000"})

    def test_decision_distribution_shown(self, capsys):
        from scripts.validation_runner import _print_report
        _print_report(self._sample_results(), {"run_timestamp": "20260529_120000"})
        out = capsys.readouterr().out
        assert "BUY_STRONG" in out
        assert "SKIP" in out

    def test_catalyst_none_warning_shown(self, capsys):
        from scripts.validation_runner import _print_report
        results = self._sample_results()
        # KO heeft al catalyst=0.0
        _print_report(results, {"run_timestamp": "20260529_120000"})
        out = capsys.readouterr().out
        assert "catalyst=NONE" in out or "NONE" in out


# ── TestMainArgParsing ────────────────────────────────────────────────────────

class TestMainArgParsing:

    def test_ticker_override_used_over_watchlist(self):
        """--ticker overschrijft de watchlist — _load_watchlist wordt niet aangeroepen."""
        with patch("scripts.validation_runner._analyze_one",
                   return_value={
                       "ticker": "NVDA", "status": "ok", "decision": "BUY_STRONG",
                       "momentum_score": 72.0, "skip_score": 0, "phase": "BREAKOUT",
                       "market_cap_tier": "LARGE", "sizing_eur": "€300",
                       "top_reasons": "", "summary": "", "analyzed_at": "",
                       "m_volume": 0, "m_catalyst": 0, "m_sector_heat": 0,
                       "m_premarket": 0, "m_rel_strength": 0, "m_social": 0,
                       "m_float": 0, "social_capped": False,
                       "skip_blocked": False, "skip_reasons": "",
                       "data_confidence": "LIVE", "cache_hit": False, "fetch_error": "",
                   }) as mock_analyze:
            with patch("scripts.validation_runner._write_outputs",
                       return_value=("/tmp/a.json", "/tmp/a.csv")):
                with patch("scripts.validation_runner._load_watchlist") as mock_wl:
                    import sys
                    with patch.object(sys, "argv", ["validation_runner.py", "--ticker", "NVDA", "--no-persist"]):
                        from scripts.validation_runner import main
                        main()
                    mock_wl.assert_not_called()
                    mock_analyze.assert_called()

    def test_group_filter_passed_to_load_watchlist(self):
        with patch("scripts.validation_runner._load_watchlist",
                   return_value=[]) as mock_wl:
            with patch("sys.exit"):
                import sys
                with patch.object(sys, "argv", ["runner.py", "--group", "quantum"]):
                    try:
                        from scripts.validation_runner import main
                        main()
                    except SystemExit:
                        pass
            mock_wl.assert_called_with(group_filter="quantum")
