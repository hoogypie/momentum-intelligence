# MASTER CONTEXT — MOMENTUM INTELLIGENCE
> Source of truth voor elke Claude én ChatGPT sessie.
> Begin elke sessie: "Lees MASTER_CONTEXT.md en DECISIONS.md. We zijn op fase [N]."
> Laatste update: 28 mei 2026 | v1.2 | Igor × Claude

---

## 1. PROJECT

**Wat:** Persoonlijke momentum intelligence tool voor retail belegger.
**Doel:** "Tomorrow's movers" vinden vóór retail instap.
**Scope:** Persoonlijk gebruik, niet commercieel.
**Principe:** Data berekent score. AI legt uit. Nooit andersom.

## 2. TEAM

| Rol | Agent | Verantwoordelijkheid |
|---|---|---|
| Product Owner | Igor | Richting, priorities, definitief oordeel |
| Reviewer | ChatGPT | Risk, edge-analyse, "institutioneel of hype?" |
| Builder | Claude | Code, architectuur, tests, documentatie |
| Arbiter | Igor | Altijd de laatste schakel |

**Workflow:** Data → Claude bouwt → Score Engine → ChatGPT review → Igor beslist
**GitHub:** Gedeeld geheugen. Beide AI's lezen MASTER_CONTEXT.md als source of truth.

## 3. DRIE STRATEGIEËN

| Strategie | Max allocatie |
|---|---|
| Core Portfolio (BtB earnings) | 80-85% |
| Earnings Plays (PB-score ≥4) | 10-15% |
| **Momentum Plays (dit project)** | **5-10%** |

## 4. SCORE ENGINE v1.2 — SAMENVATTING

### Momentum Score (0-100)
| Component | Max | Noot |
|---|---|---|
| Volume Anomaly | 22 | rv=today/avg_20d |
| Sector Heat | 18 | uit config/sectors.json |
| Catalyst Quality | 20 | STRONG=20, MOD=12, WEAK=4, NONE=0 |
| Premarket Strength | 14 | sweet spot 8-20% |
| Relative Strength | 10 | groen bij rode markt=max |
| Social Acceleration | 8 | quality cap actief |
| Float Score | 8 | nieuw v1.2 |

### Social Quality Cap (v1.2)
catalyst=NONE→max 2pts | WEAK→4 | MODERATE→6 | STRONG→8

### Skip Score (altijd vóór Momentum)
SEC/Class action/CFD → +100 BLOCKED | dag>40%→+40 | pm>40%→+40 |
volume<avg→+25 | geen catalyst→+20 | >10 insider sells→+15
Skip≥100=BLOCKED | Skip≥50=SKIP | catalyst=NONE+momentum<50=SKIP

### Nieuw v1.2
- **Float Score**: <5M=8pts, <15M=6.5, <50M=4.5, <200M=2, ≥200M=0, None=4
- **Market Cap Tier**: MICRO<$300M=max€250, SMALL<$2B=max€400, MID/LARGE=max€500
- **Phase**: ACCUMULATION/BREAKOUT/EXPANSION/FRENZY/EXHAUSTION/NEUTRAL
- **SectorConfig dataclass**: sector data als expliciete input

### Tests v1.2: 11/11 ✅
Regressie 1-8 intact. Kalibratie noot: QBTS BUY_SMALL (was BUY_MODERATE v1.1, score 59.4 vs 60.4 — conservatiever, niet fout).

## 5. TECH STACK
```
Score Engine:  scoring/scoring_v1_2.py (Python, geen AI)
Config:        config/sectors.json (handmatig, wekelijks)
Backend:       Python FastAPI (fase 2)
Data:          Yahoo Finance + Finnhub + StockTwits (fase 2)
AI Narrative:  Claude API — uitleg van scores, niet berekening (fase 3)
Frontend:      React dashboard (fase 3)
```

## 6. HUIDIGE FASE
**Fase 1 COMPLEET ✅** — scoring_v1_2.py, 11/11 tests
**Fase 2 NEXT 🔲** — FastAPI backend + Yahoo Finance + Finnhub
Zie ROADMAP.md voor volledige fase-details.
Zie DECISIONS.md voor architectuurkeuzes (D-001 t/m D-010).
Zie docs/KNOWN_FAILURE_MODES.md voor bekende beperkingen.

## 7. KERNREGELS
1. Skip Score gaat altijd vóór Momentum Score
2. Social kan NOOIT alleen tot BUY leiden
3. Sector config = JSON, nooit hardcoded
4. API keys nooit in frontend of git
5. Geen features toevoegen tijdens bug-fix sessie
6. Elke wijziging eerst in CHANGELOG + DECISIONS vastleggen
