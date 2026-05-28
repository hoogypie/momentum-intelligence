# API REFERENCE — MOMENTUM INTELLIGENCE
> v2.3 | Laatste update: 28 mei 2026

---

## Overzicht

| Endpoint | Methode | Tag | Beschrijving |
|---|---|---|---|
| `/health` | GET | health | Server status + cache stats |
| `/analyze/{ticker}` | GET | analysis | Momentum score voor één ticker |
| `/analyze?tickers=...` | GET | analysis | Batch scoring, max 10 |
| `/sector/{sector_name}` | GET | sector | Sector snapshot met leaders |
| `/cache/stats` | GET | cache | Cache statistieken |
| `/cache/{ticker}` | DELETE | cache | Invalideer ticker cache |
| `/docs` | GET | — | Interactieve Swagger UI |
| `/openapi.json` | GET | — | OpenAPI schema |

**Base URL lokaal:** `http://localhost:8000`

---

## DataConfidence Labels

Elke response bevat een `confidence` veld in `data_quality`:

| Label | Betekenis | Actie |
|---|---|---|
| `LIVE` | Data < 5 min oud, alle velden aanwezig | Volledig betrouwbaar |
| `DELAYED` | Data 5-60 min oud (uit cache) | Betrouwbaar voor trending |
| `STALE` | Data 1-2 uur oud (fallback) | Gebruik met voorzichtigheid |
| `PARTIAL` | Prijs aanwezig, ≥2 optionele velden ontbreken | Score minder precies |
| `MISSING` | Geen bruikbare data | Niet scoren |

---

## GET /health

**Beschrijving:** Server status, versie-info en cache statistieken.

**Voorbeeld response:**
```json
{
  "status": "ok",
  "version": "2.3.0",
  "engine": "scoring_v1_2",
  "timestamp": "2026-05-28T21:00:00Z",
  "data_sources": {
    "price_volume": "yahoo_finance (retry+backoff+cache)",
    "news": "placeholder (fase 2.2: Finnhub)",
    "cache": "actief (CACHE_ENABLED=True)"
  },
  "limitations": [
    "catalyst_type altijd NONE (news_client placeholder)",
    "social_acceleration altijd 0"
  ],
  "cache_stats": {
    "enabled": true,
    "total_entries": 3,
    "live": 2,
    "delayed": 1,
    "stale": 0,
    "market_open": true,
    "current_ttl": 60
  }
}
```

---

## GET /analyze/{ticker}

**Beschrijving:** Volledige momentum score voor één ticker.

**Query parameters:**
- `refresh=true` — bypass cache, altijd live data

**Voorbeeld request:**
```
GET /analyze/IONQ
GET /analyze/IONQ?refresh=true
```

**Voorbeeld response (200):**
```json
{
  "ticker": "IONQ",
  "decision": "BUY_MODERATE",
  "momentum_score": 64.5,
  "skip_score": 0,
  "phase": "BREAKOUT",
  "phase_description": "Eerste breakout — catalyst bevestigd, vroeg stadium",
  "market_cap_tier": "MID",
  "sizing_eur": "€200-300",
  "summary": "IONQ: BUY_MODERATE | Momentum 64.5 | Skip 0 | Phase BREAKOUT",
  "analyzed_at": "2026-05-28T21:00:00Z",
  "momentum_detail": {
    "total": 64.5,
    "volume_anomaly": 17.6,
    "sector_heat_score": 16.6,
    "catalyst_quality": 0.0,
    "premarket_strength": 14.0,
    "relative_strength_score": 10.0,
    "social_acceleration": 2.0,
    "float_score": 4.0,
    "social_was_capped": true,
    "social_cap_reason": "catalyst=NONE → max 2pts",
    "breakdown": {
      "Volume Anomaly    (max 22)": " 17.6 — 5.0x normaal — HOOG",
      "Sector Heat       (max 18)": " 16.6 — QUANTUM COMPUTING heat 92/100 — EXPLOSIEF"
    }
  },
  "skip_detail": {
    "total": 0,
    "is_hard_blocked": false,
    "reasons": [],
    "blocking_reasons": []
  },
  "data_quality": {
    "price_available": true,
    "volume_available": true,
    "float_available": false,
    "premarket_available": true,
    "news_available": false,
    "social_available": false,
    "sec_check_automated": false,
    "confidence": "LIVE",
    "fetch_error": null,
    "retries_used": 0,
    "cache_hit": false,
    "data_age_seconds": 0.0
  }
}
```

**Decision waarden:**
`BUY_MAX` | `BUY_STRONG` | `BUY_MODERATE` | `BUY_SMALL` | `WATCH` | `SKIP` | `BLOCKED`

**Sizing per decision (zonder market cap cap):**
| Decision | Sizing |
|---|---|
| BUY_MAX | €400-500 |
| BUY_STRONG | €300-400 |
| BUY_MODERATE | €200-300 |
| BUY_SMALL | €100-200 |
| WATCH | Watchlist |
| SKIP / BLOCKED | €0 |

---

## GET /analyze?tickers=...

**Beschrijving:** Batch scoring, max 10 tickers per request.

**Query parameters:**
- `tickers` — komma-gescheiden, max 10 *(vereist)*
- `refresh=true` — cache bypass voor alle tickers

**Voorbeeld request:**
```
GET /analyze?tickers=IONQ,QBTS,RGTI
```

**Voorbeeld response (200):**
```json
{
  "tickers_requested": 3,
  "tickers_scored": 3,
  "tickers_failed": 0,
  "results": [...],
  "errors": {},
  "analyzed_at": "2026-05-28T21:00:00Z"
}
```

**Partial failure voorbeeld:**
```json
{
  "tickers_requested": 3,
  "tickers_scored": 2,
  "tickers_failed": 1,
  "results": [...],
  "errors": { "BADTICKER": "Ticker niet gevonden" }
}
```

---

## GET /sector/{sector_name}

**Beschrijving:** Sector snapshot met alle leaders gescoord.

**Beschikbare sectors:**
`quantum` | `ai_infra` | `drones_defense` | `ai_software` |
`power_energy` | `robotics` | `cybersecurity` | `ai_pc`

**Voorbeeld request:**
```
GET /sector/quantum
```

**Voorbeeld response (200):**
```json
{
  "sector_id": "quantum",
  "label": "QUANTUM COMPUTING",
  "heat": 92,
  "status": "HOT",
  "leaders_scored": [
    {
      "ticker": "IONQ",
      "decision": "BUY_MODERATE",
      "momentum_score": 64.5,
      "skip_score": 0,
      "phase": "BREAKOUT",
      "confidence": "LIVE",
      "scored": true
    }
  ],
  "sympathy": ["QUBT", "IBM", "QTEX"],
  "avg_momentum": 61.2,
  "avg_skip": 5.0,
  "sector_confidence": "LIVE",
  "analyzed_at": "2026-05-28T21:00:00Z"
}
```

---

## Error Responses

Alle fouten gebruiken het `ApiError` schema:

```json
{
  "detail": {
    "error": "INVALID_TICKER",
    "ticker": "123BAD",
    "message": "Ongeldige ticker: '123BAD'. Gebruik alleen letters.",
    "hint": "Ticker mag alleen letters bevatten."
  }
}
```

**Error codes:**
| HTTP | Code | Wanneer |
|---|---|---|
| 400 | `INVALID_TICKER` | Ticker bevat cijfers of tekens |
| 400 | `TOO_MANY_TICKERS` | Meer dan 10 tickers in batch |
| 404 | `SECTOR_NOT_FOUND` | Onbekende sector naam |
| 422 | `TICKER_NOT_FOUND` | Geen Yahoo Finance data |
| 422 | `DATA_UNAVAILABLE` | Prijs nul |
| 429 | `RATE_LIMITED` | Yahoo Finance rate limit |
| 500 | `INTERNAL_ERROR` | Onverwachte serverfout |

---

## Cache Behavior

```
Request → Cache check
  Hit (niet verlopen) → TickerSnapshot(cache_hit=True, confidence=LIVE/DELAYED/STALE)
  Miss → Yahoo Finance fetch
    Success → Cache opslaan + TickerSnapshot(cache_hit=False, confidence=LIVE)
    Fail → Stale cache fallback (als beschikbaar)
      → TickerSnapshot(cache_hit=True, confidence=DELAYED/STALE, error="fallback")
    Fail + geen cache → TickerSnapshot(confidence=MISSING, price=0)
```

**Cache TTL per marktperiode:**
| Periode | TTL |
|---|---|
| Regular (9:30-16:00 ET) | 60s |
| Pre-market (4:00-9:30 ET) | 120s |
| After hours (16:00-20:00 ET) | 300s |
| Overnight (20:00-4:00 ET) | 1800s |

---

## Interactieve Docs

Swagger UI beschikbaar op: `http://localhost:8000/docs`
ReDoc beschikbaar op: `http://localhost:8000/redoc`
