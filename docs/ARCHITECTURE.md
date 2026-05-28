# ARCHITECTURE — MOMENTUM INTELLIGENCE
> Laatste update: 28 mei 2026 | v2.2

---

## 1. KERNPRINCIPES

```
1. Score engine is deterministisch en gescheiden van cache/data layer
2. Skip Score gaat altijd vóór Momentum Score
3. Data berekent score — AI legt uit — nooit andersom
4. get_snapshot() gooit nooit een exception — altijd TickerSnapshot
5. Confidence label communiceert data-kwaliteit transparant
6. Cache beschermt motor, misleidt nooit — STALE is zichtbaar voor gebruiker
```

---

## 2. VOLLEDIG SYSTEEM DIAGRAM (v2.2)

```
┌─────────────────────────────────────────────────────────────────┐
│                    DATA LAAG                                     │
│                                                                  │
│  Yahoo Finance ──(retry+backoff)──► _fetch_once()               │
│                                          │                       │
│                                          ▼                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ CACHE (in-memory, market-hours TTL)                       │  │
│  │  LIVE <300s │ DELAYED <3600s │ STALE <7200s │ dan weg    │  │
│  │  Cache hit → TickerSnapshot(cache_hit=True, age=N)        │  │
│  │  Yahoo faalt → stale fallback of MISSING                  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                          │                       │
│  Finnhub (placeholder) ──────────────────┤                       │
│  StockTwits (placeholder) ───────────────┤                       │
│  sectors.json ──────────────────────────►│                       │
└──────────────────────────────────────────┼──────────────────────┘
                                           │
                                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    ASSEMBLER                                     │
│  TickerSnapshot → classify_catalyst() → classify_rs()           │
│  Missing fields → graceful defaults                             │
│  Returns: (TickerInput, DataQuality)                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    SCORE ENGINE v1.2                            │
│  (deterministisch — geen cache, geen AI)                        │
│                                                                 │
│  Skip Score ──► ≥100? BLOCKED │ ≥50? SKIP │ combo? SKIP       │
│       │                                                         │
│       ▼ (alleen als Skip < 50)                                  │
│  Momentum Score (Volume+Heat+Catalyst+Premarket+RS+Social+Float)│
│       │                                                         │
│       ▼                                                         │
│  Phase Detection + Market Cap Tier + Sizing                     │
│       │                                                         │
│       ▼                                                         │
│  ScoringResult (dataclass)                                      │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    API LAAG (FastAPI v2.2)                      │
│                                                                 │
│  GET /health                     → HealthResponse              │
│  GET /analyze/{ticker}           → ScoringResponse             │
│  GET /analyze/{ticker}?refresh   → (cache bypass)             │
│  GET /analyze?tickers=A,B,C      → BatchScoringResponse        │
│  GET /sector/{sector_name}       → SectorSnapshotResponse      │
│                                                                 │
│  Errors: ApiError schema (400/422/429/500)                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. CACHE FLOW DETAIL

```
get_snapshot(ticker, force_refresh=False)
    │
    ├─ force_refresh=True? ──────────────────────────────► LIVE FETCH
    │
    ├─ CACHE_ENABLED=True?
    │     │
    │     └─ get_cached(ticker)
    │           │
    │           ├─ Entry aanwezig + niet te oud? ─► TickerSnapshot(cache_hit=True)
    │           │                                    confidence = worst(field, age)
    │           │
    │           └─ Miss/verlopen ──────────────────► LIVE FETCH
    │                                                    │
    │                                              ┌─────┴──────┐
    │                                           success?      fail?
    │                                              │             │
    │                                        set_cached()   cache fallback?
    │                                              │             │
    │                                        LIVE snap      DELAYED/STALE snap
    │                                                        (of MISSING)
    └─ CACHE_ENABLED=False ──────────────────────────────► LIVE FETCH (altijd)
```

---

## 4. DATACONFIDENCE MATRIX

| Situatie | Confidence |
|---|---|
| Alle velden aanwezig, data < 5 min oud | LIVE |
| Data 5-60 min oud (cache hit) | DELAYED |
| Data 1-2 uur oud (stale fallback) | STALE |
| Prijs aanwezig, ≥2 optionele velden ontbreken | PARTIAL |
| Prijs nul of ophaalfout, geen cache | MISSING |

`worst_confidence(field_conf, age_conf)` → eindlabel voor gebruiker.

---

## 5. BESTANDSSTRUCTUUR (v2.2)

```
momentum-intelligence/
├── backend/app.py              FastAPI — 4 endpoints
├── cache/market_cache.py       In-memory cache, TTL, cooldowns
├── data/
│   ├── yahoo_client.py         Cache+retry+fallback
│   ├── news_client.py          Placeholder (fase 2.2: Finnhub)
│   └── assembler.py            TickerInput builder
├── schemas/
│   ├── ticker_snapshot.py      TickerSnapshot + DataConfidence + FreshnessInfo
│   ├── scoring_response.py     ScoringResponse + Batch + Sector schemas
│   ├── sector_state.py         SectorState
│   └── api_error.py            ApiError + ErrorCode
├── scoring/scoring_v1_2.py     Score engine (deterministisch)
├── config/sectors.json         Sector config (wekelijks updaten)
└── tests/
    ├── test_scoring.py          70  engine
    ├── test_backend.py          36  API
    ├── test_data_stability.py   55  schemas + stability
    └── test_cache.py            74  cache + batch + sector
```

---

## 6. API SECURITY

```
❌ NOOIT API keys in frontend of git
❌ NOOIT .env committen
✅ .env in .gitignore
✅ Vercel environment variables voor deployment
✅ Backend als proxy — keys nooit in browser
```
