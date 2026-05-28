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
| 1 | Score Engine | ✅ Complete — v1.1, 8/8 tests passing |
| 2 | Python Backend | 🔲 Next |
| 3 | Dashboard | 🔲 Later |
| 4 | Deployment | 🔲 Later |
| 5 | Data Expansion | 🔲 Optional |

---

## Quick Start

```bash
# Phase 1 — run score engine tests (no dependencies)
python3 scoring/scoring_v1_1.py

# Phase 2 — coming soon
pip install -r requirements.txt
uvicorn main:app --reload
```

---

## Repository Structure

```
momentum-intelligence/
├── README.md                   This file
├── MASTER_CONTEXT.md           Source of truth for all Claude sessions
├── ROADMAP.md                  Phase plan with task checklists
├── DECISIONS.md                Architecture decisions with rationale
├── CHANGELOG.md                All changes, newest first
├── requirements.txt            Python dependencies
├── .gitignore
├── docs/
│   ├── MOMENTUM_FRAMEWORK.md   Trading framework, sector map, sympathy plays
│   └── SCORE_ENGINE.md         Technical spec: formulas, thresholds, test cases
├── scoring/
│   ├── __init__.py
│   └── scoring_v1_1.py         Current engine — pure functions, no AI
├── config/
│   └── sectors.json            Sector heat config — update weekly
└── tests/
    └── test_scoring.py         (Phase 2)
```

---

## Team

| Role | Who | Responsibility |
|---|---|---|
| Product Owner / Strategist | Igor | Direction, trading logic, risk model, priorities |
| Builder | Claude | Code, architecture, implementation, tests |

---

## Rules for Every Change

1. Update `CHANGELOG.md` first
2. If architecture changes: add entry to `DECISIONS.md`
3. Run all tests before committing
4. Never add features during bug-fix sessions

---

*Igor × Claude — 2026 — Geen formeel beleggingsadvies (Wft)*
