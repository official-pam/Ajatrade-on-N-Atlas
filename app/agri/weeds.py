"""Problem 6: weed identification support and control advice.

Advice order follows integrated weed management: timing and cultural
control first, then hand or mechanical weeding, then herbicides named only by
active ingredient, never with doses (AGENTS.md: no chemical dosages; the
product label and the agro-dealer give those).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Critical weed-free period: weeds that compete in these weeks cost the most
# yield. Two weedings at about 3 and 6 weeks after planting cover most crops.
WEEDING_WEEKS = {
    "maize": (3, 6), "sorghum": (3, 6), "millet": (3, 6), "rice": (3, 6),
    "cowpea": (3, 6), "soybean": (3, 6), "groundnut": (3, 6),
    "cassava": (4, 8), "yam": (4, 8),
}

KNOWN_WEEDS = {
    "striga": {
        "names": ("striga", "witchweed", "kudiji", "makasa"),  # REVIEW: local names
        "type": "parasitic",
        "crops": ("maize", "sorghum", "millet", "rice", "cowpea"),
        "advice": [
            "Pull Striga by hand before it flowers, and burn or bury it so it does not drop seed.",
            "Plant a Striga-resistant variety next season.",
            "Rotate or intercrop with soybean or cowpea; they make Striga seeds sprout and die.",
            "Fertilize well with nitrogen and add manure; Striga is worse on poor soil.",
        ],
        "herbicide": None,
    },
    "spear grass": {
        "names": ("spear grass", "imperata", "speargrass", "spear-grass"),
        "type": "grass",
        "crops": (),
        "advice": [
            "Spear grass grows back from its underground stems, so slashing alone does not kill it.",
            "Dig out the white underground stems when you prepare the land.",
            "Shade it out with a cover crop such as mucuna after you clear it.",
        ],
        "herbicide": "a glyphosate herbicide on actively growing leaves before planting",
    },
    "nutsedge": {
        "names": ("nutsedge", "nut grass", "cyperus", "sedge"),
        "type": "sedge",
        "crops": (),
        "advice": [
            "Nutsedge spreads by small tubers; repeated early weeding weakens it.",
            "Do not move soil with tubers to clean fields.",
        ],
        "herbicide": None,
    },
    "siam weed": {
        "names": ("siam weed", "chromolaena", "awolowo", "independence weed"),
        "type": "broadleaf",
        "crops": (),
        "advice": [
            "Uproot young Siam weed by hand; slash big bushes before they flower and seed.",
        ],
        "herbicide": None,
    },
}

TYPE_ADVICE = {
    "grass": "Grass weeds have narrow leaves; weed early, before they form strong root clumps.",
    "broadleaf": "Broadleaf weeds are easiest to kill young; hoe or hand-pull them before flowering.",
    "sedge": "Sedges have triangular stems; weed repeatedly while they are small.",
    "parasitic": "Parasitic weeds feed on the crop's roots; remove them before they set seed.",
}

PRE_EMERGENCE_BY_CROP = {
    # Active-ingredient examples registered for these crops in Nigeria.
    # REVIEW: confirm with the agronomist before broadening.
    "maize": "a pre-emergence herbicide containing atrazine with metolachlor, sprayed just after planting",
    "rice": "a rice herbicide recommended by your agro-dealer, used on time",
    "sorghum": "a pre-emergence herbicide labelled for sorghum",
    "cowpea": "a pre-emergence herbicide labelled for cowpea",
    "soybean": "a pre-emergence herbicide labelled for soybean",
}


@dataclass
class WeedAdvice:
    weeds: list[str]
    weed_types: list[str]
    cover: str | None
    crop: str | None
    timing: str | None
    actions: list[str] = field(default_factory=list)
    herbicide_note: str | None = None
    see_extension_officer: bool = False

    def as_facts(self) -> dict:
        return {
            "weeds": self.weeds,
            "weed_types": self.weed_types,
            "how_much_weed": self.cover,
            "crop": self.crop,
            "when_to_weed": self.timing,
            "actions": self.actions,
            "herbicide_option": self.herbicide_note,
            "always_say": "Read the herbicide label, wear gloves and a mask, and ask your agro-dealer for the product and the amount.",
            "see_extension_officer": self.see_extension_officer,
        }


def identify(name: str | None) -> str | None:
    value = (name or "").lower()
    for key, info in KNOWN_WEEDS.items():
        if any(alias in value for alias in info["names"]):
            return key
    return None


def advise(
    weeds: list[dict],
    crop: str | None,
    cover: str | None,
    *,
    weeks_after_planting: int | None = None,
    striga_seen: bool = False,
) -> WeedAdvice:
    names: list[str] = []
    types: list[str] = []
    actions: list[str] = []
    herbicide = None
    for weed in weeds:
        species = weed.get("likely_species") or weed.get("name")
        key = identify(species)
        weed_type = (weed.get("type") or "").lower() or (KNOWN_WEEDS[key]["type"] if key else "unknown")
        if key:
            names.append(key)
            actions.extend(KNOWN_WEEDS[key]["advice"])
            herbicide = herbicide or KNOWN_WEEDS[key]["herbicide"]
        elif species:
            names.append(str(species))
        if weed_type in TYPE_ADVICE and weed_type not in types:
            types.append(weed_type)
            actions.append(TYPE_ADVICE[weed_type])
    if striga_seen and "striga" not in names:
        names.append("striga")
        actions = KNOWN_WEEDS["striga"]["advice"] + actions

    timing = None
    if crop in WEEDING_WEEKS:
        first, second = WEEDING_WEEKS[crop]
        timing = f"Keep the {crop} clean from week {first} to week {second} after planting; weed at about week {first} and again at week {second}."
        if weeks_after_planting is not None and weeks_after_planting > second + 2:
            timing += " Your crop is past this period, so only remove weeds that are about to seed."
    if cover == "heavy":
        actions.insert(0, "The weeds are heavy and are already taking water and food from your crop; weed now.")
    if herbicide is None and crop in PRE_EMERGENCE_BY_CROP and "striga" not in names:
        herbicide = f"next season, {PRE_EMERGENCE_BY_CROP[crop]}"

    actions = list(dict.fromkeys(actions))[:5]
    return WeedAdvice(
        weeds=names,
        weed_types=types,
        cover=cover,
        crop=crop,
        timing=timing,
        actions=actions,
        herbicide_note=herbicide,
        see_extension_officer="striga" in names and cover == "heavy",
    )
