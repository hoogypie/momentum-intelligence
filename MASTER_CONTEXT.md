# MASTER CONTEXT — MOMENTUM INTELLIGENCE
> Source of truth. Begin elke sessie: "Lees MASTER_CONTEXT.md en DECISIONS.md. We zijn op fase [N]."
> Laatste update: 28 mei 2026 | v2.2 | Igor × Claude

---

## 1. PROJECT
Persoonlijke momentum intelligence tool. Doel: tomorrow's movers vinden vóór retail.
Principe: Data berekent score. AI legt uit. Nooit andersom.

## 2. TEAM
| Rol | Agent | Verantwoordelijkheid |
|---|---|---|
| Product Owner | Igor | Richting, priorities, definitief oordeel |
| Reviewer | ChatGPT | Risk, "institutioneel of hype?" |
| Builder | Claude | Code, architectuur, tests, documentatie |
| Arbiter | Igor | Altijd de laatste schakel |

## 3. ARCHITECTUUR (v2.2)
```
Yahoo Finance
    ↓ retry + backoff
TickerSnapshot ←→ Cache (in-memory, TTL, market-hours)
    ↓ cache hit/miss + confidence
Assembler → TickerInput
    ↓
Score Engine v1.2 (deterministisch, gescheiden van cache)
    ↓
ScoringResponse (Pydantic)
    ↓
FastAPI
  GET /health
  GET /analyze/{ticker}?refresh=true
  GET /analyze?tickers=A,B,C      (batch, max 10)
  GET /sector/{sector_name}       (sector snapshot)
```

## 4. SCORE ENGINE v1.2 (ongewijzigd)
Momentum(100): Volume(22)+Heat(18)+Catalyst(20)+Premarket(14)+RS(10)+Social(8,capped)+Float(8)
Skip: SEC/CFD/ClassAction=+100 | dag>40%=+40 | pm>40%=+40 | vol<avg=+25 | geen cat=+20
Combinatieregel: catalyst=NONE + momentum<50 → SKIP
Social cap: NONE=2 | WEAK=4 | MODERATE=6 | STRONG=8

## 5. DATA FRESHNESS (v2.2)
DataConfidence: LIVE(<5min) | DELAYED(5-60min) | STALE(1-2u) | PARTIAL | MISSING
Cache TTL: regular=60s | afterhours=300s | overnight=1800s | premarket=120s
Fallback: Yahoo faalt → stale cache (DELAYED/STALE) → MISSING als geen cache
worst_confidence() combineert veld-kwaliteit + leeftijd → eindlabel

## 6. FRESHNESS IN RESPONSE
```json
{
  "data_quality": {
    "confidence": "LIVE",
    "cache_hit": false,
    "data_age_seconds": 0.0,
    "retries_used": 0
  }
}
```

## 7. TECH STACK
```
Score Engine:  scoring/scoring_v1_2.py (pure Python)
Schemas:       schemas/ (Pydantic v2)
Cache:         cache/market_cache.py (actief, in-memory)
Backend:       backend/app.py (FastAPI v2.2)
Data:          data/yahoo_client.py (cache+retry)
               data/news_client.py (placeholder)
               data/assembler.py (TickerInput builder)
Config:        config/sectors.json (wekelijks)
```

## 8. TESTS
```
tests/test_scoring.py         70  Engine unit + regressie
tests/test_backend.py         36  API endpoints (gemockt)
tests/test_data_stability.py  55  Schemas, retry, missing data
tests/test_cache.py           74  Cache, batch, sector, freshness
Total:                       235  ✅ zonder netwerk
```

## 9. OPSTARTEN
```bash
pip install -r requirements.txt
uvicorn backend.app:app --reload --port 8000
curl http://localhost:8000/health
curl http://localhost:8000/analyze/NVDA
curl "http://localhost:8000/analyze?tickers=IONQ,QBTS,RGTI"
curl http://localhost:8000/sector/quantum
```

## 10. KERNREGELS
1. Skip Score gaat altijd vóór Momentum Score
2. Social kan NOOIT alleen tot BUY leiden
3. Score engine is deterministisch, volledig gescheiden van cache/data
4. get_snapshot() gooit nooit een exception
5. Elk veld heeft graceful fallback — engine scoort altijd
6. Confidence label communiceert databeperkingen — gebruiker beslist
7. API keys nooit in git — .env in .gitignore
8. Elke wijziging: CHANGELOG + DECISIONS bijwerken
