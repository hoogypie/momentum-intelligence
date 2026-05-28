# MASTER CONTEXT — MOMENTUM INTELLIGENCE
> Source of truth voor sessiebrug. Upload dit + DECISIONS.md bij nieuwe sessie.
> Begin sessie: "Lees MASTER_CONTEXT.md en DECISIONS.md. We zijn op v2.9."
> Laatste update: 28 mei 2026 | v2.9 | Igor x Claude

---

## 1. PROJECT

Persoonlijk momentum-intelligence tool. Detecteert early-stage market momentum
voor US equities via volume anomaly, sector heat, catalyst quality en RS.

Kernprincipe: Data berekent score. AI legt uit. Nooit andersom.
Score engine: Deterministisch -- zelfde input = zelfde output, altijd.

## 2. HUIDIG VERSIENIVEAU: v2.9

Versiegeschiedenis:
- v2.9: Alerting & Watchlist Layer
- v2.8: Documentation & Operating Manual
- v2.7: Signal Evaluation Layer
- v2.6: Replay & Observation Tooling
- v2.5: Historical Memory Layer
- v2.4: Real Signal Expansion (Finnhub, MarketSession)
- v2.3: API Polish & Developer Experience
- v2.2: Caching & Data Freshness
- v2.1: Data Stability (Pydantic schemas)
- v2.0: FastAPI backend

## 3. ARCHITECTUUR (v2.9)

```
Yahoo Finance -> TickerSnapshot <-> Cache -> Assembler -> TickerInput
                                                              |
                                              Score Engine v1.2 (deterministisch)
                                                              |
                                                      ScoringResponse (Pydantic)
                                                              |
                                              FastAPI -- 30+ endpoints
                                                              |
                                 Storage (JSON Lines) -> Replay -> Evaluation -> Alerts
```

## 4. SCORE ENGINE v1.2 (ONVERANDERD SINDS v1.3)

Momentum (100 pts): Volume(22)+Heat(18)+Catalyst(20)+Premarket(14)+RS(10)+Social(8,capped)+Float(8)
Skip: SEC/CFD/ClassAction=+100 | dag>40%=+40 | pm>40%=+40 | vol<avg=+25 | geen cat=+20
Combo: catalyst=NONE + momentum<50 -> SKIP
Social cap: NONE=2 | WEAK=4 | MODERATE=6 | STRONG=8
Decisions: >=90=BUY_MAX | >=75=BUY_STRONG | >=60=BUY_MODERATE | >=45=BUY_SMALL | >=30=WATCH

## 5. ALERT TRIGGERS (v2.9)

- momentum_threshold: Score kruist decision-grens
- phase_transition:   Fase verandert (NEUTRAL->BREAKOUT)
- sector_heat_spike:  Sector heat stijgt >10 punten
- volume_anomaly:     Volume ratio > drempelwaarde
- confidence_downgrade: Confidence verslechtert
- buy_max_signal:     Score >= 90
- score_drop:         Score daalt >15 punten
- evaluation_insight: Historisch patroon

Severity: INFO | WATCH | HIGH | CRITICAL
Cooldowns: CRITICAL=30min | HIGH=60min | WATCH=120min | INFO=240min

## 6. STORAGE STRUCTUUR

```
storage/data/tickers/{TICKER}.jsonl         snapshots
storage/data/tickers/{TICKER}_transitions.jsonl
storage/data/tickers/{TICKER}_catalysts.jsonl
storage/data/sectors/{SECTOR}.jsonl         sector history
storage/data/evaluations/{TICKER}.jsonl     signal grades
storage/data/alerts/history.jsonl           alert history
storage/data/alerts/cooldowns.json          cooldown state
watchlists/core.json                        kernposities
watchlists/momentum.json                    momentum setups
watchlists/sector_rotation.json
watchlists/custom/*.json
research/observations/                      handmatige notes
research/replay_notes/                      auto-generated
research/signal_reviews/                    exports
```

## 7. SIGNAL DECAY

```
FRESH   (0-2u)   -> 1.00x | geen downgrade
AGING   (2-8u)   -> 0.85x | geen downgrade
STALE   (8-24u)  -> 0.65x | 1 stap lager
OLD     (24-48u) -> 0.40x | altijd WATCH
EXPIRED (>48u)   -> 0.00x | altijd SKIP
FRENZY extra decay: x0.70 bij AGING/STALE
```

## 8. EVALUATIE GRADES

```
BUY signalen: SUCCESS >=+3% / FAILED <=-3% / NEUTRAL tussenin
SKIP/BLOCKED: SUCCESS <=-2% (prijs daalde) / FAILED >=+2%
WATCH: altijd NEUTRAL
Horizons: 1d -> 4h -> 1h -> PENDING
```

## 9. TECH STACK

```
Score Engine:  scoring/scoring_v1_2.py (pure Python)
Schemas:       schemas/ (Pydantic v2)
Cache:         cache/market_cache.py (in-memory, TTL)
Backend:       backend/app.py (FastAPI v2.9, 30+ endpoints)
Data:          data/yahoo_client.py + news_client.py + assembler.py
Storage:       storage/ (7 modules -- JSON Lines)
Replay:        storage/replay_engine.py + snapshot_diff.py + timeline.py
Evaluation:    storage/evaluation_store.py + signal_evaluator.py
Alerting:      alerting/alert_engine.py + alert_store.py + cooldown_manager.py + watchlist_manager.py
Watchlists:    watchlists/*.json
Research:      research/observation_store.py + evaluation_report.py
Scripts:       scripts/smoke_test.py + run_backend.py + export_snapshots.py
Config:        config/sectors.json
Docs:          docs/OPERATING_MANUAL.md
```

## 10. TESTS

```
test_scoring.py         70   Engine unit + regressie
test_backend.py         36   API endpoints (gemockt)
test_data_stability.py  55   Schemas, retry, missing data
test_cache.py           74   Cache, batch, sector
test_signals.py         57   MarketSession, Finnhub, sector intelligence
test_history.py         63   Storage, decay, transitions
test_replay.py          60   Diff, timeline, replay engine
test_evaluation.py      64   Grades, statistics, evaluation
test_dev_experience.py  51   OpenAPI, smoke, logging, DX
test_alerting.py        69   Alerts, watchlists, cooldowns, triggers
Total:                 599   OK zonder netwerk
```

## 11. OPSTARTEN

```bash
pip install -r requirements.txt
uvicorn backend.app:app --reload --port 8000
python3 scripts/smoke_test.py
```

## 12. KERNREGELS (voor Claude)

1. Skip Score gaat altijd voor Momentum Score
2. Social kan NOOIT alleen tot BUY leiden
3. Score engine is deterministisch -- nooit aanraken zonder tests
4. get_snapshot() gooit nooit een exception
5. Evaluatie beinvloedt scoring nooit direct
6. Replay-laag leest storage -- raakt scoring nooit aan
7. Alerting observeert signalen -- raakt scoring nooit aan
8. API keys nooit in git -- .env in .gitignore
9. Elke wijziging: CHANGELOG + DECISIONS bijwerken
10. MASTER_CONTEXT.md = sessiebrug -- altijd bijhouden
