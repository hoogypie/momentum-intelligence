# CLAUDE BOOTSTRAP — MOMENTUM INTELLIGENCE
> Lees dit bestand EERST. Dit is het enige bestand dat je nodig hebt om te beginnen.
> Versie: v2.12 | Igor × Claude | 29 mei 2026

---

## WAT IS DIT PROJECT?

Persoonlijk momentum-intelligence tool voor US equity trading.
Detecteert early-stage momentum via volume anomaly, sector heat,
catalyst quality en relative strength.

**Kernprincipe:** Data berekent de score. AI legt uit. Nooit andersom.
**Score engine:** Volledig deterministisch. Zelfde input = zelfde output, altijd.

---

## WAAR ZIJN WE?

**Huidig versieniveau: v2.12**

```
v2.0  FastAPI backend + Yahoo Finance
v2.1  Data Stability (Pydantic schemas, retry, backoff)
v2.2  Caching & Data Freshness (TTL, LIVE/DELAYED/STALE)
v2.3  API Polish & Developer Experience (OpenAPI, smoke test)
v2.4  Real Signal Expansion (Finnhub, MarketSession)
v2.5  Historical Memory Layer (snapshot persistence, signal decay)
v2.6  Replay & Observation Tooling (diffs, timeline, export)
v2.7  Signal Evaluation Layer (grades, statistics)
v2.8  Documentation & Operating Manual
v2.9  Alerting & Watchlist Layer
v2.10 Yahoo Fetch Compatibility Fix
v2.11 Validation Layer
v2.12 Catalyst Intelligence Layer  ← HUIDIGE VERSIE
```

---

## ARCHITECTUUR (één regel per laag)

```
Data       Yahoo Finance (retry+cache) + Finnhub (optioneel) + sectors.json
Assembler  data/assembler.py → bouwt TickerInput van alle bronnen
Engine     scoring/scoring_v1_2.py → deterministisch, geen IO
API        backend/app.py → FastAPI, 30+ endpoints
Storage    storage/data/*.jsonl → snapshots, transitions, evaluations, alerts
Alerting   alerting/ → watchlists, triggers, cooldowns
```

**Bestandsstructuur (wat telt):**
```
scoring/scoring_v1_2.py      Score engine — NOOIT aanraken zonder tests
data/assembler.py             Bouwt TickerInput
backend/app.py                Alle endpoints
cache/market_cache.py         In-memory cache met TTL
storage/                      7 modules voor persistentie
alerting/                     Alert engine + watchlist manager
watchlists/*.json             Drie default watchlists
config/sectors.json           Sector heat config — wekelijks updaten
docs/OPERATING_MANUAL.md      Volledige gebruikershandleiding
DECISIONS.md                  Alle architectuurkeuzes met rationale
MASTER_CONTEXT.md             Uitgebreide technische context
```

---

## SCORE ENGINE v1.2 — DE KERNREGELS

**Momentum Score (100 pts totaal):**
```
Volume Anomaly     max 22  (rv = today/avg_20d)
Catalyst Quality   max 20  (STRONG=20, MODERATE=12, WEAK=4, NONE=0)
Sector Heat        max 18  (heat/100 × 18)
Premarket Strength max 14  (sweet spot 8-20%)
Relative Strength  max 10  (vs SPY return)
Social Accel.      max  8  (gecapped door catalyst!)
Float Score        max  8  (lage float = hogere score)
```

**Skip Score — blokkeert altijd voor Momentum:**
```
SEC/ClassAction/CFD  → +100 BLOCKED (onomkeerbaar)
Dag ≥ 40%            → +40  Skip
Pre-market ≥ 40%     → +40  Skip
Volume < 80% normaal → +25  Skip
Catalyst = NONE      → +20  Skip
Insiders > 10        → +15  Skip
```

**Decision mapping:**
```
≥ 90 → BUY_MAX      €400-500 (max €250 voor MICRO cap)
≥ 75 → BUY_STRONG   €300-400
≥ 60 → BUY_MODERATE €200-300
≥ 45 → BUY_SMALL    €100-200
≥ 30 → WATCH
< 30 → SKIP
Combinatieregel: catalyst=NONE + score<50 → SKIP
```

---

## TESTRESULTATEN

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
TOTAAL                            739  ✓  (geen netwerk vereist)
```

---

## WAT NOOIT VERANDERT ZONDER EXPLICIETE INSTRUCTIE

1. Score engine formules (scoring_v1_2.py)
2. API response schemas (breaking change)
3. Storage file formats (backward compat)
4. Decision drempels (≥90 BUY_MAX etc.)
5. Grade thresholds (SUCCESS ≥+3%, FAILED ≤-3%)
6. De drie tests in test_scoring.py die regressie bewaken

---

## ANTI-GOALS (nooit doen)

- Geen auto-trading of broker-integratie
- Geen ML/AI in de scoringsketen
- Geen frontend (nog niet)
- Geen real-time WebSocket feeds
- Geen backtesting engine
- Geen portfolio management
- Geen AI narrative layer (nog niet)

---

## BEKENDE RISICO'S

- Yahoo Finance is unofficial — kan rate-limited worden (> ~30 req/min)
- Zonder FINNHUB_API_KEY: catalyst altijd NONE → scores conservatief
- Evaluatie is sampling-afhankelijk: PENDING tot er toekomstige snapshots zijn
- float_shares = shares_outstanding (benadering, niet exacte float)
- Sectors.json is handmatig — kan verouderen

---

## OPSTARTEN (lokaal)

```bash
pip install -r requirements.txt
cp .env.example .env          # Optioneel: FINNHUB_API_KEY invullen
uvicorn backend.app:app --reload --port 8000
python3 scripts/smoke_test.py # Verificeer alle endpoints
pytest tests/ -q              # Moet 618 tests groen geven

# Yahoo Finance diagnose (bij fetch-fouten):
python3 scripts/debug_yahoo.py
python3 scripts/debug_yahoo.py IONQ MSFT

# Volledige tracebacks in logs:
MOMENTUM_DEBUG=1 uvicorn backend.app:app --reload --port 8000
```

---

## VOOR DE VOLGENDE SESSIE

Lees dit bestand. Daarna kun je direct bouwen. Vraag altijd:
1. "Welke versie bouwen we?" → antwoord geeft de richting
2. "Zijn er open action items?" → check DECISIONS.md voor recente D-XXX
3. "Welke tests mogen niet breken?" → alle 739

Als er twijfel is over architectuurkeuzes: **DECISIONS.md is de tiebreaker.**
Als er twijfel is over wat het systeem doet: **OPERATING_MANUAL.md is de referentie.**

---

*Momentum Intelligence v2.12 · Igor × Claude · 29 mei 2026*
*Geen formeel beleggingsadvies (Wft)*
