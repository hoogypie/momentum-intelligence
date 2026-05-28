"""
data/market_session.py
Market Session Detectie — v2.4

Detecteert de huidige handelsperiode op basis van UTC tijd.
Gebruikt door:
    - yahoo_client.py  → snapshot.market_session
    - cache/market_cache.py → TTL beslissingen
    - assembler.py     → premarket vs regular scoring context

Benadering: UTC-5 offset (Eastern Standard Time). Dit is conservatief —
in de zomer is het UTC-4 (EDT), waardoor de grenzen 1 uur verschuiven.
Voor een persoonlijk tool is deze benadering goed genoeg.

Sessieschema (ET):
    PREMARKET   04:00 – 09:30   (vroege price discovery)
    REGULAR     09:30 – 16:00   (hoofdhandel, volume actief)
    AFTERHOURS  16:00 – 20:00   (light volume, grote moves mogelijk)
    CLOSED      20:00 – 04:00   (geen handel)
"""

from enum import Enum
from datetime import datetime, timezone


class MarketSession(str, Enum):
    PREMARKET   = "PREMARKET"    # 04:00–09:30 ET
    REGULAR     = "REGULAR"      # 09:30–16:00 ET
    AFTERHOURS  = "AFTERHOURS"   # 16:00–20:00 ET
    CLOSED      = "CLOSED"       # 20:00–04:00 ET


# Sessiebeschrijvingen voor logging/responses
SESSION_DESCRIPTIONS = {
    MarketSession.PREMARKET:  "Pre-market: prijsdata beperkt, volume laag",
    MarketSession.REGULAR:    "Reguliere handelsuren: live volume en prijs",
    MarketSession.AFTERHOURS: "After-hours: light volume, grotere spreads",
    MarketSession.CLOSED:     "Markt gesloten: geen actuele prijsdata",
}

# Verwacht premarket beschikbaarheid per sessie
SESSION_HAS_PREMARKET = {
    MarketSession.PREMARKET:  True,   # pre-market prijs beschikbaar
    MarketSession.REGULAR:    False,  # markt open, geen pre-market meer
    MarketSession.AFTERHOURS: False,
    MarketSession.CLOSED:     False,
}


def get_market_session(utc_now: datetime | None = None) -> MarketSession:
    """
    Geeft de huidige US market session op basis van UTC tijd.

    Args:
        utc_now: Optionele datetime voor testing. Default = nu.

    Returns:
        MarketSession enum waarde
    """
    if utc_now is None:
        utc_now = datetime.now(timezone.utc)

    # Weekdag check (0=maandag, 6=zondag)
    # Vrijdagavond 20:00 ET t/m maandagochtend 04:00 ET = CLOSED
    weekday = utc_now.weekday()
    if weekday == 5 or weekday == 6:  # zaterdag of zondag
        return MarketSession.CLOSED

    # Tijdstip in ET (UTC-5, conservatieve benadering)
    hour_et   = (utc_now.hour - 5) % 24
    minute_et = utc_now.minute
    time_et   = hour_et + minute_et / 60.0

    # Grenzen in ET uren (decimaal)
    if 4.0 <= time_et < 9.5:
        return MarketSession.PREMARKET
    elif 9.5 <= time_et < 16.0:
        return MarketSession.REGULAR
    elif 16.0 <= time_et < 20.0:
        return MarketSession.AFTERHOURS
    else:
        return MarketSession.CLOSED


def is_regular_hours(utc_now: datetime | None = None) -> bool:
    return get_market_session(utc_now) == MarketSession.REGULAR


def is_premarket(utc_now: datetime | None = None) -> bool:
    return get_market_session(utc_now) == MarketSession.PREMARKET


def session_description(session: MarketSession | None = None) -> str:
    if session is None:
        session = get_market_session()
    return SESSION_DESCRIPTIONS.get(session, "Onbekende sessie")
