# CHANGELOG
> Alle wijzigingen, nieuwste bovenaan.
> Regel: elke wijziging hier vastleggen vóór implementatie.

---

## [v2.7] — 28 mei 2026 — Signal Evaluation Layer

**Context:** v2.6 maakte signalen inzichtelijk via replay. v2.7 evalueert
ze achteraf: was een BUY_MODERATE signaal daadwerkelijk gevolgd door
prijsstijging? Geen ML, geen predictie — puur historische vergelijking.

**Nieuwe storage modules:**
- storage/evaluation_store.py  — SignalOutcome persistence per ticker
  (storage/data/evaluations/). Idempotent via version_id.
- storage/signal_evaluator.py  — Core grade logic, future price lookup,
  statistics breakdown, global stats, top signals

**Grade logica:**
- BUY signalen: SUCCESS ≥+3% / FAILED ≤-3% / NEUTRAL tussenin
- SKIP/BLOCKED: SUCCESS als prijs daadwerkelijk daalde ≤-2%
- WATCH: altijd NEUTRAL (geen richting)
- Tijdshorizon prioriteit: 1d → 4h → 1h → PENDING
- Tolerantievensters: 1h=[45-75min], 4h=[3-5h], 1d=[20-28h], 3d=[60-84h]

**research/evaluation_report.py:**
- Markdown rapporten per ticker + globaal
- JSON export met statistieken + outcomes
- ticker_evaluation_report(), global_summary_report()

**Nieuwe API endpoints:**
- POST /evaluation/run/{ticker}    — Trigger evaluatie (on-demand)
- GET  /evaluation/ticker/{ticker} — Resultaten + statistieken
- GET  /evaluation/session/{date}  — Alle evaluaties van één dag
- GET  /evaluation/top-signals     — Beste/slechtste signalen ooit
- GET  /evaluation/stats           — Globale statistieken

**Voorbeeld output:**
- IONQ BREAKOUT BUY_MODERATE: SUCCESS (return_1d=+6.1%, basis=1d)
- IONQ EXPANSION BUY_STRONG:  FAILED  (return_1d=-4.5%, basis=1d)
- NEUTRAL fase: success_rate 0% vs BREAKOUT fase 33%

**Tests:** tests/test_evaluation.py — 64 tests
**Totaal:** 530/530 ✅

**Evaluatie beïnvloedt scoring nooit direct.**

---
---
---
---
---
---
---

## [v2.0] — 28 mei 2026 — Lokale Backend + Data Ingestion

**Context:** Score engine v1.3 had 105 tests en een solide fundering.
v2.0 voegt de eerste echte data-laag toe: Yahoo Finance prijsdata +
een FastAPI backend die ScoringResult terugstuurt.

**Toegevoegd:**
- `backend/app.py` — FastAPI, GET /health + GET /analyze/{ticker}
- `backend/__init__.py`
- `data/yahoo_client.py` — prijs, volume, market cap, float (via yfinance)
- `data/news_client.py` — placeholder (fase 2.1: Finnhub)
- `data/assembler.py` — bouwt TickerInput, classify_catalyst(), RS berekening
- `data/__init__.py`
- `tests/test_backend.py` — 35 backend + assembler tests (alle gemockt)
- `requirements.txt` — yfinance, fastapi, uvicorn toegevoegd

**Bekende beperkingen v2.0 (gedocumenteerd in response):**
- `catalyst_type` altijd NONE (news placeholder)
- `social_acceleration` altijd 0 (geen StockTwits key)
- `has_sec_investigation` altijd False (handmatige check)
- `float_shares` via `shares_outstanding` (benadering)

**Test resultaten:** 105/105 ✅ (70 engine + 35 backend, geen netwerk vereist)

**Geen nieuwe features in score engine.**

---

## [v1.3] — 28 mei 2026 — Testing Infrastructure

**Context:** Score engine v1.2 werkte correct maar had geen formele regressiebeveiliging.
Live data toevoegen zonder test-suite = geen vangnet bij regressies.

**Toegevoegd:**
- `tests/test_scoring.py` — 70 pytest tests, 9 klassen
- `conftest.py` — root-level pytest path configuratie
- `requirements.txt` — pytest==9.0.3 toegevoegd

**Test klassen:**
- `TestHardBlocked` — SEC/CFD/class action vetoes (7 tests)
- `TestSkipScore` — soft skip penalties (11 tests)
- `TestCombinationRule` — catalyst=NONE + momentum<50 (3 tests)
- `TestMomentumComponents` — elke formule geïsoleerd (12 tests)
- `TestSocialQualityCap` — social capped per catalyst (6 tests)
- `TestFloatScore` — float tiers + None fallback (6 tests)
- `TestPhaseDetection` — alle fases incl. edge cases (6 tests)
- `TestMarketCapTier` — tier + sizing caps (6 tests)
- `TestDecisionThresholds` — grenswaarden BUY-niveaus (2 tests)
- `TestRegression` — 11 mock cases + samengestelde run (12 tests)

**README:**
- Sectie "How to run tests" toegevoegd
- Project status: v1.3, 70/70 tests passing

**Test resultaten:** 70/70 ✅ in 0.11s

**Geen nieuwe features toegevoegd.**

---

## [v1.2.1] — 28 mei 2026 — README Cleanup

**Context:** README verwees nog naar v1.1 en scoring_v1_1.py na de v1.2 release.

**Gewijzigd:**
- `README.md` — Project Status: v1.2, 11/11 tests passing
- `README.md` — Quick Start: scoring_v1_1.py → scoring_v1_2.py
- `README.md` — Repository Structure: nieuwe docs toegevoegd (ARCHITECTURE, ANTI_GOALS, KNOWN_FAILURE_MODES)
- `README.md` — Team: ChatGPT reviewer rol toegevoegd
- `CHANGELOG.md` — dit item

**Geen nieuwe features toegevoegd.**

---

## [v1.2] — 28 mei 2026 — Engine Hardening

**Context:** v1.1 had correcte beslissingslogica maar miste float amplificatie,
market cap context, phase detectie en sociale kwaliteitsbeveiliging.

**Nieuwe engine features:**

**1. Float Score (max 8 pts)**
Lage float = hogere momentum amplificatie per koopdruk.
Schaal: <5M=8, <15M=6.5, <50M=4.5, <200M=2, ≥200M=0, None=4 (neutraal)

**2. Market Cap Tier**
MICRO(<$300M)=max€250 | SMALL(<$2B)=max€400 | MID(<$10B)=max€500 | LARGE=max€500
Beïnvloedt sizing, niet de score zelf.

**3. Phase Label**
ACCUMULATION / BREAKOUT / EXPANSION / FRENZY / EXHAUSTION / NEUTRAL
Puur algoritmisch op basis van volume, day_change en social velocity.

**4. Social Quality Cap**
Social mag NOOIT alleen tot een BUY-beslissing leiden.
  catalyst=NONE     → social gecapped op 2/8 pts
  catalyst=WEAK     → social gecapped op 4/8 pts
  catalyst=MODERATE → social gecapped op 6/8 pts
  catalyst=STRONG   → volledige 8 pts

**5. SectorConfig dataclass**
Sector data als expliciete input parameter. Niet meer als los integer.
load_sector_config() leest uit config/sectors.json.

**6. Gewichtsherbalancering (totaal = 100)**
  Volume:    25 → 22 pts
  Heat:      20 → 18 pts
  Premarket: 15 → 14 pts
  Social:    10 →  8 pts (gecapped)
  Float:      0 →  8 pts (nieuw)

**Nieuwe docs:**
- docs/ARCHITECTURE.md
- docs/ANTI_GOALS.md
- docs/KNOWN_FAILURE_MODES.md

**Test resultaten:**
- 11 test cases (8 regressie + 3 nieuw)
- 11/11 geslaagd

**Kalibratie noot:**
QBTS_T8: BUY_SMALL (was BUY_MODERATE in v1.1). Score 59.4 (was 60.4).
Marginale verschuiving door float+social herbalancering.
Conservatiever = niet fout. Gedocumenteerd in DECISIONS D-010.

---

## [v1.1] — 28 mei 2026 — Score Engine Kalibratie

**Fix 1: Dag >40% penalty verhoogd (30 → 40 pts)**
Probleem: CHASER_TEST5 (+42%) scoorde BUY_MAX. Fix: penalty 40 → Skip Score 55 → SKIP.

**Fix 2: Combinatieregel toegevoegd**
catalyst=NONE AND momentum<50 → altijd SKIP, ook als Skip Score < 50.

**Test resultaten:** v1.0: 5/8 | v1.1: 8/8

---

## [v1.0] — 28 mei 2026 — Score Engine Initieel

Volledige score engine met 6 componenten, Skip-first architectuur,
8 mock test cases. 5/8 correct (2 bugs geïdentificeerd → v1.1).

---

## [v0.1] — 28 mei 2026 — Project Initialisatie

Momentum dashboard HTML prototype, framework document, GitHub structuur.

---

## TEMPLATE

```markdown
## [vX.Y] — DD MMM YYYY — Titel

**Context:** Waarom?
**Gewijzigd:** Welke bestanden?
**Test resultaten:** X/Y geslaagd
**Breaking changes:** Ja/Nee
**Kalibratie noot:** Onverwachte score-verschuivingen?
```
