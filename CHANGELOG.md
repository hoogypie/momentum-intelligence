# CHANGELOG
> Alle wijzigingen, nieuwste bovenaan.
> Regel: elke wijziging hier vastleggen vóór implementatie.

---

## [v2.13.1] — 29 mei 2026 — Paper Trading Bias Fixes

**Context:** End-to-end review van het paper trading framework (v2.13)
identificeerde twee kritieke methodologische fouten die statistieken
zouden vertekenen.

**FLAW 1 FIXED — Look-ahead bias in 1d horizon**
Root cause: `_HORIZON_CALENDAR_DAYS["1d"] = 2` + `_TOLERANCE_DAYS = 2`
gaf een search window van [signal_dag, signal_dag+4]. De slotkoers van
de signaaldag zelf viel binnen het window en kon als "1d outcome" worden
gevonden — dat is een prijs uit het verleden, geen toekomst.

Fix: `_fetch_close_price()` accepteert nu `earliest_allowed` parameter.
`evaluate_trade()` geeft altijd `signal_ts + 1 kalenderdag` mee als
ondergrens. Geen enkel horizon kan de signaaldag of eerder gebruiken.

**FLAW 2 FIXED — Duplicate trades bij meerdere runs per dag**
Root cause: `trade_id` was timestamp-gebaseerd. Twee runs op dezelfde
dag voor dezelfde ticker gaven twee verschillende IDs → twee records.
Bij statistieken leek de sample size groter dan hij was.

Fix: `save_trade_from_result()` controleert via `has_trade_today()` of
er al een trade bestaat voor (ticker, decision, dag). Tweede run op
zelfde dag retourneert `None` en slaat niet op.
`allow_duplicate=True` beschikbaar voor tests en backfill.

**Test resultaten:**
```
tests/test_paper_trading.py        44  ✓  (was 41, +3 dedup tests)
TOTAAL                            783  ✓
```

**Breaking changes:** Geen voor scores of API.
**Data impact:** Bestaande trades in storage worden niet gewijzigd.
Nieuwe runs slaan geen duplicaten meer op.

---

## [v2.13] — 29 mei 2026 — Paper Trading Validation Framework

**Context:** Engine scoort correct en catalyst intelligence werkt. Vóór
score-kalibratie is bewijs nodig: presteren BUY-signalen beter dan willekeurig?
v2.13 bouwt de tooling om dat systematisch te meten.

**Kernprincipe:** Evidence before calibration.
Geen scoring-wijzigingen, geen threshold-aanpassingen.
Alleen meten wat de engine al doet.

**Nieuwe bestanden:**

`storage/paper_trade_store.py` (nieuw)
- `PaperTrade` dataclass: ticker, timestamp, score, catalyst, phase, decision,
  entry_price, returns per horizon (1d/3d/5d/10d), status (OPEN/PARTIAL/COMPLETE)
- `save_trade_from_result()`: convenience wrapper, retourneert None voor niet-BUY
- `record_trade()`: idempotent opslaan via trade_id
- `update_trade_outcomes()`: vult prijs- en return-velden in, herberekent status
- `load_trades()`, `load_open_trades()`, `load_complete_trades()`: filters op
  ticker, decision en status
- Globale index: `storage/data/paper_trades/_index.jsonl`
- Nooit een exception — alle fouten worden gelogd

`storage/paper_trade_evaluator.py` (nieuw)
- `evaluate_trade()`: haalt ontbrekende prijzen op via `yfinance.history()`
  voor de vier horizons, slaat over als horizon nog niet bereikbaar is
- `evaluate_all_open()`: batch-evaluatie, retourneert samenvatting
- Tolerantievenster ±2 kalenderdagen per horizon
- Idempotent: al ingevulde horizons worden niet overschreven

`scripts/paper_trade_report.py` (nieuw)
- Drie modi: `record`, `evaluate`, `report`
- `record`: scoort tickers live, slaat BUY-signalen op
- `evaluate`: haalt toekomstige marktprijzen op
- `report`: toont win rate, gem. rendement, mediaan per horizon (1d/3d/5d/10d),
  uitsplitsing per beslissing (BUY_SMALL/MODERATE/STRONG),
  uitsplitsing per catalyst type en source

`scripts/validation_runner.py` (update)
- `_analyze_one()` heeft nu `paper_trade=True` parameter
- BUY-signalen worden automatisch opgeslagen bij elke validation run
- `paper_trade=False` voor dry runs zonder opslag

**Tests:**

`tests/test_paper_trading.py` (nieuw — 41 tests, 7 klassen)
- `TestPaperTradeMakeId`         — trade ID formaat en uniciteit
- `TestPaperTradeStore`          — opslaan, laden, idempotentie, never-raises
- `TestPaperTradeFilters`        — filters decision/status/ticker
- `TestUpdateTradeOutcomes`      — return berekening, status-overgang
- `TestPaperTradeEvaluator`      — price fetching, horizon-logica, fallback
- `TestPaperTradeStatistics`     — win rate, mediaan, threshold
- `TestValidationRunnerHook`     — paper_trade param, BUY recorded, WATCH niet

**Test resultaten:**
```
tests/test_scoring.py              70  ✓
tests/test_backend.py              36  ✓
tests/test_data_stability.py       55  ✓
tests/test_cache.py                74  ✓
tests/test_signals.py              57  ✓
tests/test_history.py              63  ✓
tests/test_replay.py               60  ✓
tests/test_evaluation.py           64  ✓
tests/test_dev_experience.py       51  ✓
tests/test_alerting.py             69  ✓
tests/test_yahoo_client.py         19  ✓
tests/test_validation_runner.py    36  ✓
tests/test_catalyst_classifier.py  85  ✓
tests/test_paper_trading.py        41  ✓
TOTAAL                            780  ✓
```

**Breaking changes:** Geen.
**Scoring changes:** Geen.
**API contract:** Ongewijzigd.

---

## [v2.12] — 29 mei 2026 — Catalyst Intelligence Layer

**Context:** Validation (v2.11) bevestigde dat de engine conservatief scoort
omdat catalyst altijd NONE is — niet omdat scoring kapot is. Oorzaak: de
bestaande news_client.py had wel Finnhub-fetching maar een primitieve keyword-
matcher zonder recency weighting, momentum-type onderscheid of confidence scoring.
v2.12 bouwt een volwaardige catalyst-intelligentie laag.

**Kernprincipe (D-005):**
Data classificeert. Engine scoort. Nooit andersom.
catalyst_classifier.py levert gestructureerde input — de score engine bepaalt
hoeveel punten een catalyst waard is. Geen threshold-wijzigingen.

**Nieuw onderscheid (Spelregel 27):**
Drie momentum-typen worden nu structureel onderscheiden:
- `OWN`      — ticker heeft een eigen, bedrijfsspecifieke catalyst (volle score)
- `SECTOR`   — sectorbreed momentum, geen ticker-specifiek nieuws (≤ MODERATE)
- `SYMPATHY` — ticker beweegt mee na een andere ticker (≤ WEAK)

**Nieuwe bestanden:**

`config/news_keywords.json` (nieuw)
- Keyword-taxonomie in vier tiers: STRONG / MODERATE / WEAK / NEGATIVE
- Per tier: gewicht (0-1), beschrijving, exhaustieve keyword-lijst
- `momentum_type_signals`: OWN / SECTOR / SYMPATHY signalen
- `recency_weights`: multipliers per ouderdomsband (0-48u)
- `source_tiers`: tier-1/2/3 bronnen voor confidence scoring
- `confidence_matrix`: HIGH/MEDIUM/LOW definitie

`data/finnhub_client.py` (nieuw)
- Pure data-fetching, geen classificatie
- `FinnhubNewsItem` dataclass: headline, source, url, unix timestamp, sentiment
- `fetch_company_news(ticker, hours)` — altijd lijst, nooit exception
- `is_available()` — snelle key-check
- Gesorteerd: nieuwste artikel eerst

`data/catalyst_classifier.py` (nieuw)
- Centrale intelligentielaag
- `CatalystResult`: type, source (OWN/SECTOR/SYMPATHY), confidence, score,
  top_headline, raw_headlines, negative_flags, news_available
- Keyword matching via news_keywords.json
- Recency multiplier: 1.0 (<2u) → 0.20 (>48u)
- Source multiplier: Reuters=1.0, PR Newswire=0.65, onbekend=0.85
- Source cap: sympathy-headlines ≤ WEAK, sector-headlines ≤ MODERATE
- Confidence: HIGH/MEDIUM/LOW op basis van bron + leeftijd + tier
- `classify_from_news_items()` — backward-compat wrapper voor legacy NewsItems

`data/assembler.py` (v2.4 → v2.5)
- Gebruikt `finnhub_client.fetch_company_news()` als primaire bron
- Valt terug op legacy `news_client.get_news()` als Finnhub niet beschikbaar
- `_run_catalyst_classification()`: twee-paden strategie
- `_classify_catalyst()` shim behouden voor backward-compat (test_backend.py)
- Debug-logging: catalyst type + source + confidence per ticker

`scripts/validation_runner.py` (update)
- Voegt `catalyst_source`, `catalyst_conf`, `top_headline`, `raw_headlines`
  toe aan output (JSON + CSV)

**Tests:**

`tests/test_catalyst_classifier.py` (nieuw — 85 tests, 10 klassen)
- `TestKeywordTierMatching`       — STRONG/MODERATE/WEAK/NEGATIVE herkenning
- `TestRecencyWeighting`          — score daalt monotoon met ouderdom
- `TestSourceMultiplier`          — Reuters > PR Newswire
- `TestMomentumSourceDetection`   — OWN / SECTOR / SYMPATHY onderscheid
- `TestSourceCap`                 — sympathy kan niet STRONG zijn
- `TestCatalystConfidence`        — HIGH/MEDIUM/LOW logica
- `TestClassifyFunction`          — end-to-end met FinnhubNewsItems
- `TestClassifyEdgeCases`         — leeg, alleen negatief, nooit exception
- `TestClassifyFromNewsItems`     — legacy backward-compat
- `TestFinnhubClientParsing`      — parsing, error handling, never-raises

**Test resultaten:**
```
tests/test_scoring.py              70  ✓
tests/test_backend.py              36  ✓
tests/test_data_stability.py       55  ✓
tests/test_cache.py                74  ✓
tests/test_signals.py              57  ✓
tests/test_history.py              63  ✓
tests/test_replay.py               60  ✓
tests/test_evaluation.py           64  ✓
tests/test_dev_experience.py       51  ✓
tests/test_alerting.py             69  ✓
tests/test_yahoo_client.py         19  ✓
tests/test_validation_runner.py    36  ✓
tests/test_catalyst_classifier.py  85  ✓
TOTAAL                            739  ✓
```

**Breaking changes:** Geen.
**Scoring changes:** Geen — alleen betere input.
**API contract:** Ongewijzigd.

---

## [v2.11] — 29 mei 2026 — Validation Layer

**Context:** Live scoring werkt na de v2.10 Yahoo fix. Vóór UI-werk is
confidence calibratie nodig: hoe gedraagt de engine zich over echte tickers,
over alle sectoren en marktcap lagen? v2.11 bouwt de tooling om dat
systematisch te meten.

**Nieuwe bestanden:**

`research/validation_watchlist.json` (nieuw)
- 39 gecureerde tickers in 9 groepen
- Dekt alle regime-fases: HOT (drones/quantum), BUILDING (AI software),
  DORMANT (power/energy), en een control groep (KO, JNJ, WMT)
- Bevat per ticker: cap label, note, expected behavior
- `expected_distribution` blok documenteert verwachte score-verdeling
- `active` flag: tickers markeren als inactief zonder te verwijderen

`scripts/validation_runner.py` (nieuw)
- Batch-analyseert tickers via `build_ticker_input()` + `score_ticker()` direct
  (geen HTTP, geen backend nodig)
- Output: JSON (volledig) + CSV (gesorteerd op momentum score, errors onderaan)
- Leesbaar terminal-rapport: beslissings-distributie, per-ticker tabel,
  data kwaliteit waarschuwingen, engine observaties (catalyst=NONE prevalentie,
  social capping, hard blocks)
- CLI opties: `--group`, `--ticker`, `--delay`, `--no-persist`, `--force-refresh`
- Rate limit bescherming: configureerbare delay tussen requests (default 1.5s)
- Nooit een exception — elke ticker-fout wordt als `status: error` opgenomen

`tests/test_validation_runner.py` (nieuw — 36 tests, 6 klassen)
- `TestWatchlistLoading`    — JSON laden, groep-filter, active flag
- `TestAnalyzeOne`          — succes/error pad, veldvolledigheid, nooit exception
- `TestExtractTopReasons`   — blocked/skip/momentum reason extractie
- `TestWriteOutputs`        — CSV + JSON aangemaakt, sortering, dir-aanmaak
- `TestPrintReport`         — print crasht niet bij leeg / all-error input
- `TestMainArgParsing`      — CLI ticker-override, group-filter doorgegeven

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
tests/test_yahoo_client.py     19  ✓
tests/test_validation_runner.py 36  ✓
TOTAAL                        654  ✓
```

**Breaking changes:** Geen.
**Scoring changes:** Geen.
**API contract:** Ongewijzigd.

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
