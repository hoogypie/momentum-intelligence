# Momentum Intelligence Dashboard

> Detect tomorrow's movers before retail piles in.

Personal momentum intelligence tool for a retail investor. Detects early-stage market momentum through volume anomalies, sector rotation, catalyst quality, and social acceleration — before stocks become mainstream hype plays.

---

## What This Is

A three-layer signal detection system:

```
Layer 1 — Data          Raw market data (price, volume, news, social)
Layer 2 — Score Engine  Algorithmic scoring — no AI, pure formulas
Layer 3 — AI Narrative  Claude explains the score. Never calculates it.
```

**Core principle:** Skip Score goes before Momentum Score. Always.

---

## Project Status

| Phase | Description | Status |
|---|---|---|
| 1 | Score Engine | ✅ Complete — v1.3, 235/235 tests passing |
| 2 | Python Backend | ✅ Complete — v2.10, Yahoo Fetch Compatibility Fix |
| 3 | Dashboard | 🔲 Later |
| 4 | Deployment | 🔲 Later |
| 5 | Data Expansion | 🔲 Optional |

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run score engine tests (no network required)
pytest tests/ -v

# Start local backend
uvicorn backend.app:app --reload --port 8000

# Test endpoints
curl http://localhost:8000/health
curl http://localhost:8000/analyze/NVDA
curl http://localhost:8000/analyze/NVDA?refresh=true
curl "http://localhost:8000/analyze?tickers=IONQ,QBTS,RGTI"
curl http://localhost:8000/sector/quantum
curl http://localhost:8000/analyze/UMAC
```

---

## How to Run Tests

```bash
# Install pytest (one-time)
pip install pytest

# Run all 618 tests
pytest tests/ -v

# Run a specific class
pytest tests/test_scoring.py::TestHardBlocked -v
pytest tests/test_scoring.py::TestRegression -v
pytest tests/test_yahoo_client.py -v

# Stop at first failure
pytest tests/ -x --tb=short
```

| Test Class | What it covers |
|---|---|
| `TestHardBlocked` | SEC / CFD / class action vetoes |
| `TestSkipScore` | Soft skip penalties |
| `TestCombinationRule` | catalyst=NONE + momentum<50 → SKIP |
| `TestMomentumComponents` | Each scoring formula in isolation |
| `TestSocialQualityCap` | Social capped per catalyst quality |
| `TestFloatScore` | Float tiers + None fallback |
| `TestPhaseDetection` | ACCUMULATION → EXHAUSTION |
| `TestMarketCapTier` | Tier assignment + sizing caps |
| `TestRegression` | All 11 mock cases with momentum/skip ranges |
| `TestFastInfoSuccess` | Normal fast_info path, no fallback |
| `TestHistoryFallback` | fast_info fails → history fallback used |
| `TestBothPathsFail` | Both paths fail → RuntimeError |
| `TestGetSnapshotFallback` | get_snapshot() never raises |
| `TestFetchFromHistoryHelper` | _fetch_from_history() edge cases |
| `TestFetchErrorLogging` | Exception type logged (caplog) |



```
momentum-intelligence/
├── README.md
├── MASTER_CONTEXT.md
├── ROADMAP.md / DECISIONS.md / CHANGELOG.md
├── requirements.txt  /  conftest.py  /  .gitignore
├── backend/
│   └── app.py                  FastAPI — typed Pydantic responses
├── cache/
│   └── market_cache.py         Cache prep (DISABLED — v2.2)
├── data/
│   ├── yahoo_client.py         Retry + backoff + DataConfidence + history fallback
│   ├── news_client.py          Finnhub placeholder
│   └── assembler.py            TickerInput builder + missing field handling
├── schemas/
│   ├── ticker_snapshot.py      TickerSnapshot + DataConfidence
│   ├── scoring_response.py     ScoringResponse + DataQuality
│   ├── sector_state.py         SectorState
│   └── api_error.py            ApiError + ErrorCode
├── scoring/
│   └── scoring_v1_2.py         Score engine (pure functions, no AI)
├── config/
│   └── sectors.json            Sector heat — update weekly
├── scripts/
│   └── debug_yahoo.py          Yahoo Finance diagnose tool
└── tests/
    ├── test_scoring.py          70 engine tests
    ├── test_backend.py          36 API tests (mocked)
    ├── test_data_stability.py   55 stability tests
    └── test_yahoo_client.py     19 yahoo client + fallback tests
```

---

## Daily Validation Workflow

See [docs/VALIDATION_CHECKLIST.md](docs/VALIDATION_CHECKLIST.md) for the full validation plan, daily/weekly workflow, metrics to track, and when calibration is justified.

---

## Team

| Role | Who | Responsibility |
|---|---|---|
| Product Owner / Strategist | Igor | Direction, trading logic, risk model, priorities |
| Reviewer | ChatGPT | Risk analysis, "institutional or hype?" challenge |
| Builder | Claude | Code, architecture, implementation, tests |

---

## Rules for Every Change

1. Update `CHANGELOG.md` first
2. If architecture changes: add entry to `DECISIONS.md`
3. Run all tests before committing
4. Never add features during bug-fix sessions

---

*Igor × Claude — 2026 — Geen formeel beleggingsadvies (Wft)*
