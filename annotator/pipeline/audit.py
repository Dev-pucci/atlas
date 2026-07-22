"""Pass 3: whole-video audit — consistency, hallucinations, missed actions, hold continuity."""

from __future__ import annotations

import json
from pathlib import Path

from .client import Router, image_part, text_part
from .frames import extract_frames, fmt_ts, video_info
from .lint import normalize_label
from .segments import Segment

AUDIT_PROMPT = """You are the AUDITOR for egocentric video annotations. Below are the current labels for
every segment of one video, plus sparse frames (segment start/middle) for visual verification.

## Rulebook (excerpt of what auditors reject)
- Missed action: a hand clearly does something goal-directed that the label omits (including holds of
  manipulatable objects).
- Missed hand: an action doesn't say which hand.
- Hallucination / added object / added action: label mentions things not visible in the frames.
- Wrong action / wrong object: verb or noun does not match what actually happens.
- Grammar: articles (the/a/an), -ing verbs, pronouns, digits, forbidden verbs
  (adjust/manipulate/move/transfer/inspect/check/examine/reach), wrong hand-exchange verbs
  (must be hand over/pass/put/switch/set).
- Consistency: the SAME object must keep the SAME name across all segments (e.g. never switch between
  "water" and "liquid"); adjacent segments must agree on what each hand is holding (hold continuity:
  if a hand holds an object at the end of segment N and still holds it in segment N+1, segment N+1
  needs the hold label or an action with that object).

## Object glossary
{glossary}

## Current labels
{labels_block}

## Task
Review every segment. {visual_instruction}
Only correct a label when you are confident it is wrong — do not rephrase acceptable labels.

Verdicts:
- "ok" — nothing wrong.
- "revise" — you are fixing GRAMMAR, FORBIDDEN WORDS, or OBJECT NAMING only; give corrected_label.
  {revise_scope}
- "suspect" — you believe something VISUAL is wrong (wrong hand, missed/hallucinated action, wrong
  verb/object) {suspect_scope}. Describe it in issues; do NOT provide a corrected label for visual
  doubts you cannot verify.

Each "issues" entry MUST be under 15 words — a label/pointer for a human to look at, not an essay.
"category: what's wrong" is enough (e.g. "wrong object: looks like tweezers, not a brush"). You are
reviewing MANY segments in this one reply — verbose issues will not fit in the response; terse ones
will.

Reply with JSON only:
{{
  "segments": [
    {{
      "index": 1,
      "verdict": "ok" | "revise" | "suspect",
      "issues": ["category: short explanation, under 15 words", ...],
      "corrected_label": "only when verdict is revise",
      "confidence": 0.0-1.0
    }}
  ],
  "video_notes": "any cross-cutting problems worth flagging to the human"
}}"""


def audit_segments(
    video_path: Path,
    segments: list[Segment],
    context: dict,
    router: Router,
    cfg: dict,
    rulebook: str = "",
) -> tuple[list[Segment], str]:
    s = cfg["sampling"]
    info = video_info(video_path)
    per_seg = int(s.get("audit_frames_per_segment", 0))

    frames = []
    if per_seg > 0:
        times = []
        for seg in segments:
            times.append(min(seg.start + 0.2, info.duration - 0.05))
            if per_seg > 1:
                times.append(min((seg.start + seg.end) / 2, info.duration - 0.05))
        frames = extract_frames(video_path, times, s["max_width"], s["jpeg_quality"])
    mode = f"{len(frames)} frames" if frames else "text-only"
    print(f"Pass 3: audit ({mode}) — {len(segments)} labels")
    if frames:
        visual_instruction = "For visual claims use the frames; for grammar/consistency use the text."
        revise_scope = "With frames available you may also correct clearly visible visual errors."
        suspect_scope = "but the frames are too sparse to be sure"
    else:
        visual_instruction = (
            "You have NO frames — check ONLY grammar, forbidden words, glossary consistency, "
            "hold continuity between adjacent segments, and internal plausibility (e.g. one hand "
            "holding two objects while acting with a third is impossible)."
        )
        revise_scope = ("NEVER change which hand performs an action and never add or remove actions "
                        "— you cannot see the video.")
        suspect_scope = "(you cannot verify visuals without frames)"

    glossary = "; ".join(o.get("name", "") for o in context.get("objects", []))
    labels_block = "\n".join(
        f"{seg.index}. [{seg.time_str()}] {seg.label}" for seg in segments
    )
    content = [text_part(AUDIT_PROMPT.format(glossary=glossary, labels_block=labels_block,
                                             visual_instruction=visual_instruction,
                                             revise_scope=revise_scope, suspect_scope=suspect_scope))]
    for _, jpeg in frames:
        content.append(image_part(jpeg))

    messages = [{"role": "user", "content": content}]
    if rulebook:
        # the auditor must judge against the REAL rules, not its own recollection of them
        messages.insert(0, {"role": "system",
                            "content": "You audit annotations against this rulebook — apply ONLY "
                                       "these rules, never invented ones:\n\n" + rulebook})
    data = router.chat_json("audit", messages)

    by_index = {seg.index: seg for seg in segments}
    for item in data.get("segments", []):
        seg = by_index.get(int(item.get("index", -1)))
        if seg is None:
            continue
        verdict = str(item.get("verdict", "ok")).lower()
        issues = [str(x) for x in item.get("issues", [])]
        conf = item.get("confidence")
        if conf is not None:
            seg.confidence = min(seg.confidence, float(conf)) if seg.confidence else float(conf)
        if verdict == "revise" and item.get("corrected_label"):
            old = seg.label
            seg.label = normalize_label(str(item["corrected_label"]))
            seg.flags.append(f"audit revised (was: {old}) — {'; '.join(issues) or 'unspecified'}")
        elif verdict == "suspect":
            seg.flags.extend(f"audit suspect: {x}" for x in (issues or ["unspecified visual doubt"]))
        elif issues:
            seg.flags.extend(f"audit note: {x}" for x in issues)
    return segments, str(data.get("video_notes", "")).strip()
