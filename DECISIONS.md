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

---

## D-010 | 28 mei 2026 | QBTS KALIBRATIE: BUY_SMALL i.p.v. BUY_MODERATE

**Beslissing:** QBTS scoort 59.4 in v1.2 (was 60.4 in v1.1) → BUY_SMALL.
Dit wordt niet gecorrigeerd.

**Rationale:** De score verschoof door drie kleine gewichtsaanpassingen:
volume -3 pts, social -2 pts, float None=+4 pts. Nettoverschil: -1.0 pt.
QBTS als sympathy play met moderate catalyst en neutrale float is
BUY_SMALL conservatiever maar niet onjuist. De threshold aanpassen
om een testuitkomst te behouden is slechte praktijk.

**Beslissing:** Accepteer de herbalancering. QBTS BUY_SMALL is correct gedrag.

---

## D-009 | 28 mei 2026 | SOCIAL QUALITY CAP: Gestaffeld per Catalyst

**Beslissing:** Social score gecapped per catalyst kwaliteit:
NONE=2, WEAK=4, MODERATE=6, STRONG=8 pts.

**Rationale:** Zonder cap kan een aandeel dat viraal gaat zonder enige
fundamentele catalyst 10/100 punten scoren op social alleen.
De combinatieregel (catalyst=NONE + momentum<50 → SKIP) vangt veel
gevallen al op, maar een aandeel met hoge andere scores én geen catalyst
kon toch tot 30+ pts komen via social. De cap zorgt dat social nooit
meer dan 8% van de totale score kan zijn — en bij slechte catalyst kwaliteit
slechts 2-6%.

**Bewijs:** SOCIALPUMP_T11: social velocity 45x maar gecapped op 2 pts.
Totale momentum score 31.7 → SKIP via combinatieregel.

---

## D-008 | 28 mei 2026 | FLOAT SCORE: Nieuw Component Max 8 Pts

**Beslissing:** Float score toegevoegd als zevende scoring component.

**Rationale:** Float is een fundamentele driver van momentum amplificatie.
Bij een lage float (<5M aandelen) kan institutioneel koopvolume de prijs
significant bewegen. Bij een hoog float (>200M) heeft hetzelfde koopvolume
veel minder impact. De engine miste dit tot v1.2.

**Implementatie:** float=None → neutrale score 4/8 (onbekend ≠ laag of hoog).
Schaal: <5M=8, <15M=6.5, <50M=4.5, <200M=2, ≥200M=0.

**Bewijs:** LOWFLOAT_T9 (2.8M float) scoort 8/8 float pts → bijdrage aan
totale score 99.6/100.

---

## D-007 | 28 mei 2026 | WORKFLOW: Twee-AI Review Pipeline

**Beslissing:** Claude = builder. ChatGPT = strategist/risk reviewer. Igor = arbiter.

**Rationale:** Twee AI-modellen met verschillende biases vullen elkaar aan.
Claude: structuur, code, documentatie. ChatGPT: strategische kritiek,
"is dit institutioneel of hype?". Igor blijft altijd de laatste schakel.
Zonder menselijke arbiter kunnen twee AI's elkaars fouten versterken.

**GitHub als gedeeld geheugen:** Beide AI's lezen MASTER_CONTEXT.md als source of truth.

---

## D-011 | 28 mei 2026 | TESTING: pytest + assert_decision() helper

**Beslissing:** Formele pytest test-suite als vereiste vóór live data integratie (fase 2).

**Rationale:** Mock tests in scoring_v1_2.py valideren logica maar beschermen
niet tegen regressies bij refactoring. Een `assert result.decision == "BUY_MAX"`
na een gewichtswijziging faalt direct en toont exact welk getal buiten bereik viel.
Zonder test-suite is fase 2 (live data) bouwen op een onzekere fundering.

**assert_decision() helper:**
Centrale assertiefunctie met duidelijke foutmeldingen (ticker, verwacht, actueel,
momentum, skip). Maakt het onmiddellijk duidelijk wát fout ging bij een regressie.

**make_input() factory:**
Minimalistische TickerInput constructie met veilige defaults. Gefocuste unit tests
overschrijven alleen het veld dat relevant is. Dit voorkomt boilerplate en maakt
tests leesbaar als specificaties.

**Regressie-aanpak:**
Ranges (±5 pts marge) in plaats van exacte scores. Exacte scores zijn fragiel —
elke gewichtsaanpassing breekt alle tests. Ranges vangen echte regressies maar
tolereren kleine kalibraties.

**Beslissingscriterium fase 2:** Alle 70 tests moeten groen zijn vóór live data
wordt geïntegreerd. Bij elke pull request: `pytest tests/ -v` verplicht.

---

## D-012 | 28 mei 2026 | BACKEND: FastAPI + Placeholder Clients

**Beslissing:** v2.0 backend met placeholder clients voor news en social.
Echte data alleen voor Yahoo Finance (prijs/volume). News en social = fase 2.1.

**Rationale:** Een werkende backend met gedeeltelijke data is beter dan
wachten op alle data tegelijk. De `data_quality` velden in de response
communiceren transparant welke data beschikbaar was. De gebruiker ziet
altijd of scores gebaseerd zijn op echte of placeholder data.

**Placeholder strategie:**
- `news_client.py` retourneert lege lijst → catalyst=NONE (meest conservatief)
- Social = 0/1 → geen social score → minder risico op social-pump false positives
- `has_sec_investigation=False` → handmatige check vereist (FM-008)

**Testbaarheid:** Alle backend tests gebruiken mocks (unittest.mock).
Geen netwerk vereist voor de test-suite. Werkt in elke CI-omgeving.

---

## D-013 | 28 mei 2026 | SCHEMAS: Pydantic Contract Layer

**Beslissing:** Vier Pydantic schemas als contract-laag tussen data en API.
schemas/ticker_snapshot.py, scoring_response.py, sector_state.py, api_error.py.

**Rationale:** v2.0 gebruikte losse dicts door de API. Bij elke refactor kon een
veld ontbreken zonder dat tests dit merkten. Pydantic schemas geven compile-time
validatie, automatische OpenAPI docs, en een stabiel contract.

**DataConfidence:** LIVE/DELAYED/PARTIAL/MISSING — transparantie over databron-
kwaliteit. Gebruiker weet of score gebaseerd is op complete of incomplete data.
DELAYED is gereserveerd voor v2.2 (cache timestamp vergelijking).

---

## D-014 | 28 mei 2026 | RETRY: Exponential Backoff in yahoo_client

**Beslissing:** Max 3 pogingen, backoff 0s/0.5s/1.5s. Rate limit → stop direct.

**Rationale:** Yahoo Finance is onbetrouwbaar bij hoog verzoekvolume. Eén
mislukte request mag de gebruiker geen 500 geven. Exponential backoff voorkomt
dat we een overbelaste server harder treffen. Bij rate limit (429/herhaalde 403)
is meer proberen contraproductief — direct stoppen is de juiste reactie.

**Consequentie:** get_snapshot() gooit nooit een exception. Altijd TickerSnapshot.
Fouten communiceren via het `error` veld + DataConfidence.MISSING.

---

## D-015 | 28 mei 2026 | CACHE: Architectuur Prep Disabled

**Beslissing:** cache/market_cache.py gebouwd maar CACHE_ENABLED=False.

**Rationale:** Voortijdige cache zonder gemeten rate limit probleem = over-engineering.
De architectuur is klaar (TTL, cooldown registry, stats). Activeren in v2.2 zodra
in productie aantoonbaar rate limiting optreedt. In-memory cache overleeft
server restart niet — dat is acceptabel voor persoonlijk gebruik.

---

## D-016 | 28 mei 2026 | CACHE: In-Memory TTL Cache Geactiveerd

**Beslissing:** CACHE_ENABLED = True. In-memory dict, market-hours-aware TTL,
age-based confidence, stale fallback.

**Rationale:** Elke /analyze request haalde live Yahoo data op. Bij batch requests
of sector scans (3-10 tickers) werd Yahoo direct rate-limited. Cache beschermt
tegen rate limits, versnelt responses 10-50x voor cache hits, en biedt graceful
degradation bij Yahoo-uitval.

**TTL keuzes:** 60s (regular hours) is kort genoeg voor trading relevantie maar
lang genoeg om rate limits te vermijden. 1800s (overnight) acceptabel omdat
prijzen dan toch niet bewegen.

**Stale fallback:** Liever STALE data dan helemaal geen data. Confidence label
communiceert de beperkingen transparant. Gebruiker beslist zelf of STALE score
actionable is.

---

## D-017 | 28 mei 2026 | BATCH ENDPOINT: Max 10, Partial Failure Tolerant

**Beslissing:** GET /analyze?tickers=A,B,C, max 10 per request, één fout stopt
de batch niet.

**Rationale:** Sympathy play scanning vereist meerdere tickers tegelijk (bijv.
IONQ + QBTS + RGTI). Zonder batch endpoint = 3 losse requests = 3x de latency.
Max 10 beschermt tegen misbruik. Partial failure tolerance: als QBTS Yahoo data
mist, moeten IONQ en RGTI nog steeds gescoord worden.

---

## D-018 | 28 mei 2026 | SECTOR ENDPOINT: Cache-First Strategy

**Beslissing:** GET /sector/{id} scoort leaders vanuit cache, valt terug op live
als niet gecached. Eén mislukte leader stopt de sector niet.

**Rationale:** Sector scans zijn nuttig als snelle oriëntatie ("hoe staat quantum
er nu voor?"). Cache-first maakt dit snel (<100ms bij alle leaders gecached).
Live-fallback zorgt dat ook ongecachte leaders worden meegenomen.
