# CHANGELOG
> Alle wijzigingen, nieuwste bovenaan.
> **Regel:** elke wijziging hier vastleggen vóór implementatie.
> Format: `[vX.Y] DATUM — BESCHRIJVING`

---

## [v1.1] — 28 mei 2026 — Score Engine Kalibratie

**Gewijzigd:**
- `scoring/scoring_v1_1.py` — twee targeted fixes na test-analyse

**Fix 1: Dag >40% penalty verhoogd (30 → 40 pts)**
- Probleem: CHASER_TEST5 (+42% dag) scoorde BUY_MAX — onjuist gedrag
- Oorzaak: Skip penalty van 30 + premarket 15 = 45, net onder drempel van 50
- Fix: penalty verhoogd naar 40 → 40 + 15 = 55 → SKIP ✅
- Impact: alleen cases met dag >40% stijging

**Fix 2: Combinatieregel toegevoegd**
- Probleem: HYPE_TEST7 (geen catalyst + zwak momentum) scoorde WATCH — te mild
- Fix: `catalyst = NONE` AND `momentum_score < 50` → altijd SKIP
- Rationale: pure social hype zonder catalyst = te riskant voor elke actie boven WATCH
- Impact: alleen cases zonder catalyst en momentum onder 50

**Test resultaten:**
- v1.0: 5/8 correct
- v1.1: 8/8 correct ✅

**Geen nieuwe features toegevoegd.**

---

## [v1.0] — 28 mei 2026 — Score Engine Initieel

**Toegevoegd:**
- `scoring/scoring_v1_1.py` — volledige score engine met pure functies
- `TickerInput` dataclass — gestandaardiseerde data input
- `MomentumScoreResult` dataclass — component breakdown
- `SkipScoreResult` dataclass — flags en redenen
- `ScoringResult` dataclass — complete output per ticker
- `calculate_volume_anomaly()` — max 25 pts
- `calculate_sector_heat_score()` — max 20 pts
- `calculate_catalyst_quality()` — max 20 pts
- `calculate_premarket_strength()` — max 15 pts
- `calculate_relative_strength()` — max 10 pts
- `calculate_social_acceleration()` — max 10 pts
- `calculate_skip_score()` — hard vetoes + soft skips
- `make_decision()` — skip-first decision logic
- `score_ticker()` — complete pipeline
- `print_report()` — colored CLI output
- 8 mock test cases (UMAC, APP, SPACE, SNOW, CHASER, SLEEPER, HYPE, QBTS)

**Test resultaten:** 5/8 correct (2 bugs geïdentificeerd → gefixed in v1.1)

---

## [v0.1] — 28 mei 2026 — Project Initialisatie

**Toegevoegd:**
- Concept momentum dashboard (HTML prototype)
- Momentum Early Detection Framework document (Word)
- Momentum Intelligence Dashboard product vision document
- GitHub project structuur opgezet
- MASTER_CONTEXT.md, ROADMAP.md, DECISIONS.md aangemaakt

---

## TEMPLATE VOOR TOEKOMSTIGE ENTRIES

```markdown
## [vX.Y] — DD MMM YYYY — Korte omschrijving

**Context:** Waarom deze wijziging?
**Gewijzigd:** Welke bestanden?
**Toegevoegd:** Nieuwe bestanden/features?
**Verwijderd:** Wat is verwijderd?
**Test resultaten:** Slaagde alle tests?
**Breaking changes:** Ja/Nee — zo ja, wat?
**Gerelateerde beslissing:** Zie DECISIONS.md D-XXX
```
