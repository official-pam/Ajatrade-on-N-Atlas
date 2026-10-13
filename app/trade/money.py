"""Read numbers and naira amounts from a trader's message, and format them.

Messages reach the brain in English (voice notes are transcribed and
translated first), so amounts can arrive as digits ("52,000", "52000",
"52k", "1.5m", "N52,000") or as words ("fifty-two thousand naira").
Both are read here so the ledger never depends on a language model for a
number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "a": 1, "an": 1,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_SPECIAL = {"half": Decimal("0.5"), "dozen": Decimal("12")}

_WORD = set(_UNITS) | set(_TENS) | set(_SCALES)

# Digits with optional thousands separators and decimals, then an optional
# k / m suffix ("52k", "1.5m"). A leading naira sign is allowed.
_DIGIT_NUMBER = re.compile(
    r"(?<![\w.])(?:₦|ngn|n)?\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?:\s?(k|m|million|thousand)(?![a-z]))?(?![\d])",
    re.I,
)


@dataclass(frozen=True)
class Number:
    """One number found in a message, with where it was found."""

    value: Decimal
    start: int
    end: int


def to_decimal(value: object) -> Decimal | None:
    """Best-effort conversion to Decimal; ``None`` when it is not a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().lower().replace("₦", "").replace("ngn", "").replace("naira", "").strip()
    found = find_numbers(text)
    if len(found) == 1:
        return found[0].value
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _digit_value(digits: str, suffix: str | None) -> Decimal:
    value = Decimal(digits.replace(",", ""))
    suffix = (suffix or "").lower()
    if suffix in ("k", "thousand"):
        value *= 1_000
    elif suffix in ("m", "million"):
        value *= 1_000_000
    if value == value.to_integral_value():
        value = value.quantize(Decimal(1))
    return value


def _words_value(words: list[str]) -> Decimal | None:
    """Value of a run of number words ("fifty two thousand five hundred")."""
    total = 0
    current = 0
    seen = False
    for word in words:
        if word in _UNITS:
            current += _UNITS[word]
            seen = True
        elif word in _TENS:
            current += _TENS[word]
            seen = True
        elif word == "hundred":
            current = (current or 1) * 100
            seen = True
        elif word in ("thousand", "million", "billion"):
            total += (current or 1) * _SCALES[word]
            current = 0
            seen = True
    if not seen:
        return None
    return Decimal(total + current)




def find_numbers(text: str | None) -> list[Number]:
    """Every number in ``text``, as digits or English words, in order.

    "and" inside a word number is allowed ("one hundred and fifty"). A digit
    followed by a scale word is combined ("52 thousand"). "a"/"an" only count
    when followed by a scale word ("a thousand"), so "a bag" is not 1.
    """
    text = text or ""
    found: list[Number] = []
    taken: list[tuple[int, int]] = []

    for match in _DIGIT_NUMBER.finditer(text):
        digits, suffix = match.group(1), match.group(2)
        start = match.start(1)
        # "half a million", "1.5 million" are handled by the suffix group.
        found.append(Number(_digit_value(digits, suffix), start, match.end()))
        taken.append((match.start(), match.end()))

    def overlaps(start: int, end: int) -> bool:
        return any(start < t_end and end > t_start for t_start, t_end in taken)

    tokens = [(m.group(0).lower(), m.start(), m.end()) for m in re.finditer(r"[A-Za-z]+(?:-[A-Za-z]+)*", text)]
    i = 0
    while i < len(tokens):
        word, start, end = tokens[i]
        parts = word.split("-")
        if not all(p in _WORD for p in parts) or overlaps(start, end):
            if word in _SPECIAL and not overlaps(start, end):
                # "half a bag", "a dozen": half alone is 0.5, dozen alone is 12.
                found.append(Number(_SPECIAL[word], start, end))
            i += 1
            continue
        run: list[str] = []
        run_start, run_end = start, end
        j = i
        extra = Decimal("0")
        while j < len(tokens):
            w, s, e = tokens[j]
            ps = w.split("-")
            # "a"/"an" after a number word ends the number ("two a day").
            if run and w in ("a", "an"):
                break
            if all(p in _WORD for p in ps) and not overlaps(s, e):
                run.extend(ps)
                run_end = e
                j += 1
                continue
            if w == "and" and run and j + 1 < len(tokens):
                # "two and a half" = 2.5
                if (
                    j + 2 < len(tokens)
                    and tokens[j + 1][0] in ("a", "an")
                    and tokens[j + 2][0] == "half"
                ):
                    extra = Decimal("0.5")
                    run_end = tokens[j + 2][2]
                    j += 3
                    break
                # "one hundred and fifty": skip "and" between number words.
                nxt = tokens[j + 1][0].split("-")
                if all(p in _WORD and p not in ("a", "an") for p in nxt):
                    j += 1
                    continue
            break
        # A lone "a"/"an" (or a run that is only articles) is not a number.
        if all(p in ("a", "an") for p in run):
            i = j
            continue
        if run and run[0] in ("a", "an") and (len(run) == 1 or run[1] not in _SCALES):
            run = run[1:]
            run_start = tokens[i + 1][1] if i + 1 < len(tokens) else run_start
        value = _words_value(run)
        if value is not None:
            found.append(Number(value + extra, run_start, run_end))
        i = j

    # Digits followed by a scale word written out: "52 thousand" is caught by
    # the digit pattern; "1.5 million" too. Sort by position.
    found.sort(key=lambda n: n.start)
    return found


def said_values(text: str | None) -> set[Decimal]:
    """The set of numeric values the trader said, normalised for comparison."""
    return {n.value.normalize() for n in find_numbers(text)}


def was_said(value: Decimal | None, text: str | None) -> bool:
    """True when ``value`` is one of the numbers in ``text``."""
    if value is None:
        return False
    return value.normalize() in said_values(text)


def naira(amount: Decimal | int | float | None) -> str:
    """Digits for the text card: ₦52,000 or ₦1,250.50 (kobo only if any)."""
    if amount is None:
        return "-"
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value == value.to_integral():
        return f"{sign}₦{int(value):,}"
    return f"{sign}₦{value:,.2f}"


def plain_naira(amount: Decimal | int | float | None) -> str:
    """Like :func:`naira` but with NGN, for the PDF (its font has no ₦)."""
    return naira(amount).replace("₦", "NGN ")


def qty(value: Decimal | int | float | None) -> str:
    """A quantity without trailing zeros: 3, 2.5, 0.25."""
    if value is None:
        return "-"
    d = Decimal(str(value))
    if d == d.to_integral():
        return f"{int(d):,}"
    return f"{d.normalize():f}"


_ONES = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS_WORDS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _below_thousand(n: int) -> str:
    words: list[str] = []
    if n >= 100:
        words.append(f"{_ONES[n // 100]} hundred")
        n %= 100
        if n:
            words.append("and")
    if n >= 20:
        words.append(_TENS_WORDS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n or not words:
        words.append(_ONES[n])
    return " ".join(words)


def number_words(value: Decimal | int | float) -> str:
    """English words for a whole number (spoken replies say money as words)."""
    n = int(Decimal(str(value)).to_integral_value(rounding=ROUND_HALF_UP))
    if n < 0:
        return "minus " + number_words(-n)
    if n < 1000:
        return _below_thousand(n)
    parts: list[str] = []
    for scale_value, scale_name in ((1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")):
        if n >= scale_value:
            parts.append(f"{_below_thousand(n // scale_value)} {scale_name}")
            n %= scale_value
    if n:
        parts.append(("and " if n < 100 else "") + _below_thousand(n))
    return " ".join(parts)


def spoken_naira(amount: Decimal | int | float) -> str:
    """Money as spoken words: "fifty-two thousand naira"."""
    return f"{number_words(amount)} naira"


def spoken_qty(value: Decimal | int | float) -> str:
    """A quantity as words: "three", "two and a half"."""
    d = Decimal(str(value))
    whole = int(d)
    frac = d - whole
    if frac == 0:
        return number_words(whole)
    if frac == Decimal("0.5"):
        return "half" if whole == 0 else f"{number_words(whole)} and a half"
    return qty(d)


def plural_unit(unit: str | None, value: Decimal | int | float | None) -> str:
    """"bag" / "bags"; units that do not change stay as they are."""
    if value is not None:
        value = Decimal(str(value))
    if not unit:
        return "unit" if value == 1 else "units"
    if value == 1 or unit in ("kg", "pcs", "dozen"):
        return unit
    if unit.endswith(("x", "ch", "sh")):
        return unit + "es"
    return unit + "s"
