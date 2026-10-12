"""Typed lexical spans: directives, opaque prose, intervals, scalar clocks.

Offsets always refer to the original text. Lookalikes are mapped only to discover
clock-like syntax; accepted text is never normalized or rewritten.
"""
from dataclasses import dataclass
from enum import Enum, auto
import re
import unicodedata

from .temporal import TIMECODE, timecode_ms

SIGNS = {"−": "-", "–": "-", "—": "-", "‐": "-", "‑": "-", "‒": "-", "﹣": "-", "－": "-"}
PUNCTUATION = {"：": ":", "∶": ":", "꞉": ":", "．": ".", "․": "."}
CLOCK_FRAGMENT = re.compile(r"[0-9]+:[+-]?(?:[0-9]*[.,]|(?:NaN|Inf(?:inity)?)\b)", re.I)
URL = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", re.I)
VERSION = re.compile(r"v?[0-9]+(?:\.[0-9]+)+(?:[-+][a-z0-9._-]+)?", re.I)
DIRECTIVES = frozenset(("at", "clock", "time", "timestamp", "timeline", "cut", "shot", "range"))


class Kind(Enum):
    CANONICAL_CLOCK = auto()
    MALFORMED_CLOCK = auto()
    INTEGER_PAIR_PROSE = auto()
    CLOCK_PREFIX = auto()
    DIRECTIVE = auto()
    WORD = auto()
    PUNCTUATION = auto()
    TEMPORAL_OPERATOR = auto()
    FILENAME = auto()
    PATH = auto()
    URL = auto()
    ADDRESS = auto()
    HOST_PORT = auto()
    VERSION = auto()


SCALAR_CLOCKS = frozenset((Kind.CANONICAL_CLOCK, Kind.MALFORMED_CLOCK))
CLOCK_OPERANDS = SCALAR_CLOCKS | {Kind.INTEGER_PAIR_PROSE, Kind.CLOCK_PREFIX}
OPAQUE = frozenset((Kind.FILENAME, Kind.PATH, Kind.URL, Kind.ADDRESS, Kind.HOST_PORT, Kind.VERSION))


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
    kind: Kind = Kind.WORD


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
    if any(not (_word_character(char) or char in ":.-+~") for char in token):
        return False
    body = token.rstrip("~")
    stem, separator, extension = body.rpartition(".")
    parts = re.split(r"[-+]", extension)
    # Extension components and backup suffixes are one filename production;
    # no media whitelist or surrounding prose is needed to establish its shape.
    if not (separator and stem and all(part.isascii() and part.isalnum() for part in parts)
            and any(char.isalpha() for char in extension)
            and any(char.isalpha() or char == "_" for char in stem.split(":", 1)[0])):
        return False
    if ":" not in body:
        return True
    components = list(re.finditer(r"[0-9]+:[0-9]+(?:\.([0-9]+)(?=[._-]|$))?", body))
    if not components:
        return False
    for component in components:
        fraction = component.group(1)
        tail = body[component.end():]
        if tail.startswith(".") and any(not part for part in tail[1:].split(".")):
            return False
        if fraction is not None:
            if len(fraction) != 3:
                return False
        elif tail.startswith(".") and re.match(r"[0-9]{3}", tail[1:]):
            # .000abc is a malformed millisecond group, not an extension;
            # a distinct .7z/.3gp extension after integer colon prose is valid.
            return False
        if tail and tail[0] not in "._-":
            return False
    return True


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
        return Kind.URL
    if _address(token):
        return Kind.ADDRESS
    if VERSION.fullmatch(token):
        return Kind.VERSION
    if _path(token):
        return Kind.PATH
    if _filename(token):
        return Kind.FILENAME
    if _host_port(token):
        return Kind.HOST_PORT
    label, separator, value = token.partition(":")
    if (separator and label and (label[0].isalpha() or label[0] == "_") and
            all(_word_character(char) for char in label)):
        # Labels may introduce address/URL syntax as well as clock values.
        if URL.fullmatch(value):
            return Kind.URL
        if _address(value):
            return Kind.ADDRESS
    return None


def _clock_kind(text):
    evidence = _evidence(text)
    if re.fullmatch(TIMECODE, text, re.ASCII):
        return Kind.CANONICAL_CLOCK
    if CLOCK_FRAGMENT.search(evidence):
        return Kind.MALFORMED_CLOCK
    if re.fullmatch(r"[0-9]+:[0-9]+", evidence):
        return Kind.INTEGER_PAIR_PROSE
    if re.search(r"[0-9]+:", evidence):
        return Kind.CLOCK_PREFIX
    return Kind.DIRECTIVE if evidence.lower() in DIRECTIVES else Kind.WORD


def _punctuation(start, end, text):
    kind = Kind.TEMPORAL_OPERATOR if _operator(text) else Kind.PUNCTUATION
    return Token(start, end, text, kind)


def _lex_piece(start, text):
    """Lex one source atom; a class tag does not yet grant opacity."""
    label, separator, value = text.partition(":")
    if separator and _evidence(label).lower() in DIRECTIVES:
        end = start + len(label)
        yield Token(start, end, label, Kind.DIRECTIVE)
        yield _punctuation(end, end + 1, ":")
        if value:
            yield from _lex_piece(end + 1, value)
        return
    opaque = _opaque_kind(text)
    if opaque is not None:
        yield Token(start, start + len(text), text, opaque)
        return
    # Wrappers delimit filename/address displays. In a directive the wrapper
    # remains punctuation in its argument gap and cannot conceal a bad operand.
    if "[" in text or "]" in text:
        cursor = 0
        for match in re.finditer(r"[\[\]]", text):
            if cursor < match.start():
                yield from _lex_piece(start + cursor, text[cursor:match.start()])
            yield _punctuation(start + match.start(), start + match.end(), match.group())
            cursor = match.end()
        if cursor < len(text):
            yield from _lex_piece(start + cursor, text[cursor:])
        return
    if separator and label.isalpha() and (not value or CLOCK_FRAGMENT.search(_evidence(value))):
        end = start + len(label)
        yield Token(start, end, label, _clock_kind(label))
        yield _punctuation(end, end + 1, ":")
        if value:
            yield from _lex_piece(end + 1, value)
        return
    cursor = 0
    for token in _tokens(text):
        if cursor < token.start:
            yield _punctuation(start + cursor, start + token.start, text[cursor:token.start])
        kind = _opaque_kind(token.text) or _clock_kind(token.text)
        yield Token(start + token.start, start + token.end, token.text, kind)
        cursor = token.end
    if cursor < len(text):
        yield _punctuation(start + cursor, start + len(text), text[cursor:])


def _lex(detection):
    # URL punctuation belongs to its lexical atom, including query/fragment
    # syntax. Directives can still consume that atom as a noncanonical operand.
    # The scalar lexer owns comma decimals versus prose commas; cutting at a
    # comma here would turn the malformed clock 00:05,000 into integer prose.
    atoms = re.compile(URL.pattern + r"|[^\s\x00()<>{};!?\"']+", re.I)
    cursor = 0
    for match in atoms.finditer(detection):
        if detection[cursor:match.start()].strip():
            yield _punctuation(cursor, match.start(), detection[cursor:match.start()])
        text = match.group().rstrip(".,;!?)}")
        if text:
            yield from _lex_piece(match.start(), text)
        cursor = match.end()
    if detection[cursor:].strip():
        yield _punctuation(cursor, len(detection), detection[cursor:])


def _next_operand(tokens, index):
    index += 1
    while index < len(tokens) and tokens[index].kind in {Kind.PUNCTUATION, Kind.TEMPORAL_OPERATOR}:
        index += 1
    return index if index < len(tokens) else None


def _construction(tokens, start, detection):
    operands = [start]
    while (following := _next_operand(tokens, operands[-1])) is not None:
        if tokens[following].kind not in CLOCK_OPERANDS:
            break
        gap = detection[tokens[operands[-1]].end:tokens[following].start]
        if not _operator(gap):
            break
        operands.append(following)
    return operands


def _interval(tokens, operands, visible, detection, target):
    if len(operands) != 2:
        raise ValueError("manual time intervals cannot contain chained boundaries")
    first, second = (tokens[index] for index in operands)
    gap = visible[first.end:second.start]
    if gap.strip() != "-" or any(char not in " \t-" for char in gap):
        raise ValueError("manual interval separator must be an ASCII hyphen")
    left = _validated_clock(first, visible, detection, target)
    right = _validated_clock(second, visible, detection, target, leading_sign=False)
    if not left.value < right.value:
        raise ValueError("manual prompt time interval must be positive and within effective duration")
    return left, right


def _directive_clocks(tokens, visible, detection, target):
    clocks, clauses = [], []
    for index, marker in enumerate(tokens):
        if marker.kind != Kind.DIRECTIVE or any(start <= marker.start < end for start, end in clauses):
            continue
        name = _evidence(marker.text).lower()
        # Structural Shot headers have their own unchanged numbering/cut grammar.
        if name == "shot" and marker.start and detection[marker.start - 1] == "[":
            continue
        header = index
        following = _next_operand(tokens, header)
        qualifiers = {"clock": {"reads"}, "time": {"is", "reads"}, "timestamp": {"is", "reads"}}
        if following is not None and _evidence(tokens[following].text).lower() in qualifiers.get(name, ()):
            header = following
            following = _next_operand(tokens, header)
        if (name == "shot" and following is not None and
                ":" not in detection[marker.end:tokens[following].start] and
                tokens[following].text.isascii() and tokens[following].text.isdigit()):
            header = following  # Shot identity, never an interval endpoint.
            following = _next_operand(tokens, header)
        if following is None:
            continue
        first = tokens[following]
        # A named declaration owns its value even when that value lacks clock
        # punctuation. Clock:2026 is malformed, while a prose 2026 after a valid
        # scalar still has no lexical evidence making it an interval operand.
        declared = ":" in detection[tokens[header].end:first.start]
        temporal = declared or ":" in _evidence(first.text) or detection[first.end:first.end + 1] == ":"
        if not temporal and name in {"range", "timeline"}:
            rhs = _next_operand(tokens, following)
            temporal = (rhs is not None and tokens[rhs].kind in CLOCK_OPERANDS and
                        _operator(detection[first.end:tokens[rhs].start]))
        if not temporal:
            continue
        gap = _evidence(detection[tokens[header].end:first.start]).strip()
        if gap and (gap != ":" or name == "at"):
            raise ValueError("manual prompt timestamp must use canonical ASCII MM:SS.mmm")
        # Parse the directive argument on the unmasked source. A PATH/FILENAME/
        # URL/ADDRESS tag is not permission to suppress a temporal clause.
        left = _validated_clock(first, visible, detection, target)
        operands = _construction(tokens, following, detection)
        if name in {"range", "timeline"} and len(operands) == 1:
            raise ValueError("manual time interval requires two complete endpoints")
        if len(operands) > 1:
            clocks.extend(_interval(tokens, operands, visible, detection, target))
        else:
            tail = detection[first.end:].split("\n", 1)[0]
            if _operator(tail):
                raise ValueError("manual time interval requires two complete endpoints")
            if detection[first.end:first.end + 1] in {"/", "\\", "~", "@"}:
                raise ValueError("manual prompt timestamp must use canonical ASCII MM:SS.mmm")
            clocks.append(left)
        clauses.append((marker.start, tokens[operands[-1]].end))
    return clocks, clauses


def _remaining_text(detection, tokens, clauses):
    remaining = list(detection)
    spans = list(clauses)
    spans.extend((token.start, token.end) for token in tokens if token.kind in OPAQUE
                 and not any(start < token.end and token.start < end for start, end in clauses))
    for start, end in spans:
        # Offset-preserving barriers stop an opaque span supplying words/signs
        # as context to the next scalar; source text is never changed.
        remaining[start:end] = "\0" * (end - start)
    return "".join(remaining)


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
    tokens = list(_lex(detection))
    # Pass 1: explicit clauses own their original operands before any opacity.
    clocks, clauses = _directive_clocks(tokens, visible, detection, target)
    # Pass 2: opaque non-temporal atoms and already-validated clauses are masked.
    remaining = _remaining_text(detection, tokens, clauses)
    tokens = list(_lex(remaining))
    consumed = set()
    # Pass 3: only clock-like lexical classes can bind a free interval. Integer
    # pairs alone remain ratio/score prose; 4K/2026/fps words are never operands.
    for index, token in enumerate(tokens):
        if index in consumed or token.kind not in CLOCK_OPERANDS:
            continue
        operands = _construction(tokens, index, detection)
        if len(operands) > 1 and any(tokens[operand].kind in SCALAR_CLOCKS for operand in operands):
            clocks.extend(_interval(tokens, operands, visible, remaining, target))
            consumed.update(operands)
    # Pass 4: remaining strong scalar clocks retain canonical/bounds/cut checks.
    for index, token in enumerate(tokens):
        if index not in consumed and token.kind in SCALAR_CLOCKS:
            tail = detection[token.end:].split("\n", 1)[0]
            if _operator(tail):
                raise ValueError("manual time interval requires two complete endpoints")
            clocks.append(_validated_clock(token, visible, remaining, target))
    return sorted(clocks, key=lambda clock: clock.start)
