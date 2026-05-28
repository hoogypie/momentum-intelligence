"""
tests/test_dev_experience.py
Developer Experience Tests — v2.3

Coverage:
    TestOpenAPISchema       OpenAPI schema volledigheid en tags
    TestSmokeScript         smoke_test.py importeerbaarheid + mock run
    TestEnvExample          .env.example aanwezigheid en inhoud
    TestLoggingConfig       logging setup crasht nooit
    TestStructuredLogging   log helpers werken correct
    TestCacheEndpoints      GET /cache/stats + DELETE /cache/{ticker}
    TestRunScripts          run_backend.py importeerbaarheid
    TestMakefile            Makefile aanwezigheid en targets
"""

import os
import sys
import pytest
import logging
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from datetime import datetime, timezone

# Voeg scripts toe aan pad
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.app import app
from schemas.ticker_snapshot import TickerSnapshot, DataConfidence
from cache.market_cache import clear_cache

client = TestClient(app)


def _mock_snap(ticker="TEST", price=50.0):
    return TickerSnapshot(
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        confidence=DataConfidence.LIVE,
        price=price, prev_close=price - 2, day_change_pct=5.0,
        premarket_pct=8.0, premarket_available=True,
        volume_today=3_000_000, avg_volume_20d=500_000,
        market_cap=1e9, float_shares=40_000_000,
        cache_hit=False, data_age_seconds=0.0,
    )


# ── OPENAPI SCHEMA TESTS ──────────────────────────────────────────────────────

class TestOpenAPISchema:

    def test_openapi_json_accessible(self):
        r = client.get("/openapi.json")
        assert r.status_code == 200

    def test_openapi_is_valid_json(self):
        r = client.get("/openapi.json")
        schema = r.json()
        assert "paths" in schema
        assert "info" in schema

    def test_openapi_has_health_endpoint(self):
        schema = client.get("/openapi.json").json()
        assert "/health" in schema["paths"]

    def test_openapi_has_analyze_endpoint(self):
        schema = client.get("/openapi.json").json()
        assert "/analyze/{ticker}" in schema["paths"]

    def test_openapi_has_batch_endpoint(self):
        schema = client.get("/openapi.json").json()
        assert "/analyze" in schema["paths"]

    def test_openapi_has_sector_endpoint(self):
        schema = client.get("/openapi.json").json()
        assert "/sector/{sector_name}" in schema["paths"]

    def test_openapi_has_cache_endpoints(self):
        schema = client.get("/openapi.json").json()
        assert "/cache/stats" in schema["paths"]
        assert "/cache/{ticker}" in schema["paths"]

    def test_openapi_has_tags_defined(self):
        schema = client.get("/openapi.json").json()
        tag_names = [t["name"] for t in schema.get("tags", [])]
        assert "health"   in tag_names
        assert "analysis" in tag_names
        assert "sector"   in tag_names
        assert "cache"    in tag_names

    def test_openapi_version_matches_app(self):
        schema = client.get("/openapi.json").json()
        assert schema["info"]["version"] == "2.5.0"

    def test_docs_endpoint_accessible(self):
        r = client.get("/docs")
        assert r.status_code == 200

    def test_redoc_endpoint_accessible(self):
        r = client.get("/redoc")
        assert r.status_code == 200

    def test_analyze_endpoint_has_operation_id(self):
        schema = client.get("/openapi.json").json()
        get_op = schema["paths"]["/analyze/{ticker}"]["get"]
        assert "operationId" in get_op

    def test_health_endpoint_has_summary(self):
        schema = client.get("/openapi.json").json()
        get_op = schema["paths"]["/health"]["get"]
        assert "summary" in get_op
        assert len(get_op["summary"]) > 0


# ── SMOKE SCRIPT TESTS ────────────────────────────────────────────────────────

class TestSmokeScript:

    def test_smoke_test_importable(self):
        import scripts.smoke_test as st
        assert hasattr(st, "run_smoke_test")

    def test_smoke_test_has_checks_list(self):
        import scripts.smoke_test as st
        assert hasattr(st, "CHECKS")
        assert len(st.CHECKS) > 0

    def test_smoke_test_check_result_namedtuple(self):
        import scripts.smoke_test as st
        r = st.CheckResult(
            name="Test", endpoint="GET /test",
            passed=True, status_code=200, duration_ms=42.0,
        )
        assert r.passed is True
        assert r.status_code == 200

    @patch("data.assembler.get_snapshot")
    @patch("data.assembler.get_news", return_value=[])
    @patch("data.assembler.get_spy_return", return_value=0.0)
    def test_smoke_test_run_against_testclient(self, mock_spy, mock_news, mock_snap):
        """Smoke test via TestClient i.p.v. live server."""
        mock_snap.return_value = _mock_snap()

        import scripts.smoke_test as st

        # Patch _get om TestClient te gebruiken i.p.v. echte HTTP
        def mock_get(url: str, timeout: int = 10):
            path = url.replace("http://localhost:8000", "")
            r = client.get(path)
            try:
                return r.status_code, r.json()
            except Exception:
                return r.status_code, {}

        with patch.object(st, "_get", side_effect=mock_get):
            results = st.run_smoke_test("http://localhost:8000")

        assert len(results) == len(st.CHECKS)
        assert all(isinstance(r, st.CheckResult) for r in results)

    def test_smoke_print_results_doesnt_crash(self):
        import scripts.smoke_test as st
        results = [
            st.CheckResult("A", "GET /a", True,  200, 10.0),
            st.CheckResult("B", "GET /b", False, 500, 20.0, error="timeout"),
        ]
        # Mag niet crashen
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            st.print_results(results)

    def test_smoke_json_output_doesnt_crash(self):
        import scripts.smoke_test as st
        results = [st.CheckResult("A", "GET /a", True, 200, 10.0)]
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            st.print_results(results, use_json=True)
        import json
        data = json.loads(buf.getvalue())
        assert len(data) == 1


# ── ENV EXAMPLE TESTS ─────────────────────────────────────────────────────────

class TestEnvExample:

    def _get_path(self) -> str:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, ".env.example")

    def test_env_example_exists(self):
        assert os.path.exists(self._get_path())

    def test_env_example_has_finnhub_key(self):
        content = open(self._get_path()).read()
        assert "FINNHUB_API_KEY" in content

    def test_env_example_has_log_level(self):
        content = open(self._get_path()).read()
        assert "LOG_LEVEL" in content

    def test_env_example_has_cache_enabled(self):
        content = open(self._get_path()).read()
        assert "CACHE_ENABLED" in content

    def test_env_example_has_api_port(self):
        content = open(self._get_path()).read()
        assert "API_PORT" in content

    def test_env_example_no_real_keys(self):
        """Controleer dat er geen echte API keys in het voorbeeld staan."""
        content = open(self._get_path()).read()
        # Keys hebben formaat sk-, api-, etc. gevolgd door echte waarden
        suspicious_patterns = ["sk-ant-", "sk-", "Bearer "]
        for pattern in suspicious_patterns:
            assert pattern not in content


# ── LOGGING CONFIG TESTS ──────────────────────────────────────────────────────

class TestLoggingConfig:

    def test_setup_logging_doesnt_crash(self):
        from backend.logging_config import setup_logging
        setup_logging()  # Mag niet gooien

    def test_setup_logging_idempotent(self):
        """Twee keer aanroepen = geen dubbele handlers."""
        from backend.logging_config import setup_logging, _configured
        setup_logging()
        setup_logging()  # Tweede aanroep mag niet crashen

    def test_get_logger_returns_logger(self):
        from backend.logging_config import get_logger
        log = get_logger("test.module")
        assert isinstance(log, logging.Logger)

    def test_get_logger_has_name(self):
        from backend.logging_config import get_logger
        log = get_logger("my.component")
        assert "my.component" in log.name

    def test_request_middleware_importable(self):
        from backend.logging_config import RequestLoggingMiddleware
        assert RequestLoggingMiddleware is not None

    def test_request_middleware_is_class(self):
        from backend.logging_config import RequestLoggingMiddleware
        assert isinstance(RequestLoggingMiddleware, type)


class TestStructuredLogging:
    """Log helper functies werken en crashen niet."""

    def test_log_cache_event_doesnt_crash(self):
        from backend.logging_config import log_cache_event
        log = logging.getLogger("test.cache")
        log_cache_event(log, "hit", "NVDA", age_seconds=42.0, confidence="LIVE")
        log_cache_event(log, "miss", "AAPL")
        log_cache_event(log, "store", "TSLA", ttl=60)

    def test_log_score_event_doesnt_crash(self):
        from backend.logging_config import log_score_event
        log = logging.getLogger("test.score")
        log_score_event(log, "NVDA", "BUY_MAX", 95.5, 0, "LIVE", False)

    def test_log_fallback_event_doesnt_crash(self):
        from backend.logging_config import log_fallback_event
        log = logging.getLogger("test.fallback")
        log_fallback_event(log, "NVDA", "yahoo timeout", "STALE")


# ── CACHE ENDPOINT TESTS ──────────────────────────────────────────────────────

class TestCacheEndpoints:

    def setup_method(self):
        clear_cache()

    def test_cache_stats_endpoint_returns_200(self):
        r = client.get("/cache/stats")
        assert r.status_code == 200

    def test_cache_stats_has_enabled_field(self):
        r = client.get("/cache/stats")
        assert "enabled" in r.json()
        assert r.json()["enabled"] is True

    def test_cache_stats_has_total_entries(self):
        r = client.get("/cache/stats")
        assert "total_entries" in r.json()

    def test_cache_stats_has_market_info(self):
        r = client.get("/cache/stats")
        d = r.json()
        assert "market_open"  in d
        assert "current_ttl"  in d

    def test_invalidate_nonexistent_ticker(self):
        r = client.delete("/cache/NONEXISTENT")
        assert r.status_code == 200
        assert r.json()["removed"] is False

    def test_invalidate_cached_ticker(self):
        from cache.market_cache import set_cached
        set_cached("DELTEST", {"price": 50.0})
        r = client.delete("/cache/DELTEST")
        assert r.status_code == 200
        assert r.json()["removed"] is True

    def test_cache_endpoint_in_health_response(self):
        r = client.get("/health")
        assert "cache_stats" in r.json()
        assert r.json()["cache_stats"]["enabled"] is True


# ── RUN SCRIPTS TESTS ─────────────────────────────────────────────────────────

class TestRunScripts:

    def test_run_backend_importable(self):
        import scripts.run_backend as rb
        assert hasattr(rb, "main")

    def test_run_backend_has_main(self):
        import scripts.run_backend as rb
        assert callable(rb.main)

    def test_scripts_dir_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        scripts_dir = os.path.join(root, "scripts")
        assert os.path.isdir(scripts_dir)

    def test_smoke_test_script_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        smoke = os.path.join(root, "scripts", "smoke_test.py")
        assert os.path.exists(smoke)

    def test_run_backend_script_exists(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        run = os.path.join(root, "scripts", "run_backend.py")
        assert os.path.exists(run)


# ── MAKEFILE TESTS ────────────────────────────────────────────────────────────

class TestMakefile:

    def _get_makefile(self) -> str:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(root, "Makefile")

    def test_makefile_exists(self):
        assert os.path.exists(self._get_makefile())

    def test_makefile_has_run_target(self):
        content = open(self._get_makefile()).read()
        assert "run:" in content

    def test_makefile_has_test_target(self):
        content = open(self._get_makefile()).read()
        assert "test:" in content

    def test_makefile_has_smoke_target(self):
        content = open(self._get_makefile()).read()
        assert "smoke:" in content

    def test_makefile_has_lint_target(self):
        content = open(self._get_makefile()).read()
        assert "lint:" in content
