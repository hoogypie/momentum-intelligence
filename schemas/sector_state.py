"""
schemas/sector_state.py
Typed sector state — v2.1

SectorState is de getypeerde versie van SectorConfig uit sectors.json.
Gebruikt bij GET /sectors/{id} (toekomstig) en in assembler validatie.
"""

from pydantic import BaseModel, Field, field_validator
from datetime import date
from typing import Optional


class SectorState(BaseModel):
    """
    Huidige staat van een momentum-sector.
    Geladen vanuit config/sectors.json.
    """
    sector_id:    str
    label:        str
    heat:         int
    status:       str
    phase:        int   = Field(ge=1, le=4)
    leaders:      list[str] = []
    sympathy:     list[str] = []
    trigger:      str  = ""
    last_updated: Optional[str] = None

    @field_validator("heat")
    @classmethod
    def heat_in_range(cls, v: int) -> int:
        return max(0, min(100, v))

    @field_validator("leaders", "sympathy")
    @classmethod
    def tickers_uppercase(cls, v: list[str]) -> list[str]:
        return [t.upper().strip() for t in v]

    def is_stale(self, max_days: int = 7) -> bool:
        """
        Geeft True als sector config ouder is dan max_days.
        Activeer in v2.2 met datum-parsing voor last_updated.
        """
        # TODO v2.2: parse last_updated en vergelijk met vandaag
        return False
