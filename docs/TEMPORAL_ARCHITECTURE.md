# TEMPORAL ARCHITECTURE — MOMENTUM INTELLIGENCE
> v2.5 | Laatste update: 28 mei 2026

---

## 1. WAAROM HISTORISCHE MEMORY?

Een real-time score van 72 vertelt je: *nu is er momentum.*
Maar het vertelt je niet:
- Is dat momentum er al 4 uur of 4 minuten?
- Is het stijgend of aan het verdwijnen?
- Is dit de derde keer dat IONQ breakout bereikt, of de eerste?
- Wanneer verscheen het catalyst? Was er eerder al een weaker catalyst?

v2.5 voegt **tijddimensie** toe. De backend onthoudt wat er is gescoord,
registreert wanneer fases veranderen, en past decay toe op verouderde signalen.

---

## 2. ARCHITECTUUR

```
/analyze/{ticker}
    ↓ score_ticker()
    ↓ ScoringResult
    ↓
_persist_snapshot()          ← Elke succesvolle scoring wordt opgeslagen
    │
    ├── save_snapshot_dict()     storage/data/tickers/{TICKER}.jsonl
    ├── record_transition_if_changed()  {TICKER}_transitions.jsonl
    └── record_catalyst_if_changed()   {TICKER}_catalysts.jsonl

/sector/{sector_name}
    ↓ leaders gescoord
    └── save_sector_snapshot()   storage/data/sectors/{SECTOR_ID}.jsonl

GET /history/{ticker}
    └── get_signal_evolution()   leest + past decay toe

GET /history/{ticker}/window
    └── get_momentum_window()    is het signaal nog actionable?

GET /sector/{name}/trend
    └── get_sector_evolution()   heat trend over tijd
```

---

## 3. OPSLAG FORMAAT

**Locatie:** `storage/data/` (gitignored — nooit committen)

**Format:** JSON Lines (.jsonl) — één object per regel, append-only

**Structuur:**
```
storage/data/
├── tickers/
│   ├── IONQ.jsonl              ← Scoring snapshots
│   ├── IONQ_transitions.jsonl  ← Fase-overgangen
│   ├── IONQ_catalysts.jsonl    ← Catalyst wijzigingen
│   ├── QBTS.jsonl
│   └── ...
└── sectors/
    ├── quantum.jsonl
    └── ...
```

**Snapshot velden:**
```json
{
  "version_id":     "20260528T210000_123456_IONQ",
  "ticker":         "IONQ",
  "timestamp":      "2026-05-28T21:00:00Z",
  "decision":       "BUY_MODERATE",
  "momentum_score": 64.5,
  "skip_score":     0,
  "phase":          "BREAKOUT",
  "confidence":     "LIVE",
  "cache_hit":      false,
  "data_age_seconds": 0.0,
  "catalyst_type":  "STRONG",
  "day_change_pct": 8.5,
  "volume_ratio":   3.2,
  "sector_heat":    92,
  "market_session": "REGULAR",
  "price":          41.80
}
```

**Retentie:** Max 500 snapshots per ticker (±50KB). Oudste verwijderd automatisch.

---

## 4. SIGNAL DECAY MODEL

Een momentum signaal veroudert. Een BUY_STRONG van 6 uur geleden is minder
relevant dan dezelfde score van 10 minuten geleden.

### Leeftijdscategorieën

| Categorie | Leeftijd | Decay multiplier | Decision effect |
|---|---|---|---|
| FRESH   | 0 – 2u   | 1.00  | Geen downgrade |
| AGING   | 2 – 8u   | 0.85  | Geen downgrade |
| STALE   | 8 – 24u  | 0.65  | 1 stap lager (BUY_MAX → BUY_STRONG) |
| OLD     | 24 – 48u | 0.40  | Altijd WATCH |
| EXPIRED | > 48u    | 0.00  | Altijd SKIP |

### FRENZY extra decay
FRENZY-fase signalen verouderen sneller (momentum-window is korter):
`multiplier × 0.70` voor AGING/STALE in FRENZY fase.

### Actionable definitie
Een signaal is actionable als:
- `effective_decision` niet SKIP, BLOCKED of WATCH is
- `signal_age` niet OLD of EXPIRED is

### Effectieve score
```
effective_score = original_score × decay_multiplier
```

### Effectieve decision downgrade
```
STALE: 1 stap lager (BUY_SMALL → WATCH)
OLD:   Altijd WATCH
EXPIRED: Altijd SKIP
BLOCKED/SKIP: Onveranderd
```

---

## 5. FASE TRANSITIE TRACKING

Elke keer dat een ticker van fase verandert (bijv. NEUTRAL → ACCUMULATION),
wordt dit geregistreerd met:
- `from_phase`, `to_phase`
- `timestamp` van de detectie
- `momentum_score` en `decision` op moment van overgang
- `version_id` van de triggerende snapshot

**Detectionlogica:** Vergelijkt `phase` van huidige snapshot met vorige snapshot.
Als anders → transitie opgeslagen.

**Fase volgorde (typisch):**
```
NEUTRAL → ACCUMULATION → BREAKOUT → EXPANSION → FRENZY → EXHAUSTION → NEUTRAL
```

---

## 6. CATALYST TIJDLIJN

Elke keer dat het catalyst type verandert (bijv. NONE → STRONG bij een earnings
beat), wordt dit geregistreerd met:
- `catalyst_type` (nieuw)
- `previous_type` (oud)
- `catalyst_desc` (eerste 100 tekens)
- `timestamp` van de detectie

Bruikbaar voor: "wanneer verscheen het catalyst precies?"

---

## 7. MOMENTUM TREND

Berekend door recente snapshots te vergelijken met oudere:
- Gemiddelde van laatste 3 scores vs vorige 3 scores
- Delta > +5 → IMPROVING
- Delta < -5 → DETERIORATING
- Overig → STABLE
- Te weinig data → INSUFFICIENT_DATA

---

## 8. SECTOR HISTORY

Na elke `/sector/{sector_name}` call wordt een sector snapshot opgeslagen:
- `heat` (0-100)
- `avg_momentum` van leaders
- `avg_skip` van leaders
- `leader_decisions` dict (ticker → decision)

**Trend analyse:**
- `get_heat_trend()` → lijst van heat waardes (nieuwste eerst)
- `is_sector_heating_up()` → vergelijkt eerste helft met tweede helft

---

## 9. MOMENTUM WINDOW

Beantwoordt: **"Is het signaal nu nog actionable?"**

`GET /history/{ticker}/window` combineert:
1. Signal age → FRESH/AGING/STALE/OLD/EXPIRED
2. Decay → effective_decision + effective_score
3. Momentum trend → IMPROVING/DETERIORATING/STABLE

`window_open = True` als:
- Signaal is FRESH of AGING
- Effectieve beslissing is een BUY variant
- Trend is niet DETERIORATING

---

## 10. API ENDPOINTS

| Endpoint | Beschrijving |
|---|---|
| `GET /history/{ticker}` | Signaal evolutie, decay, trend, overgangen |
| `GET /history/{ticker}/window` | Is momentum window open? |
| `GET /history/{ticker}/transitions` | Fase-overgangen + catalyst tijdlijn |
| `GET /sector/{name}/trend` | Sector heat trend over tijd |

**Parameters:**
- `hours=24` — tijdvenster voor evolutie (1-168u)
- `limit=50` — max snapshots (1-200)

---

## 11. PRIVACY EN OPSLAG

`storage/data/` staat in `.gitignore`. Bevat geen gevoelige data maar
wel ticker-specifieke scoring history. Verwijder handmatig indien gewenst.

**Maximale opslag:** ~50KB per ticker (500 snapshots × ~100 bytes)
Voor 100 getrackte tickers: ~5MB totaal.
