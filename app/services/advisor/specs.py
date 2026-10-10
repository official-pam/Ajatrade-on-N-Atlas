"""What each farm-advice flow needs to know, and how it asks.

All question text is English; the reply pipeline translates it into the
farmer's language with N-ATLaS and voices it, like every other reply.
Questions are short, one fact at a time, voice-friendly (AGENTS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Slot:
    key: str
    question: str
    description: str  # what the extractor should put here
    required: bool = True
    ask: bool = True  # optional slots with ask=True are asked once
    kind: str = "text"


@dataclass(frozen=True)
class FlowSpec:
    flow: str
    title: str
    intro: str
    slots: tuple[Slot, ...]
    photo_task: str | None = None  # vision task when the farmer sends a photo
    # Slots that are satisfied by a photo instead.
    photo_satisfies: tuple[str, ...] = field(default_factory=tuple)


CROP = Slot("crop", "Which crop is it?", "crop name in English, e.g. maize, cassava, tomato", kind="crop")
CROP_AGE = Slot(
    "crop_age_days",
    "How many weeks ago did you plant it?",
    "days since planting as a whole number (convert weeks x 7, months x 30)",
    kind="int",
)
AREA = Slot(
    "area",
    "How big is the farm? For example: two plots, one acre, or half a hectare.",
    'farm size as {"value": number, "unit": "plot|acre|hectare|square metre|square foot"} or {"dimensions": "20 by 30 metres"}',
    kind="area",
)
LOCATION = Slot(
    "location",
    "Where is the farm? Send your location pin, or tell me the town and the state.",
    "town, LGA or state in Nigeria as written by the farmer",
    kind="place",
)
SLOPED = Slot(
    "sloped",
    "Is the farm on a slope, with a higher part and a lower part?",
    "true if the farm is on a slope or hill side, false if flat",
    required=False,
    kind="bool",
)

FLOWS: dict[str, FlowSpec] = {
    "irrigation": FlowSpec(
        flow="irrigation",
        title="Watering and water loss",
        intro="I will work out how much water your crop needs today, and check for wasted water.",
        slots=(
            CROP,
            CROP_AGE,
            AREA,
            LOCATION,
            Slot(
                "method",
                "How do you water: watering can or bucket, hose, furrow, sprinkler, or drip?",
                "one of: watering can, bucket, hose, furrow, basin, sprinkler, drip",
                required=False,
                kind="text",
            ),
            SLOPED,
            Slot("soil_texture", "", "sandy, loamy or clay if the farmer says", required=False, ask=False),
            Slot("water_used_litres", "", "litres of water the farmer used last time, if said", required=False, ask=False, kind="number"),
            Slot(
                "loss_signs",
                "",
                "list of signs the farmer mentions, from: wet_patch, puddle, dry_line_end, weak_flow_far, one_zone_dry, blocked_emitters, pump_runs_long, water_runs_off, bottom_too_wet",
                required=False,
                ask=False,
                kind="list",
            ),
        ),
    ),
    "soil_health": FlowSpec(
        flow="soil_health",
        title="Soil health check",
        intro="I will check how healthy your soil is.",
        slots=(
            Slot(
                "soil_input",
                "Do you have soil test numbers or a soil meter? Send a photo of the result, or say the numbers. If not, tell me the colour of your soil and how your crop looks.",
                'readings as {"values": {"ph": n, "ec_ds_per_m": n, "organic_carbon_percent": n, "organic_matter_percent": n, "total_nitrogen_percent": n, "available_p_mg_per_kg": n, "exchangeable_k_cmol_per_kg": n, "moisture_percent": n, "nitrogen": "low|medium|high", "phosphorus": "...", "potassium": "..."}, "source": "lab|meter|npk_sensor|farmer_estimate", "signs": {"soil_colour": "dark|brown|red|light|grey", "yellow_lower_leaves": bool, "purple_leaves": bool, "leaf_edges_burnt": bool, "pale_crop": bool, "stunted_roots": bool}}',
                kind="soil",
            ),
            Slot("soil_texture", "", "sandy, loamy or clay if the farmer says", required=False, ask=False),
            Slot("crop", "", "crop on this soil if mentioned", required=False, ask=False, kind="crop"),
        ),
        photo_task="soil_report",
        photo_satisfies=("soil_input",),
    ),
    "fertilizer": FlowSpec(
        flow="fertilizer",
        title="Fertilizer plan",
        intro="I will make a fertilizer plan: how much, when, and the cost.",
        slots=(
            CROP,
            AREA,
            LOCATION,
            Slot(
                "prices",
                "If you want the cost, tell me the price of one bag of NPK and one bag of urea where you buy. Or say skip.",
                'price in naira per 50 kg bag as {"npk15": n, "npk20": n, "urea": n, "ssp": n, "mop": n}; only the products mentioned',
                required=False,
                kind="prices",
            ),
            Slot("budget_naira", "", "money the farmer has for fertilizer, in naira, if said", required=False, ask=False, kind="number"),
            Slot("compound", "", "npk15 or npk20 if the farmer says which NPK they use", required=False, ask=False),
            Slot("previous_crop_legume", "", "true if beans, cowpea, soybean or groundnut grew there last season", required=False, ask=False, kind="bool"),
            Slot("sloped", "", "true if the farm is on a slope", required=False, ask=False, kind="bool"),
            Slot("slope_position", "", "uphill, middle or downhill part, if said", required=False, ask=False),
        ),
    ),
    "water_source": FlowSpec(
        flow="water_source",
        title="Find water for the farm",
        intro="I will look at the land around your farm to find the best place to get or keep water.",
        slots=(
            Slot(
                "location",
                "Please send your farm's location pin from WhatsApp, so I can look at the land around it. If you cannot, tell me the nearest town.",
                "town or village near the farm",
                kind="place",
            ),
            Slot(
                "local_signs",
                "Are there wells near the farm that keep water in the dry season? Is there a stream or fadama close by?",
                '{"wells_nearby_with_water": bool, "well_depth_m": n, "stream_or_fadama_nearby": bool, "green_in_dry_season": bool, "rocky_ground": bool}',
                required=False,
                kind="dict",
            ),
            Slot("area", "", "farm size if mentioned", required=False, ask=False, kind="area"),
        ),
    ),
    "weed_control": FlowSpec(
        flow="weed_control",
        title="Weed control",
        intro="I will help you deal with the weeds.",
        slots=(
            Slot(
                "weed_info",
                "Send a clear photo of the weed in your farm, or tell me what it looks like.",
                '{"name": "weed name if known", "type": "grass|broadleaf|sedge|parasitic|unknown", "cover": "few|moderate|heavy", "striga_seen": bool}',
                kind="dict",
            ),
            Slot("crop", "Which crop is growing in that field?", "crop name", required=False, kind="crop"),
            Slot("weeks_after_planting", "", "weeks since the crop was planted, if said", required=False, ask=False, kind="int"),
        ),
        photo_task="weeds",
        photo_satisfies=("weed_info",),
    ),
    "variety_choice": FlowSpec(
        flow="variety_choice",
        title="Choose a seed variety",
        intro="I will check which seed type suits your farm and season.",
        slots=(
            CROP,
            LOCATION,
            Slot(
                "planting_month",
                "When will you plant? Say this week, or the month.",
                "month number 1 to 12 when the farmer will plant; use the current month for now or this week",
                kind="month",
            ),
            Slot(
                "stresses",
                "Does the farm have any of these: Striga weed, dry spells, flooding, armyworm, or poor soil? Or say none.",
                '{"striga": bool, "drought": bool, "flood": bool, "fall_armyworm": bool, "low_soil_n": bool}',
                required=False,
                kind="dict",
            ),
        ),
    ),
    "germination": FlowSpec(
        flow="germination",
        title="Seed germination check",
        intro="I will check how well your seeds came up.",
        slots=(
            Slot(
                "counts",
                "Send a photo of the nursery tray or the planted row. Or tell me how many seeds you planted and how many came up.",
                '{"planted": n, "germinated": n}',
                kind="counts",
            ),
            Slot("days_since_planting", "How many days ago did you plant them?", "days as a whole number", required=False, kind="int"),
            Slot(
                "causes",
                "",
                "list of possible causes the farmer mentions, from: planted_too_deep, dry_soil, waterlogged, old_seed, pests, damping_off, crusted_soil",
                required=False,
                ask=False,
                kind="list",
            ),
            Slot("crop", "", "crop if mentioned", required=False, ask=False, kind="crop"),
        ),
        photo_task="germination",
        photo_satisfies=("counts",),
    ),
}

ADVISOR_FLOWS = tuple(FLOWS)

# Farm-level facts reused across flows (not crop-specific).
PROFILE_KEYS = ("area", "location", "sloped", "method", "soil_texture")
