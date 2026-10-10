"""Crop facts shared by the farm-advice modules.

Crop coefficients (Kc) and stage lengths come from FAO Irrigation and
Drainage Paper 56 (Allen et al., 1998), Tables 11 and 12. Stage lengths are
FAO's tropical / West African values where FAO lists them. Spacing is the
common Nigerian extension spacing, used only to turn per-hectare amounts into
per-plant amounts.

Crops FAO-56 does not list (yam, okra, leafy vegetables) use the nearest
group and are marked ``kc_estimated`` so replies can say so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Crop:
    name: str
    aliases: tuple[str, ...]
    kc_ini: float
    kc_mid: float
    kc_end: float
    # Days in each FAO stage: initial, development, mid-season, late season.
    stages: tuple[int, int, int, int]
    # Ground area for one stand (m2), from common spacing; None if broadcast.
    area_per_stand_m2: float | None
    spacing_note: str
    kc_estimated: bool = False
    legume: bool = False
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def season_days(self) -> int:
        return sum(self.stages)

    def kc_on_day(self, day: int) -> float:
        """FAO-56 Kc curve: flat, linear rise, flat, linear fall."""
        ini, dev, mid, late = self.stages
        if day <= ini:
            return self.kc_ini
        if day <= ini + dev:
            return self.kc_ini + (self.kc_mid - self.kc_ini) * (day - ini) / dev
        if day <= ini + dev + mid:
            return self.kc_mid
        if day <= ini + dev + mid + late:
            return self.kc_mid + (self.kc_end - self.kc_mid) * (day - ini - dev - mid) / late
        return self.kc_end

    def stage_on_day(self, day: int) -> str:
        ini, dev, mid, _ = self.stages
        if day <= ini:
            return "early growth"
        if day <= ini + dev:
            return "fast growth"
        if day <= ini + dev + mid:
            return "flowering and filling"
        return "ripening"


CROPS: dict[str, Crop] = {
    c.name: c
    for c in [
        Crop("maize", ("maize", "corn", "agbado", "masara", "oka"), 0.3, 1.20, 0.60, (20, 35, 40, 30), 0.75 * 0.25, "75 cm by 25 cm, one plant per stand"),
        Crop("sorghum", ("sorghum", "guinea corn", "dawa", "oka baba"), 0.3, 1.05, 0.55, (20, 35, 40, 30), 0.75 * 0.30, "75 cm by 30 cm, two plants per stand"),
        Crop("millet", ("millet", "gero", "pearl millet", "joro"), 0.3, 1.00, 0.30, (15, 25, 40, 25), 0.75 * 0.30, "75 cm by 30 cm"),
        Crop("rice", ("rice", "shinkafa", "iresi", "osikapa"), 1.05, 1.20, 0.75, (30, 30, 60, 30), None, "transplanted or drilled"),
        Crop("cassava", ("cassava", "rogo", "ege", "akpu", "garri cassava"), 0.3, 0.80, 0.30, (20, 40, 90, 60), 1.0, "1 m by 1 m"),
        Crop("yam", ("yam", "doya", "isu", "ji"), 0.5, 1.10, 0.60, (30, 60, 90, 60), 1.0, "1 m by 1 m mounds or ridges", kc_estimated=True),
        Crop("cowpea", ("cowpea", "beans", "wake", "ewa", "akidi"), 0.4, 1.05, 0.35, (20, 30, 35, 20), 0.75 * 0.20, "75 cm by 20 cm", legume=True),
        Crop("soybean", ("soybean", "soya", "soya beans", "waken suya"), 0.4, 1.15, 0.50, (20, 30, 60, 25), 0.75 * 0.05, "75 cm rows, 5 cm in the row", legume=True),
        Crop("groundnut", ("groundnut", "peanut", "gyada", "epa", "ahụekere"), 0.4, 1.15, 0.60, (25, 35, 45, 25), 0.75 * 0.20, "75 cm by 20 cm", legume=True),
        Crop("tomato", ("tomato", "tumatir", "tomati"), 0.6, 1.15, 0.80, (30, 40, 40, 25), 0.75 * 0.50, "75 cm by 50 cm"),
        Crop("pepper", ("pepper", "barkono", "ata", "ose", "chilli", "tatashe"), 0.6, 1.05, 0.90, (25, 35, 40, 20), 0.75 * 0.50, "75 cm by 50 cm"),
        Crop("onion", ("onion", "albasa", "alubosa", "yabasị"), 0.7, 1.05, 0.75, (15, 25, 70, 40), 0.30 * 0.10, "30 cm by 10 cm"),
        Crop("okra", ("okra", "okro", "kubewa", "ila", "okwuru"), 0.6, 1.05, 0.90, (20, 30, 40, 20), 0.60 * 0.30, "60 cm by 30 cm", kc_estimated=True),
        Crop("vegetables", ("amaranth", "efo", "green", "ugu", "spinach", "leafy vegetable", "vegetable", "alefo"), 0.7, 1.05, 0.95, (20, 30, 15, 5), 0.30 * 0.30, "30 cm by 30 cm", kc_estimated=True),
        Crop("cabbage", ("cabbage", "kabeji"), 0.7, 1.05, 0.95, (40, 60, 50, 15), 0.60 * 0.45, "60 cm by 45 cm"),
        Crop("watermelon", ("watermelon", "kankana", "egusi melon"), 0.4, 1.00, 0.75, (20, 30, 30, 30), 2.0 * 1.0, "2 m by 1 m"),
    ]
}

_ALIAS_INDEX = {alias: name for name, crop in CROPS.items() for alias in (name, *crop.aliases)}


def find_crop(text: str | None) -> Crop | None:
    """Match a crop name (any listed alias, case-insensitive)."""
    if not text:
        return None
    value = text.strip().lower()
    if value in _ALIAS_INDEX:
        return CROPS[_ALIAS_INDEX[value]]
    # Whole-word match (plural allowed); the crop mentioned first wins, and
    # longer aliases beat shorter ones ("guinea corn" before "corn"; "ata"
    # must not match inside "potato").
    best: tuple[int, int, str] | None = None
    for alias, name in _ALIAS_INDEX.items():
        found = re.search(rf"(?<![\w]){re.escape(alias)}(?:e?s)?(?![\w])", value)
        if found:
            key = (found.start(), -len(alias), name)
            if best is None or key < best:
                best = key
    return CROPS[best[2]] if best else None


def stands_per_hectare(crop: Crop) -> int | None:
    if not crop.area_per_stand_m2:
        return None
    return int(round(10_000 / crop.area_per_stand_m2))
