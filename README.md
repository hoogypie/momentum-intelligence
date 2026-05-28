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
| 1 | Score Engine | ✅ Complete — v1.3, 105/105 tests passing |
| 2 | Python Backend | ✅ Complete — v2.0, FastAPI + Yahoo Finance |
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
├── ROADMAP.md
├── DECISIONS.md
├── CHANGELOG.md
├── requirements.txt
├── conftest.py                 pytest path config
├── .gitignore
├── backend/
│   ├── __init__.py
│   └── app.py                  FastAPI — /health + /analyze/{ticker}
├── data/
│   ├── __init__.py
│   ├── yahoo_client.py         Yahoo Finance: prijs, volume, market cap
│   ├── news_client.py          Finnhub placeholder (fase 2.1)
│   └── assembler.py            Bouwt TickerInput van alle bronnen
├── docs/
│   ├── ARCHITECTURE.md
│   ├── ANTI_GOALS.md
│   ├── KNOWN_FAILURE_MODES.md
│   ├── MOMENTUM_FRAMEWORK.md
│   └── SCORE_ENGINE.md
├── scoring/
│   ├── __init__.py
│   ├── scoring_v1_1.py         (archief)
│   └── scoring_v1_2.py         Huidige engine
├── config/
│   └── sectors.json
└── tests/
    ├── __init__.py
    ├── test_scoring.py         70 engine tests
    └── test_backend.py         35 backend + assembler tests
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
