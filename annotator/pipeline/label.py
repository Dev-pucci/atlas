"""Pass 0 (whole-video context + object glossary) and Pass 1 (batched per-segment labeling).

Cost design: segments are labeled in batches of `batch_size` per API call, so the rulebook,
few-shot examples and glossary are paid for once per batch instead of once per segment.
Low-confidence segments can be escalated afterwards (see escalate_segments) to a stronger
model with denser frames — accuracy where it matters, cheap everywhere else.
"""

from __future__ import annotations

import json
from pathlib import Path

from .client import Router, image_part, text_part
from .frames import extract_frames, fmt_ts, sample_times, video_info
from .lint import normalize_label
from .segments import Segment

CONTEXT_PROMPT = """You are analyzing an egocentric (first-person) video of a person performing a manual
task with their hands. The frames below are sampled across the WHOLE video; each frame shows its
timestamp.

Your job is to build the context that a labeling model will rely on. Study the frames and reply with
JSON only:

{
  "task_summary": "one paragraph: what task is the person performing, start to finish",
  "environment": "where this happens and what surfaces are involved (table, counter, tray...)",
  "objects": [
    {
      "name": "canonical short name to use in every label (e.g. 'fishing rod', 'measuring cylinder', 'dark bottle')",
      "descriptors": "distinguishing color/size/type ONLY if needed to disambiguate from a similar object",
      "role": "what it is used for in this task"
    }
  ],
  "hands_overview": "which hand tends to hold/stabilize vs. act, and any hand exchanges you noticed",
  "phases": [
    {"approx_time": "0:00-0:30", "activity": "short description"}
  ]
}

Rules for object names:
- lowercase, generic nouns, no articles; pick ONE canonical name per object.
- Use the SHORTEST natural name ("cloth", "fishing rod", "cup"). Add a descriptor (color/size) ONLY if
  two or more similar objects appear in the video and must be told apart.
- List EVERY distinct object or part the hands touch, including small parts and attachments
  (hooks, rings, caps, lids, buttons, handles) — missed small objects cause labeling failures later.
- Never invent objects you cannot clearly see being touched."""

LABEL_SYSTEM = """You are an expert egocentric-video annotator. You label hand-object interactions in
short video segments, strictly following the rulebook below. Accuracy over everything: never invent an
action or object you cannot verify in the frames, and never omit an action a hand is clearly performing.

IDENTIFYING LEFT vs RIGHT HAND (critical, most common audit failure):
This is FIRST-PERSON video. The wearer's LEFT hand enters the frame from the bottom-left and its thumb
points RIGHT when palm-down; the RIGHT hand enters from the bottom-right and its thumb points LEFT when
palm-down. Decide hand identity from the ARM/WRIST direction entering the frame — never from which side
of an object the hand happens to be on. A single hand cannot hold two separate objects AND perform a
different action with a third at the same time — if your description implies that, re-examine the frames.

{rulebook}

## Worked examples from past audits (wrong label -> audit reason -> corrected label)
{fewshot}"""

BATCH_PROMPT = """## Video context
Task: {task_summary}
Environment: {environment}
Object glossary (use EXACTLY these names): {glossary}
Hands overview: {hands_overview}

## Continuity
Final label of the segment just before this batch: {prev_label}
Segments in this batch are CONSECUTIVE: an object held at the end of one segment usually stays held at
the start of the next — label those holds (manipulatable objects only).

## Segments in this request
Below, each segment is introduced by a marker line "=== SEGMENT k: start - end ===" followed by its
frames (timestamps are printed on the frames). Frames belong to the marker above them.

## Protocol — apply to EACH segment independently, in order
1. Track the LEFT hand across the segment: what does it touch/hold/do?
2. Track the RIGHT hand the same way.
3. Decide the atomic action(s): only goal-directed hand-object interactions INSIDE that segment's time
   range. Include hold labels for manipulatable objects. Ignore walking, looking, idle.
4. Compose the label per the rulebook: imperative, comma-separated atomic actions, every action names
   its hand, glossary object names, no articles/-ing verbs/pronouns/digits/forbidden words.
5. Boundary check: is an action cut off at the segment edges, or does the next action bleed in?

Confidence rubric (be honest — segments below 0.7 are re-checked by a stronger model):
1.0 = every hand assignment and object is unambiguous in the frames; 0.8 = content certain but some
detail assumed; 0.6 or lower = hands occluded, a hand exchange happens, or the frames may have missed
an action. Never report 1.0 when any hand or object was inferred rather than seen.

Reply with JSON only — EXACTLY one entry per segment, in order:
{{
  "segments": [
    {{
      "index": <segment number from the marker>,
      "left_hand": "what the left hand does",
      "right_hand": "what the right hand does",
      "label": "final label",
      "boundary_note": "ok | describe cut-off or bleed",
      "confidence": 0.0-1.0,
      "uncertain_about": "anything you could not verify clearly, or empty string"
    }}
  ]
}}"""

SINGLE_PROMPT = """## Video context
Task: {task_summary}
Environment: {environment}
Object glossary (use EXACTLY these names): {glossary}
Hands overview: {hands_overview}

## Why this segment is being re-examined
{focus_hint}
Pay specific attention to this — it's the reason a stronger model was asked to look again.

## Previous segment's final label (for continuity — objects held usually stay held)
{prev_label}

## Current segment: {start} to {end} (duration {duration:.1f}s)
Draft label from a first pass (may contain errors): {draft}
Re-examine the frames independently. Correct the hands, verbs, or actions if the draft is wrong — but
KEEP the draft's object names and the glossary names unless an object is visually misidentified;
renaming objects breaks consistency with neighboring segments.

The frames below cover {pad_start} to {pad_end} — slightly wider than the segment so you can check the
boundaries. Frames whose timestamp is OUTSIDE [{start}, {end}] are context only: do NOT label actions
that happen entirely outside the segment.

## Protocol — follow in order
1. Track the LEFT hand across the segment. 2. Track the RIGHT hand.
3. Decide the atomic action(s) inside the segment (include holds of manipulatable objects).
4. Compose the label per the rulebook. 5. Check the boundaries.

Reply with JSON only:
{{
  "left_hand": "...", "right_hand": "...", "label": "...",
  "boundary_note": "ok | ...", "confidence": 0.0-1.0, "uncertain_about": ""
}}"""


def build_context(video_path: Path, router: Router, cfg: dict, out_dir: Path,
                  vocabulary: str = "") -> dict:
    """Pass 0: sample the whole video and build task summary + glossary. Cached per video."""
    cache = out_dir / "glossary.json"
    if cache.exists():
        print("Pass 0: using cached glossary")
        return json.loads(cache.read_text(encoding="utf-8"))

    info = video_info(video_path)
    s = cfg["sampling"]
    times = sample_times(0.0, max(info.duration - 0.2, 0.5), s["context_fps"], s["context_max_frames"])
    frames = extract_frames(video_path, times, s["max_width"], s["jpeg_quality"])
    print(f"Pass 0: context scan — {len(frames)} frames @ {s['context_fps']} fps")

    prompt = CONTEXT_PROMPT + (("\n\n" + vocabulary) if vocabulary else "")
    content = [text_part(prompt)]
    for _, jpeg in frames:
        content.append(image_part(jpeg))
    ctx = router.chat_json("context", [{"role": "user", "content": content}])
    cache.write_text(json.dumps(ctx, indent=2), encoding="utf-8")
    return ctx


def _glossary_line(context: dict) -> str:
    return "; ".join(
        f"{o.get('name')}" + (f" ({o.get('descriptors')})" if o.get("descriptors") else "")
        for o in context.get("objects", [])
    )


def _system(rulebook: str, fewshot: str) -> str:
    return LABEL_SYSTEM.format(rulebook=rulebook, fewshot=fewshot or "(none)")


def _apply(seg: Segment, data: dict) -> None:
    seg.label = normalize_label(str(data.get("label", "")))
    seg.confidence = float(data.get("confidence", 0.0) or 0.0)
    seg.evidence = {
        "left_hand": data.get("left_hand", ""),
        "right_hand": data.get("right_hand", ""),
        "boundary_note": data.get("boundary_note", ""),
        "uncertain_about": data.get("uncertain_about", ""),
    }
    note = str(data.get("boundary_note", "")).strip().lower()
    if note and note != "ok":
        seg.flags.append(f"boundary: {data['boundary_note']}")
    if data.get("uncertain_about"):
        seg.flags.append(f"uncertain: {data['uncertain_about']}")


def label_segments(
    video_path: Path,
    segments: list[Segment],
    context: dict,
    rulebook: str,
    fewshot: str,
    router: Router,
    cfg: dict,
) -> list[Segment]:
    """Pass 1: label segments in batches (one API call per `batch_size` segments)."""
    s = cfg["sampling"]
    info = video_info(video_path)
    system = _system(rulebook, fewshot)
    glossary = _glossary_line(context)
    batch_size = max(int(s.get("batch_size", 6)), 1)

    prev_label = "(this is the first segment of the video)"
    for i in range(0, len(segments), batch_size):
        chunk = segments[i : i + batch_size]

        # one sequential video scan per chunk: gather every segment's frame times
        per_seg_times = {
            seg.index: sample_times(
                seg.start, min(seg.end, info.duration - 0.05), s["label_fps"], s["label_max_frames"]
            )
            for seg in chunk
        }
        all_times = [t for times in per_seg_times.values() for t in times]
        frames = dict(extract_frames(video_path, all_times, s["max_width"], s["jpeg_quality"]))

        content = [
            text_part(
                BATCH_PROMPT.format(
                    task_summary=context.get("task_summary", ""),
                    environment=context.get("environment", ""),
                    glossary=glossary,
                    hands_overview=context.get("hands_overview", ""),
                    prev_label=prev_label,
                )
            )
        ]
        for seg in chunk:
            content.append(
                text_part(f"=== SEGMENT {seg.index}: {fmt_ts(seg.start)} - {fmt_ts(seg.end)} "
                          f"(duration {seg.duration:.1f}s) ===")
            )
            for t in per_seg_times[seg.index]:
                if t in frames:
                    content.append(image_part(frames[t]))

        data = router.chat_json(
            "label",
            [{"role": "system", "content": system}, {"role": "user", "content": content}],
        )
        replies = {int(item.get("index", -1)): item for item in data.get("segments", [])}
        for seg in chunk:
            item = replies.get(seg.index)
            if item is None:
                seg.flags.append("labeling: model returned no entry for this segment")
                seg.confidence = 0.0
                continue
            _apply(seg, item)
            print(f"  seg {seg.index:>2} [{seg.time_str()}] conf={seg.confidence:.2f}  {seg.label}")
        prev_label = chunk[-1].label or prev_label
    return segments


def label_one_segment(
    video_path: Path,
    seg: Segment,
    prev_label: str,
    context: dict,
    rulebook: str,
    fewshot: str,
    router: Router,
    cfg: dict,
    fps: float | None = None,
    max_frames: int | None = None,
    model: str | None = None,
    focus_hint: str = "",
) -> Segment:
    """Single-segment labeling with boundary padding — used for escalation."""
    s = cfg["sampling"]
    info = video_info(video_path)
    fps = fps or s["label_fps"]
    max_frames = max_frames or s["label_max_frames"]
    pad_start = max(seg.start - s["pad_seconds"], 0.0)
    pad_end = min(seg.end + s["pad_seconds"], info.duration - 0.05)
    times = sample_times(pad_start, pad_end, fps, max_frames)
    frames = extract_frames(video_path, times, s["max_width"], s["jpeg_quality"])

    prompt = SINGLE_PROMPT.format(
        task_summary=context.get("task_summary", ""),
        environment=context.get("environment", ""),
        glossary=_glossary_line(context),
        hands_overview=context.get("hands_overview", ""),
        focus_hint=focus_hint or "(no specific reason given — re-verify hands, objects, and actions generally)",
        prev_label=prev_label,
        draft=seg.label or "(none)",
        start=fmt_ts(seg.start),
        end=fmt_ts(seg.end),
        duration=seg.duration,
        pad_start=fmt_ts(pad_start),
        pad_end=fmt_ts(pad_end),
    )
    content = [text_part(prompt)]
    for _, jpeg in frames:
        content.append(image_part(jpeg))
    data = router.chat_json(
        "label",
        [{"role": "system", "content": _system(rulebook, fewshot)}, {"role": "user", "content": content}],
        model=model,
    )
    _apply(seg, data)
    return seg


def escalate_segments(
    video_path: Path,
    segments: list[Segment],
    context: dict,
    rulebook: str,
    fewshot: str,
    router: Router,
    cfg: dict,
) -> list[Segment]:
    """Re-label suspect segments with the stronger `escalate` model + denser frames.

    A segment is escalated when the audit raised a visual doubt ('audit suspect' flag) or its
    confidence fell below the threshold. Cheap-model self-confidence is unreliable on its own,
    so the audit flags are the primary trigger.
    """
    s = cfg["sampling"]
    model = (cfg["models"].get("escalate") or "").strip()
    threshold = float(s.get("escalate_confidence", 0.0) or 0.0)
    if not model:
        return segments

    def escalation_reason(seg: Segment) -> str:
        """Empty string means "don't escalate"; non-empty is also the focus_hint passed to the
        stronger model, so it knows exactly what to re-examine instead of blindly re-labeling."""
        suspect_reasons = [f.removeprefix("audit suspect: ") for f in seg.flags if f.startswith("audit suspect")]
        if suspect_reasons:
            return "The audit flagged this segment as visually suspect: " + " | ".join(suspect_reasons)
        if threshold > 0 and seg.confidence < threshold:
            return f"The first labeling pass reported low confidence ({seg.confidence:.2f}) on this segment."
        return ""

    targets = [(seg, escalation_reason(seg)) for seg in segments]
    targets = [(seg, reason) for seg, reason in targets if reason]
    if not targets:
        print("Escalation: no suspect or low-confidence segments — skipping")
        return segments
    print(f"Escalation: {len(targets)} segment(s) (audit-suspect or conf<{threshold}) -> {model}")
    for seg, reason in targets:
        idx = segments.index(seg)
        prev_label = segments[idx - 1].label if idx > 0 else "(this is the first segment of the video)"
        old = seg.label
        label_one_segment(
            video_path, seg, prev_label, context, rulebook, fewshot, router, cfg,
            fps=s.get("escalate_fps"), max_frames=s.get("escalate_max_frames"), model=model,
            focus_hint=reason,
        )
        seg.flags.append(f"escalated to {model}" + (f" (was: {old})" if old and old != seg.label else ""))
        print(f"  seg {seg.index:>2} re-labeled conf={seg.confidence:.2f}  {seg.label}")
    return segments
