"""Shared pipeline orchestration: Pass 0 (context) through Pass 4 (escalate) + write_report.

Used by both the CLI's `annotate` command (which resolves segments from one of several
sources first) and the local watcher's `watch` command (which always auto-segments, since
a web upload has no way to supply a platform segments screenshot/text file).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import ingest as ingest_mod
from .audit import audit_segments
from .boundaries import propose_segments
from .frames import video_info
from .label import build_context, escalate_segments, label_segments
from .lint import lint_label, repair_label
from .report import write_report
from .segments import Segment, validate_segments


def run_pipeline(
    video_path: Path,
    out_dir: Path,
    router,
    cfg: dict,
    rulebook: str,
    fewshot: str,
    segments: list[Segment] | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> tuple[list[Segment], dict, str]:
    """segments=None triggers auto-segmentation (Pass 0.5). Runs build_context through
    write_report; on_progress (if given) is called with a short stage label at each pass
    boundary. Returns (segments, context, video_notes) — write_report has already run as
    a side effect."""

    def note(msg: str) -> None:
        print(msg)
        if on_progress:
            on_progress(msg)

    info = video_info(video_path)
    note(f"Video: {video_path.name} — {info.duration:.1f}s @ {info.fps:.1f}fps {info.width}x{info.height}")

    note("Building context")
    context = build_context(video_path, router, cfg, out_dir, ingest_mod.load_vocabulary())
    print(f"Task: {context.get('task_summary', '')[:120]}...")

    segmentation_mode = "given"
    if segments is None:
        note("Proposing segments (auto-segment)")
        segments = propose_segments(video_path, context, rulebook, router, cfg)
        segmentation_mode = "auto"
    print(f"Segments: {len(segments)} ({segments[0].time_str()} ... {segments[-1].time_str()})")
    for w in validate_segments(segments, info.duration):
        print(f"  ! {w}")

    note("Labeling segments (Pass 1)")
    segments = label_segments(video_path, segments, context, rulebook, fewshot, router, cfg)

    note("Lint + repair (Pass 2)")
    for seg in segments:
        violations = lint_label(seg.label, seg.duration)
        errors = [v for v in violations if v.severity == "error"]
        if errors:
            fixed, remaining = repair_label(seg.label, violations, router, seg.duration)
            if fixed != seg.label:
                print(f"  seg {seg.index}: repaired -> {fixed}")
                seg.label = fixed
            for v in remaining:
                if v.severity == "error":
                    seg.flags.append(f"lint: {v}")
        for v in violations:
            if v.severity == "warn":
                seg.flags.append(f"lint: {v}")

    note("Auditing (Pass 3)")
    segments, video_notes = audit_segments(video_path, segments, context, router, cfg, rulebook)

    note("Escalating flagged segments (Pass 4)")
    segments = escalate_segments(video_path, segments, context, rulebook, fewshot, router, cfg)

    for seg in segments:
        for v in lint_label(seg.label, seg.duration):
            if v.severity == "error":
                fixed, _ = repair_label(seg.label, [v], router, seg.duration)
                seg.label = fixed

    note("Writing report")
    write_report(out_dir, video_path.name, segments, context, video_notes, router.cost,
                 video_path=video_path, cfg=cfg, segmentation_mode=segmentation_mode)

    return segments, context, video_notes
