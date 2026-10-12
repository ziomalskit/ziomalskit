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
CLOCK_FRAGMENT = re.compile(r"[0-9]+:[+-]?(?:[0-9]*[.,]|(?:NaN|Infinity)\b)", re.I)


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


def _word_character(char):
    # Combining marks and invisible format characters cannot split an embedded
    # clock away from its prefix/suffix or hide part of its numeric syntax.
    category = unicodedata.category(char)
    return char.isalnum() or char == "_" or category[0] == "M" or category == "Cf"


def _tokens(detection):
    """Consume whole word/colon tokens, including both sides of embedded clocks.

    Signs between tokens remain interval operators. Fractional punctuation stays
    inside a token; a comma after fractional seconds remains prose punctuation.
    """
    cursor = 0
    while cursor < len(detection):
        if not (_word_character(detection[cursor]) or detection[cursor] == ":"):
            cursor += 1
            continue
        start = end = cursor
        while end < len(detection):
            char = detection[end]
            if _word_character(char) or char in ":.":
                end += 1
            elif char in "+-" and end > start and detection[end - 1] in ":.":
                end += 1
            elif char == "," and end + 1 < len(detection) and detection[end + 1].isdigit():
                prefix = detection[start:end]
                if ":" not in prefix or "." in prefix.rsplit(":", 1)[1]:
                    break
                end += 1
            else:
                break
        cursor = end
        # Sentence full stops/ellipses are outside the token. Additional decimal
        # groups followed by digits remain inside and cannot validate a prefix.
        token = detection[start:end].rstrip(".")
        label, separator, value = token.partition(":")
        # Named labels delimit values; directly adjoining letters (abc00:...)
        # do not. At cuts retain their existing whitespace-only syntax.
        if (separator and label.isalpha() and label.lower() != "at" and CLOCK_FRAGMENT.search(value)
                and not _ipv6(token) and not _filename(token)):
            yield start + len(label) + 1, start + len(token), value
        else:
            yield start, start + len(token), token


def _ipv6(token):
    """Recognize obvious hex-address shape, without networking-stack validation."""
    if token.count(":") < 2:
        return False
    groups = token.split(":")
    ipv4_tail = "." in groups[-1]
    # IPv6 may end in a dotted IPv4-shaped component. Its decimal groups are
    # address syntax, while an explicit temporal directive still takes priority.
    if ipv4_tail:
        octets = groups[-1].split(".")
        if len(octets) != 4 or any(not part.isascii() or not part.isdigit() or
                                  not 1 <= len(part) <= 3 for part in octets):
            return False
        groups[-1] = "ffff"
    if any(char not in "0123456789abcdefABCDEF" for group in groups for char in group):
        return False
    if any(len(group) > 4 for group in groups):
        return False
    if "::" in token:
        return token.count("::") == 1 and ":::" not in token and sum(bool(group) for group in groups) + ipv4_tail < 8
    return len(groups) + ipv4_tail == 8 and all(groups)


def _filename(token):
    stem, separator, extension = token.rpartition(".")
    # A named file with a letter extension is distinct from a scalar clock's
    # numeric fraction/suffix. No extension list or surrounding phrase is used.
    return bool(separator and extension.isalpha() and
                any(char.isalpha() or char == "_" for char in stem.split(":", 1)[0]))


def _kind(token, before):
    # Established temporal directives take precedence even over address-shaped
    # text. Otherwise integer pairs, labels, ports and addresses are ordinary
    # prose; only fractional/nonfinite numeric clock syntax supplies evidence.
    label, separator, value = token.partition(":")
    if TEMPORAL_CONTEXT.search(before) or separator and value and TEMPORAL_CONTEXT.fullmatch(label):
        return "clock"
    if _ipv6(token):
        return "address"
    if _filename(token):
        return "filename"
    evidence = "".join(char for char in token if unicodedata.category(char)[0] != "M"
                       and unicodedata.category(char) != "Cf")
    return "clock" if CLOCK_FRAGMENT.search(evidence) else "prose"


def manual_clocks(visible: str, target: int) -> list[Clock]:
    detection = "".join(detection_character(char) for char in visible)
    clocks = []
    for start, end, token in _tokens(detection):
        if ":" not in token:
            continue
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
        if _kind(token, before) != "clock":
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
