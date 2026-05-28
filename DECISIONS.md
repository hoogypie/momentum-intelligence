# DECISIONS LOG — MOMENTUM INTELLIGENCE
> Architectuur en product beslissingen met rationale.
> **Regel:** Nieuwe beslissingen bovenaan toevoegen vóór implementatie.
> Format: `D-XXX | DATUM | TITEL`

---

## D-007 | 28 mei 2026 | WORKFLOW: Twee-AI review pipeline

**Beslissing:** Claude = builder/researcher. ChatGPT = strategist/risk reviewer. Igor = arbiter.

**Rationale:** Twee AI-modellen met verschillende biases vullen elkaar aan:
- Claude: structuur, code, documentatie, implementatie
- ChatGPT: strategische kritiek, edge-analyse, "is dit institutioneel of hype?"
- Voordeel: één AI die altijd gelijk krijgt produceert blindspots. Twee modellen die elkaar corrigeren via Igor als arbiter produceert betere uitkomsten.

**Kritieke eis:** Igor blijft altijd de laatste schakel. Twee AI's zonder menselijke arbiter versterken elkaars fouten.

**GitHub als gedeeld geheugen:** Beide AI's lezen dezelfde MASTER_CONTEXT.md, DECISIONS.md, ROADMAP.md als source of truth.

**Implicatie:** Begin elke sessie (Claude én ChatGPT) met het lezen van MASTER_CONTEXT.md + DECISIONS.md.

---

## D-006 | 28 mei 2026 | GEEN FEATURES TIJDENS BUG-FIX SESSIES

**Beslissing:** Een sessie heeft één doel: óf bug fixen, óf features toevoegen. Nooit beide.

**Rationale:** v1.1 sessie toonde dit aan: "fix twee bugs" werd niet uitgebreid met nieuwe features. Dat principe formeel vastleggen voorkomt scope creep en regressies.

**Regel:** Als de instructie "fix X" is, wordt alleen X gefixt. Nieuwe features worden in ROADMAP.md genoteerd voor de volgende sessie.

---

## D-005 | 28 mei 2026 | AI ALLEEN VOOR NARRATIVE, NOOIT VOOR SCORING

**Beslissing:** Claude API wordt niet gebruikt voor het berekenen van scores. Alleen voor het uitleggen van al berekende scores.

**Rationale:** AI zonder live data hallucineert actuele informatie. "Momentum score 88/100" berekend door AI zonder volume/prijs data is "AI-gevoel", geen signal intelligence. De score is algoritmisch en reproduceerbaar. De uitleg is contextueel en menselijk leesbaar.

**Implementatie:**
```
✅ "Ticker X heeft score 72, volume 5.3x, catalyst: earnings beat +18%. Leg uit wat dit betekent."
❌ "Analyseer ticker X en geef een momentum score."
```

**Consequentie:** `scoring_v1_1.py` bevat geen AI API calls. Nooit toevoegen.

---

## D-004 | 28 mei 2026 | SKIP-FIRST ARCHITECTUUR

**Beslissing:** Skip Score wordt altijd berekend en geëvalueerd vóór Momentum Score.

**Rationale:** Een aandeel met Momentum Score 95 maar Skip Score 100 (SEC investigation) is een BLOCKED trade. De Momentum Score heeft nul waarde als een hard veto actief is. Door Skip-first te evalueren wordt een sterk momentum signaal nooit toegelaten om een fundamentele risicovlag te overschaduwen.

**Implementatie:** In `make_decision()`:
1. `skip.is_hard_blocked` → BLOCKED (regels 1-2)
2. `skip.total >= 50` → SKIP (regel 3)
3. Combinatieregel → SKIP (regel 4)
4. Momentum score evaluatie (regel 5+)

**Test bewijs:** APP_TEST2 — Momentum 68.4/100, maar BLOCKED door SEC. Score wordt niet getoond als beslissingsgrond.

---

## D-003 | 28 mei 2026 | SECTOR CONFIG: JSON, NIET HARDCODED

**Beslissing:** Sector heat scores en metadata worden opgeslagen in `config/sectors.json`, niet in applicatiecode.

**Rationale:** Hardcoded sector data veroudert binnen 2 weken. Quantum sector heat van 92 vandaag kan 40 zijn volgende maand. Code aanpassen voor content updates is slechte architectuur. JSON config kan zonder deployment worden bijgewerkt.

**Update frequentie:** Handmatig, wekelijks (~10 minuten).

**Schema:**
```json
{
  "sectors": [{
    "id": "quantum",
    "label": "QUANTUM",
    "heat": 92,
    "status": "HOT",
    "leaders": ["IONQ", "QBTS", "RGTI"],
    "sympathy": ["QUBT", "IBM"],
    "trigger": "US $2B quantum funding",
    "last_updated": "2026-05-28"
  }]
}
```

---

## D-002 | 28 mei 2026 | BACKEND: PYTHON FASTAPI

**Beslissing:** Python FastAPI als backend. Niet Node.js. Niet pure browser-app.

**Rationale:**
1. **API key security** — Browser-apps lekken keys via DevTools. Backend houdt keys server-side.
2. **Python ecosystem** — yfinance, pandas, numpy superieur voor financiële data vs. Node.js.
3. **CORS** — Yahoo Finance blokkeert browser-requests. Backend heeft dit probleem niet.
4. **Consistentie** — scoring_v1_1.py is Python. Één taal door de hele stack.

**Overwogen alternatieven:**
- Node.js → afgewezen: Python data ecosystem ontbreekt
- Pure browser-app → afgewezen: API key security onoplosbaar
- Serverless functions → overwegen in fase 4 voor Vercel deployment

---

## D-001 | 28 mei 2026 | DATA: GRATIS EERST, UNUSUAL WHALES LATER

**Beslissing:** Start met gratis data. Eerste betaalde abonnement = Unusual Whales ($30/mo), maar pas als gratis tier bewezen werkt.

**Gratis plan:**
- Yahoo Finance (unofficial) — prijs, volume, premarket, market cap
- Finnhub free — nieuws (60 calls/min)
- StockTwits — social mentions (geen key vereist)

**Beslissingscriterium:** Unusual Whales pas na 4 weken aantoonbaar goede signalen op gratis data.

**Expliciet afgewezen:**
- Bloomberg Terminal ($25k/jaar) — institutioneel, overkill voor persoonlijk gebruik
- Meerdere AI agents nu — overengineering, later in fase 6
- Polygon.io onmiddellijk — geen toegevoegde waarde vóór gratis tier bewezen is

---

## OPEN BESLISSINGEN

| Vraag | Status | Verwachte fase |
|---|---|---|
| Vercel of eigen server voor hosting? | Open | Fase 4 |
| Sector heat automatiseren via data? | Open | Fase 5 |
| Backtesting framework: eigen of library? | Open | Fase 5 |
| Float data: Finviz Elite of scraping? | Open | Fase 2 onderzoeken |
| LangChain vs CrewAI vs AutoGen voor fase 6? | Open | Fase 6 |

---

## TEMPLATE

```markdown
## D-XXX | DD MMM YYYY | TITEL

**Beslissing:** Wat is er besloten?
**Rationale:** Waarom? Welk probleem lost dit op?
**Overwogen alternatieven:** Wat is afgewezen en waarom?
**Implicatie:** Wat verandert er in de codebase/workflow?
**Gerelateerd:** CHANGELOG v-X.Y, of eerder beslissing D-XXX
```
