"""N-ATLaS writes the spoken summary; code checks it before it is sent.

The summary may only use numbers that appear in the computed facts. If
N-ATLaS adds or changes a number, writes too much, or (for weeds) slips in a
chemical dose, the plain fallback sentence is sent instead. The exact numbers
always go out in the text card as well.
"""

from __future__ import annotations

import json
import logging
import re

from app.services import natlas
from app.services.diagnoses.reply import DOSE_PATTERN

logger = logging.getLogger("advisor.compose")

MAX_WORDS = 90
ALWAYS_ALLOWED = {"1", "2"}
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

SYSTEM = (
    "You are AjaTrade, a farming assistant for Nigerian smallholder farmers. Write a short "
    "spoken reply for the farmer from the facts JSON. Rules: use ONLY the facts; copy every "
    "number exactly as it appears in the facts and add no other number; simple English, short "
    "sentences, under 70 words, no lists, no markdown, no emoji; never give herbicide or "
    "pesticide amounts; end with one practical next step. The farmer will also get the full "
    "numbers as text, so mention only the most important one or two."
)


def _numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def _allowed_numbers(facts: dict) -> set[str]:
    allowed = set(ALWAYS_ALLOWED)
    for token in _numbers_in(json.dumps(facts, ensure_ascii=False)):
        allowed.add(token)
        try:
            value = float(token)
        except ValueError:
            continue
        # Allow the same value written without trailing ".0" or rounded.
        allowed.add(f"{value:g}")
        allowed.add(str(int(round(value))))
    return allowed


def check_summary(summary: str, facts: dict, *, check_doses: bool = False) -> str | None:
    """Return the reason the summary is unsafe, or None if it can be sent."""
    text = (summary or "").strip()
    if not text:
        return "empty"
    if len(text.split()) > MAX_WORDS:
        return "too long"
    unknown = _numbers_in(text) - _allowed_numbers(facts)
    if unknown:
        return f"numbers not in facts: {sorted(unknown)}"
    if check_doses and DOSE_PATTERN.search(text):
        return "dose"
    if "```" in text or text.startswith("{"):
        return "format"
    return None


async def spoken_summary(title: str, facts: dict, fallback: str, *, check_doses: bool = False) -> tuple[str, bool]:
    """(summary, came_from_natlas). Never raises."""
    prompt = f"Topic: {title}\nFacts: {json.dumps(facts, ensure_ascii=False)}"
    try:
        summary = await natlas.chat(SYSTEM, prompt, temperature=0.2, max_tokens=220)
    except Exception as error:  # noqa: BLE001
        logger.warning("N-ATLaS summary failed: %s", error)
        return fallback, False
    summary = summary.strip().strip('"').strip()
    problem = check_summary(summary, facts, check_doses=check_doses)
    if problem:
        logger.warning("N-ATLaS summary rejected (%s): %r", problem, summary[:200])
        return fallback, False
    return summary, True
