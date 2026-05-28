#!/usr/bin/env python3
"""
scripts/smoke_test.py
CLI smoke test — v2.3

Test alle API endpoints tegen een draaiende server.
Geeft exit code 0 bij succes, 1 bij één of meer fouten.

Gebruik:
    python3 scripts/smoke_test.py                          # localhost:8000
    python3 scripts/smoke_test.py http://localhost:8000    # expliciete URL
    python3 scripts/smoke_test.py --json                   # JSON output

In CI:
    pytest tests/test_dev_experience.py::TestSmokeScript   # gemockt
"""

import sys
import json
import time
import argparse
from typing import NamedTuple

try:
    import httpx
    _CLIENT = "httpx"
except ImportError:
    import urllib.request as _urllib
    _CLIENT = "urllib"


# ── CHECK DEFINITIE ───────────────────────────────────────────────────────────

class CheckResult(NamedTuple):
    name:        str
    endpoint:    str
    passed:      bool
    status_code: int
    duration_ms: float
    error:       str = ""
    note:        str = ""


CHECKS = [
    ("Health check",         "GET", "/health",                     {}),
    ("Analyze single",       "GET", "/analyze/NVDA",               {}),
    ("Analyze with refresh", "GET", "/analyze/NVDA?refresh=true",  {}),
    ("Analyze batch",        "GET", "/analyze?tickers=IONQ,QBTS",  {}),
    ("Sector snapshot",      "GET", "/sector/quantum",             {}),
    ("Cache stats",          "GET", "/cache/stats",                {}),
    ("OpenAPI schema",       "GET", "/openapi.json",               {}),
]


# ── HTTP CLIENT WRAPPER ───────────────────────────────────────────────────────

def _get(url: str, timeout: int = 10) -> tuple[int, dict]:
    """Simpele GET, werkt met httpx of urllib."""
    start = time.monotonic()
    if _CLIENT == "httpx":
        r = httpx.get(url, timeout=timeout, follow_redirects=True)
        return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    else:
        try:
            req = _urllib.Request(url)
            with _urllib.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
                try:
                    return resp.status, json.loads(body)
                except json.JSONDecodeError:
                    return resp.status, {}
        except Exception as exc:
            raise exc


# ── RUNNER ────────────────────────────────────────────────────────────────────

def run_smoke_test(base_url: str = "http://localhost:8000") -> list[CheckResult]:
    """
    Voert alle smoke checks uit.
    Kan worden gemockt in unit tests.
    Returns: lijst van CheckResult
    """
    base_url = base_url.rstrip("/")
    results  = []

    for name, method, path, _ in CHECKS:
        url   = f"{base_url}{path}"
        start = time.monotonic()
        try:
            status, body = _get(url)
            duration     = (time.monotonic() - start) * 1000
            passed       = 200 <= status < 300

            note = ""
            if path == "/health" and passed:
                note = f"v{body.get('version', '?')}"
            elif path == "/analyze/NVDA" and passed:
                note = f"decision={body.get('decision', '?')}"
            elif path.startswith("/analyze?") and passed:
                note = f"scored={body.get('tickers_scored', '?')}"

            results.append(CheckResult(
                name=name, endpoint=f"{method} {path}",
                passed=passed, status_code=status,
                duration_ms=round(duration, 1), note=note,
            ))

        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            results.append(CheckResult(
                name=name, endpoint=f"{method} {path}",
                passed=False, status_code=0,
                duration_ms=round(duration, 1),
                error=str(exc),
            ))

    return results


# ── OUTPUT ────────────────────────────────────────────────────────────────────

_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"


def print_results(results: list[CheckResult], use_json: bool = False) -> None:
    if use_json:
        print(json.dumps([r._asdict() for r in results], indent=2))
        return

    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    print(f"\n{_BOLD}Momentum Intelligence — Smoke Test{_RESET}")
    print(f"{'─' * 60}")

    for r in results:
        icon    = f"{_GREEN}✓{_RESET}" if r.passed else f"{_RED}✗{_RESET}"
        status  = f"[{r.status_code}]" if r.status_code else "[ERR]"
        detail  = r.note if r.note else (r.error[:40] if r.error else "")
        detail  = f"  {_YELLOW}{detail}{_RESET}" if detail else ""
        print(f"  {icon}  {r.name:<28} {status:6}  {r.duration_ms:7.1f}ms{detail}")

    print(f"{'─' * 60}")
    color = _GREEN if failed == 0 else _RED
    print(f"  {color}{_BOLD}{passed}/{total} checks geslaagd{_RESET}\n")


# ── CLI ENTRY POINT ───────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke test voor Momentum Intelligence API"
    )
    parser.add_argument(
        "base_url", nargs="?", default="http://localhost:8000",
        help="API base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="use_json",
        help="Output als JSON",
    )
    args = parser.parse_args()

    results = run_smoke_test(args.base_url)
    print_results(results, use_json=args.use_json)

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
