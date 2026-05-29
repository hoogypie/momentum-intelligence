# Validation Checklist — Momentum Intelligence

> **Doel:** Bewijzen of BUY-signalen voorspellende waarde hebben.
> Evidence before calibration.
> Geen scoring-wijzigingen voordat de drempels hieronder zijn gehaald.

---

## Dagelijkse workflow (15-20 min)

```bash
# Vóór markt (09:00–09:15 CET)
python scripts/validation_runner.py --ticker UMAC RCAT ASTS TEM SOUN RKLB KTOS ACHR CRDO CRWV LUNR IONQ QBTS RGTI QUBT PLTR JOBY APP

# Na markt (22:00–22:10 CET)
python scripts/paper_trade_report.py evaluate
python scripts/paper_trade_report.py report
```

Één run per dag per ticker. Deduplicatie voorkomt dubbele opslag bij meerdere runs.

---

## Wekelijkse workflow (vrijdag, 30 min)

```bash
python scripts/paper_trade_report.py report
python scripts/paper_trade_report.py report --decision BUY_MODERATE
python scripts/paper_trade_report.py report --decision BUY_SMALL
```

Noteer in spreadsheet:
- Totaal complete trades
- Win rate 5d overall
- Win rate 5d per beslissing
- Beste/slechtste trade die week
- Catalyst type verdeling van winners vs. losers

---

## Dagelijkse checklist

```
□ Één run vóór markt (09:00–09:15 CET)
□ evaluate na markt (22:00 CET)
□ Controleer rapport op errors / PARTIAL data
```

## Wekelijkse checklist

```
□ Noteer win rate 5d in spreadsheet
□ Noteer complete trade count
□ Check: zijn BUY_STRONG outcomes beter dan BUY_SMALL?
□ Check: zijn OWN catalyst outcomes beter dan SYMPATHY?
□ Check: is er één ticker die alle resultaten domineert?
```

## Bij 30 complete trades

```
□ Eerste echte analyse uitvoeren
□ Vergelijk win rates per beslissing
□ Vergelijk win rates per catalyst type
□ Besluit: validatie afronden of meer data verzamelen
```

## Bij 50 complete trades

```
□ Statistische significantie berekenen (binomial test)
□ Besluit: kalibratie starten of niet
□ Documenteer in DECISIONS.md
```

---

## Metrics om bij te houden

| Metric | Drempel voor actie | Frequentie |
|---|---|---|
| Win rate 5d overall | Onder 50% na 30+ trades → bekijk engine | Wekelijks |
| Gem. return 5d | Negatief na 20+ trades → probleem | Wekelijks |
| BUY_STRONG vs BUY_SMALL | BUY_STRONG moet beter presteren | Na 10 per klasse |
| OWN vs SYMPATHY catalyst | OWN moet hoger winnen | Na 15 per type |
| PARTIAL vs LIVE confidence | Verschil in outcomes? | Maandelijks |

---

## Wanneer is kalibratie gerechtvaardigd?

| Fase | Trades | Wat je kunt zeggen |
|---|---|---|
| Nu | 0 | Niets — begin te meten |
| A | 20–30 complete | "BUY-signalen presteren X% gemiddeld op 5d" — indicatief |
| B | 50+ complete | Statistisch significante win rate mogelijk bij 65%+ edge |
| C | 75+ complete | Betrouwbaar genoeg voor kalibratie per signal strength |
| D | 100+ complete | OWN vs. SYMPATHY catalyst splitsing betrouwbaar |

**Kalibratie is gerechtvaardigd als:**
- ≥50 complete trades
- Win rate 5d ≥65% OF gemiddeld rendement 5d ≥+3%
- BUY_STRONG presteert aantoonbaar beter dan BUY_SMALL
- OWN catalyst presteert beter dan SYMPATHY catalyst
- Resultaten consistent over ≥3 weken (niet één rally-week)

**Kalibratie is NIET gerechtvaardigd als:**
- Data van minder dan 3 weken
- Alle trades in één sector (bijv. alleen drones-week)
- Win rate gedreven door 1–2 uitschieters

---

## Bekende risico's in de methodologie

**Selectie bias** — de 18 tickers zijn gecureerd als momentum-kandidaten, niet willekeurig gekozen. Win rates zijn waarschijnlijk hoger dan op een willekeurige universe.

**Tijdsperiode bias** — resultaten bewijzen of de engine werkt *in het huidige regime*, niet universeel. Bull market in drones/quantum geeft te hoge win rates.

**Entry price is geen uitvoerbare prijs** — real return ligt 0.5–1% lager door slippage in liquide namen, meer in micro-caps.

**10d horizon** — een trade is pas compleet na 10 handelsdagen. Wacht met conclusies over 10d tot na de eerste 4 weken.

---

*Momentum Intelligence · Igor × Claude · 2026 · Geen formeel beleggingsadvies (Wft)*
