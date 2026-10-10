"""Where the farm is: state, coordinates, agro-ecological zone, rainy season.

Zones follow the usual Nigerian split by latitude. Season windows are typical
onset and end dates for each zone; real seasons shift year to year, so
replies call them typical, never certain.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# State -> (capital, latitude, longitude). Used when the farmer names a state
# or capital and no location pin was shared.
STATES: dict[str, tuple[str, float, float]] = {
    "abia": ("Umuahia", 5.53, 7.49),
    "adamawa": ("Yola", 9.21, 12.48),
    "akwa ibom": ("Uyo", 5.04, 7.91),
    "anambra": ("Awka", 6.21, 7.07),
    "bauchi": ("Bauchi", 10.31, 9.84),
    "bayelsa": ("Yenagoa", 4.92, 6.26),
    "benue": ("Makurdi", 7.73, 8.54),
    "borno": ("Maiduguri", 11.83, 13.15),
    "cross river": ("Calabar", 4.95, 8.32),
    "delta": ("Asaba", 6.20, 6.73),
    "ebonyi": ("Abakaliki", 6.32, 8.11),
    "edo": ("Benin City", 6.34, 5.63),
    "ekiti": ("Ado-Ekiti", 7.62, 5.22),
    "enugu": ("Enugu", 6.44, 7.49),
    "fct": ("Abuja", 9.06, 7.49),
    "gombe": ("Gombe", 10.29, 11.17),
    "imo": ("Owerri", 5.48, 7.03),
    "jigawa": ("Dutse", 11.76, 9.34),
    "kaduna": ("Kaduna", 10.52, 7.44),
    "kano": ("Kano", 12.00, 8.52),
    "katsina": ("Katsina", 12.99, 7.60),
    "kebbi": ("Birnin Kebbi", 12.45, 4.20),
    "kogi": ("Lokoja", 7.80, 6.74),
    "kwara": ("Ilorin", 8.50, 4.55),
    "lagos": ("Ikeja", 6.60, 3.35),
    "nasarawa": ("Lafia", 8.49, 8.52),
    "niger": ("Minna", 9.61, 6.56),
    "ogun": ("Abeokuta", 7.15, 3.35),
    "ondo": ("Akure", 7.25, 5.19),
    "osun": ("Osogbo", 7.77, 4.56),
    "oyo": ("Ibadan", 7.38, 3.95),
    "plateau": ("Jos", 9.90, 8.86),
    "rivers": ("Port Harcourt", 4.82, 7.03),
    "sokoto": ("Sokoto", 13.06, 5.24),
    "taraba": ("Jalingo", 8.89, 11.36),
    "yobe": ("Damaturu", 11.75, 11.96),
    "zamfara": ("Gusau", 12.16, 6.66),
}

_STATE_ALIASES = {
    "abuja": "fct",
    "federal capital territory": "fct",
    "f.c.t": "fct",
    "f.c.t.": "fct",
    "nassarawa": "nasarawa",
    "akwaibom": "akwa ibom",
    "port harcourt": "rivers",
    "ibadan": "oyo",
    "ikeja": "lagos",
    "benin": "edo",
    "benin city": "edo",
    "jos": "plateau",
    "minna": "niger",
    "makurdi": "benue",
    "maiduguri": "borno",
    "ilorin": "kwara",
    "lokoja": "kogi",
    "akure": "ondo",
    "abeokuta": "ogun",
    "osogbo": "osun",
    "owerri": "imo",
    "umuahia": "abia",
    "awka": "anambra",
    "yola": "adamawa",
    "uyo": "akwa ibom",
    "calabar": "cross river",
    "asaba": "delta",
    "abakaliki": "ebonyi",
    "ado ekiti": "ekiti",
    "ado-ekiti": "ekiti",
    "dutse": "jigawa",
    "zaria": "kaduna",
    "birnin kebbi": "kebbi",
    "lafia": "nasarawa",
    "yenagoa": "bayelsa",
    "jalingo": "taraba",
    "damaturu": "yobe",
    "gusau": "zamfara",
}

ZONE_NAMES = {
    "sudan_sahel": "Sudan and Sahel savanna (far north)",
    "northern_guinea": "Northern Guinea savanna",
    "southern_guinea": "Southern Guinea savanna (middle belt)",
    "forest": "forest and derived savanna (south)",
}

# Typical rainy season (month, day) start and end per zone.
SEASONS = {
    "sudan_sahel": ((6, 1), (9, 30)),
    "northern_guinea": ((5, 1), (10, 15)),
    "southern_guinea": ((4, 1), (10, 31)),
    "forest": ((3, 15), (11, 15)),
}


@dataclass(frozen=True)
class Place:
    label: str
    latitude: float
    longitude: float
    state: str | None
    exact: bool  # True for a location pin or a geocoded town

    @property
    def zone(self) -> str:
        return zone_for_latitude(self.latitude)

    @property
    def fertilizer_region(self) -> str:
        """FMARD fertilizer tables split the country into north and south."""
        return "north" if self.zone in ("sudan_sahel", "northern_guinea") else "south"


def zone_for_latitude(latitude: float) -> str:
    if latitude >= 12.0:
        return "sudan_sahel"
    if latitude >= 10.0:
        return "northern_guinea"
    if latitude >= 8.0:
        return "southern_guinea"
    return "forest"


def find_state(text: str | None) -> str | None:
    """Return a state key if the text names a state or its capital."""
    if not text:
        return None
    value = text.strip().lower().replace(" state", "")
    if value in STATES:
        return value
    if value in _STATE_ALIASES:
        return _STATE_ALIASES[value]
    for name in sorted([*STATES, *_STATE_ALIASES], key=len, reverse=True):
        if f" {name} " in f" {value} ":
            return _STATE_ALIASES.get(name, name)
    return None


def place_from_state(state: str) -> Place:
    capital, lat, lon = STATES[state]
    return Place(label=f"{capital}, {state.title()} State", latitude=lat, longitude=lon, state=state, exact=False)


def season_days_left(zone: str, on: date) -> int:
    """Days from ``on`` to the typical end of the rainy season (0 if over)."""
    (_, _), (end_month, end_day) = SEASONS[zone]
    end = date(on.year, end_month, end_day)
    return max(0, (end - on).days)


def in_rainy_season(zone: str, on: date) -> bool:
    (start_month, start_day), (end_month, end_day) = SEASONS[zone]
    return date(on.year, start_month, start_day) <= on <= date(on.year, end_month, end_day)
