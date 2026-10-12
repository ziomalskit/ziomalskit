"""Detection-only spans, whole intervals, then scalar manual clocks.

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
CLOCK_FRAGMENT = re.compile(r"[0-9]+:[+-]?(?:[0-9]*[.,]|(?:NaN|Inf(?:inity)?)\b)", re.I)
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.I)
VERSION = re.compile(r"v?[0-9]+(?:\.[0-9]+)+(?:[-+][a-z0-9._-]+)?", re.I)


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


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    text: str


def _context(detection, start):
    """Read at most four preceding words, preserving absolute cut offsets."""
    offset, words = start, 0
    while offset and words < 4:
        while offset and detection[offset - 1].isspace():
            offset -= 1
        if not words and offset and detection[offset - 1] == ":":
            offset -= 1
        if not offset or not _word_character(detection[offset - 1]):
            break
        while offset and _word_character(detection[offset - 1]):
            offset -= 1
        words += 1
    return offset, "".join(detection[offset:start])


def _word_character(char):
    # Combining marks and invisible format characters cannot split an embedded
    # clock away from its prefix/suffix or hide part of its numeric syntax.
    category = unicodedata.category(char)
    return char.isalnum() or char == "_" or category[0] == "M" or category == "Cf"


def _evidence(text):
    return "".join(char for char in text if unicodedata.category(char)[0] != "M"
                   and unicodedata.category(char) != "Cf")


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
        if _evidence(token):
            yield Token(start, start + len(token), token)


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
    if any(not (_word_character(char) or char in ":.-+") for char in token):
        return False
    stem, separator, extension = token.rpartition(".")
    # An alphanumeric extension needs a letter and a named stem. A numeric
    # millisecond group with an adjoining suffix is not a separate extension.
    if not (separator and stem and extension.isascii() and extension.isalnum()
            and any(char.isalpha() for char in extension)
            and any(char.isalpha() or char == "_" for char in stem.split(":", 1)[0])):
        return False
    return not (extension[:3].isdigit() and len(extension) >= 3 and
                re.search(r"[0-9]+:[0-9]+$", stem))


def _path(token):
    if any(not (_word_character(char) or char in ":.-+/\\~@") for char in token):
        return False
    parts = re.split(r"[/\\]", token)
    if len(parts) < 2 or not parts[-1]:
        return False
    return (token.startswith(("/", "\\", "./", "../")) or
            re.match(r"[a-z]:[/\\]", token, re.I) is not None or
            ":" not in parts[0] and any(char.isalpha() or char == "_" for char in parts[0]))


def _address(token):
    if token.startswith("[") and token.endswith("]"):
        token = token[1:-1]
    address, separator, zone = token.partition("%")
    return (not separator or zone and all(_word_character(char) or char in ".-" for char in zone)) and _ipv6(address)


def _host_port(token):
    bracketed = re.fullmatch(r"\[([^\]]+)\]:[0-9]+", token)
    if bracketed:
        return _address(bracketed.group(1))
    host, separator, port = token.rpartition(":")
    return bool(separator and port.isascii() and port.isdigit() and
                re.fullmatch(r"[a-z0-9_][a-z0-9_.-]*", host, re.I) and
                any(char.isalpha() or char in "_." for char in host))


def _opaque_kind(token):
    if URL.fullmatch(token):
        return "url"
    if _address(token):
        return "address"
    if _path(token) or _filename(token):
        return "file"
    if _host_port(token):
        return "host_port"
    if VERSION.fullmatch(token):
        return "version"
    label, separator, value = token.partition(":")
    if (separator and label and (label[0].isalpha() or label[0] == "_") and
            all(_word_character(char) for char in label)):
        # Labels may introduce address/URL syntax as well as clock values.
        if URL.fullmatch(value) or _address(value):
            return "labeled_prose"
    return None


def _temporal_context(token, before):
    label, separator, value = _evidence(token).partition(":")
    return bool(TEMPORAL_CONTEXT.search(_evidence(before)) or
                separator and value and TEMPORAL_CONTEXT.fullmatch(label))


def _remaining_text(detection):
    """Stage 1: mask opaque syntax without moving offsets or changing source."""
    remaining = list(detection)
    # URLs own their punctuation, including query separators and path hyphens.
    # The second scan can then use prose delimiters for other lexical classes.
    for pattern in (URL, re.compile(r"[^\s()<>{},;!?\"']+")):
        for match in pattern.finditer("".join(remaining)):
            start, end = match.span()
            token = match.group().rstrip(".,;!?)}")
            if token.startswith("[") and token.endswith("]"):
                start += 1
                token = token[1:-1]
            else:
                token = token.rstrip("]")
            end = start + len(token)
            _, before = _context(remaining, start)
            if _opaque_kind(token) and not _temporal_context(token, before):
                # A barrier, rather than whitespace, keeps an opaque span's
                # trailing words/signs from supplying context to the next value.
                remaining[start:end] = "\0" * (end - start)
    return "".join(remaining)


def _scalar_token(token):
    label, separator, value = token.text.partition(":")
    # Named labels delimit scalar values. Interval RHS tokens deliberately do
    # not use this operation: an endpoint must itself be a complete timestamp.
    if (separator and label.isalpha() and label.lower() != "at" and
            CLOCK_FRAGMENT.search(_evidence(value)) and not _opaque_kind(token.text)):
        return Token(token.start + len(label) + 1, token.end, value)
    return token


def _kind(token, before):
    # Opaque syntax has already been removed unless temporal context overrides
    # it. Remaining integer pairs are prose; fractional/nonfinite operands give
    # scalar temporal evidence without a numerator/denominator heuristic.
    if _temporal_context(token, before):
        return "clock"
    return "clock" if CLOCK_FRAGMENT.search(_evidence(token)) else "prose"


def _clock_candidate(token, detection):
    _, before = _context(detection, token.start)
    return ":" in token.text and _kind(token.text, before) == "clock"


def _endpoint_like(token):
    evidence = _evidence(token.text)
    return ":" in evidence or evidence[0].isdigit() or bool(re.fullmatch(r"NaN|Inf(?:inity)?", evidence, re.I))


def _operator(gap):
    evidence = _evidence(gap)
    return any(char in "+-" for char in evidence) and not any(_word_character(char) for char in evidence)


def _validated_clock(token, visible, detection, target, *, leading_sign=True):
    start, end = token.start, token.end
    if leading_sign:
        sign = start
        while sign and (detection[sign - 1].isspace()
                        or unicodedata.category(detection[sign - 1])[0] == "M"
                        or unicodedata.category(detection[sign - 1]) == "Cf"):
            sign -= 1
        if sign and detection[sign - 1] in "+-":
            start = sign - 1
    context_offset, before = _context(detection, start)
    cut = re.search(r"\bat\s+$", before, re.I)
    if cut is None and re.search(r"\bat\s+$", _evidence(before), re.I):
        raise ValueError("manual cut directive contains noncanonical characters")
    raw = visible[start:end]
    if raw != detection[start:end] or not re.fullmatch(TIMECODE, raw, re.ASCII):
        raise ValueError("manual prompt timestamp must use canonical ASCII MM:SS.mmm")
    value = timecode_ms(raw)
    if value > target:
        raise ValueError("manual prompt timestamp exceeds effective duration")
    return Clock(start, end, value, context_offset + cut.start() if cut else None)


def manual_clocks(visible: str, target: int) -> list[Clock]:
    detection = "".join(detection_character(char) for char in visible)
    remaining = _remaining_text(detection)
    tokens = list(_tokens(remaining))
    clocks = []
    consumed = set()
    # Stage 2: recognize maximal interval-like constructions before deciding
    # whether individual endpoints would otherwise be ordinary integer prose.
    index = 0
    while index < len(tokens):
        first = tokens[index]
        if not _endpoint_like(first):
            index += 1
            continue
        last = index
        while (last + 1 < len(tokens) and _endpoint_like(tokens[last + 1]) and
               _operator(detection[tokens[last].end:tokens[last + 1].start])):
            last += 1
        construction = tokens[index:last + 1]
        construction_start, index = index, last + 1
        if len(construction) == 1 or not any(_clock_candidate(_scalar_token(token), remaining) for token in construction):
            continue
        if len(construction) != 2:
            raise ValueError("manual time intervals cannot contain chained boundaries")
        gap = visible[first.end:construction[1].start]
        if gap.strip() != "-" or any(char not in " \t-" for char in gap):
            raise ValueError("manual interval separator must be an ASCII hyphen")
        left = _validated_clock(_scalar_token(first), visible, remaining, target)
        right = _validated_clock(construction[1], visible, remaining, target, leading_sign=False)
        if not left.value < right.value:
            raise ValueError("manual prompt time interval must be positive and within effective duration")
        clocks.extend((left, right))
        consumed.update(range(construction_start, last + 1))
    # Stage 3: remaining scalar candidates retain the original canonical/bounds
    # and cut-offset contract. Bare integer pairs remain ordinary prose.
    for index, token in enumerate(tokens):
        scalar = _scalar_token(token)
        if index not in consumed and _clock_candidate(scalar, remaining):
            tail = detection[token.end:].split("\n", 1)[0]
            if _operator(tail):
                raise ValueError("manual time interval requires two complete endpoints")
            clocks.append(_validated_clock(scalar, visible, remaining, target))
    return sorted(clocks, key=lambda clock: clock.start)
