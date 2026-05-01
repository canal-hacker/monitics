from typing import Optional

_PARTY_MAP = {
    "DEM": "DEM",
    "D": "DEM",
    "DEMOCRAT": "DEM",
    "DEMOCRATIC": "DEM",
    "REP": "REP",
    "R": "REP",
    "REPUBLICAN": "REP",
}


def normalize_party(party: Optional[str]) -> str:
    if not party or not isinstance(party, str):
        return "OTHER"

    cleaned = party.strip().upper()
    return _PARTY_MAP.get(cleaned, "OTHER")
