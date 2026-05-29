# Momentum Intelligence

> Detect early-stage market momentum before retail piles in.

Personal momentum intelligence tool for a retail investor. Detects early-stage market momentum through volume anomalies, catalyst quality, sector rotation, and social acceleration — before stocks become mainstream hype plays.

**Core principle:** Skip Score goes before Momentum Score. Always. Data classifies. Engine scores. Never the other way around.

---

## Project Status

| Phase | Description | Status |
|---|---|---|
| 1 | Score Engine | ✅ Complete — v1.3, 235/235 tests |
| 2 | Python Backend | ✅ Complete — v2.13.2 |
| 3 | Dashboard | 🔲 Later — validation first |
| 4 | Deployment | 🔲 Later |
| 5 | Data Expansion | 🔲 Optional |

Current version: **v2.13.2** — Paper Trading Validation Framework + Bias Fixes

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  DATA LAYER                                                 │
│  yahoo_client.py      Price, volume — yfinance + fallback   │
│  finnhub_client.py    Raw news articles — Finnhub API       │
│  catalyst_classifier  OWN / SECTOR / SYMPATHY detection     │
│  news_keywords.json   Keyword taxonomy — STRONG/MOD/WEAK    │
│  sector_intelligence  Dynamic sector heat — blended         │
│  social_client.py     Placeholder (StockTwits — fase 2.3)   │
│  assembler.py         Builds TickerInput from all sources   │
└────────────────────────────┬────────────────────────────────┘
                             │ TickerInput
┌────────────────────────────▼────────────────────────────────┐
│  SCORE ENGINE  (scoring_v1_2.py)                            │
│  Pure deterministic functions — no IO, no AI, no network    │
│  Skip Score → Momentum Score → Decision → Phase → Sizing    │
│  Decisions: BLOCKED / SKIP / WATCH / BUY_SMALL–BUY_MAX     │
└────────────────────────────┬────────────────────────────────┘
                             │ ScoringResult
┌────────────────────────────▼────────────────────────────────┐
│  STORAGE LAYER                                              │
│  snapshot_store.py    Append-only scored snapshots          │
│  signal_tracker.py    Phase transitions + catalyst events   │
│  signal_evaluator.py  Grade signals against future prices   │
│  paper_trade_store.py BUY-signal recording for validation   │
│  paper_trade_evaluator Fetch future prices, calc returns    │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  API + ALERTING                                             │
│  backend/app.py       FastAPI — 30+ endpoints               │
│  alerting/            Threshold alerts + watchlist manager  │
└────────────────────────────┬────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────┐
│  SCRIPTS (CLI tools — no backend required)                  │
│  validation_runner.py  Batch score 39 tickers, CSV + JSON   │
│  paper_trade_report.py record / evaluate / report modes     │
│  debug_yahoo.py        Yahoo Finance connectivity diagnose  │
└─────────────────────────────────────────────────────────────┘
```

---

## Catalyst Intelligence

The engine distinguishes three momentum types — a signal source that determines the catalyst score ceiling:

| Source | Description | Max catalyst score |
|---|---|---|
| `OWN` | Ticker has its own company-specific catalyst | Full (20 pts) |
| `SECTOR` | Sector-wide momentum, no ticker-specific news | Capped at MODERATE (12 pts) |
| `SYMPATHY` | Moving because another ticker in the sector moved | Capped at WEAK (4 pts) |

Recency and source tier are applied as multipliers: a Reuters article from 1 hour ago scores higher than a PR Newswire article from 40 hours ago, even with identical keywords.

---

## Validation Layer

Before any calibration or threshold changes, BUY-signal predictive value must be proven with real market data.

```bash
# Daily — before market (09:00–09:15 CET)
python scripts/validation_runner.py --ticker UMAC RCAT ASTS TEM IONQ QBTS RKLB KTOS ACHR CRDO CRWV LUNR SOUN RGTI QUBT PLTR JOBY APP

# Daily — after market (22:00 CET)
python scripts/paper_trade_report.py evaluate
python scripts/paper_trade_report.py report
```

See [docs/VALIDATION_CHECKLIST.md](docs/VALIDATION_CHECKLIST.md) for the full plan: daily/weekly workflow, metrics to track, statistical power requirements, and when calibration is justified.

**Calibration is not justified until:** ≥50 complete trades, win rate 5d ≥65%, results consistent over ≥3 weeks.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env
# Add: FINNHUB_API_KEY=your_key  (free tier: finnhub.io)

# Run all tests (no network required)
pytest tests/ -q

# Start local backend
uvicorn backend.app:app --reload --port 8000

# Diagnose Yahoo Finance connectivity
python scripts/debug_yahoo.py

# Run batch validation
python scripts/validation_runner.py --group ai_infra --no-persist

# Paper trading report
python scripts/paper_trade_report.py report
```

---

## Tests

```bash
pytest tests/ -v                                    # All 783 tests
pytest tests/test_scoring.py -v                    # Score engine only
pytest tests/test_catalyst_classifier.py -v        # Catalyst intelligence
pytest tests/test_paper_trading.py -v              # Paper trading framework
pytest tests/ -x --tb=short                        # Stop at first failure
```

| Test file | Tests | What it covers |
|---|---|---|
| `test_scoring.py` | 70 | Score engine — all formulas, skip rules, decisions |
| `test_backend.py` | 36 | FastAPI endpoints — mocked, no network |
| `test_data_stability.py` | 55 | Yahoo client, assembler, data contracts |
| `test_cache.py` | 74 | TTL, confidence, market session routing |
| `test_signals.py` | 57 | Market session, news client, sector intelligence |
| `test_history.py` | 63 | Snapshot storage, signal decay, timeline |
| `test_replay.py` | 60 | Replay engine, diffs, export |
| `test_evaluation.py` | 64 | Signal grading, evaluation store |
| `test_dev_experience.py` | 51 | OpenAPI, error formats, smoke test |
| `test_alerting.py` | 69 | Alert engine, watchlist manager, cooldowns |
| `test_yahoo_client.py` | 19 | Yahoo fetch, history fallback, error logging |
| `test_catalyst_classifier.py` | 85 | Keywords, recency, source tier, OWN/SECTOR/SYMPATHY |
| `test_validation_runner.py` | 36 | Batch runner, CSV/JSON export, watchlist loading |
| `test_paper_trading.py` | 44 | Paper trade store, evaluator, deduplication, statistics |
| **Total** | **783** | No network required |

---

## Repository Structure

```
momentum-intelligence/
├── README.md  ·  CHANGELOG.md  ·  DECISIONS.md  ·  ROADMAP.md
├── CLAUDE_BOOTSTRAP.md         Session context for Claude
├── requirements.txt  ·  conftest.py  ·  .env.example
│
├── backend/
│   └── app.py                  FastAPI — 30+ typed endpoints
│
├── cache/
│   └── market_cache.py         TTL cache — LIVE/DELAYED/STALE/PARTIAL
│
├── config/
│   ├── sectors.json            Sector heat, leaders, sympathy lists
│   └── news_keywords.json      Catalyst keyword taxonomy
│
├── data/
│   ├── assembler.py            Builds TickerInput from all sources
│   ├── yahoo_client.py         Price/volume — yfinance + history fallback
│   ├── finnhub_client.py       Raw news fetch — Finnhub API
│   ├── catalyst_classifier.py  OWN/SECTOR/SYMPATHY + recency scoring
│   ├── news_client.py          Legacy news client (backward compat)
│   ├── sector_intelligence.py  Dynamic sector heat blending
│   ├── social_client.py        Placeholder — StockTwits (fase 2.3)
│   └── market_session.py       PREMARKET/REGULAR/AFTERHOURS/CLOSED
│
├── schemas/
│   ├── ticker_snapshot.py      TickerSnapshot + DataConfidence
│   ├── scoring_response.py     ScoringResponse + DataQuality
│   ├── sector_state.py         SectorState
│   └── api_error.py            ApiError + ErrorCode
│
├── scoring/
│   └── scoring_v1_2.py         Score engine — pure functions, no IO
│
├── storage/
│   ├── snapshot_store.py       Append-only scored snapshots (.jsonl)
│   ├── signal_tracker.py       Phase transitions + catalyst events
│   ├── signal_evaluator.py     Grade signals against future prices
│   ├── signal_decay.py         Momentum decay over time
│   ├── evaluation_store.py     Signal outcome persistence
│   ├── paper_trade_store.py    BUY-signal recording + deduplication
│   ├── paper_trade_evaluator.py Fetch future prices, calculate returns
│   ├── snapshot_diff.py        Snapshot comparison
│   ├── timeline.py             Chronological signal history
│   ├── history_replay.py       Replay stored signals
│   ├── replay_engine.py        Replay with diff output
│   └── sector_history.py       Sector-level history
│
├── alerting/
│   ├── alert_engine.py         Threshold-based alert generation
│   ├── alert_store.py          Alert persistence
│   ├── cooldown_manager.py     Per-ticker alert cooldowns
│   └── watchlist_manager.py    Watchlist CRUD
│
├── scripts/
│   ├── validation_runner.py    Batch score tickers — CSV + JSON output
│   ├── paper_trade_report.py   record / evaluate / report
│   ├── debug_yahoo.py          Yahoo Finance connectivity diagnose
│   ├── smoke_test.py           API endpoint smoke test
│   └── export_snapshots.py     Export storage to CSV
│
├── research/
│   ├── validation_watchlist.json  39 curated tickers, 9 groups
│   ├── observation_store.py    Research observation storage
│   └── evaluation_report.py    Evaluation reporting
│
├── docs/
│   ├── VALIDATION_CHECKLIST.md Daily/weekly validation workflow ← start here
│   ├── OPERATING_MANUAL.md     Full system operating guide
│   ├── ARCHITECTURE.md         Detailed architecture decisions
│   ├── SCORE_ENGINE.md         Scoring formula documentation
│   ├── API.md                  API endpoint reference
│   ├── MOMENTUM_FRAMEWORK.md   Investment thesis + regime model
│   ├── TEMPORAL_ARCHITECTURE.md Time-based data design
│   ├── ANTI_GOALS.md           What this system never does
│   └── KNOWN_FAILURE_MODES.md  Documented failure patterns
│
├── watchlists/
│   ├── core.json               Core AI infrastructure names
│   ├── momentum.json           Current momentum universe
│   └── sector_rotation.json    Sector rotation watchlist
│
└── tests/                      783 tests — no network required
    └── test_*.py               (14 test files)
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
3. Run all 783 tests before committing — zero failures required
4. Never add features during bug-fix sessions
5. No scoring changes without evidence from paper trading validation

---

*Igor × Claude — 2026 — Geen formeel beleggingsadvies (Wft)*
