# MASTER CONTEXT — MOMENTUM INTELLIGENCE
> Source of truth. Begin elke sessie: "Lees MASTER_CONTEXT.md en DECISIONS.md. We zijn op fase [N]."
> Laatste update: 28 mei 2026 | v2.1 | Igor × Claude

---

## 1. PROJECT
Persoonlijke momentum intelligence tool. Doel: tomorrow's movers vinden vóór retail.
Principe: Data berekent score. AI legt uit. Nooit andersom.

## 2. TEAM
| Rol | Agent | Verantwoordelijkheid |
|---|---|---|
| Product Owner | Igor | Richting, priorities, definitief oordeel |
| Reviewer | ChatGPT | Risk, edge-analyse, hype vs. institutioneel |
| Builder | Claude | Code, architectuur, tests, documentatie |
| Arbiter | Igor | Altijd de laatste schakel |

## 3. ARCHITECTUUR (v2.1)
```
Yahoo Finance → TickerSnapshot (retry+confidence) → Assembler → TickerInput
                                                                      ↓
                                                              Score Engine v1.2
                                                                      ↓
                                                            ScoringResponse (Pydantic)
                                                                      ↓
                                                          FastAPI GET /analyze/{ticker}
```

## 4. SCORE ENGINE v1.2
Momentum Score (100 pts): Volume(22) + Heat(18) + Catalyst(20) + Premarket(14) + RS(10) + Social(8, capped) + Float(8)
Skip Score: SEC/CFD/ClassAction=+100 BLOCKED | dag>40%=+40 | pm>40%=+40 | vol<avg=+25 | geen cat=+20
Combinatieregel: catalyst=NONE + momentum<50 → SKIP
Social cap: NONE=2 | WEAK=4 | MODERATE=6 | STRONG=8

## 5. DATA STABILITY (v2.1)
**Schemas:** TickerSnapshot, ScoringResponse, SectorState, ApiError (Pydantic)
**DataConfidence:** LIVE | DELAYED (v2.2) | PARTIAL | MISSING
**Retry:** 3x, backoff 0s/0.5s/1.5s, rate limit → stop direct
**Missing fields:** market_cap=None→$1B default | float=None→4pts neutraal | volume=0→avg fallback
**Cache:** architectuur klaar in cache/market_cache.py, DISABLED (v2.2)

## 6. TECH STACK
```
Score Engine:  scoring/scoring_v1_2.py
Schemas:       schemas/ (Pydantic v2)
Backend:       backend/app.py (FastAPI)
Data:          data/yahoo_client.py (yfinance + retry)
               data/news_client.py (placeholder → Finnhub v2.2)
               data/assembler.py (TickerInput builder)
Cache:         cache/market_cache.py (DISABLED)
Config:        config/sectors.json (wekelijks updaten)
```

## 7. TESTS
```
tests/test_scoring.py       70  Engine unit + regressie
tests/test_backend.py       36  API endpoints (gemockt)
tests/test_data_stability.py 53  Schemas, retry, missing data, cache
Total:                      159  ✅ zonder netwerk
```

## 8. HUIDIGE STATUS
Fase 1 ✅ Score engine v1.3 (70 tests)
Fase 2 ✅ Backend v2.1 (159 tests totaal)
Fase 3 🔲 Dashboard (next)
Fase 4 🔲 Deployment

## 9. KERNREGELS
1. Skip Score gaat altijd vóór Momentum Score
2. Social kan NOOIT alleen tot BUY leiden
3. Data berekent score, AI legt uit — nooit andersom
4. get_snapshot() gooit nooit een exception — altijd TickerSnapshot
5. Elk veld heeft een graceful fallback — engine scoort altijd
6. API keys nooit in git — .env in .gitignore
7. Elke wijziging: CHANGELOG + DECISIONS bijwerken
8. Geen features tijdens bug-fix/stability sessies
