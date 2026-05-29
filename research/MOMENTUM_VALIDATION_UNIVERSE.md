# Momentum Validation Universe — v1.0

> **Versie:** v1.0 | **Aangemaakt:** 29 mei 2026 | **Stabiel tot:** 29 juni 2026 (30 dagen minimum)
>
> **Doel:** Een vaste, reproduceerbare universe voor het paper trading validatieframework.
> De engine bepaalt welke tickers een BUY-signaal krijgen. Dit document bepaalt alleen
> welke tickers dagelijks worden aangeboden.
>
> **Kernregel:** Wijzig de universe niet tijdens een validatieperiode van 30 dagen.
> Toevoegingen of verwijderingen markeer je in het changelog onderaan — niet tussentijds.

---

## Selectiecriteria

Een ticker is opgenomen als hij aan **alle vier** criteria voldoet:

| # | Criterium | Reden |
|---|---|---|
| 1 | **Beschikbaar als echt aandeel op T212** (niet alleen CFD) | Spelregel 29 — CFD is automatisch skip voor analyses langer dan 1 dag |
| 2 | **Geen actief SEC-onderzoek of class-action** | Spelregel 3 — rode vlag overschrijft alle andere signalen |
| 3 | **Minimaal één kwartaal omzet** | Spelregel VKTX — pre-revenue zonder earnings history leidt tot onbetrouwbare scores |
| 4 | **Voldoende Finnhub nieuwsdekking** | Catalyst classifier heeft input nodig; tickers met <1 artikel/week leveren altijd NONE catalyst |

Micro-caps met zero revenue zijn opgenomen **uitsluitend** als ze al een gedocumenteerde institutionele klantenbasis hebben (ACHR, JOBY) of als ze de primaire sector-leader zijn voor een verplicht te dekken sector.

---

## Core Universe — Dagelijks verplicht (30 tickers)

Dit zijn de tickers die elke handelsdag worden meegenomen in `validation_runner.py` en `paper_trade_report.py record`. Dekt alle acht verplichte sectoren.

### AI Infrastructure (5)

| Ticker | Naam | Cap | Catalyst freq. | Verwachte volatiliteit | Reden |
|---|---|---|---|---|---|
| **NVDA** | Nvidia | LARGE | Hoog | Medium | GPU-monopolie. Engine benchmark — altijd aanwezig als referentiepunt. Verwacht WATCH of BUY_SMALL bij flat markt, BUY_MODERATE bij volume spikes. |
| **MU** | Micron Technology | LARGE | Hoog | Medium | HBM/DRAM pricing power. Eigen kwartaalcatalyst onafhankelijk van NVDA. Structureel sterk door AI memory demand. |
| **AVGO** | Broadcom | LARGE | Hoog | Medium | Custom AI chips + networking. Dubbele thesis: chip design én netwerk-infra. Kwartaalcatalysts betrouwbaar. |
| **CRDO** | Credo Technology | MID | Medium | Hoog | Optische networking bottleneck. Pure-play leader in 800G/1.6T ethernet. OWN catalysts bij deals en kwartaalresultaten. |
| **VRT** | Vertiv Holdings | LARGE | Hoog | Medium | Datacenter cooling en power infrastructure. Directe beneficiary van AI capex-explosie. Consistent earnings beats. |

### Quantum Computing (4)

| Ticker | Naam | Cap | Catalyst freq. | Verwachte volatiliteit | Reden |
|---|---|---|---|---|---|
| **IONQ** | IonQ | SMALL | Hoog | Hoog | Sector-leader quantum hardware. Eigen OWN catalysts (partnerships, hardware milestones). Referentiepunt voor quantum sector heat. |
| **QBTS** | D-Wave Quantum | MICRO | Medium | Zeer hoog | Sympathy play — beweegt primair mee met IONQ. Laag float → explosieve moves bij volume. Valideert sympathy-detectie in classifier. |
| **RGTI** | Rigetti Computing | MICRO | Medium | Hoog | Photonics-based qubit — eigen technologie. Minder pure sympathy dan QBTS, meer eigen nieuwsflow. |
| **QUBT** | Quantum Computing Inc. | MICRO | Laag | Hoog | Quantum software. Geen Finnhub key = NONE catalyst verwacht. Controle: engine moet SKIP geven bij hoog skip-score. Valideert combinatieregel. |

### Space / New Space (3)

| Ticker | Naam | Cap | Catalyst freq. | Verwachte volatiliteit | Reden |
|---|---|---|---|---|---|
| **RKLB** | Rocket Lab | SMALL | Hoog | Hoog | Launch manifest = voorspelbare OWN catalysts. Backlog-driven met NASA/DoD contracten. Sector-leader voor commercial space. |
| **ASTS** | AST SpaceMobile | SMALL | Hoog | Zeer hoog | Satellite connectivity deals met telecom operators. Snelle nieuwsflow, hoge maatschappelijke relevantie. Valideert STRONG catalyst scoring. |
| **LUNR** | Intuitive Machines | SMALL | Medium | Hoog | NASA CLPS-contractmijlpalen als primaire catalyst. Missie-gedreven nieuwsflow (slaagt/faalt). Goede test voor mission-event catalyst classificatie. |

### Defense / Drones (5)

| Ticker | Naam | Cap | Catalyst freq. | Verwachte volatiliteit | Reden |
|---|---|---|---|---|---|
| **UMAC** | Unusual Machines | MICRO | Hoog | Zeer hoog | Pure-play drone components. Pentagon equity deal context. Sector-leader qua nieuwsflow voor kleine drone namen. |
| **RCAT** | Red Cat Holdings | SMALL | Medium | Hoog | Drone carrier/integrator. STRONG catalyst bij DoD contracten. Goede test voor OWN vs SECTOR onderscheid in defense. |
| **KTOS** | Kratos Defense | MID | Medium | Medium | Autonome systemen en defense tech. Meer gediversifieerd dan UMAC/RCAT. Valideert engine bij grotere cap in zelfde sector. |
| **PLTR** | Palantir | LARGE | Medium | Medium | Defense AI data platform. Overheid + commercieel. Grote float → lagere float_score. Verwacht BUY_SMALL tot WATCH. |
| **AXON** | Axon Enterprise | LARGE | Medium | Medium | AI law enforcement tech. Defense-adjacent. Eigen kwartaalresultaten onafhankelijk van Pentagon news. |

### Cybersecurity (5)

| Ticker | Naam | Cap | Catalyst freq. | Verwachte volatiliteit | Reden |
|---|---|---|---|---|---|
| **CRWD** | CrowdStrike | LARGE | Medium | Medium | Cloud-native sector-leader. Verwacht consistent BUY_SMALL tot BUY_MODERATE. Goede baseline voor cyber sector. |
| **PANW** | Palo Alto Networks | LARGE | Medium | Laag | Marktleider, grote cap. Verwacht WATCH door lage volatiliteit en hoge koers. Valideert dat engine grote caps conservatief behandelt. |
| **S** | SentinelOne | MID | Medium | Medium | Snelste omzetgroei in sector. Kleinere cap dan PANW/CRWD → potentieel hogere scores bij beats. |
| **ZS** | Zscaler | LARGE | Medium | Medium | Zero-trust cloud leader. Solide kwartaalcatalysts. Goede vergelijking met PANW qua score-niveau. |
| **FTNT** | Fortinet | LARGE | Medium | Laag | Network security incumbent. Stabiel, langzame beweger. Verwacht WATCH. Controle voor sector. |

### Robotics / Automation (3)

| Ticker | Naam | Cap | Catalyst freq. | Verwachte volatiliteit | Reden |
|---|---|---|---|---|---|
| **ISRG** | Intuitive Surgical | LARGE | Medium | Laag | Robotic surgery monopolie. Stabiele kwartaalcatalysts. Verwacht BUY_SMALL bij beats. Valideert engine bij stabiele groeiers. |
| **TER** | Teradyne | MID | Medium | Medium | Semiconductor test automation. AI chip cycle beneficiary. OWN catalysts bij kwartaalresultaten en orders. |
| **BRZE** | Braze | MID | Laag | Medium | Customer engagement automation + AI features. Langzamere nieuwsflow. Verwacht WATCH tot BUY_SMALL. Extended oorspronkelijk, maar representeert automation software. |

### Healthcare Growth (2)

| Ticker | Naam | Cap | Catalyst freq. | Verwachte volatiliteit | Reden |
|---|---|---|---|---|---|
| **TEM** | Tempus AI | MID | Hoog | Hoog | AI healthcare platform. Partnership nieuwsflow. OWN catalysts bij zorgverzekeraar-deals. Sterke momentum eigenschappen. |
| **HIMS** | Hims & Hers | SMALL | Hoog | Hoog | Telehealth + GLP-1 platform. Hoge omzetgroei, kwartaalbeats. Valideert engine bij consumentengezondheidszorg-momentum. |

### Control Group — Engine moet deze skippen of watchen (3)

| Ticker | Naam | Cap | Verwachte beslissing | Reden voor opname |
|---|---|---|---|---|
| **KO** | Coca-Cola | LARGE | SKIP / WATCH | Consumer staples zonder momentum driver. Valideert dat engine defensieve namen niet onjuist koopt. |
| **JNJ** | Johnson & Johnson | LARGE | SKIP | Pharma incumbent, lage volatiliteit, geen AI-thesis. Controle dat SKIP-logica werkt voor grote stabiele namen. |
| **GLD** | SPDR Gold ETF | LARGE | SKIP | ETF — geen Finnhub nieuws mogelijk, altijd catalyst=NONE. Perfecte controle voor combinatieregel (NONE + momentum<50 → SKIP). |

---

**Core Universe totaal: 30 tickers**

---

## Extended Universe — Optioneel, wekelijks (23 tickers)

Voeg toe wanneer je extra dekking wilt per sector of een specifieke thesis wilt valideren. Niet verplicht voor dagelijkse validatierun.

### AI Software / Orchestration (5)

| Ticker | Naam | Cap | Reden |
|---|---|---|---|
| **SNOW** | Snowflake | LARGE | AI consumption inflection — +34% AH na beat bewezen. Valideert catalyst_source voor software names. |
| **DDOG** | Datadog | LARGE | AI observability. Consistent kwartaalbeats. Eigen portfolio naam — extra validatiewaarde. |
| **NOW** | ServiceNow | LARGE | Enterprise workflow AI. Hoge P/E — valideert skip bij overwaardering. |
| **ORCL** | Oracle | LARGE | Cloud + AI database. Sympathy play voor AI software sector. |
| **HUBS** | HubSpot | LARGE | CRM/marketing AI. Sympathy SNOW. Lage eigen catalyst frequentie. |

### AI PC / Edge (3)

| Ticker | Naam | Cap | Reden |
|---|---|---|---|
| **HPQ** | HP Inc. | LARGE | AI PC refresh bevestigd (EPS +21% beat). Incumbent Layer play. |
| **DELL** | Dell Technologies | LARGE | AI server + PC dual thesis. |
| **MSFT** | Microsoft | LARGE | Copilot + Azure anchor. Verwacht WATCH door mega-cap. |

### Power / Energy (4)

| Ticker | Naam | Cap | Reden |
|---|---|---|---|
| **GEV** | GE Vernova | LARGE | Grid infra — AI power demand thesis. Eigen portfolio naam. |
| **VST** | Vistra Energy | LARGE | Power producer — datacenter contracten. Hormuz hedge context. |
| **CEG** | Constellation Energy | LARGE | Nuclear clean energy — AI datacenter contracten. |
| **CCJ** | Cameco | LARGE | Uranium — Hormuz hedge. Eigenstandige thesis van AI regime. |

### Defense / Drones (extended) (2)

| Ticker | Naam | Cap | Reden |
|---|---|---|---|
| **ACHR** | Archer Aviation | SMALL | eVTOL — FAA certificering milestones. Pre-revenue maar institutioneel gefinancierd. |
| **JOBY** | Joby Aviation | SMALL | eVTOL leader — langzamere catalyst frequentie. Sympathy ACHR. |

### Data Center / Networking (1)

| Ticker | Naam | Cap | Reden |
|---|---|---|---|
| **EQIX** | Equinix | LARGE | Colocation REIT. Laag signal potentieel maar representeert DC-infra sector volledig. |

### Large Cap Anchors (5)

| Ticker | Naam | Cap | Reden |
|---|---|---|---|
| **GOOGL** | Alphabet | LARGE | AI + cloud anchor. Eigen portfolio naam. Verwacht BUY_SMALL bij volume. |
| **ASML** | ASML Holding | LARGE | EUV lithografie monopolie. Europese naam — valideert internationale tickers. |
| **TSM** | Taiwan Semiconductor | LARGE | Foundry monopolie. Geen gewone kwartaalcatalysts — Foundry momentum. |
| **AAPL** | Apple | LARGE | AI PC optionaliteit. Verwacht lage score door grote cap en lage dag-volatiliteit. |
| **META** | Meta Platforms | LARGE | AI infra + Llama — verwacht matig. Valideert engine bij mega-cap met AI thesis. |

### Control Group (uitgebreid) (1)

| Ticker | Naam | Cap | Reden |
|---|---|---|---|
| **WMT** | Walmart | LARGE | Retail incumbent. Verwacht SKIP/WATCH. Vergelijking met KO en JNJ controle. |

---

**Extended Universe totaal: 23 tickers**

---

## Aanbevolen Validatie Universe — 53 tickers totaal

```
Core (30)    + Extended (23)  = 53 tickers
```

### Dagelijkse run — 30 tickers

```bash
python scripts/validation_runner.py --ticker \
  NVDA MU AVBO CRDO VRT \
  IONQ QBTS RGTI QUBT \
  RKLB ASTS LUNR \
  UMAC RCAT KTOS PLTR AXON \
  CRWD PANW S ZS FTNT \
  ISRG TER BRZE \
  TEM HIMS \
  KO JNJ GLD
```

### Wekelijkse uitgebreide run — alle 53 tickers

```bash
python scripts/validation_runner.py --ticker \
  NVDA MU AVBO CRDO VRT \
  IONQ QBTS RGTI QUBT \
  RKLB ASTS LUNR \
  UMAC RCAT KTOS PLTR AXON ACHR JOBY \
  CRWD PANW S ZS FTNT \
  ISRG TER BRZE \
  TEM HIMS \
  SNOW DDOG NOW ORCL HUBS \
  HPQ DELL MSFT \
  GEV VST CEG CCJ \
  GOOGL ASML TSM AAPL META \
  EQIX \
  KO JNJ GLD WMT
```

---

## Sector Coverage Overzicht

| Sector | Core tickers | Extended tickers | Totaal |
|---|---|---|---|
| AI Infrastructure | NVDA, MU, AVBO, CRDO, VRT | — | 5 |
| AI Software / Orchestration | — | SNOW, DDOG, NOW, ORCL, HUBS | 5 |
| AI PC / Edge | — | HPQ, DELL, MSFT | 3 |
| Quantum Computing | IONQ, QBTS, RGTI, QUBT | — | 4 |
| Space / New Space | RKLB, ASTS, LUNR | — | 3 |
| Defense / Drones | UMAC, RCAT, KTOS, PLTR, AXON | ACHR, JOBY | 7 |
| Power / Energy | — | GEV, VST, CEG, CCJ | 4 |
| Cybersecurity | CRWD, PANW, S, ZS, FTNT | — | 5 |
| Robotics / Automation | ISRG, TER, BRZE | — | 3 |
| Healthcare Growth | TEM, HIMS | — | 2 |
| Data Center / Networking | VRT (gedeeld) | EQIX | 1 |
| Large Cap Anchors | — | GOOGL, ASML, TSM, AAPL, META | 5 |
| Control Group | KO, JNJ, GLD | WMT | 4 |
| **Totaal** | **30** | **23** | **53** |

---

## Waarom deze samenstelling?

**Minimaliseer selectie bias:** De universe bevat namen waarvan we zowel hoge als lage scores verwachten. QUBT, KO, JNJ en GLD zijn er bewust in opgenomen zodat de engine ook SKIP- en WATCH-beslissingen maakt. Een universe vol hoge momentum namen geeft een kunstmatig hoge win rate.

**Sector-leader + sympathy structuur:** Elk sector heeft minstens één leader (IONQ, RKLB, UMAC) en minstens één sympathy play (QBTS, LUNR, RCAT). Zo kan de OWN vs SYMPATHY catalyst-source classificatie worden gevalideerd op echte data.

**Cap-spreiding:** 12 LARGE caps, 14 MID/SMALL caps, 8 MICRO caps. Dit dwingt de engine om haar float_score en market_cap_tier logica te demonstreren over het volledige spectrum.

**Exclusies met reden:**

| Ticker | Reden voor exclusie |
|---|---|
| SMCI | Actief SEC/boekhoudingsprobleem per mei 2026 — Spelregel 3 automatisch veto |
| SOUN | Pre-earnings run van +15%+ recent — Spelregel 8 (halveer sizing bij >20% run); pas toe als separate test |
| APP | SEC-onderzoek AI data harvesting + insider selling >$50M — Spelregel 3 veto |
| CRWV | Relatief nieuw, beperkte Finnhub-dekking voor kwartalen geschiedenis |
| NBIS | Beperkte omzethistorie, minder liquide |

---

## Stabiliteitsvereisten

Deze universe is **bevroren** tot 29 juni 2026. Geen toevoegingen of verwijderingen gedurende de validatieperiode, tenzij:

1. **Delisting of handelsstop** → markeer als `active: false` in validation_watchlist.json
2. **Nieuw SEC-onderzoek** → verwijder onmiddellijk, documenteer in changelog
3. **Overname afgerond** → markeer als `active: false`

Alle andere aanpassingen (koersdoelen, sector-heat wijzigingen, nieuwe tickers) wachten tot na 29 juni 2026.

---

## Verwachte Score-verdeling

Op basis van de Task 3 engine-simulatie met vergelijkbare inputprofielen:

| Beslissing | Verwacht % | Verwacht aantal |
|---|---|---|
| BUY_MAX | 0–5% | 0–3 |
| BUY_STRONG | 3–8% | 1–4 |
| BUY_MODERATE | 10–20% | 3–10 |
| BUY_SMALL | 20–35% | 6–18 |
| WATCH | 25–35% | 7–18 |
| SKIP | 10–20% | 3–10 |
| BLOCKED | 0–3% | 0–2 |

Bij gemiddelde marktomstandigheden: **~10-15 BUY-signalen per dag** uit de core universe van 30.

---

## Statistische verwachting

Per `docs/VALIDATION_CHECKLIST.md`:

- **30 complete trades** (10d horizon) nodig voor eerste echte analyse
- Bij 10-15 BUY-signalen per dag, maar maximaal 1 per ticker per dag (deduplicatie): **2-3 weken** voor 30 complete trades
- **50+ complete trades** voor kalibratie-beslissingen
- Verwacht tijdspad: **8-9 weken** voor betrouwbare kalibratie op 10d horizon

---

## Changelog

| Datum | Versie | Wijziging |
|---|---|---|
| 2026-05-29 | v1.0 | Universe aangemaakt — 30 core + 23 extended |

---

*Momentum Intelligence · Igor × Claude · 29 mei 2026 · Geen formeel beleggingsadvies (Wft)*
