"""
MOMENTUM SCORE ENGINE v1.1
Igor × Claude — 28 mei 2026

Wijzigingen t.o.v. v1.0:
    Fix 1: Dag >40% skip penalty: 30 → 40 pts
            Reden: chaser scenario scoorde BUY_MAX ondanks +42% dag.
    Fix 2: Combinatieregel toegevoegd: catalyst=NONE + momentum<50 → SKIP
            Reden: pure hype zonder catalyst en zwak momentum = WATCH was te mild.

Architectuur principe:
    Skip Score gaat ALTIJD voor Momentum Score.
    Een aandeel met Momentum Score 95 maar Skip Score 100 = BLOCKED.
    Nooit kopen wat geblokkeerd is, ongeacht hoe sterk de momentum.

Geen AI, geen live data, geen side effects.
Pure functies + mock data voor logica-validatie.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import math


# ── ENUMS ─────────────────────────────────────────────────────────────────────

class Decision(Enum):
    BLOCKED      = "BLOCKED"       # Skip Score >= 100 — hard veto, nooit kopen
    SKIP         = "SKIP"          # Skip Score 50-99 — te veel risico
    WATCH        = "WATCH"         # Momentum 30-44 — op radar houden
    BUY_SMALL    = "BUY_SMALL"     # Momentum 45-59 — max €100-200
    BUY_MODERATE = "BUY_MODERATE"  # Momentum 60-74 — max €200-300
    BUY_STRONG   = "BUY_STRONG"    # Momentum 75-89 — max €300-400
    BUY_MAX      = "BUY_MAX"       # Momentum 90+   — max €400-500


class CatalystType(Enum):
    STRONG   = "STRONG"    # Earnings beat, gov contract, major partnership
    MODERATE = "MODERATE"  # Analyst upgrade, product launch, sector news
    WEAK     = "WEAK"      # Vague news, minor update, only social buzz
    NONE     = "NONE"      # No catalyst in last 48 hours


class RelativeStrength(Enum):
    STRONG_POSITIVE  = "STRONG_POSITIVE"   # Stock groen, markt rood
    MODERATE_POSITIVE = "MODERATE_POSITIVE" # Stock > markt (beide groen)
    NEUTRAL          = "NEUTRAL"           # Vergelijkbaar met markt
    UNDERPERFORMING  = "UNDERPERFORMING"   # Stock < markt


# ── INPUT DATACLASS ────────────────────────────────────────────────────────────

@dataclass
class TickerInput:
    """
    Ruwe marktdata per ticker.
    In v2 gevuld door Yahoo Finance / Finnhub.
    Nu: mock data voor logica-tests.
    """
    ticker: str

    # Prijs & volume
    price: float
    day_change_pct: float           # % verandering vandaag (bijv. 12.5 = +12.5%)
    premarket_pct: float            # % verandering pre-market (0 als markt open is)
    volume_today: int               # Aantal aandelen verhandeld vandaag
    avg_volume_20d: int             # 20-daags gemiddeld volume

    # Bedrijfsdata
    market_cap_usd: float           # In dollars (bijv. 500_000_000 = $500M)
    float_shares: Optional[int]     # Vrij verhandelbare aandelen (None = onbekend)
    is_cfd_only: bool               # Alleen als CFD op T212 = directe veto

    # Fundamentele context
    catalyst_type: CatalystType     # Kwaliteit van de meest recente catalyst
    catalyst_description: str       # Korte beschrijving van de catalyst
    relative_strength: RelativeStrength  # Koers vs markt vandaag
    sector_heat: int                # 0-100, uit sector config (handmatig)

    # Social
    social_mentions_today: int      # Mentions vandaag (StockTwits + Reddit)
    social_mentions_avg: int        # Gemiddelde per dag (20-daags)

    # Risico flags
    has_sec_investigation: bool     # SEC onderzoek actief
    has_class_action: bool          # Class action lopend
    insider_sells_90d: int          # Aantal insider sell transacties (90 dagen)


# ── OUTPUT DATACLASSES ────────────────────────────────────────────────────────

@dataclass
class SkipScoreResult:
    total: int
    is_hard_blocked: bool           # True als score >= 100 (SEC/CFD)
    reasons: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)  # Hard veto redenen


@dataclass
class MomentumScoreResult:
    total: float
    volume_anomaly: float           # Max 25 pts
    sector_heat_score: float        # Max 20 pts
    catalyst_quality: float         # Max 20 pts
    premarket_strength: float       # Max 15 pts
    relative_strength_score: float  # Max 10 pts
    social_acceleration: float      # Max 10 pts
    breakdown: dict[str, str]       # Menselijk leesbare uitleg per component


@dataclass
class ScoringResult:
    ticker: str
    decision: Decision
    momentum_score: float
    skip_score: int
    momentum_detail: MomentumScoreResult
    skip_detail: SkipScoreResult
    sizing_eur: str                 # Aanbevolen positiegrootte
    summary: str                   # Één zin samenvatting


# ── SKIP SCORE ENGINE ─────────────────────────────────────────────────────────

def calculate_skip_score(data: TickerInput) -> SkipScoreResult:
    """
    Skip Score berekening.

    Hard vetoes (score += 100, is_hard_blocked = True):
        - SEC onderzoek actief
        - Class action lopend
        - CFD-only op T212

    Soft skips (cumulatief):
        - >40% stijging op dag: +30 (te laat, Spelregel 8)
        - Premarket >40%: +40 (te laat pre-market)
        - Geen catalyst: +20
        - Volume onder gemiddelde: +25
        - >10 insider sells 90d: +15 (Spelregel 13 Framework)
    """
    score = 0
    reasons = []
    blocking_reasons = []
    is_hard_blocked = False

    # ── HARD VETOES ──
    if data.has_sec_investigation:
        score += 100
        is_hard_blocked = True
        blocking_reasons.append("SEC INVESTIGATION ACTIEF — Spelregel 3")

    if data.has_class_action:
        score += 100
        is_hard_blocked = True
        blocking_reasons.append("CLASS ACTION LOPEND — Spelregel 3")

    if data.is_cfd_only:
        score += 100
        is_hard_blocked = True
        blocking_reasons.append("CFD-ONLY OP T212 — Spelregel 29")

    # ── SOFT SKIPS ──
    if data.day_change_pct >= 40.0:
        score += 40
        reasons.append(f"Dag +{data.day_change_pct:.1f}% — te laat (>40%, Spelregel 8) [+40]")
    elif data.day_change_pct >= 20.0:
        score += 10
        reasons.append(f"Dag +{data.day_change_pct:.1f}% — significante pre-run, halveer sizing [+10]")

    if data.premarket_pct >= 40.0:
        score += 40
        reasons.append(f"Pre-market +{data.premarket_pct:.1f}% — volledig ingeprijsd [+40]")
    elif data.premarket_pct >= 20.0:
        score += 15
        reasons.append(f"Pre-market +{data.premarket_pct:.1f}% — wacht op consolidatie [+15]")

    if data.catalyst_type == CatalystType.NONE:
        score += 20
        reasons.append("Geen catalyst — puur social-driven risico [+20]")

    relative_vol = data.volume_today / data.avg_volume_20d if data.avg_volume_20d > 0 else 0
    if relative_vol < 0.8:
        score += 25
        reasons.append(f"Volume {relative_vol:.1f}x normaal — onder gemiddelde, geen institutioneel [+25]")

    if data.insider_sells_90d > 10:
        score += 15
        reasons.append(f"{data.insider_sells_90d} insider sells in 90d — Spelregel 13 [+15]")
    elif data.insider_sells_90d > 5:
        score += 8
        reasons.append(f"{data.insider_sells_90d} insider sells in 90d — elevated, monitor [+8]")

    return SkipScoreResult(
        total=score,
        is_hard_blocked=is_hard_blocked,
        reasons=reasons,
        blocking_reasons=blocking_reasons
    )


# ── MOMENTUM SCORE ENGINE ─────────────────────────────────────────────────────

def calculate_volume_anomaly(data: TickerInput) -> tuple[float, str]:
    """Volume anomaly score: max 25 punten."""
    if data.avg_volume_20d == 0:
        return 0.0, "Onvoldoende volume data"

    rv = data.volume_today / data.avg_volume_20d

    if rv >= 8.0:
        pts = 25.0
        label = f"{rv:.1f}x normaal — EXTREEM (institutioneel)"
    elif rv >= 5.0:
        pts = 20.0
        label = f"{rv:.1f}x normaal — HOOG"
    elif rv >= 3.0:
        pts = 15.0
        label = f"{rv:.1f}x normaal — ELEVATED"
    elif rv >= 2.0:
        pts = 10.0
        label = f"{rv:.1f}x normaal — VERHOOGD"
    elif rv >= 1.0:
        pts = 5.0
        label = f"{rv:.1f}x normaal — LICHT BOVEN GEMIDDELDE"
    else:
        pts = 0.0
        label = f"{rv:.1f}x normaal — ONDER GEMIDDELDE"

    return pts, label


def calculate_sector_heat_score(data: TickerInput) -> tuple[float, str]:
    """Sector heat score: max 20 punten. Input 0-100 van sector config."""
    pts = (data.sector_heat / 100.0) * 20.0
    if data.sector_heat >= 80:
        label = f"Sector heat {data.sector_heat}/100 — EXPLOSIEF"
    elif data.sector_heat >= 60:
        label = f"Sector heat {data.sector_heat}/100 — HOT"
    elif data.sector_heat >= 40:
        label = f"Sector heat {data.sector_heat}/100 — BUILDING"
    else:
        label = f"Sector heat {data.sector_heat}/100 — DORMANT"
    return round(pts, 1), label


def calculate_catalyst_quality(data: TickerInput) -> tuple[float, str]:
    """Catalyst quality: max 20 punten."""
    mapping = {
        CatalystType.STRONG:   (20.0, f"STERK: {data.catalyst_description}"),
        CatalystType.MODERATE: (12.0, f"MATIG: {data.catalyst_description}"),
        CatalystType.WEAK:     (4.0,  f"ZWAK: {data.catalyst_description}"),
        CatalystType.NONE:     (0.0,  "Geen catalyst in laatste 48u"),
    }
    return mapping[data.catalyst_type]


def calculate_premarket_strength(data: TickerInput) -> tuple[float, str]:
    """
    Premarket strength: max 15 punten.
    Sweet spot: +8% tot +20%. Boven +20% = afnemend signaal (Spelregel 8).
    Boven +40% = 0 punten (Skip Score neemt het over).
    """
    pct = data.premarket_pct

    if pct >= 40.0:
        pts, label = 0.0, f"+{pct:.1f}% — volledig ingeprijsd, geen alpha meer"
    elif pct >= 20.0:
        # Lineair afnemen van 15 naar 5 tussen 20% en 40%
        pts = 15.0 - ((pct - 20.0) / 20.0) * 10.0
        label = f"+{pct:.1f}% — hoog maar te ver, halveer sizing (Spelregel 8)"
    elif pct >= 8.0:
        pts = 15.0
        label = f"+{pct:.1f}% — SWEET SPOT voor pre-market entry"
    elif pct >= 3.0:
        pts = 8.0
        label = f"+{pct:.1f}% — licht positief pre-market"
    elif pct >= 0.0:
        pts = 3.0
        label = f"+{pct:.1f}% — neutraal/minimaal pre-market"
    else:
        pts = 0.0
        label = f"{pct:.1f}% — negatief pre-market"

    return round(pts, 1), label


def calculate_relative_strength(data: TickerInput) -> tuple[float, str]:
    """Relative strength vs markt: max 10 punten."""
    mapping = {
        RelativeStrength.STRONG_POSITIVE:   (10.0, "Groen terwijl markt rood — sterkste RS signaal"),
        RelativeStrength.MODERATE_POSITIVE: (7.0,  "Outperformt markt — positief signaal"),
        RelativeStrength.NEUTRAL:           (3.0,  "In lijn met markt — neutraal"),
        RelativeStrength.UNDERPERFORMING:   (0.0,  "Onderpresteert t.o.v. markt — negatief"),
    }
    return mapping[data.relative_strength]


def calculate_social_acceleration(data: TickerInput) -> tuple[float, str]:
    """Social acceleration: max 10 punten. Velocity telt, niet absolute mentions."""
    if data.social_mentions_avg == 0:
        return 0.0, "Geen social baseline beschikbaar"

    velocity = data.social_mentions_today / data.social_mentions_avg

    if velocity >= 10.0:
        pts, label = 10.0, f"{velocity:.0f}x normaal mentions — VIRAL"
    elif velocity >= 5.0:
        pts, label = 8.0, f"{velocity:.0f}x normaal mentions — ACCELERATING"
    elif velocity >= 2.0:
        pts, label = 5.0, f"{velocity:.1f}x normaal mentions — ELEVATED"
    elif velocity >= 1.0:
        pts, label = 2.0, f"{velocity:.1f}x normaal mentions — LICHT VERHOOGD"
    else:
        pts, label = 0.0, f"{velocity:.1f}x normaal mentions — NORMAAL/LAAG"

    return pts, label


def calculate_momentum_score(data: TickerInput) -> MomentumScoreResult:
    """Berekent de volledige Momentum Score (0-100)."""
    vol_pts,    vol_label    = calculate_volume_anomaly(data)
    heat_pts,   heat_label   = calculate_sector_heat_score(data)
    cat_pts,    cat_label    = calculate_catalyst_quality(data)
    pm_pts,     pm_label     = calculate_premarket_strength(data)
    rs_pts,     rs_label     = calculate_relative_strength(data)
    social_pts, social_label = calculate_social_acceleration(data)

    total = vol_pts + heat_pts + cat_pts + pm_pts + rs_pts + social_pts

    return MomentumScoreResult(
        total=round(total, 1),
        volume_anomaly=vol_pts,
        sector_heat_score=heat_pts,
        catalyst_quality=cat_pts,
        premarket_strength=pm_pts,
        relative_strength_score=rs_pts,
        social_acceleration=social_pts,
        breakdown={
            f"Volume Anomaly    (max 25)": f"{vol_pts:5.1f} — {vol_label}",
            f"Sector Heat       (max 20)": f"{heat_pts:5.1f} — {heat_label}",
            f"Catalyst Quality  (max 20)": f"{cat_pts:5.1f} — {cat_label}",
            f"Premarket Strength(max 15)": f"{pm_pts:5.1f} — {pm_label}",
            f"Relative Strength (max 10)": f"{rs_pts:5.1f} — {rs_label}",
            f"Social Acceleration(max 10)": f"{social_pts:5.1f} — {social_label}",
        }
    )


# ── DECISION ENGINE ───────────────────────────────────────────────────────────

def make_decision(momentum: float, skip: SkipScoreResult, data: TickerInput) -> tuple[Decision, str]:
    """
    Centrale beslissingslogica.
    Skip Score domineert altijd boven Momentum Score.

    Combinatieregel (v1.1):
        catalyst=NONE + momentum<50 → SKIP, ook als Skip Score <50.
        Rationale: pure social hype zonder fundamentele catalyst is te riskant
        voor elke positie boven WATCH-niveau. Momentum<50 betekent geen echt signaal.
    """
    if skip.is_hard_blocked:
        return Decision.BLOCKED, "; ".join(skip.blocking_reasons)

    if skip.total >= 50:
        top_reason = skip.reasons[0] if skip.reasons else "Meerdere skip flags"
        return Decision.SKIP, f"Skip Score {skip.total} ≥ 50 — {top_reason}"

    # Combinatieregel v1.1: geen catalyst + zwak momentum = altijd SKIP
    if data.catalyst_type == CatalystType.NONE and momentum < 50:
        return Decision.SKIP, "Combinatieregel: geen catalyst + momentum <50 — te riskant"

    if momentum >= 90:
        return Decision.BUY_MAX, "Uitzonderlijk sterk momentum signaal"
    elif momentum >= 75:
        return Decision.BUY_STRONG, "Sterk momentum signaal"
    elif momentum >= 60:
        return Decision.BUY_MODERATE, "Solide momentum signaal"
    elif momentum >= 45:
        return Decision.BUY_SMALL, "Matig momentum signaal"
    elif momentum >= 30:
        return Decision.WATCH, "Zwak signaal — op radar houden"
    else:
        return Decision.SKIP, "Onvoldoende momentum"


SIZING_MAP = {
    Decision.BLOCKED:      "€0 — GEBLOKKEERD",
    Decision.SKIP:         "€0 — SKIP",
    Decision.WATCH:        "Watchlist — nog niet kopen",
    Decision.BUY_SMALL:    "€100-200",
    Decision.BUY_MODERATE: "€200-300",
    Decision.BUY_STRONG:   "€300-400",
    Decision.BUY_MAX:      "€400-500",
}


# ── HOOFD SCORINGSFUNCTIE ─────────────────────────────────────────────────────

def score_ticker(data: TickerInput) -> ScoringResult:
    """
    Volledige scoring pipeline voor één ticker.
    Volgorde: Skip eerst, dan Momentum, dan Decision.
    """
    skip = calculate_skip_score(data)
    momentum = calculate_momentum_score(data)
    decision, reason = make_decision(momentum.total, skip, data)

    flag_str = " | ".join(skip.blocking_reasons + skip.reasons[:2]) if skip.reasons or skip.blocking_reasons else "Geen flags"

    summary = (
        f"{data.ticker}: {decision.value} — "
        f"Momentum {momentum.total:.0f}/100 | Skip {skip.total}/100 | {reason}"
    )

    return ScoringResult(
        ticker=data.ticker,
        decision=decision,
        momentum_score=momentum.total,
        skip_score=skip.total,
        momentum_detail=momentum,
        skip_detail=skip,
        sizing_eur=SIZING_MAP[decision],
        summary=summary,
    )


# ── RAPPORT PRINTER ───────────────────────────────────────────────────────────

def print_report(result: ScoringResult) -> None:
    WIDTH = 68
    COLORS = {
        "BLOCKED":      "\033[91m",  # rood
        "SKIP":         "\033[93m",  # geel
        "WATCH":        "\033[94m",  # blauw
        "BUY_SMALL":    "\033[96m",  # cyan
        "BUY_MODERATE": "\033[92m",  # groen
        "BUY_STRONG":   "\033[92m",
        "BUY_MAX":      "\033[92m",
    }
    RESET = "\033[0m"
    BOLD  = "\033[1m"
    c = COLORS.get(result.decision.value, "")

    print(f"\n{'═' * WIDTH}")
    print(f"  {BOLD}{result.ticker}{RESET}  →  {c}{BOLD}{result.decision.value}{RESET}  |  {result.sizing_eur}")
    print(f"{'─' * WIDTH}")

    # Skip Score
    skip = result.skip_detail
    skip_color = "\033[91m" if skip.total >= 100 else "\033[93m" if skip.total >= 50 else "\033[92m"
    print(f"  SKIP SCORE:     {skip_color}{skip.total:3d}/100{RESET}  {'⛔ HARD BLOCKED' if skip.is_hard_blocked else ('⚠  SKIP' if skip.total >= 50 else '✓  OK')}")
    for r in skip.blocking_reasons:
        print(f"    🔴 {r}")
    for r in skip.reasons:
        print(f"    ⚠  {r}")

    print(f"{'─' * WIDTH}")

    # Momentum Score
    ms = result.momentum_detail
    m_color = "\033[92m" if ms.total >= 60 else "\033[93m" if ms.total >= 40 else "\033[91m"
    print(f"  MOMENTUM SCORE: {m_color}{ms.total:5.1f}/100{RESET}")
    for label, detail in ms.breakdown.items():
        print(f"    {label}: {detail}")

    print(f"{'─' * WIDTH}")
    print(f"  {result.summary}")
    print(f"{'═' * WIDTH}")


# ── MOCK TEST CASES ───────────────────────────────────────────────────────────
#
#  Elke test case valideert een specifiek scenario.
#  Verwacht resultaat staat in de commentaar.
#
#  Leeswijzer:
#    ✓ = logica klopt als resultaat overeenkomt met verwachting
#    ✗ = formule aanpassen
#

MOCK_TICKERS = [

    # ── TEST 1: EXPLOSIVE momentum, geen skip flags ──────────────────────────
    # Scenario: UMAC vandaag — Pentagon news, volume 9x, pre-market +22%
    # Verwacht: BUY_STRONG of BUY_MAX, Skip Score laag
    TickerInput(
        ticker="UMAC_TEST1",
        price=26.31,
        day_change_pct=39.0,        # Net onder 40% grens
        premarket_pct=22.0,         # Hoog maar nog niet geblokkeerd
        volume_today=9_200_000,
        avg_volume_20d=1_100_000,   # 8.4x normaal
        market_cap_usd=1_200_000_000,
        float_shares=46_000_000,
        is_cfd_only=False,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Pentagon equity investment deals — WSJ rapport",
        relative_strength=RelativeStrength.STRONG_POSITIVE,
        sector_heat=98,
        social_mentions_today=8_400,
        social_mentions_avg=420,    # 20x normaal
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=2,
    ),

    # ── TEST 2: Hoge Momentum maar BLOCKED door SEC ───────────────────────────
    # Scenario: APP-achtige situatie — goede cijfers maar SEC onderzoek
    # Verwacht: BLOCKED (Skip Score 100+), ondanks sterke momentum
    TickerInput(
        ticker="APP_TEST2",
        price=385.0,
        day_change_pct=8.5,
        premarket_pct=6.0,
        volume_today=4_500_000,
        avg_volume_20d=800_000,     # 5.6x normaal
        market_cap_usd=25_000_000_000,
        float_shares=None,
        is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Sterke Q2 guidance, AI monetization groei",
        relative_strength=RelativeStrength.MODERATE_POSITIVE,
        sector_heat=82,
        social_mentions_today=3_200,
        social_mentions_avg=800,
        has_sec_investigation=True,  # ← HARD VETO
        has_class_action=False,
        insider_sells_90d=18,        # Massale insider selling
    ),

    # ── TEST 3: CFD-only — directe blokkade ──────────────────────────────────
    # Scenario: Ruimtevaart aandeel, geweldig momentum maar alleen CFD op T212
    # Verwacht: BLOCKED door CFD-only (Spelregel 29)
    TickerInput(
        ticker="SPACE_TEST3",
        price=142.0,
        day_change_pct=15.0,
        premarket_pct=12.0,
        volume_today=5_000_000,
        avg_volume_20d=600_000,
        market_cap_usd=6_000_000_000,
        float_shares=None,
        is_cfd_only=True,            # ← HARD VETO
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Record kwartaal, hypersonisch contract $190M",
        relative_strength=RelativeStrength.STRONG_POSITIVE,
        sector_heat=75,
        social_mentions_today=2_100,
        social_mentions_avg=310,
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=1,
    ),

    # ── TEST 4: SNOW pre-earnings setup (laag skip, hoog momentum) ────────────
    # Scenario: SNOW vóór earnings — stock -20% YTD, lage verwachtingen
    # Verwacht: BUY_STRONG of BUY_MAX (precies wat we gemist hadden)
    TickerInput(
        ticker="SNOW_TEST4",
        price=172.0,
        day_change_pct=-1.5,         # Licht negatief — lage verwachtingen
        premarket_pct=0.0,           # Pre-earnings, weinig pre-market actie
        volume_today=2_800_000,
        avg_volume_20d=1_200_000,    # 2.3x — opbouwend
        market_cap_usd=55_000_000_000,
        float_shares=None,
        is_cfd_only=False,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Q1 FY2027 earnings morgen — AI consumption inflection verwacht",
        relative_strength=RelativeStrength.NEUTRAL,
        sector_heat=78,
        social_mentions_today=1_800,
        social_mentions_avg=450,     # 4x — buzz bouwt op
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=0,
    ),

    # ── TEST 5: +42% dag — te laat instappen ─────────────────────────────────
    # Scenario: Naam die al +42% doet vandaag, geen reden meer om in te stappen
    # Verwacht: SKIP door Skip Score (dag > 40%)
    TickerInput(
        ticker="CHASER_TEST5",
        price=31.50,
        day_change_pct=42.0,         # ← Te laat (>40%)
        premarket_pct=35.0,
        volume_today=12_000_000,
        avg_volume_20d=800_000,      # 15x — maar te laat
        market_cap_usd=750_000_000,
        float_shares=25_000_000,
        is_cfd_only=False,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Contract overheid aangekondigd",
        relative_strength=RelativeStrength.STRONG_POSITIVE,
        sector_heat=90,
        social_mentions_today=9_000,
        social_mentions_avg=200,
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=0,
    ),

    # ── TEST 6: Sleeping Giant — volume begint op te bouwen ───────────────────
    # Scenario: Naam die maanden stil lag, volume begint te stijgen, nog geen hype
    # Verwacht: WATCH of BUY_SMALL — vroeg signaal
    TickerInput(
        ticker="SLEEPER_TEST6",
        price=18.40,
        day_change_pct=3.2,
        premarket_pct=2.1,
        volume_today=1_800_000,
        avg_volume_20d=600_000,      # 3x — begint op te bouwen
        market_cap_usd=380_000_000,
        float_shares=20_000_000,
        is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Sector peer rapporteerde sterk — sympathie move verwacht",
        relative_strength=RelativeStrength.MODERATE_POSITIVE,
        sector_heat=85,
        social_mentions_today=380,
        social_mentions_avg=120,     # 3.2x — licht verhoogd
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=1,
    ),

    # ── TEST 7: Pure social hype zonder catalyst ──────────────────────────────
    # Scenario: Reddit-hype aandeel, geen echt nieuws, lage liquiditeit
    # Verwacht: SKIP (geen catalyst +20, volume laag +25 = Skip 45+)
    TickerInput(
        ticker="HYPE_TEST7",
        price=4.20,
        day_change_pct=18.0,
        premarket_pct=8.0,
        volume_today=400_000,
        avg_volume_20d=600_000,      # 0.67x — ONDER gemiddelde
        market_cap_usd=80_000_000,
        float_shares=19_000_000,
        is_cfd_only=False,
        catalyst_type=CatalystType.NONE,  # ← Geen catalyst
        catalyst_description="Geen recent nieuws — alleen Reddit buzz",
        relative_strength=RelativeStrength.MODERATE_POSITIVE,
        sector_heat=50,
        social_mentions_today=4_000,
        social_mentions_avg=80,      # 50x — pure meme hype
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=0,
    ),

    # ── TEST 8: Quantum sympathy play — IONQ heeft bewogen ────────────────────
    # Scenario: QBTS nadat IONQ +15% deed — sympathy play nog niet bewogen
    # Verwacht: BUY_MODERATE of BUY_STRONG
    TickerInput(
        ticker="QBTS_TEST8",
        price=27.80,
        day_change_pct=4.5,          # Nog niet bewogen
        premarket_pct=3.2,
        volume_today=2_200_000,
        avg_volume_20d=800_000,      # 2.75x — begint te bouwen
        market_cap_usd=2_800_000_000,
        float_shares=None,
        is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Sympathy: IONQ +18% door US $2B quantum funding — QBTS nog niet bewogen",
        relative_strength=RelativeStrength.MODERATE_POSITIVE,
        sector_heat=92,              # Quantum sector HOT
        social_mentions_today=1_200,
        social_mentions_avg=280,     # 4.3x
        has_sec_investigation=False,
        has_class_action=False,
        insider_sells_90d=0,
    ),

]


# ── MAIN: TEST ALLE CASES ─────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "█" * 68)
    print("  MOMENTUM SCORE ENGINE v1.0 — TEST RUN")
    print("  Igor × Claude — 28 mei 2026")
    print("█" * 68)

    results = []
    for mock in MOCK_TICKERS:
        result = score_ticker(mock)
        results.append(result)
        print_report(result)

    # ── SAMENVATTING ──────────────────────────────────────────────────────────
    print("\n" + "═" * 68)
    print("  SAMENVATTING — ALLE TEST CASES")
    print("═" * 68)
    print(f"  {'TICKER':<18} {'DECISION':<16} {'MOMENTUM':>8} {'SKIP':>6}  SIZING")
    print(f"  {'─'*18} {'─'*16} {'─'*8} {'─'*6}  {'─'*14}")

    DECISION_COLORS = {
        Decision.BLOCKED:      "\033[91m",
        Decision.SKIP:         "\033[93m",
        Decision.WATCH:        "\033[94m",
        Decision.BUY_SMALL:    "\033[96m",
        Decision.BUY_MODERATE: "\033[92m",
        Decision.BUY_STRONG:   "\033[92m",
        Decision.BUY_MAX:      "\033[92m",
    }

    for r in results:
        c = DECISION_COLORS.get(r.decision, "")
        print(f"  {r.ticker:<18} {c}{r.decision.value:<16}\033[0m {r.momentum_score:>8.1f} {r.skip_score:>6}  {r.sizing_eur}")

    print("═" * 68)
    print()
    print("  LOGICA VERWACHTINGEN:")
    print("  TEST1 UMAC    → BUY_STRONG/MAX  (explosief, geen veto)")
    print("  TEST2 APP     → BLOCKED         (SEC investigation)")
    print("  TEST3 SPACE   → BLOCKED         (CFD-only)")
    print("  TEST4 SNOW    → BUY_STRONG/MAX  (pre-earnings setup)")
    print("  TEST5 CHASER  → SKIP            (+42% dag, te laat)")
    print("  TEST6 SLEEPER → WATCH/BUY_SMALL (vroeg signaal)")
    print("  TEST7 HYPE    → SKIP            (geen catalyst, laag volume)")
    print("  TEST8 QBTS    → BUY_MODERATE    (sympathy play quantum)")
    print()
