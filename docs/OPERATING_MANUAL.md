# OPERATING MANUAL — MOMENTUM INTELLIGENCE
> v2.8 | Igor × Claude | 28 mei 2026
> Geen formeel beleggingsadvies (Wft)

---

## INHOUDSOPGAVE

1. [Wat is dit systeem?](#1-wat-is-dit-systeem)
2. [Architectuur op één pagina](#2-architectuur-op-één-pagina)
3. [Lokaal draaien](#3-lokaal-draaien)
4. [Volledige workflow](#4-volledige-workflow)
5. [Scores interpreteren](#5-scores-interpreteren)
6. [Evaluatieresultaten interpreteren](#6-evaluatieresultaten-interpreteren)
7. [Bekende beperkingen](#7-bekende-beperkingen)
8. [Wanneer het systeem NIET vertrouwen](#8-wanneer-het-systeem-niet-vertrouwen)
9. [Versioning en tagging policy](#9-versioning-en-tagging-policy)
10. [Snelreferentie](#10-snelreferentie)

---

## 1. Wat is dit systeem?

**Momentum Intelligence** is een persoonlijk momentum-scanner voor US aandelen.
Het beantwoordt één vraag: *"Is er nu genoeg momentum in dit aandeel om een
gecontroleerde positie te overwegen?"*

Het systeem doet **niet**:
- Kopen of verkopen namens jou
- Voorspellen wat een aandeel gaat doen
- Rekening houden met jouw portfolio, belastingen of risicotolerantie
- ML of AI gebruiken in de scoringsketen

Het systeem doet **wel**:
- Volume-anomalieën detecteren (institutioneel volume vs. normaal)
- Sector heat in kaart brengen
- Catalyst kwaliteit classificeren (earnings beat, contract, upgrade)
- Signaalleeftijd bijhouden en decay toepassen
- Historische signalen evalueren (was het signaal achteraf correct?)

**Architectuurprincipe:** Data berekent de score. AI legt uit. Nooit andersom.
De score engine is volledig deterministisch — dezelfde input geeft altijd
dezelfde output.

---

## 2. Architectuur op één pagina

```
┌─────────────────────────────────────────────────────────────────────┐
│  DATA LAAG                                                           │
│                                                                      │
│  Yahoo Finance ──(retry 3x + backoff)──► TickerSnapshot             │
│       ↕                                         │                   │
│  In-memory Cache ◄──────────────────────────────┘                   │
│  (TTL 60s regulier / 1800s overnight)                               │
│                                                                      │
│  Finnhub News ──────────────────────────► NewsItem[]               │
│  (key vereist in .env; fallback: leeg)                              │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  ASSEMBLER (data/assembler.py)                                       │
│  Bouwt TickerInput van alle bronnen                                  │
│  Graceful defaults voor elk ontbrekend veld                         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  SCORE ENGINE (scoring/scoring_v1_2.py)                              │
│  Deterministisch — geen IO, geen netwerk, geen staat                 │
│                                                                      │
│  1. Skip Score  → ≥100? BLOCKED │ ≥50? SKIP                        │
│  2. Combinatieregel → catalyst=NONE + score<50 = SKIP               │
│  3. Momentum Score → 100 punten max                                  │
│  4. Decision + Sizing                                                │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  STORAGE LAAG                                                        │
│  storage/data/tickers/{TICKER}.jsonl  ← snapshot na elke scoring    │
│  storage/data/sectors/{SECTOR}.jsonl  ← sector snapshot per call    │
│  storage/data/evaluations/{TICKER}.jsonl ← grade na evaluatie run   │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  API LAAG (FastAPI, backend/app.py)                                  │
│                                                                      │
│  /analyze/{ticker}          ← score + sla op                        │
│  /analyze?tickers=A,B,C     ← batch (max 10)                        │
│  /sector/{name}             ← sector snapshot                       │
│  /history/{ticker}          ← evolutie + decay                      │
│  /replay/ticker/{ticker}    ← diffs + tijdlijn                      │
│  /evaluation/run/{ticker}   ← trigger evaluatie                     │
│  /evaluation/ticker/{ticker}← statistieken                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Lokaal draaien

### Vereisten

```
Python 3.11+
pip install -r requirements.txt
```

### Setup

```bash
# 1. Clone of unzip het project
cd momentum-intelligence

# 2. Installeer dependencies
pip install -r requirements.txt

# 3. Kopieer .env.example en vul optionele keys in
cp .env.example .env
# FINNHUB_API_KEY= (optioneel — voor nieuws/catalyst data)
# LOG_LEVEL=INFO   (DEBUG voor uitgebreide logging)

# 4. Start de backend
uvicorn backend.app:app --reload --port 8000
# Of via script:
python3 scripts/run_backend.py

# 5. Test of alles werkt
python3 scripts/smoke_test.py
```

### Eerste gebruik

```bash
# Score één aandeel
curl http://localhost:8000/analyze/NVDA

# Score meerdere tegelijk (sympathy scan)
curl "http://localhost:8000/analyze?tickers=IONQ,QBTS,RGTI"

# Sector overzicht
curl http://localhost:8000/sector/quantum

# Server status
curl http://localhost:8000/health
```

### Makefile shortcuts

```bash
make run     # Start backend
make test    # Run alle tests
make smoke   # Smoke test (server moet draaien)
make lint    # Syntax check
make clean   # Verwijder cache bestanden
```

### Test suite draaien

```bash
pytest tests/ -v
# Verwacht: 530 passed (geen netwerk vereist — alles gemockt)
```

---

## 4. Volledige workflow

### Stap 1 — Data ophalen

```
GET /analyze/IONQ
```

De backend roept `yahoo_client.get_snapshot("IONQ")` aan:
1. **Cache check** — is er een verse entry (< TTL)? Zo ja: gebruik die.
2. **Yahoo Finance fetch** — via yfinance (unofficial API, geen key nodig).
   Max 3 pogingen met backoff 0s / 0.5s / 1.5s.
3. **Cache fallback** — als Yahoo faalt maar cache heeft stale data:
   lever die met confidence=STALE/DELAYED.
4. **MISSING** — als beide falen: confidence=MISSING, prijs=0.

Tegelijk wordt `news_client.get_news("IONQ")` aangeroepen:
- **Met FINNHUB_API_KEY**: echte headlines, keyword-classificatie.
- **Zonder key**: lege lijst → catalyst_type altijd NONE.

### Stap 2 — Assembler

`assembler.build_ticker_input()` combineert alle bronnen tot een `TickerInput`:

| Veld | Bron | Fallback |
|---|---|---|
| price, volume | Yahoo Finance | 0 / avg_volume |
| market_cap | Yahoo Finance | $1B (SMALL tier) |
| float_shares | Yahoo (shares_outstanding) | None (neutral 4.5 pts) |
| catalyst_type | Finnhub headlines | NONE |
| relative_strength | Vergelijkt met SPY return | NEUTRAL |
| sector | config/sectors.json | heat=50 (neutraal) |
| social_mentions | Placeholder (fase 3) | 0/1 |

### Stap 3 — Score Engine

`score_ticker(ticker_input)` retourneert `ScoringResult`.

**Volgorde (altijd):**
```
1. Skip Score berekenen
2. Als skip ≥ 100 → BLOCKED (stop)
3. Als skip ≥ 50  → SKIP (stop)
4. Combinatieregel: catalyst=NONE + momentum<50 → SKIP (stop)
5. Momentum Score berekenen (7 componenten)
6. Decision mappen op score
7. Sizing berekenen op tier
```

De engine heeft **geen IO** — geen netwerk, geen cache, geen storage.
Zelfde input → altijd zelfde output.

### Stap 4 — Storage

Na elke succesvolle scoring sloeg `_persist_snapshot()` het resultaat op:
- `storage/data/tickers/{TICKER}.jsonl` — scoring snapshot
- Fase-overgang gedetecteerd? → `{TICKER}_transitions.jsonl`
- Catalyst veranderd? → `{TICKER}_catalysts.jsonl`

Sla op met `persist=false` om dit te skipppen:
```bash
curl "http://localhost:8000/analyze/IONQ?persist=false"
```

### Stap 5 — Replay

Nadat je een paar scores hebt opgeslagen:

```bash
# Volledige tijdlijn met diffs
curl http://localhost:8000/replay/ticker/IONQ

# Alleen significante veranderingen
curl "http://localhost:8000/replay/ticker/IONQ/diff?significant=true"

# Wat was er actief op een specifieke dag?
curl http://localhost:8000/replay/session/2026-05-28
```

### Stap 6 — Evaluatie

Nadat er toekomstige snapshots zijn (de volgende keren dat je IONQ scoort):

```bash
# Trigger evaluatie
curl -X POST http://localhost:8000/evaluation/run/IONQ

# Bekijk resultaten
curl http://localhost:8000/evaluation/ticker/IONQ

# Welke fase werkt het best?
curl http://localhost:8000/evaluation/stats
```

---

## 5. Scores interpreteren

### Decision labels

| Label | Momentum score | Sizing (MID/LARGE cap) | Betekenis |
|---|---|---|---|
| **BUY_MAX** | ≥ 90 | €400-500 | Uitzonderlijk sterk signaal |
| **BUY_STRONG** | ≥ 75 | €300-400 | Sterk momentum signaal |
| **BUY_MODERATE** | ≥ 60 | €200-300 | Solide momentum signaal |
| **BUY_SMALL** | ≥ 45 | €100-200 | Matig momentum signaal |
| **WATCH** | ≥ 30 | Watchlist | Zwak signaal — monitor |
| **SKIP** | < 30 of combinatieregel | €0 | Onvoldoende of geblokkeerd |
| **BLOCKED** | Elke score | €0 | Hard veto (SEC/CFD/class action) |

⚠️ **MICRO cap cap:** Elk BUY signaal voor een aandeel < $300M market cap wordt
afgetopt op max €250, ongeacht de decision label.

### Momentum Score componenten (totaal 100 pts)

| Component | Max | Wat het meet |
|---|---|---|
| Volume Anomaly | 22 | Huidig volume vs 20-daags gemiddelde |
| Catalyst Quality | 20 | Kwaliteit van het nieuwscatalyst |
| Sector Heat | 18 | Temperatuur van de sector (0-100) |
| Premarket Strength | 14 | Pre-market beweging (sweet spot 8-20%) |
| Relative Strength | 10 | Prestatie vs SPY vandaag |
| Social Acceleration | 8 | Mention velocity (gecapped door catalyst) |
| Float Score | 8 | Lage float = hogere amplificatie |

**Volume Anomaly details:**
```
≥ 8x normaal → 22 pts (institutioneel niveau)
≥ 5x         → 17.6 pts
≥ 3x         → 13.2 pts
≥ 2x         →  8.8 pts
≥ 1x         →  4.4 pts
< 1x normaal →  0 pts
```

**Social Acceleration — opgelet:**
Social kan nooit alleen een BUY veroorzaken. Het wordt gecapped door catalyst:
```
catalyst=NONE     → max 2 pts social (ongeacht mentions)
catalyst=WEAK     → max 4 pts
catalyst=MODERATE → max 6 pts
catalyst=STRONG   → max 8 pts (geen cap)
```

### Skip Score — wat blokkeert een signaal?

**Hard vetoes (elk +100, onomkeerbaar):**
- SEC investigation
- Class action lawsuit
- CFD-only op T212 (kan niet als echt aandeel kopen)

**Soft blocks (cumulatief, total ≥ 50 = SKIP):**
```
Dag ≥ 40%           → +40  (te laat, Spelregel 8)
Pre-market ≥ 40%    → +40  (volledig ingeprijsd)
Dag ≥ 20%           → +10  (pre-run risico)
Pre-market ≥ 20%    → +15  (hoog pre-market)
Volume < 80% normaal → +25 (geen institutioneel)
Catalyst = NONE     → +20  (pure hype risico)
Insiders >10 trades → +15  (Spelregel 13)
Insiders >5 trades  →  +8  (monitoren)
```

### Fase labels

| Fase | Betekenis | Typische score range |
|---|---|---|
| ACCUMULATION | Vroeg opbouwen, laag volume groeit | 45-60 |
| BREAKOUT | Eerste uitbraak — catalyst aanwezig | 60-75 |
| EXPANSION | Momentum versterkt, fase 2 | 75-85 |
| FRENZY | Piek momentum, hoog risico | 85-100 |
| EXHAUSTION | Momentum verzwakt na top | 45-60 dalend |
| NEUTRAL | Geen duidelijke richting | < 45 |

### DataConfidence labels

| Label | Betekenis | Actie |
|---|---|---|
| LIVE | Data < 5 min oud, alle velden aanwezig | Volledig vertrouwen |
| DELAYED | 5-60 min oud (uit cache) | Geschikt voor trending |
| STALE | 1-2 uur oud (fallback) | Gebruik met voorzichtigheid |
| PARTIAL | Prijs aanwezig, optionele velden ontbreken | Score minder precies |
| MISSING | Geen bruikbare prijs | Niet scoren |

---

## 6. Evaluatieresultaten interpreteren

### Grade labels

| Grade | Betekenis (BUY signalen) | Threshold |
|---|---|---|
| SUCCESS | Prijs steeg ≥ +3% binnen 24u | return_1d ≥ 3.0% |
| NEUTRAL | Prijs bewoog tussen -3% en +3% | -3.0% < return_1d < 3.0% |
| FAILED | Prijs daalde ≥ -3% binnen 24u | return_1d ≤ -3.0% |
| PENDING | Geen toekomstige snapshot data | — |

Voor SKIP/BLOCKED signalen zijn de assen omgekeerd:
- SUCCESS = prijs daalde ≤ -2% (signaal had gelijk)
- FAILED = prijs steeg ≥ +2% (signaal had ongelijk)

### Tijdshorizon prioriteit

Het systeem kijkt naar toekomstige **opgeslagen snapshots**
(geen extra Yahoo calls). Prioriteit:

```
1d-horizon (T+20h tot T+28h) → gebruik als beschikbaar
4h-horizon (T+3h  tot T+5h)  → fallback als geen 1d data
1h-horizon (T+45m tot T+75m) → fallback als geen 4h data
PENDING                       → als geen enkele horizon data heeft
```

**Belangrijk:** De evaluatie is nooit beter dan de scanfrequentie.
Als je IONQ om 09:30 scoort maar pas de volgende dag om 15:00 opnieuw,
dan is de "1d-horizon prijs" in werkelijkheid 29.5 uur later.

### Statistieken interpreteren

```json
{
  "success_rate": 0.62,        // 62% van BUY signalen waren succesvol
  "avg_score_success": 78.3,   // Succesvolle signalen hadden gemiddeld hogere scores
  "avg_score_failed":  51.2,   // Gefaalde signalen hadden lagere scores
  "by_phase": {
    "BREAKOUT":   { "success_rate": 0.71 },  // Beste fase
    "FRENZY":     { "success_rate": 0.38 },  // Gevaarlijkste instap
    "NEUTRAL":    { "success_rate": 0.22 }   // Geen richting = geen edge
  }
}
```

**Vuistregel:** Als `avg_score_success` significant hoger is dan
`avg_score_failed`, werkt het score-systeem als scheidingsmechanisme.
Als de scores vergelijkbaar zijn, discrimineert het systeem onvoldoende.

### Signal decay (v2.5)

Opgeslagen signalen verouderen:

| Leeftijd | SignalAge | Decay | Decision effect |
|---|---|---|---|
| 0-2 uur | FRESH | 1.00× | Geen downgrade |
| 2-8 uur | AGING | 0.85× | Geen downgrade |
| 8-24 uur | STALE | 0.65× | 1 stap lager |
| 24-48 uur | OLD | 0.40× | Altijd WATCH |
| > 48 uur | EXPIRED | 0.00× | Altijd SKIP |

FRENZY-fase signalen krijgen extra decay ×0.70 bij AGING/STALE —
momentum-windows zijn korter bij extreem hoge scores.

---

## 7. Bekende beperkingen

### Data kwaliteit

**Yahoo Finance (unofficieel)**
- yfinance is een niet-officiële library die de Yahoo Finance website scrapt.
- Kan rate-limited worden bij veel requests (> ~30/min).
- `float_shares` is eigenlijk `shares_outstanding` — niet exact de tradeable float.
- Pre-market data is alleen beschikbaar tijdens de pre-market sessie (04:00-09:30 ET).
- Weekenden en feestdagen: Yahoo retourneert soms stale data.

**Finnhub (gratis tier)**
- 60 API calls/min op de gratis tier.
- Nieuws heeft een vertraging van soms 15-30 minuten t.o.v. publicatie.
- Zonder key: catalyst_type altijd NONE → score is structureel te laag.

**Social (placeholder)**
- StockTwits integratie is nog niet actief (fase 3).
- social_acceleration altijd 0 pts.
- De social_cap werkt correct zodra echte data beschikbaar is.

**SEC/Insider checks**
- has_sec_investigation altijd False zonder Finnhub key.
- Handmatige verificatie vereist voor FRAMEWORK Spelregel 3.

### Score engine beperkingen

**Sector heat is statisch**
- config/sectors.json bevat handmatig ingestelde heat-waarden.
- Dynamische sector heat (v2.4) blends 60% live / 40% statisch, maar
  heeft gecachede leader-data nodig om te werken.
- Sectoren buiten de config krijgen heat=50 (neutraal) — noch gebufferd
  noch afgestraft.

**Catalyst type is binary**
- STRONG/MODERATE/WEAK/NONE — geen nuance in sterkte binnen STRONG.
- Een €5M contract en een $5B overname zijn beide "STRONG".

**Geen macro-context**
- Fed-vergaderingen, earnings seasons, geopolitieke events: het systeem
  weet er niets van.
- Op een dag als FOMC-dag zijn alle scores minder betrouwbaar.

**Float score benadering**
- Lage float = hoge amplificatie. Maar float van Yahoo is shares_outstanding
  — netto float (na insiders, locked-up shares) kan lager zijn.

### Evaluatie beperkingen

**Evaluatie is sampling-afhankelijk**
- De evaluatie vergelijkt entry-prijs met een latere opgeslagen snapshot.
- Als je een ticker 1x per dag scoort, is de "1h-horizon" altijd PENDING.
- Frequente scanning (elk uur) geeft nauwkeurigere evaluaties.

**Survivorship bias risico**
- Je evalueert wat je hebt gescand. Als je IONQ alleen scoort na goede
  nieuwtjes, oververtegen succesvolle setups.

**Geen slippage, spreads of kosten**
- Een "SUCCESS" van +3.1% is theoretisch. In de praktijk: spread + T212
  commissions eten hiervan een deel.

---

## 8. Wanneer het systeem NIET vertrouwen

Vertrouw de score **niet** in de volgende situaties:

### 1. Pre-earnings (FRAMEWORK Spelregel 2)
Het systeem scoort gewoon pre-earnings. Hoge scores pre-earnings zijn
**gevaarlijker**, niet veiliger — de markt heeft vaak al geprijsd.
**Actie:** Nooit bijkopen op bestaande posities pre-earnings op basis
van een hoge score. Bestaande spel: check de Beat-the-Beat analyse.

### 2. Confidence = MISSING of PARTIAL
Prijs is 0 of data is onvolledig. Een BUY_MODERATE op MISSING data
is betekenisloos.
**Actie:** Controleer `data_quality.confidence` in de response.
Alleen LIVE en DELAYED zijn betrouwbaar voor actie.

### 3. Catalyst = NONE (zonder Finnhub key)
Zonder nieuws-integratie is catalyst altijd NONE. De combinatieregel
filtert de meeste pure-hype signalen, maar niet alle.
**Actie:** Stel FINNHUB_API_KEY in of doe handmatige catalyst check.

### 4. Dag of pre-market ≥ 20%
Skip Score stijgt bij grote moves, maar het systeem stopt niet automatisch
bij 20-39% moves (alleen bij ≥ 40%). Een score van BUY_MODERATE na een
+28% dag is misleidend — je koopt laat.
**Actie:** Check `data_quality` voor `day_change_pct` en vergelijk met
FRAMEWORK Spelregel 8 (> 20% pre-run = halveer sizing).

### 5. Sector in sectors.json niet bijgewerkt
Als sectors.json outdated heat-waarden heeft, is de sector-component
foutief. Een "DORMANT" sector met heat=30 die plotseling hot is, wordt
incorrect gescoord.
**Actie:** Update sectors.json wekelijks (FRAMEWORK Spelregel 21).

### 6. SignalAge = STALE, OLD of EXPIRED
Een opgeslagen score van 6+ uur geleden is stale. De markt kan
fundamenteel veranderd zijn.
**Actie:** Gebruik `effective_decision` en `effective_score` uit
`/history/{ticker}` in plaats van de originele score.

### 7. Macro-evenementen
Fed, CPI, FOMC, geopolitiek nieuws, earnings van sector-leaders.
Het systeem heeft geen macro-bewustzijn.
**Actie:** Pas FRAMEWORK Spelregel 10 (Macro Impact Check) toe.
Bij grote macro-events: geen nieuwe posities, ongeacht de score.

### 8. Succes rate < 40% in evaluatie
Als de historische evaluatie voor een specifiek type setup (fase +
catalyst + score range) een success rate < 40% toont, is die setup
historisch counterproductief.
**Actie:** Gebruik `/evaluation/stats` voor een breakdown per fase
en catalyst type. Vermijd setups met bewezen negatieve edge.

---

## 9. Versioning en tagging policy

### Versie nummering

```
v{MAJOR}.{MINOR}

MAJOR  →  Wijzigt zodra de architectuur fundamenteel verandert
           (nieuwe storage laag, nieuwe API contract, etc.)
MINOR  →  Nieuwe features, endpoints, of aanzienlijke uitbreiding
           Geen MAJOR als bestaande testen blijven slagen
```

Huidige versie: **v2.8** (documentatie release)

### Versiegeschiedenis

| Versie | Datum | Omschrijving |
|---|---|---|
| v1.0 | 28 mei 2026 | Score engine v1.0 — 5/8 tests |
| v1.1 | 28 mei 2026 | Score engine fix — 8/8 tests |
| v1.2 | 28 mei 2026 | Float score + social cap toegevoegd |
| v1.3 | 28 mei 2026 | 11 regressietests, LOWFLOAT + MEGACAP + SOCIALPUMP |
| v2.0 | 28 mei 2026 | FastAPI backend + Yahoo Finance data ingestion |
| v2.1 | 28 mei 2026 | Data Stability Layer — Pydantic schemas, retry |
| v2.2 | 28 mei 2026 | Caching & Data Freshness Layer |
| v2.3 | 28 mei 2026 | API Polish & Developer Experience |
| v2.4 | 28 mei 2026 | Real Signal Expansion — Finnhub, marktssessie |
| v2.5 | 28 mei 2026 | Historical Memory Layer — snapshot persistence |
| v2.6 | 28 mei 2026 | Replay & Observation Tooling |
| v2.7 | 28 mei 2026 | Signal Evaluation Layer |
| v2.8 | 28 mei 2026 | Documentation & Operating Manual |

### Wat verandert NIET zonder versie-bump

- Score engine formules (scoring_v1_2.py)
- API response schema's (breaking changes)
- Storage file formats (backward compat vereist)
- Grade thresholds (SUCCESS_THRESHOLD, FAILED_THRESHOLD)
- Decision drempels (≥90 BUY_MAX, etc.)

### Git tagging beleid

```bash
# Tag bij elke minor release
git tag -a v2.8 -m "v2.8 — Documentation & Operating Manual"

# Tag formaat
v{MAJOR}.{MINOR}[.{PATCH}]

# Patch = bug fix zonder feature change
v2.7.1  # Bug fix in evaluation grade logic
```

### MASTER_CONTEXT.md als sessiebrug

Bij elke nieuwe Claude-sessie:
1. Upload `MASTER_CONTEXT.md` + `DECISIONS.md`
2. Zeg: *"Lees MASTER_CONTEXT.md en DECISIONS.md. We zijn op v2.X."*
3. Claude herkent de architectuur en kan direct doorwerken

`MASTER_CONTEXT.md` is de enige bron van waarheid voor sessie-overdracht.
Update het na elke significante wijziging.

---

## 10. Snelreferentie

### Endpoints overzicht

```
# Data & scoring
GET  /health                         Server status + cache stats
GET  /analyze/{ticker}               Score één ticker
GET  /analyze?tickers=A,B,C          Batch scoring (max 10)
GET  /analyze/{ticker}?refresh=true  Cache bypass
GET  /analyze/{ticker}?persist=false Score zonder opslaan

# Sector
GET  /sector/{sector_name}           Sector snapshot
GET  /sector/{sector_name}/trend     Heat trend over tijd

# Cache
GET  /cache/stats                    Cache statistieken
DEL  /cache/{ticker}                 Cache invalideren

# History
GET  /history/{ticker}               Evolutie + decay
GET  /history/{ticker}/window        Momentum window open?
GET  /history/{ticker}/transitions   Fase-overgangen

# Replay
GET  /replay/ticker/{ticker}         Diffs + tijdlijn
GET  /replay/ticker/{ticker}/diff    Diffs (?significant=true)
GET  /replay/sector/{sector}         Sector replay
GET  /replay/session/YYYY-MM-DD      Dag-overzicht
GET  /replay/summary                 Alle tickers samengevat

# Evaluatie
POST /evaluation/run/{ticker}        Trigger evaluatie
GET  /evaluation/ticker/{ticker}     Statistieken + grades
GET  /evaluation/session/YYYY-MM-DD  Dag-evaluatie
GET  /evaluation/top-signals         Beste/slechtste ooit
GET  /evaluation/stats               Globale statistieken

# Docs
GET  /docs                           Swagger UI
GET  /openapi.json                   OpenAPI schema
```

### Score engine — directe mapping

```
Momentum ≥ 90  →  BUY_MAX      (€400-500, max €250 voor MICRO)
Momentum ≥ 75  →  BUY_STRONG   (€300-400)
Momentum ≥ 60  →  BUY_MODERATE (€200-300)
Momentum ≥ 45  →  BUY_SMALL    (€100-200)
Momentum ≥ 30  →  WATCH
Momentum <  30  →  SKIP
Skip ≥ 50      →  SKIP (overschrijft momentum)
Skip ≥ 100     →  BLOCKED (overschrijft alles)
```

### Sector IDs (config/sectors.json)

```
quantum          QUANTUM COMPUTING
ai_infra         AI INFRASTRUCTURE
drones_defense   DRONES & DEFENSE
ai_software      AI SOFTWARE
power_energy     POWER & ENERGY
robotics         ROBOTICS
cybersecurity    CYBERSECURITY
ai_pc            AI PC REFRESH
```

### Omgeving instellen (.env)

```bash
FINNHUB_API_KEY=          # Nieuws/catalyst data (gratis tier)
LOG_LEVEL=INFO            # DEBUG voor uitgebreide logging
CACHE_ENABLED=true        # Cache aan/uit
CACHE_TTL_SECONDS=60      # TTL regular hours
EVAL_SUCCESS_THRESHOLD=3.0 # % voor SUCCESS grade
EVAL_FAILED_THRESHOLD=-3.0 # % voor FAILED grade
MAX_SNAPSHOTS_PER_TICKER=500
```

### Signaalleeftijd snel checken

```bash
curl http://localhost:8000/history/IONQ/window
# window_open: true/false
# signal_age: FRESH/AGING/STALE/OLD/EXPIRED
# effective_decision: huidige decision na decay
```

### Evaluatie workflow

```bash
# 1. Score meerdere keren (spreide scanmomenten)
curl http://localhost:8000/analyze/IONQ
# ... 4+ uur later ...
curl http://localhost:8000/analyze/IONQ

# 2. Trigger evaluatie
curl -X POST http://localhost:8000/evaluation/run/IONQ

# 3. Bekijk resultaten
curl http://localhost:8000/evaluation/ticker/IONQ

# 4. Export rapport
curl "http://localhost:8000/evaluation/ticker/IONQ?export=true"
```

---

*Momentum Intelligence v2.8 · Igor × Claude · 28 mei 2026*
*Geen formeel beleggingsadvies (Wft)*
