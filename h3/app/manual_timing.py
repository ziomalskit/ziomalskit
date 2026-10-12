"""Small detection-only lexer for manual clocks, ratios and interval operators.

Offsets always refer to the original text. Lookalikes are mapped only to discover
clock-like syntax; accepted text is never normalized or rewritten.
"""
from dataclasses import dataclass
import re
import unicodedata

from .temporal import TIMECODE, timecode_ms

SIGNS = {"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-", "‒": "-", "﹣": "-", "－": "-"}
PUNCTUATION = {"：": ":", "∶": ":", "꞉": ":", "．": ".", "․": "."}
TEMPORAL_CONTEXT = re.compile(r"\b(?:at|clock(?:\s+reads)?|time(?:stamp)?(?:\s+(?:is|reads))?|timeline|cut|shot)\s*:?\s*$", re.I)


def detection_character(char):
    if char in SIGNS:
        return SIGNS[char]
    if char in PUNCTUATION:
        return PUNCTUATION[char]
    if char.isdecimal():
        return str(unicodedata.decimal(char))
    folded = unicodedata.normalize("NFKC", char)
    return folded if len(folded) == 1 else char


@dataclass(frozen=True)
class Clock:
    start: int
    end: int
    value: int
    cut_start: int | None


def _context(detection, start):
    """Read at most four preceding words, preserving absolute cut offsets."""
    offset, words = start, 0
    while offset and words < 4:
        while offset and detection[offset - 1].isspace():
            offset -= 1
        if not words and offset and detection[offset - 1] == ":":
            offset -= 1
        if not offset or not detection[offset - 1].isalnum():
            break
        while offset and (detection[offset - 1].isalnum() or detection[offset - 1] == "_"):
            offset -= 1
        words += 1
    return offset, detection[offset:start]


def _ratio(token, before):
    if not re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", token):
        return False
    # Integer proportions are prose, independent of the denominator's width.
    # Explicit temporal contexts take precedence; fractional clocks never enter
    # this branch. Explicit aspect-ratio prose remains ordinary text.
    if re.search(r"\b(?:aspect\s+)?ratio(?:\s+of)?\s*$", before, re.I):
        return True
    return TEMPORAL_CONTEXT.search(before) is None


def manual_clocks(visible: str, target: int) -> list[Clock]:
    detection = "".join(detection_character(char) for char in visible)
    clocks = []
    cursor = 0
    while cursor < len(detection):
        if not detection[cursor].isascii() or not detection[cursor].isdigit():
            cursor += 1
            continue
        if cursor and (detection[cursor - 1].isalnum() or detection[cursor - 1] in "_:."):
            cursor += 1
            continue
        start = cursor
        end = cursor
        # Consume the entire punctuation/numeric suffix, including malformed
        # extra decimal groups and NaN/Infinity; never validate a prefix.
        while end < len(detection):
            char = detection[end]
            if char.isascii() and (char.isdigit() or char.isalpha()) or char in ":._":
                end += 1
            elif char in "+-" and end > start and detection[end - 1] in ":.":
                end += 1
            else:
                break
        cursor = max(end, cursor + 1)
        token = detection[start:end]
        if ":" not in token:
            continue
        # Full stops/ellipses after a clock are punctuation, but a dot followed
        # by another number remains part of the malformed clock token.
        end -= len(token) - len(token.rstrip("."))
        token = detection[start:end]
        sign = start
        while sign and detection[sign - 1].isspace():
            sign -= 1
        if sign and detection[sign - 1] in "+-":
            interval_operator = bool(clocks and detection[clocks[-1].end:start].strip() == "-")
            if not interval_operator:
                start = sign - 1
                token = detection[start:end]
        context_offset, before = _context(detection, start)
        cut = re.search(r"\bat\s+$", before, re.I)
        if _ratio(token, before):
            continue
        left, right = token.split(":", 1)
        # A numeric prose label ("Reference 1: ...") has no numeric right
        # operand. Leading-zero clocks, explicit temporal contexts, and numeric
        # or nonfinite operands remain clock candidates and fail if malformed.
        if (left.isdigit() and not left.startswith("0") and not TEMPORAL_CONTEXT.search(before)
                and (not right or right[0].isalpha() and not right.lower().startswith(("nan", "inf")))):
            continue
        raw = visible[start:end]
        if raw != token or not re.fullmatch(TIMECODE, raw, re.ASCII):
            raise ValueError("manual prompt timestamp must use canonical ASCII MM:SS.mmm")
        value = timecode_ms(raw)
        if value > target:
            raise ValueError("manual prompt timestamp exceeds effective duration")
        clocks.append(Clock(start, end, value, context_offset + cut.start() if cut else None))
    joined_previous = False
    for first, second in zip(clocks, clocks[1:]):
        gap = visible[first.end:second.start]
        operator = "".join(detection_character(char) for char in gap).strip()
        joined = operator == "-"
        if joined:
            if joined_previous:
                raise ValueError("manual time intervals cannot contain chained boundaries")
            if gap.strip() != "-" or any(char not in " \t-" for char in gap):
                raise ValueError("manual interval separator must be an ASCII hyphen")
            if not first.value < second.value <= target:
                raise ValueError("manual prompt time interval must be positive and within effective duration")
        joined_previous = joined
    return clocks
