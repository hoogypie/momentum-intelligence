# ARCHITECTURE — MOMENTUM INTELLIGENCE
> Laatste update: 28 mei 2026 | v1.2

---

## 1. KERNPRINCIPES

```
1. Skip Score gaat ALTIJD vóór Momentum Score
2. Data berekent score — AI legt score uit — nooit andersom
3. Sector config = JSON, nooit hardcoded in applicatiecode
4. API keys nooit in frontend of gecommit naar Git
5. Geen feature toevoegen tijdens bug-fix sessie
```

---

## 2. SYSTEEM OVERZICHT

```
┌─────────────────────────────────────────────────────────────┐
│                    DATA LAAG (fase 2)                       │
│  Yahoo Finance   Finnhub News   StockTwits   sectors.json   │
└──────────────┬──────────────┬──────────────┬───────────────┘
               │              │              │
               ▼              ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                  ASSEMBLER (fase 2)                         │
│  data/assembler.py → bouwt TickerInput van losse bronnen    │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  SCORE ENGINE (fase 1 ✅)                    │
│                                                             │
│  ┌────────────────────────────────────────────────────┐     │
│  │ SKIP SCORE (draait ALTIJD eerst)                   │     │
│  │   Hard vetoes: SEC / Class Action / CFD → BLOCKED  │     │
│  │   Soft skips: dag>40% / volume< / no catalyst      │     │
│  │   Score ≥ 100 → BLOCKED  |  Score ≥ 50 → SKIP     │     │
│  └────────────────────┬───────────────────────────────┘     │
│                       │ alleen als Skip < 50                │
│  ┌────────────────────▼───────────────────────────────┐     │
│  │ MOMENTUM SCORE (0-100)                             │     │
│  │   Volume Anomaly    22 pts                         │     │
│  │   Sector Heat       18 pts  ← uit sectors.json     │     │
│  │   Catalyst Quality  20 pts                         │     │
│  │   Premarket         14 pts                         │     │
│  │   Relative Strength 10 pts                         │     │
│  │   Social Accel.      8 pts  ← quality cap          │     │
│  │   Float Score        8 pts  ← nieuw v1.2           │     │
│  └────────────────────┬───────────────────────────────┘     │
│                       │                                     │
│  ┌────────────────────▼───────────────────────────────┐     │
│  │ DECISION ENGINE                                    │     │
│  │   Phase Detection (ACCUMULATION→EXHAUSTION)        │     │
│  │   Market Cap Tier (MICRO/SMALL/MID/LARGE)          │     │
│  │   Sizing = min(decision range, tier cap)           │     │
│  └────────────────────────────────────────────────────┘     │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  API LAAG (fase 2)                          │
│  FastAPI  GET /score/{ticker}  GET /sector/{id}             │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  AI NARRATIVE (fase 3)                      │
│  Claude API: legt score UIT — berekent hem NIET             │
│  Input:  "UMAC score 95, volume 12x, catalyst: DoD deal"    │
│  Output: 2 zinnen contextuele uitleg                        │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                  DASHBOARD (fase 3)                         │
│  React frontend → calls localhost:8000 (of Vercel)         │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. BESTANDSSTRUCTUUR

```
momentum-intelligence/
├── README.md
├── MASTER_CONTEXT.md          Source of truth voor elke sessie
├── ROADMAP.md                 Fase planning
├── DECISIONS.md               Architectuurkeuzes met rationale
├── CHANGELOG.md               Alle wijzigingen
├── requirements.txt
├── .gitignore                 .env nooit committen
│
├── config/
│   └── sectors.json           Sector heat, leaders, sympathy map
│                              Handmatig updaten, wekelijks
│
├── docs/
│   ├── ARCHITECTURE.md        Dit bestand
│   ├── SCORE_ENGINE.md        Technische spec
│   ├── MOMENTUM_FRAMEWORK.md  Trading strategie
│   ├── ANTI_GOALS.md          Wat dit systeem NIET is
│   └── KNOWN_FAILURE_MODES.md Waar het systeem faalt
│
├── scoring/
│   ├── __init__.py
│   ├── scoring_v1_1.py        Vorige versie (archief)
│   └── scoring_v1_2.py        Huidige engine
│
├── tests/
│   └── __init__.py            Fase 2: test_scoring.py hier
│
└── (fase 2 toevoegingen)
    ├── main.py                FastAPI entry point
    └── data/
        ├── yahoo.py           Prijs/volume ophalen
        ├── finnhub.py         Nieuws/sentiment
        ├── stocktwits.py      Social mentions
        └── assembler.py       Bouwt TickerInput van alle bronnen
```

---

## 4. DATAFLOW PER TICKER (fase 2)

```python
# Fase 2 assembler.py (nog niet gebouwd)
async def build_ticker_input(ticker: str) -> TickerInput:
    price    = await yahoo.get_quote(ticker)
    news     = await finnhub.get_news(ticker)
    social   = await stocktwits.get_mentions(ticker)
    sector   = load_sector_config(ticker_to_sector(ticker))

    return TickerInput(
        ticker          = ticker,
        price           = price.last,
        day_change_pct  = price.change_pct,
        premarket_pct   = price.premarket_pct,
        volume_today    = price.volume,
        avg_volume_20d  = price.avg_volume_20d,
        market_cap_usd  = price.market_cap,
        float_shares    = price.float_shares,   # soms None
        is_cfd_only     = False,                # handmatig of T212 API
        catalyst_type   = classify_catalyst(news),
        catalyst_description = news[0].headline if news else "Geen nieuws",
        relative_strength = calc_rs(price, spy_return),
        sector          = sector,
        social_mentions_today = social.today,
        social_mentions_avg   = social.avg_20d,
        has_sec_investigation = check_sec_flag(ticker),
        has_class_action      = check_class_action(ticker),
        insider_sells_90d     = price.insider_sells_90d,
    )
```

De `score_ticker()` functie verandert niet. Alleen de input verandert.

---

## 5. API SECURITY (fase 2+)

```
❌ NOOIT API keys in frontend code
❌ NOOIT .env committen naar Git
❌ NOOIT Anthropic API key in browser-requests

✅ API keys in .env bestand (staat in .gitignore)
✅ Backend als proxy: frontend → backend → externe API
✅ Vercel environment variables voor deployment
```

---

## 6. REVIEW PIPELINE

```
Finnhub/Yahoo data
       ↓
Claude — categoriseert, bouwt, implementeert
       ↓
Score Engine — algoritmische score
       ↓
ChatGPT — review: "is dit institutioneel of hype?"
       ↓
Igor — definitief oordeel + trade beslissing
```

GitHub repo = gedeeld geheugen tussen Claude en ChatGPT.
Igor = altijd de laatste schakel.
