# SCORE ENGINE — TECHNISCHE SPECIFICATIE
> Volledige documentatie van scoring_v1_1.py
> Laatste update: 28 mei 2026

---

## 1. ARCHITECTUUR PRINCIPE

```
INPUT (TickerInput)
    ↓
Skip Score Engine        ← draait ALTIJD eerst
    ↓
  ≥ 100? → BLOCKED (hard veto, stop hier)
  ≥  50? → SKIP (stop hier)
  Combo? → SKIP (stop hier)
    ↓
Momentum Score Engine    ← draait alleen als Skip Score < 50
    ↓
Decision Engine          ← mapt score naar actie
    ↓
OUTPUT (ScoringResult)
```

**Kerninvariant:** Geen Momentum Score kan een Skip Score van ≥ 100 overschrijven. Ooit. Punt.

---

## 2. INPUT STRUCTUUR (TickerInput)

```python
@dataclass
class TickerInput:
    ticker: str

    # Prijs & volume
    price: float
    day_change_pct: float        # % verandering vandaag
    premarket_pct: float         # % verandering pre-market
    volume_today: int
    avg_volume_20d: int          # 20-daags gemiddeld volume

    # Bedrijfsdata
    market_cap_usd: float
    float_shares: Optional[int]  # None = onbekend
    is_cfd_only: bool            # T212 CFD-only check

    # Fundamentele context
    catalyst_type: CatalystType  # STRONG/MODERATE/WEAK/NONE
    catalyst_description: str
    relative_strength: RelativeStrength
    sector_heat: int             # 0-100, uit sectors.json

    # Social
    social_mentions_today: int
    social_mentions_avg: int     # 20-daags gemiddelde

    # Risico flags
    has_sec_investigation: bool
    has_class_action: bool
    insider_sells_90d: int
```

---

## 3. SKIP SCORE ENGINE

### Hard Vetoes (onomkeerbaar)

| Conditie | Punten | Reden |
|---|---|---|
| `has_sec_investigation = True` | +100 | Spelregel 3 FRAMEWORK.docx |
| `has_class_action = True` | +100 | Spelregel 3 FRAMEWORK.docx |
| `is_cfd_only = True` | +100 | Spelregel 29 FRAMEWORK.docx |

Hard vetoes zijn **cumulatief** (meerdere vetoes = meerdere keren +100).

### Soft Skips (cumulatief)

| Conditie | Punten | Rationale |
|---|---|---|
| `day_change_pct >= 40.0` | +40 | Te laat voor entry, Spelregel 8 |
| `day_change_pct >= 20.0` | +10 | Pre-run risico |
| `premarket_pct >= 40.0` | +40 | Volledig ingeprijsd pre-market |
| `premarket_pct >= 20.0` | +15 | Hoog pre-market, halveer sizing |
| `volume_today / avg_volume_20d < 0.8` | +25 | Geen institutioneel volume |
| `catalyst_type == NONE` | +20 | Pure hype risico |
| `insider_sells_90d > 10` | +15 | Spelregel 13 FRAMEWORK.docx |
| `insider_sells_90d > 5` | +8 | Monitor |

### Drempels

```python
skip_score >= 100  →  BLOCKED (is_hard_blocked = True)
skip_score >= 50   →  SKIP
skip_score <  50   →  OK, ga door naar Momentum Score
```

---

## 4. MOMENTUM SCORE ENGINE

### 4.1 Volume Anomaly (max 25 pts)

```python
rv = volume_today / avg_volume_20d

rv >= 8.0  →  25 pts  # EXTREEM (institutioneel)
rv >= 5.0  →  20 pts  # HOOG
rv >= 3.0  →  15 pts  # ELEVATED
rv >= 2.0  →  10 pts  # VERHOOGD
rv >= 1.0  →   5 pts  # LICHT BOVEN GEMIDDELDE
rv <  1.0  →   0 pts  # ONDER GEMIDDELDE
```

### 4.2 Sector Heat (max 20 pts)

```python
pts = (sector_heat / 100.0) * 20.0

# Sector heat = integer 0-100 uit config/sectors.json
# 80-100 → 16-20 pts (EXPLOSIEF/HOT)
# 60-79  → 12-16 pts (HOT/BUILDING)
# 40-59  →  8-12 pts (BUILDING/STABLE)
# 0-39   →   0-8 pts (DORMANT)
```

### 4.3 Catalyst Quality (max 20 pts)

```python
CatalystType.STRONG   →  20 pts  # Earnings beat, gov contract, major deal
CatalystType.MODERATE →  12 pts  # Analyst upgrade, product launch, sector news
CatalystType.WEAK     →   4 pts  # Vage news, minor update, social buzz only
CatalystType.NONE     →   0 pts  # Geen nieuws afgelopen 48u
```

### 4.4 Premarket Strength (max 15 pts)

```python
pct >= 40.0  →   0 pts  # Volledig ingeprijsd (Skip Score pakt dit ook op)
pct >= 20.0  →  lineair 15→5 pts  # Afnemend signaal (Spelregel 8 zone)
pct >=  8.0  →  15 pts  # SWEET SPOT
pct >=  3.0  →   8 pts  # Licht positief
pct >=  0.0  →   3 pts  # Neutraal
pct <   0.0  →   0 pts  # Negatief

# Lineaire interpolatie 20-40%:
# pts = 15.0 - ((pct - 20.0) / 20.0) * 10.0
```

### 4.5 Relative Strength (max 10 pts)

```python
RelativeStrength.STRONG_POSITIVE    →  10 pts  # Groen bij rode markt
RelativeStrength.MODERATE_POSITIVE  →   7 pts  # Outperformt markt
RelativeStrength.NEUTRAL            →   3 pts  # In lijn met markt
RelativeStrength.UNDERPERFORMING    →   0 pts  # Onderpresteert
```

### 4.6 Social Acceleration (max 10 pts)

```python
velocity = social_mentions_today / social_mentions_avg

velocity >= 10.0  →  10 pts  # VIRAL
velocity >=  5.0  →   8 pts  # ACCELERATING
velocity >=  2.0  →   5 pts  # ELEVATED
velocity >=  1.0  →   2 pts  # LICHT VERHOOGD
velocity <   1.0  →   0 pts  # NORMAAL/LAAG
```

---

## 5. COMBINATIEREGEL (v1.1)

```python
if catalyst_type == NONE and momentum_score < 50:
    return Decision.SKIP, "Combinatieregel: geen catalyst + momentum <50"
```

**Rationale:** Pure social hype zonder fundamentele catalyst is te riskant voor BUY_SMALL of hoger. Als het momentum toch al laag is (<50) én er geen catalyst is, is er geen reden om te kopen.

**Volgorde:** Combinatieregel wordt geëvalueerd ná Skip Score check maar vóór Decision mapping.

---

## 6. DECISION MAPPING

```python
# Skip-first altijd
if skip.is_hard_blocked:       return BLOCKED
if skip.total >= 50:           return SKIP
if combo_rule_triggered:       return SKIP

# Dan momentum
if momentum >= 90:  return BUY_MAX      # €400-500
if momentum >= 75:  return BUY_STRONG   # €300-400
if momentum >= 60:  return BUY_MODERATE # €200-300
if momentum >= 45:  return BUY_SMALL    # €100-200
if momentum >= 30:  return WATCH        # Watchlist
else:               return SKIP         # €0
```

---

## 7. TEST SUITE (v1.1 — 8/8 ✅)

### Test Doelen

| Test | Scenario | Wat wordt getest |
|---|---|---|
| UMAC_TEST1 | Explosieve move, geen flags | Happy path — BUY_MAX |
| APP_TEST2 | Hoog momentum maar SEC | Hard veto overschrijft alles |
| SPACE_TEST3 | Sterk momentum maar CFD | CFD hard veto |
| SNOW_TEST4 | Pre-earnings, geen momentum | BtB ≠ momentum (bewust BUY_SMALL) |
| CHASER_TEST5 | +42% dag | Dag >40% = SKIP (penalty fix v1.1) |
| SLEEPER_TEST6 | Volume opbouwen, vroeg | Vroeg signaal detectie |
| HYPE_TEST7 | Reddit hype, geen catalyst | Combinatieregel (v1.1 fix) |
| QBTS_TEST8 | Sympathy play quantum | Sector heat + sympathy context |

### Verwachte Outputs (v1.1)

```
UMAC_TEST1    → BUY_MAX     (98.6 momentum, 25 skip)
APP_TEST2     → BLOCKED     (68.4 momentum, 115 skip — SEC)
SPACE_TEST3   → BLOCKED     (93.0 momentum, 100 skip — CFD)
SNOW_TEST4    → BUY_SMALL   (56.6 momentum,   0 skip — BtB, bewust)
CHASER_TEST5  → SKIP        (90.5 momentum,  55 skip — dag +42%)
SLEEPER_TEST6 → BUY_SMALL   (59.0 momentum,   0 skip)
HYPE_TEST7    → SKIP        (42.0 momentum,  45 skip — combinatieregel)
QBTS_TEST8    → BUY_MODERATE(60.4 momentum,   0 skip)
```

### Uitvoeren

```bash
python3 scoring/scoring_v1_1.py
```

Verwachte output: 8/8 ✅ in de samenvatting.

---

## 8. FASE 2 INTEGRATIE

In fase 2 wordt `TickerInput` gevuld vanuit live data:

```python
# data/assembler.py (fase 2)
async def build_ticker_input(ticker: str) -> TickerInput:
    price_data    = await yahoo.get_quote(ticker)
    news_data     = await finnhub.get_news(ticker)
    social_data   = await stocktwits.get_mentions(ticker)
    sector_config = load_sector_config(ticker)

    return TickerInput(
        ticker=ticker,
        price=price_data.price,
        day_change_pct=price_data.change_pct,
        premarket_pct=price_data.premarket_pct,
        volume_today=price_data.volume,
        avg_volume_20d=price_data.avg_volume_20d,
        # ... etc
        catalyst_type=classify_catalyst(news_data),
        sector_heat=sector_config.heat,
        social_mentions_today=social_data.mentions_today,
        social_mentions_avg=social_data.mentions_avg,
    )
```

`score_ticker()` wordt dan aangeroepen met dit live `TickerInput` object. De score engine zelf verandert niet.

---

## 9. VERSIEHISTORIE

| Versie | Datum | Wijziging |
|---|---|---|
| v1.0 | 28 mei 2026 | Initiële engine — 5/8 tests correct |
| v1.1 | 28 mei 2026 | Fix dag >40% penalty (30→40), combinatieregel toegevoegd — 8/8 |
