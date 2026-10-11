"""Trusted prompt sources and read-only validation for direct final prompts.

Manual text is never normalized, retimed, compiled or treated as graph content.
Autoprompt keeps its separate, stricter generated-plan/compiler contract.
"""
from __future__ import annotations

import hashlib
import json
import re

from .temporal import (TIMECODE, structural_headers, timecode_ms,
                       milliseconds, validate_duration)

PROMPT_MODES = (
    {"id": "autoprompt", "label": "Autoprompt",
     "help": "Scene prompt + references → Step 0–4 → 10 candidates.",
     "batches_help": "Each batch generates 10 prompt candidates: 5 auto + 5 review."},
    {"id": "manual_final", "label": "Own final H3 prompt",
     "help": "Exact final text → local validation → render. No prompt generation or rewriting.",
     "batches_help": "Each batch creates one render using your final prompt."},
)


def text_sha256(text: str) -> str:
    try:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    except UnicodeEncodeError as error:
        raise ValueError("final prompt must be valid UTF-8 text") from error


def validate_prompt_fields(mode, prompt, manual_final_prompt) -> None:
    if mode not in {item["id"] for item in PROMPT_MODES}:
        raise ValueError("unknown prompt mode")
    if mode == "manual_final":
        if prompt is not None:
            raise ValueError("manual_final accepts manual_final_prompt, not a scene prompt")
        if not isinstance(manual_final_prompt, str) or not manual_final_prompt.strip():
            raise ValueError("manual_final requires a non-empty Final H3 prompt")
    elif not isinstance(prompt, str) or manual_final_prompt is not None:
        raise ValueError("autoprompt requires prompt and does not accept manual_final_prompt")


def validate_manual_final(text: str, effective: float) -> str:
    """Accept plain H3 text; validate explicit structural/timecode directives.

    There is no generated plan to compare with. Optional shots must be sequential;
    Shot 1 starts implicitly at zero, later cuts increase strictly inside the
    effective duration. Quoted reference text follows the existing guard's masking
    rules. Every unquoted clock timestamp must be canonical and within duration.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Final H3 prompt is empty")
    text_sha256(text)
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        raise ValueError("Final H3 prompt contains invalid control characters")
    if any(line.strip() in {"[Start thinking]", "[End thinking]", "<think>", "</think>"}
           for line in text.splitlines()):
        raise ValueError("Final H3 prompt contains a reasoning boundary; paste final text only")
    target = milliseconds(effective)
    visible, headers = structural_headers(text)
    if [int(match.group(1)) for match in headers] != list(range(1, len(headers) + 1)):
        raise ValueError("manual final prompt shot identities must be sequential from Shot 1")
    actual_cuts, last_cut = [], 0
    for index, header in enumerate(headers):
        tail = visible[header.end():]
        if index == 0:
            if re.match(r"\s*(?:At|at)\s+[+-]?[0-9]+:[0-9]+", tail) or re.match(r"\s*" + TIMECODE, tail):
                raise ValueError("Shot 1 must not have a timestamp")
            continue
        stamp = re.match(r"[ \t]+At (" + TIMECODE + r"),", tail)
        if not stamp:
            raise ValueError("manual final cut requires '[Shot N] At MM:SS.mmm,'")
        supplied = timecode_ms(stamp.group(1))
        if not last_cut < supplied < target:
            raise ValueError("manual final cuts must increase strictly inside effective duration")
        last_cut = supplied
        actual_cuts.append(header.end() + stamp.start(1) - 3)
    cuts = [match.start() for match in re.finditer(r"\bat\s+[+-]?[0-9]+:(?:[+-]?[0-9]+|NaN|Infinity)", visible, re.I)]
    if cuts != actual_cuts:
        raise ValueError("extra or misplaced temporal cut directive in manual final prompt")
    # A hyphen between two canonical clocks is an interval separator, not a
    # negative timestamp. Mask only that proven separator for the scalar scan.
    clocks = list(visible)
    for match in re.finditer(r"(" + TIMECODE + r")([ \t]*-[ \t]*)(" + TIMECODE + r")", visible):
        if not timecode_ms(match.group(1)) < timecode_ms(match.group(3)) <= target:
            raise ValueError("manual prompt time interval must be positive and within effective duration")
        clocks[match.start(2):match.end(2)] = " " * (match.end(2) - match.start(2))
    # A short ratio such as 16:9 is ordinary prompt prose. Clock-like values
    # have fractional seconds or a two-digit seconds field; cut directives
    # above are checked independently and cannot disguise malformed clocks.
    clock_pattern = (r"(?<![\w:])[+-]?[0-9]+:(?:[+-]?[0-9]+\.[0-9]+|"
                     r"[+-]?[0-9]{2,}(?![0-9]|\.[0-9])|NaN|Infinity)(?::[+-]?[0-9]+(?:\.[0-9]+)?)*")
    for match in re.finditer(clock_pattern, "".join(clocks)):
        if not re.fullmatch(TIMECODE, match.group()) or timecode_ms(match.group()) > target:
            raise ValueError("manual prompt timestamp must use MM:SS.mmm within effective duration")
    intervals = []
    for match in re.finditer(r"(?m)^[ \t]*Timeline:([^\n]*)$", visible):
        interval = re.fullmatch(r"[ \t]*(" + TIMECODE + r")[ \t]*-[ \t]*(" + TIMECODE + r")[ \t]*\r?", match.group(1))
        if interval is None:
            raise ValueError("manual Timeline requires a canonical MM:SS.mmm-MM:SS.mmm interval")
        start, end = map(timecode_ms, interval.groups())
        if not 0 <= start < end <= target or intervals and intervals[-1][1] > start:
            raise ValueError("manual Timeline intervals must be positive, ordered and within effective duration")
        intervals.append((start, end))
    return text


def prompt_identity(context: dict) -> str:
    source = {"prompt_mode": context.get("prompt_mode", "autoprompt"),
              "prompt": context.get("prompt"),
              "manual_final_prompt_sha256": context.get("manual_final_prompt_sha256")}
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def freeze_prompt_source(mode, prompt, manual_final_prompt, effective) -> dict:
    validate_prompt_fields(mode, prompt, manual_final_prompt)
    source = {"prompt_mode": mode, "prompt": prompt if mode == "autoprompt" else ""}
    if mode == "manual_final":
        validate_manual_final(manual_final_prompt, effective)
        source.update(manual_final_prompt=manual_final_prompt,
                      manual_final_prompt_sha256=text_sha256(manual_final_prompt))
    source["workflow_prompt_identity"] = prompt_identity(source)
    return source


def validate_prompt_source(context: dict) -> None:
    mode = context.get("prompt_mode", "autoprompt")
    if mode == "manual_final":
        validate_duration(context)
        text = context.get("manual_final_prompt")
        validate_manual_final(text, context["effective_duration_seconds"])
        if context.get("prompt") != "" or context.get("manual_final_prompt_sha256") != text_sha256(text):
            raise ValueError("persisted manual prompt differs from its frozen identity; reconciliation required")
    elif mode != "autoprompt" or context.get("manual_final_prompt") is not None:
        raise ValueError("persisted prompt source is inconsistent; reconciliation required")
    # Old Autoprompt fixtures retain their existing contract. Adapter drift still
    # fences old production jobs; no new identity/default is filled on recovery.
    if "prompt_mode" in context or "workflow_prompt_identity" in context:
        if context.get("workflow_prompt_identity") != prompt_identity(context):
            raise ValueError("persisted prompt source identity changed; reconciliation required")


def is_manual_final(job: dict) -> bool:
    return job.get("prompt_mode") == "manual_final" or job.get("context", {}).get("prompt_mode") == "manual_final"


def validate_job_prompt_source(job: dict) -> None:
    context = job["context"]
    validate_prompt_source(context)
    if job.get("prompt_mode", "autoprompt") != context.get("prompt_mode", "autoprompt"):
        raise ValueError("job prompt mode differs from frozen source; reconciliation required")
    if is_manual_final(job):
        if (job.get("final_h3_prompt") != context["manual_final_prompt"] or job.get("approved_final_prompt") is not None or
                job.get("review_required") is not False or job.get("active_service") == "prompt" or
                job.get("status", "").startswith(("prompt_", "recovery_prompt")) or
                job.get("status") in {"pending_review", "render_queued_review"} or
                any(job.get(key) is not None for key in ("visual_facts", "expanded_intent", "reference_map", "creative_plan",
                                                       "prompt_prompt_id", "prompt_submission_state", "prompt_kind"))):
            raise ValueError("manual final job has generated/review state or changed text; reconciliation required")


def final_prompt_for_render(job: dict) -> str | None:
    if is_manual_final(job):
        validate_job_prompt_source(job)
        return job["context"]["manual_final_prompt"]
    return job.get("approved_final_prompt") or job.get("final_h3_prompt")


def validate_render_prompt(job: dict, text: str) -> str:
    if is_manual_final(job):
        validate_job_prompt_source(job)
        if text != job["context"]["manual_final_prompt"]:
            raise ValueError("render override differs from frozen manual Final H3 prompt")
        return validate_manual_final(text, job["context"]["effective_duration_seconds"])
    from .temporal import final_timeline
    return final_timeline(text, job.get("creative_plan"), job["context"]["effective_duration_seconds"])
