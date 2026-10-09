"""Pure v20 temporal contract, shared by ComfyUI guards and AJ's controller."""
from decimal import Decimal
import heapq
import math
import re

FRAME_EXPRESSION = "max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17"
LENGTH_EXPRESSION = "floor(round(a / 24, 4) * 100) / 100"
SECTIONS = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
            "overall_soundscape", "non_diegetic_music")
TIMECODE = r"[0-9]{2,}:[0-5][0-9]\.[0-9]{3}"
SHOT = re.compile(r"(?m)^\[Shot ([1-9][0-9]*)\]")
TIMELINE = re.compile(r"(?m)^Timeline:[ \t]*(" + TIMECODE + r")-(" + TIMECODE + r")[ \t]*$")


def directive_text(text):
    """Mask clearly quoted references and escaped markers, preserving offsets."""
    masked = list(text)
    index = 0
    quotes = {'"': '"', "'": "'", '`': '`', '“': '”', '‘': '’'}
    while index < len(text):
        char = text[index]
        if char == '\\' and index + 1 < len(text):
            masked[index:index + 2] = [' ', ' ']
            index += 2
            continue
        if char in quotes and not (char in {"'", '‘'} and index and text[index - 1].isalnum()):
            end = index + 1
            while end < len(text) and text[end] != '\n':
                if text[end] == '\\':
                    end += 2
                elif text[end] == quotes[char]:
                    break
                else:
                    end += 1
            if end < len(text) and text[end] == quotes[char]:
                masked[index:end + 1] = [' '] * (end + 1 - index)
                index = end + 1
                continue
        index += 1
    return ''.join(masked)


def structural_headers(text):
    visible = directive_text(text)
    headers = list(SHOT.finditer(visible))
    if [match.start() for match in re.finditer(r'\[shot(?=\s|\d|\]|$)', visible, re.I)] != [match.start() for match in headers]:
        raise ValueError("inline, malformed or misplaced structural shot directive")
    return visible, headers


def milliseconds(seconds):
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("effective duration must be finite and positive")
    value = Decimal(str(seconds)) * 1000
    if value != value.to_integral_value():
        raise ValueError("effective duration must have exact millisecond precision")
    return int(value)


def duration_identity(requested):
    if type(requested) not in (int, float) or not math.isfinite(requested) or requested <= 0:
        raise ValueError("requested duration must be finite and positive")
    # Execute the literal, checksum-locked v20 expressions, with the same Python
    # round/max/floor semantics as ComfyMathExpression. No second rounding rule.
    functions = {"__builtins__": {}, "max": max, "round": round, "floor": math.floor}
    frames = eval(FRAME_EXPRESSION, functions, {"a": requested})
    effective = eval(LENGTH_EXPRESSION, functions, {"a": frames})
    return {"requested_duration_seconds": requested, "legal_frame_count": frames,
            "effective_duration_seconds": effective, "effective_duration_ms": milliseconds(effective),
            "duration_contract": "v20_24fps_v1"}


def validate_duration(identity):
    if not isinstance(identity, dict) or identity.get("duration_contract") != "v20_24fps_v1":
        raise ValueError("persisted effective duration missing; explicit reconciliation is required")
    requested = identity.get("requested_duration_seconds")
    if type(requested) not in (int, float) or not math.isfinite(requested) or requested <= 0:
        raise ValueError("invalid persisted requested duration")
    frames = identity.get("legal_frame_count")
    if type(frames) is not int or frames < 5 or frames % 17 != 5:
        raise ValueError("invalid persisted H3 frame alignment")
    effective = identity.get("effective_duration_seconds")
    ms = milliseconds(effective)
    if type(identity.get("effective_duration_ms")) is not int or ms != identity["effective_duration_ms"]:
        raise ValueError("persisted effective duration precision mismatch")
    # v20 floors the frame-derived duration to centiseconds. Check the frozen
    # values against each other, without recomputing from a new UI default.
    if effective != eval(LENGTH_EXPRESSION, {"__builtins__": {}, "round": round, "floor": math.floor}, {"a": frames}):
        raise ValueError("persisted effective duration differs from H3 frames")
    return identity


def timecode_ms(value):
    if not re.fullmatch(TIMECODE, value):
        raise ValueError("malformed canonical timecode")
    minute, rest = value.split(":")
    second, ms = rest.split(".")
    return (int(minute) * 60 + int(second)) * 1000 + int(ms)


def timecode(value):
    minute, rest = divmod(value, 60000)
    second, ms = divmod(rest, 1000)
    return f"{minute:02d}:{second:02d}.{ms:03d}"


def shots(plan):
    if not isinstance(plan, str):
        raise ValueError("creative plan missing")
    visible, headers = structural_headers(plan)
    if not headers or [int(match.group(1)) for match in headers] != list(range(1, len(headers) + 1)):
        raise ValueError("creative plan shot identities are not sequential")
    if len(re.findall(r"(?m)^\[Shot\b", plan)) != len(headers):
        raise ValueError("malformed creative plan shot header")
    result = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(plan)
        matches = list(TIMELINE.finditer(plan, header.end(), end))
        if len(matches) != 1 or len(re.findall(r"(?m)^Timeline:", plan[header.end():end])) != 1:
            raise ValueError("each creative shot requires one canonical Timeline")
        match = matches[0]
        start, finish = timecode_ms(match.group(1)), timecode_ms(match.group(2))
        if finish <= start:
            raise ValueError("creative shot interval must be positive")
        result.append((start, finish, match))
    if len(list(TIMELINE.finditer(plan))) != len(result) or result[0][0] != 0:
        raise ValueError("creative timeline has an orphan interval or nonzero first start")
    return result


def canonical_plan(plan, effective):
    target = milliseconds(effective)
    schedule = shots(plan)
    if schedule[0][0] != 0 or schedule[-1][1] != target or any(a[1] != b[0] for a, b in zip(schedule, schedule[1:])):
        raise ValueError("creative timeline is not contiguous or does not end at effective duration")
    return schedule


def normalize_plan(plan, effective):
    target = milliseconds(effective)
    proposed = shots(plan)
    if target < len(proposed):
        raise ValueError("too many positive shots for effective duration")
    weights = [end - start for start, end, _ in proposed]
    total = sum(weights)
    quotients = [divmod(target * weight, total) for weight in weights]
    allocations = [max(1, quotient) for quotient, _ in quotients]
    remainder = target - sum(allocations)
    if remainder >= 0:
        eligible = sorted((i for i, (q, _) in enumerate(quotients) if q > 0), key=lambda i: (-quotients[i][1], i))
        for index in eligible[:remainder]:
            allocations[index] += 1
    else:
        # Lower bound of one millisecond can over-allocate tiny shots. Remove
        # milliseconds from the most over-allocated eligible interval; stable
        # index ties and integer comparisons make the result deterministic.
        heap = [(-(amount * total - target * weights[i]), i) for i, amount in enumerate(allocations) if amount > 1]
        heapq.heapify(heap)
        for _ in range(-remainder):
            if not heap:
                raise ValueError("creative timeline cannot be allocated safely")
            _, index = heapq.heappop(heap)
            allocations[index] -= 1
            if allocations[index] > 1:
                heapq.heappush(heap, (-(allocations[index] * total - target * weights[index]), index))
    if sum(allocations) != target:
        raise ValueError("creative timeline allocation failed")
    replacements, cursor = [], 0
    for (_, _, match), length in zip(proposed, allocations):
        replacements.extend(((match.start(1), match.end(1), timecode(cursor)),
                             (match.start(2), match.end(2), timecode(cursor + length))))
        cursor += length
    result = plan
    for start, end, value in reversed(replacements):
        result = result[:start] + value + result[end:]
    canonical_plan(result, effective)
    return result


def final_answer(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("compiler final answer is empty")
    if any(line.strip() in {"[Start thinking]", "[End thinking]", "<think>", "</think>"} for line in text.splitlines()):
        raise ValueError("ambiguous native reasoning boundary in compiler final answer")
    headers = list(re.finditer(r"(?m)^(" + "|".join(SECTIONS) + r"):[ \t]*", text))
    if tuple(match.group(1) for match in headers) != SECTIONS or text[:headers[0].start()].strip():
        raise ValueError("compiler final answer violates the v20 six-section contract")
    for index, match in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        if not text[match.end():end].strip():
            raise ValueError("compiler final answer has an empty v20 section")
    return text


def final_timeline(text, plan, effective, *, canonicalize=False):
    final_answer(text)
    schedule = canonical_plan(plan, effective)
    start = re.search(r"(?m)^detailed_description:[ \t]*", text).end()
    end = re.search(r"(?m)^overall_soundscape:", text).start()
    description = text[start:end]
    visible, headers = structural_headers(description)
    if [int(match.group(1)) for match in headers] != list(range(1, len(schedule) + 1)):
        raise ValueError("final prompt shot identities differ from creative plan")
    whole_visible, whole_headers = structural_headers(text)
    if len(whole_headers) != len(headers):
        raise ValueError("final prompt has malformed or misplaced shot headers")
    replacements, actual_cuts = [], []
    for index, header in enumerate(headers):
        tail = description[header.end():]
        if index == 0:
            if re.match(r"\s*(?:At|at)\s+[+-]?[0-9]+:[0-9]+", tail) or re.match(r"\s*" + TIMECODE, tail):
                raise ValueError("Shot 1 must not have a timestamp")
            continue
        stamp = re.match(r"[ \t]+At (" + TIMECODE + r"),", tail)
        if not stamp:
            raise ValueError("final cut requires a canonical timestamp")
        actual_cuts.append(start + header.end() + stamp.start(1) - 3)
        expected = schedule[index][0]
        supplied = timecode_ms(stamp.group(1))
        if supplied != expected:
            if not canonicalize:
                raise ValueError("final cut differs from canonical creative timeline")
            replacements.append((start + header.end() + stamp.start(1), start + header.end() + stamp.end(1), timecode(expected)))
    # Every unquoted cut directive must be the canonical one immediately after
    # its structural header, including directives hidden inside ordinary prose.
    cuts = [match.start() for match in re.finditer(r'\bat\s+[+-]?[0-9]+:[0-9]+', whole_visible, re.I)]
    if cuts != actual_cuts:
        raise ValueError("extra or misplaced temporal cut directive")
    result = text
    for begin, finish, value in reversed(replacements):
        result = result[:begin] + value + result[finish:]
    return result
