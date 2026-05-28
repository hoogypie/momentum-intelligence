# ROADMAP — MOMENTUM INTELLIGENCE
> Laatste update: 28 mei 2026
> Regel: geen nieuwe features toevoegen tijdens bug-fix sessies.

---

## FASE 1 — SCORE ENGINE ✅ COMPLEET

**Principe:** Als de scoring niet klopt, bouw je een mooi dashboard dat slechte signalen sneller toont.

- [x] Momentum Score formule (6 componenten, 100 pts)
- [x] Skip Score engine (hard vetoes + soft skips)
- [x] Skip-first architectuur
- [x] 8 mock test cases
- [x] v1.0 bugs geïdentificeerd (5/8 correct)
- [x] v1.1 kalibratie — 8/8 correct
- [x] Combinatieregel: catalyst=NONE + momentum<50 → SKIP
- [x] GitHub project structuur
- [x] MASTER_CONTEXT, ROADMAP, DECISIONS, CHANGELOG

**Deliverable:** `scoring/scoring_v1_1.py`

---

## FASE 2 — PYTHON BACKEND 🔲 NEXT

**Principe:** Echte data ophalen en score engine aanroepen. Geen AI in de scoringsketen.

### 2a — Projectstructuur
- [ ] `main.py` — FastAPI app entry point
- [ ] `data/__init__.py`
- [ ] `data/yahoo.py` — Yahoo Finance: prijs, volume, premarket, market cap
- [ ] `data/finnhub.py` — nieuws headlines + sentiment
- [ ] `data/stocktwits.py` — mention velocity
- [ ] `data/assembler.py` — bouwt TickerInput van losse data sources
- [ ] `config/sectors.json` — sector heat config (handmatig, wekelijks)
- [ ] `.env.example` — API keys template (nooit `.env` committen)
- [ ] `requirements.txt`

### 2b — API Endpoints
- [ ] `GET /score/{ticker}` — volledige scoring pipeline
- [ ] `GET /sector/{id}` — sector heat + leaders
- [ ] `GET /sympathy/{ticker}` — sympathy play lijst
- [ ] `GET /watchlist?tickers=A,B,C` — batch scoring

### 2c — Validatie
- [ ] Dezelfde 8 test cases draaien met live data
- [ ] Mock vs. live scores vergelijken
- [ ] Edge cases: weekend, halted stocks, delisted, geen Finnhub data

### 2d — Aandachtspunten
- Float data: Yahoo Finance geeft dit niet altijd — fallback logica nodig
- Pre-market data: alleen beschikbaar voor/na markturen
- Rate limiting: Finnhub 60 calls/min gratis — batch requests spreiden

**Deliverable:** Werkende lokale server op `localhost:8000`

---

## FASE 3 — DASHBOARD UPGRADE 🔲 LATER

**Principe:** UI volgt data, niet andersom.

- [ ] HTML prototype omzetten naar React + Vite
- [ ] API calls naar `localhost:8000`
- [ ] Sector config dynamisch laden uit backend
- [ ] Momentum Score breakdown zichtbaar (per component)
- [ ] Skip Score prominenter dan Momentum Score in UI
- [ ] AI Narrative: Claude legt score uit via `/explain` endpoint
- [ ] Geen hardcoded data meer
- [ ] Daily Checklist behouden

**Deliverable:** React dashboard op `localhost:3000` gekoppeld aan backend

---

## FASE 4 — DEPLOYMENT 🔲 LATER

**Principe:** Toegankelijk op telefoon zonder lokale server.

- [ ] Vercel deployment frontend
- [ ] Railway of Render voor Python backend
- [ ] Environment variables voor API keys (nooit in code)
- [ ] CORS configuratie
- [ ] Basic auth (persoonlijk gebruik)

**Deliverable:** Bereikbaar op `momentum.[domein].app`

---

## FASE 5 — DATA UITBREIDING 🔲 OPTIONEEL

**Beslissingscriterium:** Pas investeren als engine aantoonbaar goede signalen geeft gedurende 4 weken live gebruik.

- [ ] Unusual Whales ($30/mo) — volume anomalies, options flow, dark pool
- [ ] Backtesting framework — historische validatie score engine
- [ ] Sector heat automatisch berekend (niet handmatig)
- [ ] Float data integratie (Finviz Elite $25/mo of scraping)
- [ ] Pre-market scanner (dagelijks 08:00 CET geautomatiseerd)

**Deliverable:** Betere signaalnauwkeurigheid, meetbaar via backtesting

---

## FASE 6 — MULTI-AGENT PIPELINE 🔲 VEEL LATER

**Principe:** Eerst één goed werkend systeem. Pas dan uitbreiden.

```
News Agent     → Finnhub/Benzinga headlines ophalen + categoriseren
Narrative Agent → Catalyst type bepalen, sector mapping
Scoring Agent  → score_ticker() aanroepen
Risk Agent     → Skip Score review, portfolio impact check
Review Agent   → ChatGPT challenge: institutioneel of hype?
Ranking Agent  → Top-5 dagelijkse candidates
```

**Tools:** LangChain, CrewAI, of AutoGen — keuze afhankelijk van complexiteit.
**Verwachte timing:** Als fase 1-5 volledig werken en bewezen waarde leveren.

---

## PRIORITEITSREGELS

1. Werkt de score engine correct? → Fase 1 eerst ✅
2. Klopt de data? → Fase 2 nu
3. Ziet het er goed uit? → Fase 3
4. Overal toegankelijk? → Fase 4
5. Betere signalen? → Fase 5 als bewezen nodig
6. Geautomatiseerd? → Fase 6 als alles werkt
