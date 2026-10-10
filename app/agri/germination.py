"""Problem 8: seed germination and seedling failure.

Works from counts (the farmer's own or the photo count) and gives a
germination percentage, what it means, and what to do. Thresholds:
certified seed of most field crops is expected to germinate at about 80 %
or more (seed certification standards); below that the seed rate must go
up, and below about 50 % the seed lot should be replaced.
# REVIEW: confirm crop-specific NASC minimums.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GOOD = 80.0
POOR = 50.0

CAUSES = {
    "planted_too_deep": "Seeds were planted too deep. Plant at about 2 to 5 cm (two finger joints).",
    "dry_soil": "The soil was too dry. Plant after good rain or water the day before planting.",
    "waterlogged": "The soil was waterlogged and seeds rotted. Plant on ridges and avoid low wet spots.",
    "old_seed": "The seed may be old or badly stored. Buy certified seed and store it cool and dry.",
    "pests": "Insects, termites, birds or rodents ate the seed. Use treated seed and cover the holes well.",
    "damping_off": "Seedlings collapsed at the stem (damping off). Avoid overwatering and use clean nursery soil.",
    "crusted_soil": "A hard crust stopped seedlings coming up. Break the crust gently or add mulch.",
}


@dataclass
class GerminationResult:
    planted: int
    germinated: int
    percent: float
    verdict: str
    replant: bool
    seeds_per_hole_next_time: int
    advice: list[str] = field(default_factory=list)
    from_photo: bool = False
    count_confidence: str | None = None

    def as_facts(self) -> dict:
        return {
            "seeds_or_holes_counted": self.planted,
            "germinated": self.germinated,
            "germination_percent": round(self.percent),
            "verdict": self.verdict,
            "replant_gaps": self.replant,
            "seeds_per_hole_next_time": self.seeds_per_hole_next_time,
            "advice": self.advice,
            "counted_from_photo": self.from_photo,
            "count_confidence": self.count_confidence,
        }


def assess(
    planted: int,
    germinated: int,
    causes: list[str] | None = None,
    *,
    days_since_planting: int | None = None,
    from_photo: bool = False,
    count_confidence: str | None = None,
) -> GerminationResult:
    planted = max(1, int(planted))
    germinated = max(0, min(int(germinated), planted))
    percent = germinated / planted * 100
    advice: list[str] = []

    if percent >= GOOD:
        verdict = "good"
        seeds = 1
    elif percent >= POOR:
        verdict = "fair"
        seeds = 2
        advice.append("Next time plant two seeds per hole and thin to the best one after two weeks.")
    else:
        verdict = "poor"
        seeds = 3
        advice.append("This seed lot is weak. Do not use it again; buy certified seed.")

    replant = percent < 90
    if replant:
        if days_since_planting is None or days_since_planting <= 14:
            advice.append("Fill the empty holes now (supply gaps) so the plants grow up together.")
        else:
            advice.append("It is late to fill gaps; late plants will be shaded. Fill only large empty patches.")
    if days_since_planting is not None and days_since_planting < 5 and percent < GOOD:
        advice.insert(0, "Some seeds may still come up; check again in two or three days.")

    for cause in causes or []:
        text = CAUSES.get(cause)
        if text and text not in advice:
            advice.append(text)

    if from_photo and count_confidence == "low":
        advice.append("The count from the photo is rough. Count 100 holes yourself for a better number.")

    return GerminationResult(
        planted=planted,
        germinated=germinated,
        percent=percent,
        verdict=verdict,
        replant=replant,
        seeds_per_hole_next_time=seeds,
        advice=advice[:5],
        from_photo=from_photo,
        count_confidence=count_confidence,
    )


SEED_TEST_STEPS = (
    "Before planting, test your seed: put 100 seeds on a wet cloth or paper, roll it, "
    "keep it damp in a warm place, and count the sprouted seeds after 5 to 7 days. "
    "80 or more sprouted means the seed is good."
)
