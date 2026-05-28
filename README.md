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
| 1 | Score Engine | ✅ Complete — v1.3, 159/159 tests passing |
| 2 | Python Backend | ✅ Complete — v2.1, Data Stability Layer |
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
curl http://localhost:8000/analyze/UMAC
```

---

## How to Run Tests

```bash
# Install pytest (one-time)
pip install pytest

# Run all 70 tests
pytest tests/ -v

# Run a specific class
pytest tests/test_scoring.py::TestHardBlocked -v
pytest tests/test_scoring.py::TestRegression -v

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
│   ├── yahoo_client.py         Retry + backoff + DataConfidence
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
└── tests/
    ├── test_scoring.py          70 engine tests
    ├── test_backend.py          36 API tests (mocked)
    └── test_data_stability.py   53 stability tests
```

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
