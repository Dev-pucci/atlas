"""Pass 0.5 (auto-segmentation): propose segment boundaries directly from raw video when the
platform hasn't given them, using the SAME rulebook boundary rules as everywhere else. Chunks
the video into overlapping windows (bounded frames/call, like label_segments' batching), asks a
cheap model per window, then deterministically merges + normalizes into a contiguous Segment list
that flows unchanged into label_segments()/lint/audit/escalate.

Never caches to disk (unlike build_context's glossary.json) — the validation harness in eval.py
needs to re-run this repeatedly while sampling.segment_* values in config.yaml get tuned, and a
cache would silently serve stale results.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .client import Router, image_part, text_part
from .frames import extract_frames, fmt_ts, sample_times, video_info
from .lint import MAX_SEGMENT_SECONDS
from .segments import Segment, validate_segments

WINDOW_PROMPT = """You are proposing SEGMENT BOUNDARIES for a slice of an egocentric hand-action video,
following these exact rules:

{boundary_rules}

## Video context
Task: {task_summary}
Environment: {environment}

## This window covers {w_start} to {w_end}
Frames below are sampled across this window (timestamps burned into each frame). This window OVERLAPS
neighboring windows by a few seconds on each side — propose every boundary you can identify inside this
window's frames, even ones close to the edges; a separate merge step resolves duplicates across windows,
so do not try to guess whether a boundary "belongs" to this window or a neighbor.

For each distinct goal-directed hand action (or idle stretch) you can identify, propose one segment:
- start/end per the boundary rules above (engage -> disengage/goal-change).
- "idle": true if hands touch nothing / irrelevant behavior for this segment, false otherwise.
- "uncertain_edge": true if you are not confident about the exact start or end time (e.g. the action
  starts before the window began, or continues past when the window ends).
- "activity": a short (5-10 word) plain description, NOT a formatted label — just enough for a human
  skimming to recognize what happens (e.g. "wipe rod with cloth", "idle, drinking water").

Reply with JSON only:
{{"segments": [
  {{"start": "0:03.5", "end": "0:09.0", "activity": "...", "idle": false, "uncertain_edge": false}}
]}}"""


@dataclass
class RawBoundary:
    start: float
    end: float
    activity: str
    idle: bool
    uncertain_edge: bool
    window_index: int


def _boundary_rules(rulebook: str) -> str:
    """Slice ONLY the boundaries + repeated-actions sections out of the real rulebook — no second
    hand-written copy of these rules. Falls back to the full rulebook if headings ever move."""
    start_marker = "## 4. Segment boundaries and timestamps"
    mid_marker = "## 5. Labeling standard"
    rpt_marker = "## 11. Repeated actions"
    end_marker = "## Auditor failure taxonomy"

    i1, i2 = rulebook.find(start_marker), rulebook.find(mid_marker)
    i3, i4 = rulebook.find(rpt_marker), rulebook.find(end_marker)
    if -1 in (i1, i2, i3, i4) or not (i1 < i2 <= i3 < i4):
        print("  ! boundary_rules: rulebook headings have moved, using the full rulebook instead")
        return rulebook

    return rulebook[i1:i2].strip() + "\n\n" + rulebook[i3:i4].strip()


def _windows(duration: float, window_seconds: float, overlap_seconds: float) -> list[tuple[float, float]]:
    """Overlapping window (start, end) pairs covering [0, duration]. Pure function, no API calls."""
    stride = window_seconds - overlap_seconds
    starts: list[float] = []
    t = 0.0
    while t < duration:
        starts.append(t)
        t += stride
    windows = [(s, min(s + window_seconds, duration)) for s in starts]
    # avoid a trailing sliver window shorter than the overlap — fold it into the previous window
    if len(windows) > 1 and windows[-1][1] - windows[-1][0] < overlap_seconds + 1.0:
        windows[-2] = (windows[-2][0], windows[-1][1])
        windows.pop()
    return windows


def _propose_window(
    video_path,
    w_start: float,
    w_end: float,
    window_index: int,
    context: dict,
    boundary_rules: str,
    router: Router,
    cfg: dict,
) -> list[RawBoundary]:
    s = cfg["sampling"]
    times = sample_times(w_start, w_end, s["segment_fps"], s["segment_max_frames"])
    frames = extract_frames(video_path, times, s["max_width"], s["jpeg_quality"])

    prompt = WINDOW_PROMPT.format(
        boundary_rules=boundary_rules,
        task_summary=context.get("task_summary", ""),
        environment=context.get("environment", ""),
        w_start=fmt_ts(w_start),
        w_end=fmt_ts(w_end),
    )
    content = [text_part(prompt)]
    for _, jpeg in frames:
        content.append(image_part(jpeg))

    data = router.chat_json("segment", [{"role": "user", "content": content}])
    out: list[RawBoundary] = []
    for item in data.get("segments", []):
        try:
            b_start = _parse_ts(str(item["start"])) + 0.0
            b_end = _parse_ts(str(item["end"]))
        except (KeyError, ValueError):
            continue
        if b_end <= b_start:
            continue
        out.append(
            RawBoundary(
                start=b_start,
                end=b_end,
                activity=str(item.get("activity", "")).strip(),
                idle=bool(item.get("idle", False)),
                uncertain_edge=bool(item.get("uncertain_edge", False)),
                window_index=window_index,
            )
        )
    return out


def _parse_ts(t: str) -> float:
    m, sec = t.strip().split(":")
    return int(m) * 60 + float(sec)


def merge_windows(
    windows: list[tuple[float, float]], per_window: list[list[RawBoundary]]
) -> list[RawBoundary]:
    """Deterministic ownership partition: a window's proposal is kept only if its MIDPOINT falls
    inside that window's own 'core' (the span up to the next window's start). This discards each
    window's redundant preview into a neighbor's overlap territory with zero threshold-tuning,
    while keeping the proposal's own overlap-informed start/end (not clamped to the core box)."""
    starts = [w[0] for w in windows]
    duration = windows[-1][1]
    kept: list[RawBoundary] = []
    for i, proposals in enumerate(per_window):
        core_lo = starts[i]
        core_hi = starts[i + 1] if i + 1 < len(starts) else duration
        for p in proposals:
            mid = (p.start + p.end) / 2
            if core_lo <= mid < core_hi:
                kept.append(p)
    return sorted(kept, key=lambda p: p.start)


def normalize_segments(raw: list[RawBoundary], duration: float) -> list[Segment]:
    """Deterministic post-processing mirroring lint.py::normalize_label — model output goes in,
    code guarantees the invariants, not another API call."""
    if not raw:
        raw = [RawBoundary(0.0, duration, "(no proposals — treat as one segment)", True, True, -1)]

    items = [dict(start=b.start, end=b.end, activity=b.activity, idle=b.idle, flag=None) for b in raw]

    # 1. stitch to contiguity; fill true gaps with synthetic idle placeholders
    stitched: list[dict] = []
    cursor = 0.0
    for it in items:
        if it["start"] > cursor + 0.05:
            stitched.append(dict(start=cursor, end=it["start"], activity="(gap — no proposal covered this span)",
                                 idle=True, flag="auto-segment: gap-filled"))
        elif it["start"] < cursor:
            gap = cursor - it["start"]
            flag = "auto-segment: large overlap snapped" if gap > 3.0 else None
            it = dict(it, start=cursor, flag=flag)
        stitched.append(it)
        cursor = max(cursor, it["end"])
    if cursor < duration - 0.05:
        stitched.append(dict(start=cursor, end=duration, activity="(gap — no proposal covered this span)",
                             idle=True, flag="auto-segment: gap-filled"))

    # snap adjacent boundaries together (closes small gaps/overlaps left after the pass above)
    for i in range(len(stitched) - 1):
        a, b = stitched[i], stitched[i + 1]
        if abs(a["end"] - b["start"]) > 0.01:
            mid = (a["end"] + b["start"]) / 2
            a["end"] = mid
            b["start"] = mid
    stitched[0]["start"] = 0.0
    stitched[-1]["end"] = duration

    # 2. collapse idle <= 5s into a neighbor
    collapsed: list[dict] = []
    for it in stitched:
        if it["idle"] and (it["end"] - it["start"]) <= 5.0 and collapsed:
            collapsed[-1]["end"] = it["end"]
        elif it["idle"] and (it["end"] - it["start"]) <= 5.0:
            collapsed.append(it)  # first item, nothing to merge backward into yet
        else:
            collapsed.append(it)
    # second pass: any still-short leading idle merges forward into the next real item
    if len(collapsed) > 1 and collapsed[0]["idle"] and (collapsed[0]["end"] - collapsed[0]["start"]) <= 5.0:
        collapsed[1]["start"] = collapsed[0]["start"]
        collapsed.pop(0)

    # 3. split anything > MAX_SEGMENT_SECONDS into equal parts
    split: list[dict] = []
    for it in collapsed:
        dur = it["end"] - it["start"]
        if dur <= MAX_SEGMENT_SECONDS + 0.05:
            split.append(it)
            continue
        n = math.ceil(dur / MAX_SEGMENT_SECONDS)
        part = dur / n
        for k in range(n):
            split.append(
                dict(
                    start=it["start"] + k * part,
                    end=it["start"] + (k + 1) * part,
                    activity=it["activity"],
                    idle=it["idle"],
                    flag="auto-segment: split (was over 10s)",
                )
            )

    # 4. round <1s segments up by stealing from a neighbor, or merge if neither can afford it
    for _ in range(3):
        changed = False
        i = 0
        while i < len(split):
            dur = split[i]["end"] - split[i]["start"]
            if dur >= 1.0 - 1e-6:
                i += 1
                continue
            deficit = 1.0 - dur
            nxt = split[i + 1] if i + 1 < len(split) else None
            prv = split[i - 1] if i > 0 else None
            if nxt is not None and (nxt["end"] - nxt["start"]) - deficit >= 1.0:
                split[i]["end"] += deficit
                nxt["start"] = split[i]["end"]
                split[i]["flag"] = split[i]["flag"] or "auto-segment: rounded up to 1s"
                changed = True
            elif prv is not None and (prv["end"] - prv["start"]) - deficit >= 1.0:
                split[i]["start"] -= deficit
                prv["end"] = split[i]["start"]
                split[i]["flag"] = split[i]["flag"] or "auto-segment: rounded up to 1s"
                changed = True
            elif nxt is not None:
                nxt["start"] = split[i]["start"]
                nxt["flag"] = "auto-segment: micro-segment merged into neighbor"
                split.pop(i)
                changed = True
                continue
            elif prv is not None:
                prv["end"] = split[i]["end"]
                prv["flag"] = "auto-segment: micro-segment merged into neighbor"
                split.pop(i)
                changed = True
                continue
            i += 1
        if not changed:
            break

    # 5. build real Segment objects. Activity text goes in .evidence (transient — label.py::_apply
    # fully overwrites .evidence once Pass 1 labels the segment, which is fine: by then the real
    # per-hand evidence is what matters). Only NOTEWORTHY corrections go in .flags (append-only,
    # survives end-to-end) — a clean proposal must NOT get a flag, since report.py/review.html
    # treat bool(seg.flags) as "needs review."
    segments: list[Segment] = []
    for idx, it in enumerate(split, start=1):
        seg = Segment(index=idx, start=it["start"], end=it["end"])
        seg.evidence = {"activity": it["activity"]}
        if it["flag"]:
            seg.flags.append(it["flag"])
        segments.append(seg)
    return segments


def propose_segments(video_path, context: dict, rulebook: str, router: Router, cfg: dict) -> list[Segment]:
    s = cfg["sampling"]
    info = video_info(video_path)
    boundary_rules = _boundary_rules(rulebook)
    windows = _windows(info.duration, s["segment_window_seconds"], s["segment_overlap_seconds"])
    print(f"Pass 0.5: auto-segment — {len(windows)} window(s) @ {s['segment_fps']} fps")

    per_window: list[list[RawBoundary]] = []
    for i, (w_start, w_end) in enumerate(windows):
        proposals = _propose_window(video_path, w_start, w_end, i, context, boundary_rules, router, cfg)
        per_window.append(proposals)
        print(f"  window {i} [{fmt_ts(w_start)}-{fmt_ts(w_end)}]: {len(proposals)} proposal(s)")

    merged = merge_windows(windows, per_window)
    segments = normalize_segments(merged, info.duration)

    for w in validate_segments(segments, info.duration):
        print(f"  ! {w}")
    return segments
