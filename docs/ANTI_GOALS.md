# ANTI-GOALS — WAT DIT SYSTEEM NIET IS
> Expliciete grenzen. Net zo belangrijk als de doelen zelf.
> Laatste update: 28 mei 2026

---

## Waarom Anti-Goals?

Een systeem zonder expliciete grenzen groeit ongecontroleerd.
Elke feature die hieronder staat is een bewuste **nee** — niet bij gebrek aan ideeën,
maar omdat het de kern van het systeem zou verzwakken.

---

## 1. DIT IS GEEN GEAUTOMATISEERDE TRADING BOT

**Wat het NIET doet:** Orders plaatsen, posities openen/sluiten, stop-losses automatisch instellen.

**Waarom:** Geautomatiseerde uitvoering vereist:
- Risicomodellen die menselijke context begrijpen
- Broker-integratie met audit trail
- Regulatoire compliance (MiFID II, Wft)
- Failure recovery bij API-fouten

**Wat het WEL doet:** Beslissingsondersteuning. Igor beslist. Igor klikt.

---

## 2. DIT IS GEEN FUNDAMENTELE ANALYSE TOOL

**Wat het NIET doet:** DCF-modellen, P/E ratio's, balance sheet analyse, earnings forecasts, fair value berekeningen.

**Waarom:** Momentum trading en fundamentele analyse zijn complementaire maar aparte processen. Fundamentele analyse is voor de Core Portfolio (BtB framework). Dit systeem is voor momentum plays (max 5-10% van portfolio).

**Verwisseling is gevaarlijk:** Een fundamenteel zwak bedrijf kan kortstondig sterk momentum hebben. De engine detecteert het momentum, niet de kwaliteit van het bedrijf.

---

## 3. DIT IS GEEN VOORSPELLINGSMODEL

**Wat het NIET doet:** Koersdoelen berekenen, toekomstige prices voorspellen, "dit gaat naar $50" claims maken.

**Waarom:** De engine scoort de **huidige toestand van momentum-signalen**. Het rankt setup-kwaliteit. Hoge score = betere setup, niet gegarandeerd rendement.

**Eerlijk:** Een score van 95/100 betekent dat alle signalen goed aligned zijn. Het betekent niet dat het aandeel stijgt. Macro-events, spoofing, en random news kunnen elke setup overrulen.

---

## 4. DIT IS GEEN REAL-TIME HIGH-FREQUENCY SYSTEEM

**Wat het NIET doet:** Tick-by-tick data verwerken, milliseconde latency, intraday scalping signals.

**Waarom:** De data-bronnen (Yahoo Finance, Finnhub free) hebben inherente vertraging. De beslissingslogica werkt op minuut-niveau, niet seconde-niveau. High-frequency trading vereist co-located servers en L1/L2 orderbook data.

**Doelgroep:** Retail belegger die een paar keer per dag kijkt, niet een HFT desk.

---

## 5. DIT IS GEEN PORTFOLIO MANAGEMENT SYSTEEM

**Wat het NIET doet:** Portfolio tracking, P&L berekeningen, positie-grootte management over alle posities heen, risico-allocatie across het hele portfolio.

**Waarom:** Portfolio management is de verantwoordelijkheid van T212 + BELEGGINGEN.docx + FRAMEWORK.docx. Dit systeem identificeert kandidaten. Het beheert geen portfolio.

**Grens:** Het systeem geeft een sizing aanbeveling per ticker (bijv. "€200-300"). Het weet niet of je al €2.000 in andere momentum plays hebt. Igor houdt dit bij.

---

## 6. DIT VERVANGT GEEN MENSELIJK OORDEEL

**Wat het NIET doet:** Finale handelsbeslissingen maken, context begrijpen die niet in de data zit, politieke risico's, CEO-karakter, management track record beoordelen.

**Waarom:** De engine werkt op getallen. Het weet niet dat een CEO berucht is voor misleidende guidance. Het weet niet dat een "government contract" eigenlijk een LOI is zonder binding commitment. Igor weet dat wel.

**Werking:** Score engine → ChatGPT challenge → Igor beslist. Nooit: Score engine → automatisch handelen.

---

## 7. DIT IS GEEN MULTI-ASSET SYSTEEM

**Wat het NIET doet:** Crypto, opties, forex, futures, ETFs, obligaties analyseren.

**Waarom:** De score-componenten zijn gekalibreerd op US equity momentum. Volume anomaly voor crypto heeft andere baselines. Opties hebben hun eigen volatility-metrics. Andere asset classes vereisen fundamenteel andere scores.

**Scope:** US equity stocks beschikbaar als echt aandeel op T212.

---

## 8. DIT IS GEEN SOCIALE MEDIA HYPE DETECTOR

**Wat het WEL doet:** Social acceleration als één van zeven componenten meten.
**Wat het NIET doet:** Reddit-posts volgen, influencer tracking, sentiment analysis op tekst.

**Social Quality Cap is exact hiervoor bedacht:** Social kan nooit meer dan 8/100 punten bijdragen, en bij gebrek aan catalyst wordt dat gecapped op 2/100. Een aandeel dat viraal gaat op Reddit zonder enige andere signal scoort maximaal 30-35 punten — SKIP of WATCH.

---

## 9. DIT WORDT NIET GEDEELD OF COMMERCIEEL GEBRUIKT (NU)

**Wat het NIET is:** Een SaaS product, een abonnementsdienst, een algo-trading platform voor meerdere gebruikers.

**Waarom nu:** De engine is nog niet gevalideerd op historische data. Deployment voor anderen vereist:
- Backtesting resultaten
- Risk disclaimers (Wft/MiFID)
- Robuuste error handling
- Schaalbaarheid

**Later mogelijk:** Als de engine aantoonbaar alpha genereert over 6+ maanden live gebruik, is commercialisering een optie. Dat is fase 7+.

---

## 10. DIT IS GEEN VERVANGER VAN HET BEAT-THE-BEAT FRAMEWORK

**Wat het NIET doet:** Earnings plays scoren, BtB-scores berekenen, pre-earnings setups identificeren.

**Waarom:** BtB en Momentum zijn twee aparte strategieën met aparte logica:
- BtB: fundamentele analysis + earnings catalysts + 3+ kwartalen data
- Momentum: prijs/volume signalen + sector rotatie + hot money detectie

Ze zijn complementair. SNOW pre-earnings scoort bewust laag in momentum (56/100 = BUY_SMALL) omdat het momentum nog niet begonnen is. Dat is correct gedrag, geen fout.
