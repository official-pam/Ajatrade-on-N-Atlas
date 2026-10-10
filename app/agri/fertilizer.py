"""Problem 3: fertilizer amount, products, cost and timing.

Nutrient targets (kg per hectare of N, P2O5 and K2O) are derived from the
Federal Ministry of Agriculture (FMARD) recommendations as tabulated in
"Review of Fertilizer Use by Crop and by Product, Nigeria 2015-2017"
(AfricaFertilizer.org / IFDC, 2018), converted from product rates with
urea 46 % N, SSP 18 % P2O5 and MOP 60 % K2O. The ministry splits the country
into a northern group (Sahel, Sudan, Northern Guinea savanna) and a southern
group (Southern Guinea savanna and forest).

Amounts are computed here, never by the language model. Crops without an
official rate get no numbers, only a referral (AGENTS.md: never invent
dosages).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agri.crops import Crop, stands_per_hectare
from app.agri.units import bags, round_sensible

SOURCE = "FMARD fertilizer recommendations (AfricaFertilizer.org review, 2018)"

# crop -> region -> (N, P2O5, K2O) kg/ha
TARGETS: dict[str, dict[str, tuple[float, float, float]]] = {
    "maize": {"north": (120, 60, 30), "south": (100, 50, 30)},
    "sorghum": {"north": (64, 32, 30), "south": (32, 16, 30)},
    "millet": {"north": (60, 30, 30), "south": (30, 15, 15)},
    "rice": {"north": (80, 20, 20), "south": (80, 20, 20)},
    "cassava": {"north": (90, 50, 90), "south": (90, 50, 90)},
    "yam": {"north": (90, 50, 90), "south": (90, 50, 90)},
    "cowpea": {"north": (20, 40, 20), "south": (10, 36, 20)},
    "soybean": {"north": (30, 60, 30), "south": (10, 36, 20)},
    "groundnut": {"north": (0, 54, 25), "south": (0, 54, 25)},
}

TIMING: dict[str, dict[str, list[tuple[str, float, bool]]]] = {
    # list of (when, share of N, P and K go here?)
    "maize": {
        "north": [("at planting or 2 to 3 weeks after planting", 0.5, True), ("5 to 6 weeks after planting", 0.5, False)],
        "south": [("2 to 3 weeks after planting", 0.5, True), ("5 to 6 weeks after planting", 0.5, False)],
    },
    "sorghum": {
        "north": [("at planting or 2 to 3 weeks after planting", 0.5, True), ("6 to 8 weeks after planting", 0.5, False)],
        "south": [("at planting or 2 to 3 weeks after planting", 0.5, True), ("6 to 8 weeks after planting", 0.5, False)],
    },
    "millet": {
        "north": [("at planting", 0.0, True), ("3 weeks after planting", 1.0, False)],
        "south": [("at planting", 0.0, True), ("3 weeks after planting", 1.0, False)],
    },
    "rice": {
        "north": [("1 to 2 weeks after planting", 0.5, True), ("6 weeks after planting", 0.5, False)],
        "south": [("1 to 2 weeks after planting", 0.5, True), ("6 weeks after planting", 0.5, False)],
    },
    "cassava": {
        "north": [("8 weeks after planting, in a ring about 10 cm from the plant", 1.0, True)],
        "south": [("8 weeks after planting, in a ring about 10 cm from the plant", 1.0, True)],
    },
    "yam": {
        "north": [("8 weeks after planting, in a ring 15 cm from the vine", 1.0, True)],
        "south": [("9 weeks after planting, in a ring 15 cm from the vine", 1.0, True)],
    },
    "cowpea": {
        "north": [("at planting or 2 weeks after planting", 1.0, True)],
        "south": [("at planting or 2 weeks after planting", 1.0, True)],
    },
    "soybean": {
        "north": [("at planting or 2 weeks after planting", 1.0, True)],
        "south": [("at planting or 2 weeks after planting", 1.0, True)],
    },
    "groundnut": {
        "north": [("at planting or 2 weeks after planting", 1.0, True)],
        "south": [("at planting or 2 weeks after planting", 1.0, True)],
    },
}

PRODUCTS = {
    "urea": ("Urea", (0.46, 0.0, 0.0)),
    "npk15": ("NPK 15-15-15", (0.15, 0.15, 0.15)),
    "npk20": ("NPK 20-10-10", (0.20, 0.10, 0.10)),
    "ssp": ("SSP", (0.0, 0.18, 0.0)),
    "mop": ("MOP (potash)", (0.0, 0.0, 0.60)),
}
COMPOUNDS = ("npk15", "npk20")
BAG_KG = 50.0
MIN_TOP_UP_KG = 8.0  # ignore tiny leftovers under this nutrient amount (kg/ha)

# Terrain: fertilizer and water move downhill, so the bottom of a slope
# needs less nitrogen and the top loses more to runoff (start values, to be
# adjusted from crop response).
SLOPE_N_FACTOR = {"uphill": 1.0, "middle": 0.95, "downhill": 0.85}


def normalize_product(text: str | None) -> str | None:
    value = (text or "").lower().replace(" ", "")
    if "urea" in value:
        return "urea"
    if "15-15-15" in value or "151515" in value or value in ("npk15", "npk"):
        return "npk15"
    if "20-10-10" in value or "201010" in value or value == "npk20":
        return "npk20"
    if "ssp" in value or "superphosphate" in value or "super" in value:
        return "ssp"
    if "mop" in value or "potash" in value or "muriate" in value:
        return "mop"
    return None


@dataclass
class ProductAmount:
    product: str
    label: str
    kg_per_ha: float
    kg_total: float
    bags_total: float
    grams_per_stand: float | None
    grams_per_m2: float
    cost_naira: float | None = None


@dataclass
class FertilizerPlan:
    crop: str
    region: str
    area_m2: float
    targets_kg_per_ha: tuple[float, float, float]
    products: list[ProductAmount]
    schedule: list[dict]
    total_cost_naira: float | None
    affordable_share: float | None
    adjustments: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: str = SOURCE

    def as_facts(self) -> dict:
        return {
            "crop": self.crop,
            "farm_size_m2": round(self.area_m2),
            "nutrients_kg_per_hectare": {
                "nitrogen": self.targets_kg_per_ha[0],
                "phosphate": self.targets_kg_per_ha[1],
                "potash": self.targets_kg_per_ha[2],
            },
            "products": [
                {
                    "product": p.label,
                    "total_kg": round_sensible(p.kg_total),
                    "bags_of_50kg": p.bags_total,
                    "grams_per_plant": round_sensible(p.grams_per_stand) if p.grams_per_stand else None,
                    "grams_per_square_metre": round_sensible(p.grams_per_m2),
                    "cost_naira": round(p.cost_naira, -2) if p.cost_naira else None,
                }
                for p in self.products
            ],
            "when_to_apply": self.schedule,
            "total_cost_naira": round(self.total_cost_naira, -2) if self.total_cost_naira else None,
            "budget_covers_percent": round(self.affordable_share * 100) if self.affordable_share is not None else None,
            "adjustments": self.adjustments,
            "warnings": self.warnings,
            "source": self.source,
        }


def has_recommendation(crop: Crop) -> bool:
    return crop.name in TARGETS


def _solve(n: float, p: float, k: float, compound: str, include_k: bool) -> dict[str, float]:
    """kg/ha of each product: compound first, then straight fertilizers."""
    _, (cn, cp, ck) = PRODUCTS[compound]
    limits = [x / c for x, c in ((n, cn), (p, cp)) if c > 0 and x > 0]
    amount = min(limits) if limits and n > 0 and p > 0 else 0.0
    plan: dict[str, float] = {}
    if amount > 0:
        plan[compound] = amount
    rest_n = n - amount * cn
    rest_p = p - amount * cp
    rest_k = k - amount * ck
    if rest_n > MIN_TOP_UP_KG:
        plan["urea"] = rest_n / 0.46
    if rest_p > MIN_TOP_UP_KG:
        plan["ssp"] = rest_p / 0.18
    if include_k and rest_k > MIN_TOP_UP_KG:
        plan["mop"] = rest_k / 0.60
    return plan


def recommend(
    crop: Crop,
    region: str,
    area_m2: float,
    *,
    soil_ratings: dict[str, str] | None = None,
    compound: str | None = None,
    prices_per_bag: dict[str, float] | None = None,
    budget_naira: float | None = None,
    slope_position: str | None = None,
    sloped: bool = False,
    heavy_rain_soon: bool = False,
    previous_crop_legume: bool = False,
) -> FertilizerPlan | None:
    """Fertilizer plan for one crop and field. None when no official rate."""
    if crop.name not in TARGETS:
        return None
    region = region if region in ("north", "south") else "south"
    n, p, k = TARGETS[crop.name][region]
    adjustments: list[str] = []
    warnings: list[str] = []
    ratings = {key: (value or "").split(" ")[0] for key, value in (soil_ratings or {}).items()}

    if ratings.get("available_p_mg_per_kg") == "high" or ratings.get("phosphorus") == "high":
        p *= 0.5
        adjustments.append("Your soil already has enough phosphorus, so the phosphate is halved.")
    if ratings.get("exchangeable_k_cmol_per_kg") == "high" or ratings.get("potassium") == "high":
        k *= 0.5
        adjustments.append("Your soil already has enough potassium, so the potash is halved.")
    if ratings.get("total_nitrogen_percent") == "high":
        n *= 0.75
        adjustments.append("Your soil nitrogen is high, so nitrogen is cut by a quarter.")
    if previous_crop_legume and not crop.legume and n > 0:
        n *= 0.8
        adjustments.append("A legume grew here last season, so nitrogen is cut by a fifth.")
    if slope_position in SLOPE_N_FACTOR and sloped:
        factor = SLOPE_N_FACTOR[slope_position]
        if factor != 1.0:
            n *= factor
            adjustments.append(
                f"This part is {slope_position} where washed-down nutrients collect, so nitrogen is reduced."
            )
    ph_rating = (soil_ratings or {}).get("ph", "")
    if "acidic" in ph_rating and ("strongly" in ph_rating or "extremely" in ph_rating):
        warnings.append("Your soil is acidic. Lime the soil first, or much of this fertilizer will be wasted.")

    compound_key = compound if compound in COMPOUNDS else "npk15"
    plan_kg_ha = _solve(n, p, k, compound_key, include_k=True)

    stands = stands_per_hectare(crop)
    hectares = area_m2 / 10_000
    products: list[ProductAmount] = []
    total_cost = 0.0
    priced = bool(prices_per_bag)
    for key, kg_ha in plan_kg_ha.items():
        label = PRODUCTS[key][0]
        kg_total = kg_ha * hectares
        bag_count = bags(kg_total, BAG_KG)
        price = (prices_per_bag or {}).get(key)
        cost = None
        if price:
            cost = (kg_total / BAG_KG) * price
            total_cost += cost
        elif priced:
            priced = False  # a missing price means no total
        products.append(
            ProductAmount(
                product=key,
                label=label,
                kg_per_ha=kg_ha,
                kg_total=kg_total,
                bags_total=bag_count,
                grams_per_stand=(kg_ha * 1000 / stands) if stands else None,
                grams_per_m2=kg_ha / 10,
            )
        )
        products[-1].cost_naira = cost

    # Timing: split each product by its nutrient's share in each application.
    schedule: list[dict] = []
    splits = TIMING[crop.name][region]
    if sloped and crop.name in ("maize", "sorghum", "rice") and len(splits) == 2:
        first, second = splits
        splits = [
            (first[0], 0.4, True),
            ("3 to 4 weeks after planting", 0.3, False),
            (second[0], 0.3, False),
        ]
        adjustments.append("On a slope, nitrogen is split into three smaller doses so less washes away.")
    total_n = _n(plan_kg_ha)
    compound_n = _n_compound(plan_kg_ha)
    urea_n_total = _urea_n(plan_kg_ha)
    # Nitrogen each application should carry, minus what the compound brings.
    wanted = [
        max(0.0, n_share * total_n - (compound_n if with_pk else 0.0))
        for _, n_share, with_pk in splits
    ]
    scale = urea_n_total / sum(wanted) if sum(wanted) > 0 else 0.0
    urea_share = [w * scale / urea_n_total if urea_n_total else 0.0 for w in wanted]
    for (when, _, with_pk), u_share in zip(splits, urea_share):
        items = []
        for prod in products:
            share = u_share if prod.product == "urea" else (1.0 if with_pk else 0.0)
            if share > 0.01:
                kg = prod.kg_total * share
                items.append(
                    {
                        "product": prod.label,
                        "kg": round_sensible(kg),
                        "bags_of_50kg": bags(kg, BAG_KG),
                        "grams_per_plant": round_sensible(prod.grams_per_stand * share) if prod.grams_per_stand else None,
                    }
                )
        if items:
            schedule.append({"when": when, "apply": items})

    affordable = None
    if budget_naira and priced and total_cost > 0:
        affordable = min(1.0, budget_naira / total_cost)
        if affordable < 1.0:
            warnings.append(
                "Your budget does not cover the full plan. Buy the first application first, "
                "and do not spread less on the whole farm; fertilize part of the farm well instead."
            )
    if heavy_rain_soon:
        warnings.append("Heavy rain is expected in the next two days. Wait until after it, so the fertilizer is not washed away.")
    if crop.name == "yam":
        warnings.append("If your soil pH is below 4.5, apply lime before the fertilizer.")
    if crop.name == "groundnut" and region == "north":
        adjustments.append("In the savanna, groundnut also benefits from a little boron (borax); ask your agro-dealer.")

    return FertilizerPlan(
        crop=crop.name,
        region=region,
        area_m2=area_m2,
        targets_kg_per_ha=(round(n), round(p), round(k)),
        products=products,
        schedule=schedule,
        total_cost_naira=total_cost if priced and total_cost > 0 else None,
        affordable_share=affordable,
        adjustments=adjustments,
        warnings=warnings,
    )


def _n(plan: dict[str, float]) -> float:
    return sum(kg * PRODUCTS[key][1][0] for key, kg in plan.items())


def _n_compound(plan: dict[str, float]) -> float:
    return sum(kg * PRODUCTS[key][1][0] for key, kg in plan.items() if key in COMPOUNDS)


def _urea_n(plan: dict[str, float]) -> float:
    return plan.get("urea", 0.0) * 0.46
