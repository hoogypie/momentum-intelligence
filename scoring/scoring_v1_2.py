"""
MOMENTUM SCORE ENGINE v1.2
Igor × Claude — 28 mei 2026

Wijzigingen t.o.v. v1.1:
    1. Float Score (max 8 pts) — lage float = hogere momentum amplificatie
    2. Market Cap Tier — MICRO/SMALL/MID/LARGE, beïnvloedt sizing (niet score)
    3. Phase Label — ACCUMULATION/BREAKOUT/EXPANSION/FRENZY/EXHAUSTION
    4. Social Quality Cap — social mag NOOIT alleen tot BUY leiden:
          catalyst=NONE     → social gecapped op 2 pts
          catalyst=WEAK     → social gecapped op 4 pts
          catalyst=MODERATE → social gecapped op 6 pts
          catalyst=STRONG   → volledige 8 pts
    5. Gewichten herbalanceerd (totaal = 100):
          Volume:    25 → 22  (-3)
          Heat:      20 → 18  (-2)
          Premarket: 15 → 14  (-1)
          Social:    10 →  8  (-2, gecapped)
          Float:      0 →  8  (nieuw)
    6. SectorConfig dataclass — sector data als expliciete input (geen hardcoding)

Kernprincipe:
    Skip Score gaat ALTIJD vóór Momentum Score.
    Social kan NOOIT als enige driver tot een koopsignaal leiden.
    Geen AI, geen live data, geen side effects.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import json, os


# ── ENUMS ─────────────────────────────────────────────────────────────────────

class Decision(Enum):
    BLOCKED      = "BLOCKED"
    SKIP         = "SKIP"
    WATCH        = "WATCH"
    BUY_SMALL    = "BUY_SMALL"
    BUY_MODERATE = "BUY_MODERATE"
    BUY_STRONG   = "BUY_STRONG"
    BUY_MAX      = "BUY_MAX"


class CatalystType(Enum):
    STRONG   = "STRONG"
    MODERATE = "MODERATE"
    WEAK     = "WEAK"
    NONE     = "NONE"


class RelativeStrength(Enum):
    STRONG_POSITIVE   = "STRONG_POSITIVE"
    MODERATE_POSITIVE = "MODERATE_POSITIVE"
    NEUTRAL           = "NEUTRAL"
    UNDERPERFORMING   = "UNDERPERFORMING"


class MarketCapTier(Enum):
    MICRO  = "MICRO"   # <$300M   → max €250
    SMALL  = "SMALL"   # $300M-2B → max €400
    MID    = "MID"     # $2B-10B  → max €500
    LARGE  = "LARGE"   # >$10B    → max €500


class Phase(Enum):
    ACCUMULATION = "ACCUMULATION"  # Volume bouwt stil, geen retail
    BREAKOUT     = "BREAKOUT"      # Eerste explosie, catalyst bevestigd
    EXPANSION    = "EXPANSION"     # Move gevestigd, sympathy plays volgen
    FRENZY       = "FRENZY"        # Retail stormt in — laat stadium
    EXHAUSTION   = "EXHAUSTION"    # Volume krimpt, move vervaagt — te laat
    NEUTRAL      = "NEUTRAL"       # Geen duidelijk signaal


# ── SECTOR CONFIG ─────────────────────────────────────────────────────────────

@dataclass
class SectorConfig:
    """
    Sector context die als input meekomt.
    In fase 2 geladen uit config/sectors.json via load_sector_config().
    In mock tests handmatig ingevuld.
    """
    sector_id: str
    sector_label: str
    heat: int              # 0-100
    phase: int             # Regime fase (1-4)
    leaders: list[str]
    sympathy: list[str]


def load_sector_config(sector_id: str,
                       config_path: str = None) -> Optional[SectorConfig]:
    """
    Laadt sector data uit config/sectors.json.
    Geeft None terug als sector_id niet gevonden.
    Fase 2: assembler.py roept dit aan bij het bouwen van TickerInput.
    """
    if config_path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base, "config", "sectors.json")

    if not os.path.exists(config_path):
        return None

    with open(config_path) as f:
        data = json.load(f)

    for s in data.get("sectors", []):
        if s["id"] == sector_id:
            return SectorConfig(
                sector_id=s["id"],
                sector_label=s["label"],
                heat=s["heat"],
                phase=s.get("phase", 1),
                leaders=s.get("leaders", []),
                sympathy=s.get("sympathy", []),
            )
    return None


# ── INPUT DATACLASS ────────────────────────────────────────────────────────────

@dataclass
class TickerInput:
    """
    Ruwe marktdata per ticker.
    Fase 2: gevuld door data/assembler.py vanuit Yahoo Finance + Finnhub + StockTwits.
    Nu:     mock data voor logica-validatie.
    """
    ticker: str

    # Prijs & volume
    price: float
    day_change_pct: float
    premarket_pct: float
    volume_today: int
    avg_volume_20d: int

    # Bedrijfsdata
    market_cap_usd: float
    float_shares: Optional[int]    # None = onbekend → neutrale score (4/8 pts)
    is_cfd_only: bool

    # Fundamentele context
    catalyst_type: CatalystType
    catalyst_description: str
    relative_strength: RelativeStrength
    sector: SectorConfig           # Sector context (heat + leaders + sympathy)

    # Social
    social_mentions_today: int
    social_mentions_avg: int

    # Risico flags
    has_sec_investigation: bool
    has_class_action: bool
    insider_sells_90d: int


# ── OUTPUT DATACLASSES ────────────────────────────────────────────────────────

@dataclass
class SkipScoreResult:
    total: int
    is_hard_blocked: bool
    reasons: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)


@dataclass
class MomentumScoreResult:
    total: float
    volume_anomaly: float          # Max 22 pts
    sector_heat_score: float       # Max 18 pts
    catalyst_quality: float        # Max 20 pts
    premarket_strength: float      # Max 14 pts
    relative_strength_score: float # Max 10 pts
    social_acceleration: float     # Max 8 pts (gecapped)
    float_score: float             # Max 8 pts
    social_was_capped: bool
    social_cap_reason: str
    breakdown: dict[str, str]


@dataclass
class ScoringResult:
    ticker: str
    decision: Decision
    momentum_score: float
    skip_score: int
    phase: Phase
    phase_description: str
    market_cap_tier: MarketCapTier
    momentum_detail: MomentumScoreResult
    skip_detail: SkipScoreResult
    sizing_eur: str
    summary: str


# ── HELPERS ───────────────────────────────────────────────────────────────────

def get_market_cap_tier(market_cap_usd: float) -> MarketCapTier:
    if market_cap_usd < 300_000_000:   return MarketCapTier.MICRO
    elif market_cap_usd < 2_000_000_000: return MarketCapTier.SMALL
    elif market_cap_usd < 10_000_000_000: return MarketCapTier.MID
    else:                               return MarketCapTier.LARGE


TIER_MAX_EUR = {
    MarketCapTier.MICRO:  250,
    MarketCapTier.SMALL:  400,
    MarketCapTier.MID:    500,
    MarketCapTier.LARGE:  500,
}

DECISION_RANGE = {
    Decision.BUY_SMALL:    (100, 200),
    Decision.BUY_MODERATE: (200, 300),
    Decision.BUY_STRONG:   (300, 400),
    Decision.BUY_MAX:      (400, 500),
}


def compute_sizing(decision: Decision, tier: MarketCapTier) -> str:
    """
    Effectieve sizing = minimum van decision range EN market cap tier maximum.
    MICRO cap krijgt altijd max €250, ook bij BUY_MAX.
    """
    if decision in (Decision.BLOCKED, Decision.SKIP):
        return "€0 — " + decision.value
    if decision == Decision.WATCH:
        return "Watchlist — nog niet kopen"

    lo, hi = DECISION_RANGE[decision]
    cap = TIER_MAX_EUR[tier]
    eff_hi = min(hi, cap)
    eff_lo = min(lo, eff_hi)

    suffix = f" (gelimiteerd door {tier.value}-cap)" if cap < hi else ""
    return f"€{eff_lo}-{eff_hi}{suffix}"


# ── PHASE DETECTOR ────────────────────────────────────────────────────────────

def detect_phase(data: TickerInput) -> tuple[Phase, str]:
    """
    Detecteert momentum-fase puur op basis van TickerInput data.
    Doel: vroegst mogelijke detectie = meeste alpha.
    FRENZY + EXHAUSTION = rode vlag, niet instappen.
    """
    rv = data.volume_today / data.avg_volume_20d if data.avg_volume_20d > 0 else 0
    sv = (data.social_mentions_today / data.social_mentions_avg
          if data.social_mentions_avg > 0 else 0)
    pct = data.day_change_pct

    if rv < 0.8 and pct < 5.0:
        return Phase.EXHAUSTION, "Volume krimpt, move vervaagt — te laat stadium"

    if pct > 25.0 and sv > 6.0 and rv > 6.0:
        return Phase.FRENZY, "Retail frenzy — hot money op piek, uitstapmoment"

    if pct > 12.0 and rv > 4.0:
        return Phase.EXPANSION, "Move gevestigd — sympathy plays volgen waarschijnlijk"

    if (pct > 4.0 and rv > 2.5
            and data.catalyst_type in (CatalystType.STRONG, CatalystType.MODERATE)):
        return Phase.BREAKOUT, "Eerste breakout — catalyst bevestigd, vroeg stadium"

    if rv > 1.8 and pct < 5.0:
        return Phase.ACCUMULATION, "Stille accumulatie — volume opbouwend, geen retail"

    return Phase.NEUTRAL, "Geen duidelijk phase signaal"


# ── SKIP SCORE ENGINE ─────────────────────────────────────────────────────────

def calculate_skip_score(data: TickerInput) -> SkipScoreResult:
    """
    Skip Score — draait altijd vóór Momentum Score.
    Hard vetoes (≥100): onmiddellijk BLOCKED, ongeacht momentum.
    Soft skips (cumulatief): ≥50 = SKIP.
    """
    score = 0
    reasons: list[str] = []
    blocking: list[str] = []
    hard = False

    # ── HARD VETOES ──────────────────────────────────────────────────────────
    if data.has_sec_investigation:
        score += 100; hard = True
        blocking.append("SEC INVESTIGATION ACTIEF — Spelregel 3")
    if data.has_class_action:
        score += 100; hard = True
        blocking.append("CLASS ACTION LOPEND — Spelregel 3")
    if data.is_cfd_only:
        score += 100; hard = True
        blocking.append("CFD-ONLY OP T212 — Spelregel 29")

    # ── SOFT SKIPS ───────────────────────────────────────────────────────────
    if data.day_change_pct >= 40.0:
        score += 40
        reasons.append(f"Dag +{data.day_change_pct:.1f}% ≥ 40% — te laat [+40]")
    elif data.day_change_pct >= 20.0:
        score += 10
        reasons.append(f"Dag +{data.day_change_pct:.1f}% — significante pre-run [+10]")

    if data.premarket_pct >= 40.0:
        score += 40
        reasons.append(f"Pre-market +{data.premarket_pct:.1f}% — volledig ingeprijsd [+40]")
    elif data.premarket_pct >= 20.0:
        score += 15
        reasons.append(f"Pre-market +{data.premarket_pct:.1f}% — wacht consolidatie [+15]")

    if data.catalyst_type == CatalystType.NONE:
        score += 20
        reasons.append("Geen catalyst — puur social-driven risico [+20]")

    rv = data.volume_today / data.avg_volume_20d if data.avg_volume_20d > 0 else 0
    if rv < 0.8:
        score += 25
        reasons.append(f"Volume {rv:.2f}x — onder gemiddelde, geen institutioneel [+25]")

    if data.insider_sells_90d > 10:
        score += 15
        reasons.append(f"{data.insider_sells_90d} insider sells 90d [+15] — Spelregel 13")
    elif data.insider_sells_90d > 5:
        score += 8
        reasons.append(f"{data.insider_sells_90d} insider sells 90d [+8] — monitor")

    return SkipScoreResult(total=score, is_hard_blocked=hard,
                           reasons=reasons, blocking_reasons=blocking)


# ── MOMENTUM COMPONENTS ───────────────────────────────────────────────────────

def _volume_anomaly(data: TickerInput) -> tuple[float, str]:
    """Max 22 pts."""
    if not data.avg_volume_20d:
        return 0.0, "Geen baseline"
    rv = data.volume_today / data.avg_volume_20d
    tbl = [(8.0, 22.0), (5.0, 17.6), (3.0, 13.2), (2.0, 8.8), (1.0, 4.4)]
    labels = {22.0: "EXTREEM", 17.6: "HOOG", 13.2: "ELEVATED",
              8.8: "VERHOOGD", 4.4: "LICHT BOVEN GEMIDDELD", 0.0: "ONDER GEMIDDELDE"}
    pts = 0.0
    for threshold, score in tbl:
        if rv >= threshold:
            pts = score; break
    return pts, f"{rv:.1f}x normaal — {labels.get(pts, 'LAAG')}"


def _sector_heat_score(data: TickerInput) -> tuple[float, str]:
    """Max 18 pts."""
    pts = round((data.sector.heat / 100.0) * 18.0, 1)
    lvl = ("EXPLOSIEF" if data.sector.heat >= 80 else
           "HOT"       if data.sector.heat >= 60 else
           "BUILDING"  if data.sector.heat >= 40 else "DORMANT")
    return pts, f"{data.sector.sector_label} heat {data.sector.heat}/100 — {lvl}"


def _catalyst_quality(data: TickerInput) -> tuple[float, str]:
    """Max 20 pts."""
    m = {CatalystType.STRONG:   (20.0, f"STERK: {data.catalyst_description}"),
         CatalystType.MODERATE: (12.0, f"MATIG: {data.catalyst_description}"),
         CatalystType.WEAK:     (4.0,  f"ZWAK:  {data.catalyst_description}"),
         CatalystType.NONE:     (0.0,  "Geen catalyst (48u)")}
    return m[data.catalyst_type]


def _premarket_strength(data: TickerInput) -> tuple[float, str]:
    """Max 14 pts. Sweet spot 8-20%. Boven 40% = 0."""
    p = data.premarket_pct
    if p >= 40.0:
        return 0.0, f"+{p:.1f}% — volledig ingeprijsd"
    if p >= 20.0:
        pts = round(14.0 - ((p - 20.0) / 20.0) * 9.0, 1)
        return pts, f"+{p:.1f}% — Spelregel 8 zone, halveer sizing"
    if p >= 8.0:  return 14.0, f"+{p:.1f}% — SWEET SPOT"
    if p >= 3.0:  return 7.0,  f"+{p:.1f}% — licht positief"
    if p >= 0.0:  return 2.5,  f"+{p:.1f}% — neutraal"
    return 0.0, f"{p:.1f}% — negatief"


def _relative_strength(data: TickerInput) -> tuple[float, str]:
    """Max 10 pts."""
    m = {RelativeStrength.STRONG_POSITIVE:   (10.0, "Groen bij rode markt"),
         RelativeStrength.MODERATE_POSITIVE: (7.0,  "Outperformt markt"),
         RelativeStrength.NEUTRAL:           (3.0,  "In lijn met markt"),
         RelativeStrength.UNDERPERFORMING:   (0.0,  "Onderpresteert")}
    return m[data.relative_strength]


def _social_acceleration(data: TickerInput) -> tuple[float, str, bool, str]:
    """
    Max 8 pts — met Social Quality Cap.
    Returns: (pts, label, was_capped, cap_reason)

    Cap limieten per catalyst kwaliteit:
        NONE     → max 2 pts   (social nooit alleen genoeg)
        WEAK     → max 4 pts
        MODERATE → max 6 pts
        STRONG   → max 8 pts   (volledige score)
    """
    if not data.social_mentions_avg:
        return 0.0, "Geen baseline", False, ""

    v = data.social_mentions_today / data.social_mentions_avg
    if v >= 10.0: raw, lbl = 8.0, f"{v:.0f}x — VIRAL"
    elif v >= 5.0: raw, lbl = 6.4, f"{v:.0f}x — ACCELERATING"
    elif v >= 2.0: raw, lbl = 4.0, f"{v:.1f}x — ELEVATED"
    elif v >= 1.0: raw, lbl = 1.6, f"{v:.1f}x — LICHT VERHOOGD"
    else:          raw, lbl = 0.0, f"{v:.1f}x — NORMAAL/LAAG"

    caps = {CatalystType.NONE: 2.0, CatalystType.WEAK: 4.0,
            CatalystType.MODERATE: 6.0, CatalystType.STRONG: 8.0}
    cap = caps[data.catalyst_type]

    if raw > cap:
        reason = f"catalyst={data.catalyst_type.value} → max {cap:.0f}pts"
        return cap, f"{lbl} [CAP: {reason}]", True, reason
    return raw, lbl, False, ""


def _float_score(data: TickerInput) -> tuple[float, str]:
    """
    Max 8 pts — nieuw v1.2.
    Lage float → grotere prijsbeweging per koopdruk = hogere momentum amplificatie.
    float=None → neutrale score 4/8 (onbekend).
    """
    if data.float_shares is None:
        return 4.0, "Float onbekend — neutrale score"
    fm = data.float_shares / 1_000_000
    if fm < 5:     return 8.0, f"{fm:.1f}M — EXTREEM LAAG"
    elif fm < 15:  return 6.5, f"{fm:.1f}M — LAAG"
    elif fm < 50:  return 4.5, f"{fm:.1f}M — MEDIUM"
    elif fm < 200: return 2.0, f"{fm:.0f}M — HOOG"
    else:          return 0.0, f"{fm:.0f}M — ZEER HOOG"


# ── MOMENTUM SCORE ────────────────────────────────────────────────────────────

def calculate_momentum_score(data: TickerInput) -> MomentumScoreResult:
    """Volledige Momentum Score (0-100). Gewichten v1.2."""
    vol_pts,  vol_lbl  = _volume_anomaly(data)
    heat_pts, heat_lbl = _sector_heat_score(data)
    cat_pts,  cat_lbl  = _catalyst_quality(data)
    pm_pts,   pm_lbl   = _premarket_strength(data)
    rs_pts,   rs_lbl   = _relative_strength(data)
    soc_pts, soc_lbl, capped, cap_rsn = _social_acceleration(data)
    flt_pts,  flt_lbl  = _float_score(data)

    total = vol_pts + heat_pts + cat_pts + pm_pts + rs_pts + soc_pts + flt_pts

    return MomentumScoreResult(
        total=round(total, 1),
        volume_anomaly=vol_pts, sector_heat_score=heat_pts,
        catalyst_quality=cat_pts, premarket_strength=pm_pts,
        relative_strength_score=rs_pts, social_acceleration=soc_pts,
        float_score=flt_pts, social_was_capped=capped, social_cap_reason=cap_rsn,
        breakdown={
            "Volume Anomaly    (max 22)": f"{vol_pts:5.1f} — {vol_lbl}",
            "Sector Heat       (max 18)": f"{heat_pts:5.1f} — {heat_lbl}",
            "Catalyst Quality  (max 20)": f"{cat_pts:5.1f} — {cat_lbl}",
            "Premarket Strength(max 14)": f"{pm_pts:5.1f} — {pm_lbl}",
            "Relative Strength (max 10)": f"{rs_pts:5.1f} — {rs_lbl}",
            "Social Accel.     (max  8)": f"{soc_pts:5.1f} — {soc_lbl}",
            "Float Score       (max  8)": f"{flt_pts:5.1f} — {flt_lbl}",
        }
    )


# ── DECISION ENGINE ───────────────────────────────────────────────────────────

def make_decision(momentum: float, skip: SkipScoreResult,
                  data: TickerInput) -> tuple[Decision, str]:
    """Skip-first. Combinatieregel als extra veiligheidsnet."""
    if skip.is_hard_blocked:
        return Decision.BLOCKED, "; ".join(skip.blocking_reasons)
    if skip.total >= 50:
        return Decision.SKIP, skip.reasons[0] if skip.reasons else "Skip Score ≥ 50"
    if data.catalyst_type == CatalystType.NONE and momentum < 50:
        return Decision.SKIP, "Combinatieregel: geen catalyst + momentum <50"
    if momentum >= 90:   return Decision.BUY_MAX,      "Uitzonderlijk sterk signaal"
    if momentum >= 75:   return Decision.BUY_STRONG,   "Sterk momentum signaal"
    if momentum >= 60:   return Decision.BUY_MODERATE, "Solide momentum signaal"
    if momentum >= 45:   return Decision.BUY_SMALL,    "Matig momentum signaal"
    if momentum >= 30:   return Decision.WATCH,        "Zwak signaal — monitor"
    return Decision.SKIP, "Onvoldoende momentum"


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────

def score_ticker(data: TickerInput) -> ScoringResult:
    skip     = calculate_skip_score(data)
    momentum = calculate_momentum_score(data)
    decision, reason = make_decision(momentum.total, skip, data)
    tier     = get_market_cap_tier(data.market_cap_usd)
    phase, phase_desc = detect_phase(data)
    sizing   = compute_sizing(decision, tier)

    return ScoringResult(
        ticker=data.ticker, decision=decision,
        momentum_score=momentum.total, skip_score=skip.total,
        phase=phase, phase_description=phase_desc,
        market_cap_tier=tier, momentum_detail=momentum, skip_detail=skip,
        sizing_eur=sizing,
        summary=(f"{data.ticker}: {decision.value} | Momentum {momentum.total:.1f} | "
                 f"Skip {skip.total} | Phase {phase.value} | {reason}"),
    )


# ── CLI PRINTER ───────────────────────────────────────────────────────────────

def print_report(r: ScoringResult) -> None:
    W = 74
    DC = {"BLOCKED":"\033[91m","SKIP":"\033[93m","WATCH":"\033[94m",
          "BUY_SMALL":"\033[96m","BUY_MODERATE":"\033[92m",
          "BUY_STRONG":"\033[92m","BUY_MAX":"\033[92m"}
    PC = {"ACCUMULATION":"\033[94m","BREAKOUT":"\033[96m","EXPANSION":"\033[92m",
          "FRENZY":"\033[93m","EXHAUSTION":"\033[91m","NEUTRAL":"\033[90m"}
    R = "\033[0m"; B = "\033[1m"
    c = DC.get(r.decision.value, ""); p = PC.get(r.phase.value, "")

    print(f"\n{'═'*W}")
    print(f"  {B}{r.ticker}{R}  →  {c}{B}{r.decision.value}{R}  |  {r.sizing_eur}")
    print(f"  Phase: {p}{r.phase.value}{R} — {r.phase_description}")
    print(f"  MarketCap: {r.market_cap_tier.value}")
    print(f"{'─'*W}")

    sk = r.skip_detail
    sc = "\033[91m" if sk.total>=100 else "\033[93m" if sk.total>=50 else "\033[92m"
    state = "⛔ BLOCKED" if sk.is_hard_blocked else ("⚠ SKIP" if sk.total>=50 else "✓ OK")
    print(f"  SKIP SCORE: {sc}{sk.total:3d}/100{R}  {state}")
    for x in sk.blocking_reasons: print(f"    🔴 {x}")
    for x in sk.reasons:          print(f"    ⚠  {x}")
    print(f"{'─'*W}")

    ms = r.momentum_detail
    mc = "\033[92m" if ms.total>=60 else "\033[93m" if ms.total>=40 else "\033[91m"
    cap_note = "  ⚠ SOCIAL GECAPPED" if ms.social_was_capped else ""
    print(f"  MOMENTUM SCORE: {mc}{ms.total:5.1f}/100{R}{cap_note}")
    for lbl, det in ms.breakdown.items():
        print(f"    {lbl}: {det}")
    print(f"{'─'*W}")
    print(f"  {r.summary}")
    print(f"{'═'*W}")


# ── MOCK SECTOR CONFIGS ───────────────────────────────────────────────────────

def _sc(id_, label, heat, phase=1, leaders=None, sympathy=None):
    return SectorConfig(id_, label, heat, phase, leaders or [], sympathy or [])

S_DRONE    = _sc("drones_defense", "DRONES/DEFENSE",      98, 3, ["UMAC","KTOS"], ["RCAT"])
S_AI_INFRA = _sc("ai_infra",       "AI INFRASTRUCTURE",   95, 1, ["NVDA","AVGO"], ["CRDO"])
S_QUANTUM  = _sc("quantum",        "QUANTUM COMPUTING",   92, 3, ["IONQ","QBTS"], ["RGTI"])
S_AI_SOFT  = _sc("ai_software",    "AI SOFTWARE",         78, 2, ["SNOW","NOW"],  ["DDOG"])
S_POWER    = _sc("power_energy",   "POWER/ENERGY",        55, 4, ["GEV","VST"],   ["CCJ"])
S_CYBER    = _sc("cybersecurity",  "CYBERSECURITY",       62, 2, ["PANW","CRWD"], ["S"])
S_LARGE    = _sc("ai_infra",       "AI INFRASTRUCTURE",   62, 2, ["NVDA"],        [])


# ── MOCK TEST CASES ───────────────────────────────────────────────────────────

MOCK_TICKERS = [
    # ── REGRESSIETESTS 1-8 ────────────────────────────────────────────────────
    TickerInput(  # TEST1 UMAC — BUY_MAX
        ticker="UMAC_T1", price=26.31, day_change_pct=39.0, premarket_pct=22.0,
        volume_today=9_200_000, avg_volume_20d=1_100_000,
        market_cap_usd=1_200_000_000, float_shares=46_000_000, is_cfd_only=False,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Pentagon equity deals — WSJ 28 mei",
        relative_strength=RelativeStrength.STRONG_POSITIVE, sector=S_DRONE,
        social_mentions_today=8_400, social_mentions_avg=420,
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=2,
    ),
    TickerInput(  # TEST2 APP — BLOCKED (SEC)
        ticker="APP_T2", price=385.0, day_change_pct=8.5, premarket_pct=6.0,
        volume_today=4_500_000, avg_volume_20d=800_000,
        market_cap_usd=25_000_000_000, float_shares=None, is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Sterke Q2 guidance",
        relative_strength=RelativeStrength.MODERATE_POSITIVE, sector=S_AI_INFRA,
        social_mentions_today=3_200, social_mentions_avg=800,
        has_sec_investigation=True, has_class_action=False, insider_sells_90d=18,
    ),
    TickerInput(  # TEST3 SPACE — BLOCKED (CFD)
        ticker="SPACE_T3", price=142.0, day_change_pct=15.0, premarket_pct=12.0,
        volume_today=5_000_000, avg_volume_20d=600_000,
        market_cap_usd=6_000_000_000, float_shares=None, is_cfd_only=True,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Record kwartaal, hypersonisch contract",
        relative_strength=RelativeStrength.STRONG_POSITIVE, sector=S_AI_INFRA,
        social_mentions_today=2_100, social_mentions_avg=310,
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=1,
    ),
    TickerInput(  # TEST4 SNOW — BUY_SMALL (BtB, niet momentum — bewust laag)
        ticker="SNOW_T4", price=172.0, day_change_pct=-1.5, premarket_pct=0.0,
        volume_today=2_800_000, avg_volume_20d=1_200_000,
        market_cap_usd=55_000_000_000, float_shares=None, is_cfd_only=False,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Q1 earnings morgen — AI consumption inflection",
        relative_strength=RelativeStrength.NEUTRAL, sector=S_AI_SOFT,
        social_mentions_today=1_800, social_mentions_avg=450,
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=0,
    ),
    TickerInput(  # TEST5 CHASER — SKIP (+42% dag)
        ticker="CHASER_T5", price=31.50, day_change_pct=42.0, premarket_pct=35.0,
        volume_today=12_000_000, avg_volume_20d=800_000,
        market_cap_usd=750_000_000, float_shares=25_000_000, is_cfd_only=False,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="Overheidscontract aangekondigd",
        relative_strength=RelativeStrength.STRONG_POSITIVE, sector=S_DRONE,
        social_mentions_today=9_000, social_mentions_avg=200,
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=0,
    ),
    TickerInput(  # TEST6 SLEEPER — BUY_SMALL
        ticker="SLEEPER_T6", price=18.40, day_change_pct=3.2, premarket_pct=2.1,
        volume_today=1_800_000, avg_volume_20d=600_000,
        market_cap_usd=380_000_000, float_shares=20_000_000, is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Sector peer sterk — sympathie move verwacht",
        relative_strength=RelativeStrength.MODERATE_POSITIVE, sector=S_QUANTUM,
        social_mentions_today=380, social_mentions_avg=120,
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=1,
    ),
    TickerInput(  # TEST7 HYPE — SKIP (combinatieregel)
        ticker="HYPE_T7", price=4.20, day_change_pct=18.0, premarket_pct=8.0,
        volume_today=400_000, avg_volume_20d=600_000,
        market_cap_usd=80_000_000, float_shares=19_000_000, is_cfd_only=False,
        catalyst_type=CatalystType.NONE,
        catalyst_description="Alleen Reddit buzz",
        relative_strength=RelativeStrength.MODERATE_POSITIVE, sector=S_CYBER,
        social_mentions_today=4_000, social_mentions_avg=80,
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=0,
    ),
    TickerInput(  # TEST8 QBTS — BUY_SMALL (v1.2 herbalancering, was BUY_MODERATE in v1.1)
        ticker="QBTS_T8", price=27.80, day_change_pct=4.5, premarket_pct=3.2,
        volume_today=2_200_000, avg_volume_20d=800_000,
        market_cap_usd=2_800_000_000, float_shares=None, is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Sympathy: IONQ +18% quantum funding",
        relative_strength=RelativeStrength.MODERATE_POSITIVE, sector=S_QUANTUM,
        social_mentions_today=1_200, social_mentions_avg=280,
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=0,
    ),

    # ── NIEUWE TESTS 9-11 — valideren v1.2 features ──────────────────────────
    TickerInput(
        # TEST9: Low float runner
        # Valideert: float_score (2.8M = max 8 pts), EXPANSION phase, MICRO-cap sizing cap
        # Verwacht: BUY_MAX maar gesized op €250 (MICRO)
        ticker="LOWFLOAT_T9",
        price=14.80, day_change_pct=22.0, premarket_pct=14.0,
        volume_today=9_600_000, avg_volume_20d=800_000,   # 12x
        market_cap_usd=195_000_000,                        # MICRO-cap
        float_shares=2_800_000,                            # 2.8M — extreem laag
        is_cfd_only=False,
        catalyst_type=CatalystType.STRONG,
        catalyst_description="DoD contract $52M sole-source award",
        relative_strength=RelativeStrength.STRONG_POSITIVE, sector=S_DRONE,
        social_mentions_today=2_800, social_mentions_avg=280,  # 10x
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=0,
    ),
    TickerInput(
        # TEST10: Mega cap, laag momentum
        # Valideert: LARGE market cap tier, lage score door laag volume
        # Verwacht: WATCH — momentum te laag voor BUY, geen skip
        ticker="MEGACAP_T10",
        price=284.50, day_change_pct=4.5, premarket_pct=2.8,
        volume_today=4_800_000, avg_volume_20d=4_000_000,  # 1.2x — normaal
        market_cap_usd=82_000_000_000,                      # LARGE-cap
        float_shares=None, is_cfd_only=False,
        catalyst_type=CatalystType.MODERATE,
        catalyst_description="Analyst upgrade — AI infrastructure thesis",
        relative_strength=RelativeStrength.NEUTRAL, sector=S_LARGE,
        social_mentions_today=1_100, social_mentions_avg=440,  # 2.5x
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=0,
    ),
    TickerInput(
        # TEST11: Social-only pump blocked
        # Valideert: social quality cap (catalyst=NONE → 2 pts max), combinatieregel
        # Verwacht: SKIP — social gecapped + geen catalyst + volume laag
        ticker="SOCIALPUMP_T11",
        price=6.80, day_change_pct=12.0, premarket_pct=6.0,
        volume_today=480_000, avg_volume_20d=700_000,       # 0.69x — ONDER
        market_cap_usd=160_000_000,
        float_shares=22_000_000, is_cfd_only=False,
        catalyst_type=CatalystType.NONE,                    # geen catalyst
        catalyst_description="Geen nieuws — alleen Reddit r/pennystocks",
        relative_strength=RelativeStrength.MODERATE_POSITIVE, sector=S_CYBER,
        social_mentions_today=18_000, social_mentions_avg=400,  # 45x viral
        has_sec_investigation=False, has_class_action=False, insider_sells_90d=0,
    ),
]

EXPECTATIONS = [
    ("UMAC_T1",        "BUY_MAX",   "Explosief, geen flags"),
    ("APP_T2",         "BLOCKED",   "SEC investigation"),
    ("SPACE_T3",       "BLOCKED",   "CFD-only"),
    ("SNOW_T4",        "BUY_SMALL", "BtB niet momentum — bewust"),
    ("CHASER_T5",      "SKIP",      "+42% dag, te laat"),
    ("SLEEPER_T6",     "BUY_SMALL", "Vroeg signaal"),
    ("HYPE_T7",        "SKIP",      "Geen catalyst, combinatieregel"),
    ("QBTS_T8",        "BUY_SMALL", "Herbalancering v1.2 (was BUY_MODERATE in v1.1)"),
    ("LOWFLOAT_T9",    "BUY_MAX",   "Low float, sizing cap MICRO €250"),
    ("MEGACAP_T10",    "WATCH",     "Mega cap, laag momentum"),
    ("SOCIALPUMP_T11", "SKIP",      "Social gecapped + combinatieregel"),
]


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    DC = {"BLOCKED":"\033[91m","SKIP":"\033[93m","WATCH":"\033[94m",
          "BUY_SMALL":"\033[96m","BUY_MODERATE":"\033[92m",
          "BUY_STRONG":"\033[92m","BUY_MAX":"\033[92m"}
    PC = {"ACCUMULATION":"\033[94mACCUM\033[0m","BREAKOUT":"\033[96mBREAK\033[0m",
          "EXPANSION":"\033[92mEXPAN\033[0m","FRENZY":"\033[93mFRENZ\033[0m",
          "EXHAUSTION":"\033[91mEXHAU\033[0m","NEUTRAL":"\033[90mNEUTR\033[0m"}
    R = "\033[0m"; B = "\033[1m"

    print(f"\n{'█'*74}")
    print("  MOMENTUM SCORE ENGINE v1.2 — TEST RUN")
    print("  Igor × Claude — 28 mei 2026")
    print(f"{'█'*74}")

    results = [score_ticker(m) for m in MOCK_TICKERS]
    for r in results:
        print_report(r)

    print(f"\n{'═'*74}")
    print("  SAMENVATTING — 11 TEST CASES")
    print(f"{'═'*74}")
    print(f"  {'TICKER':<18} {'VERWACHT':<16} {'ACTUAL':<16} "
          f"{'MOM':>5} {'SKIP':>5} {'PHASE':<7} {'CAP':<3} STATUS")
    print(f"  {'─'*18} {'─'*16} {'─'*16} {'─'*5} {'─'*5} {'─'*7} {'─'*3} {'─'*8}")

    passed = 0
    for r, (name, exp, note) in zip(results, EXPECTATIONS):
        ok = r.decision.value == exp
        if ok: passed += 1
        c = DC.get(r.decision.value, "")
        ph = PC.get(r.phase.value, r.phase.value[:5])
        cap = "⚠" if r.momentum_detail.social_was_capped else "  "
        st = f"\033[92m✓\033[0m" if ok else f"\033[91m✗ exp {exp}\033[0m"
        print(f"  {name:<18} {exp:<16} {c}{r.decision.value:<16}{R} "
              f"{r.momentum_score:>5.1f} {r.skip_score:>5}  {ph}  {cap} {st}")

    print(f"{'═'*74}")
    col = "\033[92m" if passed == 11 else "\033[91m"
    print(f"\n  {col}{passed}/11 tests geslaagd{R}\n")
    print("  CALIBRATIE NOOT:")
    print("  QBTS_T8: BUY_SMALL (was BUY_MODERATE in v1.1) — gewichtsherbalancering.")
    print("  Score 59.4 (was 60.4). Marginale verschuiving door float+social aanpassing.")
    print("  Beslissing is conservatiever, niet fout. Gedocumenteerd in CHANGELOG.\n")
