# CHANGELOG
> Alle wijzigingen, nieuwste bovenaan.
> Regel: elke wijziging hier vastleggen vóór implementatie.

---

## [v2.10] — 29 mei 2026 — Yahoo Fetch Compatibility Fix

**Context:** yfinance 0.2.36 breekt op `fast_info.last_price` met
`KeyError: 'currentTradingPeriod'`. Yahoo heeft hun interne API-response
gewijzigd; de sleutel `currentTradingPeriod` ontbreekt in de reply.
Gevolg: elke `/analyze/{ticker}` call retourneerde 422 FETCH_ERROR.
Dit is een data-laag fix — geen scoring, geen API-contract, geen schema's.

**Root cause:**
`fast_info.last_price` roept intern `_get_1y_prices()` aan, die
`self._md["currentTradingPeriod"]` verwacht. Na Yahoo's API-wijziging
staat die sleutel er niet meer in. yfinance>=0.2.54 lost dit op.

**Gewijzigd:**

`requirements.txt`
- `yfinance==0.2.36` → `yfinance>=0.2.54`
- Reden: 0.2.54 herstelt de cookie/crumb auth en de `currentTradingPeriod`
  key handling. Exact >= in plaats van pin omdat Yahoo periodiek hun
  auth flow wijzigt; vastpinnen op een vaste patch-versie reproduceert
  dit probleem.

`data/yahoo_client.py` (v2.4 → v2.5)
- `_log_fetch_error(ticker, call_name, exc)`: centrale log-helper die
  altijd `ExceptionType: message` + welke yfinance-call faalde logt.
  Met `MOMENTUM_DEBUG=1` ook volledige traceback via `logger.debug`.
- `_fetch_from_history(ticker, t)`: nieuwe fallback-helper. Als
  `fast_info` faalt, haalt deze `price`, `prev_close`, `volume_today`
  en `avg_volume_20d` op uit `history(period="5d")`. `market_cap` en
  `float_shares` zijn dan `None` (niet afleidbaar uit history).
- `_fetch_once()`: twee-paden structuur. Pad 1: `fast_info` (normaal).
  Pad 2: `_fetch_from_history()` als pad 1 faalt. Alleen als beide
  paden falen → `RuntimeError` met duidelijke melding.
- `get_snapshot()`: error-string bevat nu altijd `ExceptionType: message`
  (was: alleen de message, type werd weggegooied).
- Poging-logging in retry-loop toont nu ook exception type + message.

`scripts/debug_yahoo.py` (nieuw)
- Standalone script, geen project-imports nodig.
- Test drie paden: `fast_info`, `history(period="5d")`, `info`.
- Print volledige traceback bij elke fout.
- Eindvonnis per ticker: VOLLEDIG WERKEND / GEDEELTELIJK / BEIDE KAPOT.
- Gebruik: `python3 scripts/debug_yahoo.py` of
  `python3 scripts/debug_yahoo.py IONQ MSFT`

**Nieuwe tests:**

`tests/test_yahoo_client.py` (nieuw — 19 tests, 6 klassen)
- `TestFastInfoSuccess`         — normaal pad, geen fallback aangeroepen
- `TestHistoryFallback`         — fast_info faalt → history correct gebruikt
- `TestBothPathsFail`           — beide paden kapot → RuntimeError
- `TestGetSnapshotFallback`     — get_snapshot() gooit nooit een exception
- `TestFetchFromHistoryHelper`  — _fetch_from_history() edge cases
- `TestFetchErrorLogging`       — exception type staat in logs (caplog)

**Test resultaten:**
```
tests/test_scoring.py          70  ✓
tests/test_backend.py          36  ✓
tests/test_data_stability.py   55  ✓
tests/test_cache.py            74  ✓
tests/test_signals.py          57  ✓
tests/test_history.py          63  ✓
tests/test_replay.py           60  ✓
tests/test_evaluation.py       64  ✓
tests/test_dev_experience.py   51  ✓
tests/test_alerting.py         69  ✓
tests/test_yahoo_client.py     19  ✓  (nieuw)
TOTAAL                        618  ✓
```

**Breaking changes:** Geen.
**Scoring changes:** Geen.
**API contract:** Ongewijzigd.
**Kalibratie noot:** Geen score-verschuivingen — data-laag only.

---

## [v2.9] — 28 mei 2026 — Alerting & Watchlist Layer

**Context:** v2.8 was documentatie-only. v2.9 voegt actionable monitoring
toe: watchlists bijhouden welke tickers gescand moeten worden, de alert
engine detecteert wat er veranderd is en genereert gestructureerde alerts.

**Nieuwe alerting/ laag:**
- alerting/alert_engine.py    Trigger logic op opgeslagen snapshots
- alerting/alert_store.py     Alert history persistence
- alerting/cooldown_manager.py  Dedup + cooldown per trigger-type
- alerting/watchlist_manager.py  Watchlist CRUD

**Alert trigger types:**
- momentum_threshold  Score kruist decision-grens
- phase_transition    Fase verandert (NEUTRAL naar BREAKOUT)
- sector_heat_spike   Sector heat stijgt >10 punten
- volume_anomaly      Volume ratio > drempelwaarde
- confidence_downgrade  Confidence verslechtert (LIVE naar STALE)
- buy_max_signal      Score >= 90 (altijd HIGH/CRITICAL)
- score_drop          Score daalt >15 punten
- evaluation_insight  Historisch patroon gedetecteerd

**Severity:** INFO | WATCH | HIGH | CRITICAL
**Cooldowns:** CRITICAL=30min | HIGH=60min | WATCH=120min | INFO=240min

**Watchlists (watchlists/):**
- core.json           Kernposities (NVDA, MU, GOOGL, ASML, MSFT)
- momentum.json       Actieve momentum setups
- sector_rotation.json  Sector rotatie plays

**Endpoints:**
- GET  /alerts                      Recent alerts
- GET  /watchlists                  Alle watchlists
- GET  /watchlists/{name}           Specifieke watchlist
- POST /watchlists/{name}           Nieuwe watchlist
- POST /watchlists/{name}/add       Ticker toevoegen
- POST /watchlists/{name}/remove    Ticker verwijderen
- POST /alerts/scan                 Scan alle watchlist-tickers
- POST /alerts/scan/{ticker}        Scan een ticker

**Voorbeeld alert:**
  HIGH: QBTS: BUY_SMALL naar BUY_STRONG (score 52 naar 81, +29 pts)
  HIGH: QBTS: fase ACCUMULATION naar BREAKOUT
  HIGH: QBTS: volume 6.8x normaal

**Tests:** tests/test_alerting.py -- 69 tests
**Totaal:** 599/599

**Alerting observeert signalen -- raakt scoring nooit aan.**

---
---
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
