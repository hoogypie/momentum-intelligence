# MASTER CONTEXT — MOMENTUM INTELLIGENCE
> **Source of truth voor elke Claude-sessie.**
> Begin elke sessie met: *"Lees MASTER_CONTEXT.md en DECISIONS.md. We zijn op fase [N]."*
>
> Laatste update: 28 mei 2026 | Igor × Claude

---

## 1. PROJECT DEFINITIE

**Wat:** Persoonlijke momentum intelligence tool voor retail belegger.
**Doel:** "Tomorrow's movers" detecteren vóór retail instap — niet reageren op stocks die al +40% staan.
**Scope:** Persoonlijk gebruik, niet commercieel.
**Filosofie:** Data berekent score. AI legt score uit. Nooit andersom.

---

## 2. TEAM & WORKFLOW

| Rol | Agent | Verantwoordelijkheid |
|---|---|---|
| **Product Owner** | Igor | Richting, priorities, definitief oordeel |
| **Strategist / Reviewer** | ChatGPT | Risk, edge-analyse, systeemkritiek, hype vs. institutioneel |
| **Builder** | Claude | Code, architectuur, implementatie, tests, documentatie |
| **Arbiter** | Igor | Laatste schakel — altijd |

**Pipeline:**
```
Data (Finnhub/Yahoo) → Claude categoriseert + bouwt → Score Engine → ChatGPT review → Igor beslissing
```

**Gedeeld geheugen:** GitHub repo = source of truth voor beide AI-modellen.

**Regel:** Claude bouwt niet buiten scope. Elke sessie start met MASTER_CONTEXT + DECISIONS lezen.

---

## 3. DRIE STRATEGIEËN — PORTFOLIO CONTEXT

| Strategie | Beschrijving | Max allocatie |
|---|---|---|
| **Core Portfolio** | Seculiere groei, Beat-the-Beat earnings | 80-85% |
| **Earnings Plays** | BtB framework, PB-score ≥ 4/6 vereist | 10-15% |
| **Momentum Plays** | Dit project — hot money detectie | 5-10% |

Momentum plays worden **nooit geüpgraded naar Core** zonder volledige BtB-analyse.

---

## 4. SCORE ENGINE v1.1 — VOLLEDIGE SPECIFICATIE

### 4a. Momentum Score (0-100 punten)

| Component | Max | Berekening |
|---|---|---|
| Volume Anomaly | 25 | rv = today/avg_20d: ≥8x=25, ≥5x=20, ≥3x=15, ≥2x=10, ≥1x=5, <1x=0 |
| Sector Heat | 20 | (sector_heat / 100) × 20 — uit config/sectors.json |
| Catalyst Quality | 20 | STRONG=20, MODERATE=12, WEAK=4, NONE=0 |
| Premarket Strength | 15 | 8-20%=15, 20-40% lineair 15→5, ≥40%=0, 3-8%=8, 0-3%=3, <0%=0 |
| Relative Strength | 10 | Groen bij rode markt=10, outperform=7, neutraal=3, underperform=0 |
| Social Acceleration | 10 | velocity=today/avg: ≥10x=10, ≥5x=8, ≥2x=5, ≥1x=2, <1x=0 |

### 4b. Skip Score (blokkeert altijd vóór Momentum Score)

| Trigger | Punten | Type |
|---|---|---|
| SEC investigation actief | +100 | HARD VETO — onmiddellijk BLOCKED |
| Class action lopend | +100 | HARD VETO — onmiddellijk BLOCKED |
| CFD-only op T212 | +100 | HARD VETO — onmiddellijk BLOCKED |
| Dag >40% stijging | +40 | Soft skip |
| Premarket >40% | +40 | Soft skip |
| Volume < gemiddelde (<0.8x) | +25 | Soft skip |
| Geen catalyst (48u) | +20 | Soft skip |
| >10 insider sells 90d | +15 | Soft skip (Spelregel 13) |
| 6-10 insider sells 90d | +8 | Soft skip — monitor |

**Drempels:**
- Skip Score ≥ 100 → **BLOCKED** (hard veto, toon reden)
- Skip Score ≥ 50 → **SKIP** (toon reden)

### 4c. Combinatieregel (geïntroduceerd v1.1)
`catalyst = NONE` **AND** `momentum_score < 50` → altijd **SKIP**, ook als Skip Score < 50.

### 4d. Decision Mapping

| Momentum | Decision | Sizing |
|---|---|---|
| 90-100 | BUY_MAX | €400-500 |
| 75-89 | BUY_STRONG | €300-400 |
| 60-74 | BUY_MODERATE | €200-300 |
| 45-59 | BUY_SMALL | €100-200 |
| 30-44 | WATCH | Watchlist |
| <30 | SKIP | €0 |

### 4e. Test Suite Status (v1.1 — 8/8 geslaagd)

| Test | Scenario | Verwacht | Resultaat |
|---|---|---|---|
| UMAC_TEST1 | Explosief, geen flags | BUY_MAX | ✅ BUY_MAX (98.6) |
| APP_TEST2 | SEC investigation | BLOCKED | ✅ BLOCKED |
| SPACE_TEST3 | CFD-only | BLOCKED | ✅ BLOCKED |
| SNOW_TEST4 | Pre-earnings (BtB, geen momentum) | BUY_SMALL | ✅ BUY_SMALL (56.6) |
| CHASER_TEST5 | +42% dag — te laat | SKIP | ✅ SKIP (Skip=55) |
| SLEEPER_TEST6 | Sleeping giant, vroeg signaal | BUY_SMALL | ✅ BUY_SMALL (59.0) |
| HYPE_TEST7 | Pure social hype, geen catalyst | SKIP | ✅ SKIP (combinatieregel) |
| QBTS_TEST8 | Quantum sympathy play | BUY_MODERATE | ✅ BUY_MODERATE (60.4) |

---

## 5. TECH STACK

```
Frontend:      React (prototype: HTML/JS dashboard v1)
Backend:       Python FastAPI (lokaal fase 2, Vercel fase 4)
Score Engine:  scoring/scoring_v1_1.py (pure Python, geen AI)
Data Laag:     Yahoo Finance → Finnhub → Unusual Whales (later)
AI Narrative:  Claude API (uitleg van scores, niet berekening)
Config:        config/sectors.json (handmatig, wekelijks)
Database:      Nog niet — later Supabase free tier
```

---

## 6. DATA BRONNEN & ABONNEMENTEN

| Bron | Kosten | Fase | Wat |
|---|---|---|---|
| Yahoo Finance (unofficial) | Gratis | 2 | Prijs, volume, premarket, market cap |
| Finnhub free | Gratis | 2 | Nieuws headlines (60 calls/min) |
| StockTwits API | Gratis | 2 | Social mention velocity |
| Unusual Whales | $30/mo | 5 (optioneel) | Volume anomalies, options flow |
| Polygon.io Standard | $29/mo | 5 (optioneel) | Real-time WebSocket data |

**Beslissingscriterium Unusual Whales:** Pas abonneren als engine aantoonbaar goede signalen geeft op gratis data over 4 weken live gebruik.

---

## 7. MOMENTUM FRAMEWORK KERN PRINCIPES

1. Skip Score gaat **altijd** voor Momentum Score
2. Data berekent score, AI **legt uit** — nooit andersom
3. Niet kopen wat al >40% staat — wacht op consolidatie
4. Sector eerst → leaders → sympathy plays
5. Volume anomaly is het sterkste vroege signaal
6. Sleeping giants > viral hype (voor alpha)
7. Catalyst kwaliteit onderscheidt institutioneel van hype
8. CFD-only = altijd skip (Spelregel 29 FRAMEWORK.docx)
9. SEC/class action = hard blocked (Spelregel 3 FRAMEWORK.docx)
10. Max 3 momentum plays tegelijk open

---

## 8. BEKENDE BEPERKINGEN

| Beperking | Impact | Fix in fase |
|---|---|---|
| Sector heat is handmatig | Kan verouderen | 5 |
| Geen backtesting framework | Kan score niet historisch valideren | 5 |
| Float data niet gratis | Sizing minder precies | 2 onderzoeken |
| Pre-market data Yahoo heeft vertraging | Signaal iets laat | 2 |
| Social velocity vereist StockTwits API | Nu geschat | 2 |

---

## 9. HUIDIGE FASE

**Fase 1 COMPLEET ✅** — scoring_v1_1.py, 8/8 tests.
**Fase 2 NEXT 🔲** — Python FastAPI backend + Yahoo Finance + Finnhub.

Zie ROADMAP.md voor volledige fase-details.
Zie DECISIONS.md voor architectuurkeuzes.
Zie CHANGELOG.md voor alle wijzigingen.
