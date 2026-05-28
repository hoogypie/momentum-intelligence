# KNOWN FAILURE MODES
> Gedocumenteerde scenarios waar de engine faalt of misleidende signalen geeft.
> Eerlijkheid over beperkingen is de basis van vertrouwen in het systeem.
> Laatste update: 28 mei 2026

---

## Hoe dit document te gebruiken

Elk failure mode heeft:
- **Scenario:** wat er mis gaat
- **Waarom:** de root cause
- **Huidig gedrag:** wat de engine nu doet
- **Risico:** hoe erg is het?
- **Mitigatie:** hoe verminderen we de schade?

---

## FM-001 — Stale Sector Heat

**Scenario:** Quantum sector had heat=92 op 28 mei. Drie weken later is het thema afgekoeld, maar sectors.json is niet bijgewerkt. Engine geeft een quantum naam nog steeds 16.56 sector heat punten.

**Waarom:** Sector heat is handmatig en wekelijks bijgewerkt. Bij gemiste update veroudert de config.

**Huidig gedrag:** Engine gebruikt stale waarde zonder waarschuwing.

**Risico:** MEDIUM — overschat momentum in afgekoelde sector.

**Mitigatie:**
- `last_updated` veld in sectors.json
- Fase 2: waarschuwing als `last_updated` > 7 dagen geleden
- Wekelijkse update als vaste routine (vrijdag, 10 min)

---

## FM-002 — Social Velocity Zonder Kwaliteitscontext

**Scenario:** Een aandeel heeft 20x normale mentions. Maar alle mentions zijn negatief ("CEO gearresteerd"). Engine registreert 20x velocity en kent punten toe.

**Waarom:** Social acceleration meet velocity, niet sentiment. Negatief viral en positief viral zien er hetzelfde uit in de data.

**Huidig gedrag:** Social score omhoog, zelfs bij negatief nieuws.

**Risico:** HOOG voor pure social plays. LAAG bij goede catalyst omdat de combinatieregel veel gevallen vangt.

**Mitigatie:**
- Fase 2: Finnhub sentiment score toevoegen als modifier
- Wanneer social hoog én catalyst=NONE → sociale cap vangt dit grotendeels op
- Menselijke review bij hoge sociale scores altijd aanbevolen

---

## FM-003 — Float Data Onbetrouwbaar

**Scenario:** Yahoo Finance geeft float_shares=None voor veel tickers. Engine gebruikt dan neutrale score 4/8. Maar het echte float kan 1M zijn (extreem laag) of 500M (hoog).

**Waarom:** Float data is niet gratis beschikbaar via Yahoo Finance. Finviz Elite ($25/mo) of institutional feeds hebben betere data.

**Huidig gedrag:** float=None → 4/8 pts (neutraal). Kan 4 pts te laag of te hoog zijn.

**Risico:** MEDIUM — onderschat momentum amplificatie bij lage float stocks.

**Mitigatie:**
- Fase 2: onderzoek gratis float endpoints
- Fase 5: Finviz Elite integratie
- Interim: handmatige override mogelijk via SectorConfig

---

## FM-004 — Pre-Market Data Buiten Handelsuren

**Scenario:** Engine draait om 15:00 CET. Pre-market veld is 0% omdat de markt al open is. Premarket-component scoort neutraal terwijl er eerder een sterke pre-market was.

**Waarom:** Yahoo Finance geeft premarket_pct=0 als de markt open is. De pre-market signaal is dan verloren.

**Huidig gedrag:** Premarket score = 2.5 pts (neutraal 0%) in plaats van de werkelijke pre-market.

**Risico:** LAAG — andere componenten compenseren. Maar vroeg-ochtend setups worden minder goed gescoord later op de dag.

**Mitigatie:**
- Fase 2: pre-market snapshot opslaan bij eerste run (08:00-09:30 CET)
- Cache pre-market waarde zodat het later op de dag nog zichtbaar is

---

## FM-005 — Geen Historische Validatie

**Scenario:** Engine scoort UMAC op 95/100 vandaag. Maar we weten niet of historische hoge scores voorspellend waren voor daadwerkelijke prijsstijgingen.

**Waarom:** Backtesting framework bestaat nog niet. We valideren logica op mock data, niet op historische marktdata.

**Huidig gedrag:** Scores zijn logisch consistent maar niet empirisch gevalideerd.

**Risico:** HOOG op systeemniveau — we weten niet of de gewichten optimaal zijn.

**Mitigatie:**
- Fase 5: backtesting framework bouwen
- Minimaal 3 maanden live gebruik bijhouden: "score X gegeven op datum Y, uitkomst Z"
- Dan gewichten empirisch kalibreren

---

## FM-006 — Market Cap Tier Cap Overschrijft Momentum

**Scenario:** Een micro-cap met score 98/100 wordt gesized op max €250 door de MICRO tier cap. Dat is correct per spelregel. Maar het betekent dat de engine een "perfecte setup" aanbeveelt met een sizing die emotioneel onbevredigend klein voelt.

**Waarom:** Dit is geen bug maar een bewuste keuze (Spelregel 16: max €250 per speculatieve positie).

**Huidig gedrag:** BUY_MAX met "(gelimiteerd door MICRO-cap)" in de sizing.

**Risico:** GEDRAGSRISICO — gebruiker negeert de cap en investeert meer.

**Mitigatie:**
- Sizing cap expliciet en prominent weergeven in dashboard
- Nooit de cap aanpassen op basis van "hoge score"

---

## FM-007 — Gecombineerde Sector Exposure Niet Gemeten

**Scenario:** UMAC scoort 95/100, KTOS scoort 88/100, RCAT scoort 78/100. Alle drie zijn drones/defense. Engine geeft voor alle drie "kopen" zonder te weten dat de gebruiker al €750 in drone exposure heeft.

**Waarom:** De engine scoort per ticker, niet per portfolio. Portfolio context ontbreekt.

**Huidig gedrag:** Drie losse BUY signals, geen portfolio-aggregatie.

**Risico:** MEDIUM — gebruiker kan onbewust geconcentreerd raken in één sector.

**Mitigatie:**
- Dit is een architectuurlimiet van de huidige scope
- Fase 3: dashboard toont portfolio-brede sector exposure
- Spelregel 24 (max 50-60% dezelfde macro-driver) = Igor's eigen verantwoordelijkheid

---

## FM-008 — SEC Flag Vereist Handmatige Input

**Scenario:** Engine scoort een aandeel als BUY_STRONG. Gisteren is een SEC onderzoek geopend. Maar `has_sec_investigation` staat op False omdat de data niet automatisch bijgehouden wordt.

**Waarom:** SEC flag is handmatige input in de huidige fase. Er is geen automatische SEC feed.

**Huidig gedrag:** False negatives mogelijk als de gebruiker het nieuws niet heeft gezien.

**Risico:** HOOG — dit is precies de APP-fout uit het FRAMEWORK.docx (Fout #3).

**Mitigatie:**
- Fase 2: Finnhub news scan op keywords ("SEC", "investigation", "class action")
- Als keyword gevonden: flag automatisch op True, trigger Skip Score +100
- Tot dan: dagelijkse SEC check als onderdeel van de routine

---

## FM-009 — Phase Detection Op Eindpunt van de Dag

**Scenario:** Engine detecteert EXPANSION fase (dag +14%, volume 5x). Maar de move was al vroeg op de dag en is aan het vertragen. Het is eigenlijk early EXHAUSTION, niet EXPANSION.

**Waarom:** Phase detection gebruikt dag-cumulatieve data, niet intraday tijdseries. Een vroege piek ziet er hetzelfde uit als een aanhoudende stijging.

**Huidig gedrag:** EXPANSION phase, geen onderscheid met early exhaustion.

**Risico:** MEDIUM — late-dag entries kunnen op het verkeerde moment instappen.

**Mitigatie:**
- Fase 2: intraday volume curve analyseren (volume in eerste 30 min vs. latere uren)
- Interim: handmatig controleren of de move recent of vroeg op de dag was

---

## FM-010 — Sympathy Play Timing Niet Gemeten

**Scenario:** IONQ steeg vandaag om 09:35. Het is nu 15:00. QBTS heeft inmiddels 80% van de sympathy move al gemaakt. Engine detecteert nog steeds "quantum sector hot" en geeft QBTS een hoge sector heat.

**Waarom:** De sympathy timing (QBTS volgt IONQ met 1-4u lag) is gedocumenteerd in het framework maar niet geïmplementeerd in de engine.

**Huidig gedrag:** Sector heat is statisch. Timing van de sympathy wave wordt niet meegenomen.

**Risico:** MEDIUM — te laat instappen in sympathy plays.

**Mitigatie:**
- Fase 3: timestamp van leader breakout opslaan
- Als `now - leader_breakout_time > sympathy_window` → sector heat discount
- Interim: handmatig controleren wanneer de leader bewoog

---

## SAMENVATTING RISICOMATRIX

| FM | Beschrijving | Risico | Fase fix |
|---|---|---|---|
| FM-001 | Stale sector heat | MEDIUM | 2 |
| FM-002 | Negatief viral ≠ positief viral | HOOG | 2 |
| FM-003 | Float data onbetrouwbaar | MEDIUM | 5 |
| FM-004 | Pre-market verloren na open | LAAG | 2 |
| FM-005 | Geen historische validatie | HOOG | 5 |
| FM-006 | Market cap cap wordt genegeerd | GEDRAG | 3 |
| FM-007 | Sector concentratie niet gemeten | MEDIUM | 3 |
| FM-008 | SEC flag handmatig | HOOG | 2 |
| FM-009 | Phase op eindpunt onnauwkeurig | MEDIUM | 2 |
| FM-010 | Sympathy timing niet gemeten | MEDIUM | 3 |

**Hoogste prioriteit voor fase 2:** FM-002 (negatief viral) en FM-008 (SEC auto-detect).
