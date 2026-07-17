"""Parse segment timestamps from pasted text/CSV or from a platform screenshot."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .client import Router, image_part, text_part

TIME_RE = r"(\d+):(\d{2}(?:\.\d+)?)"
LINE_RE = re.compile(rf"{TIME_RE}\s*[-–—]\s*{TIME_RE}")


@dataclass
class Segment:
    index: int
    start: float
    end: float
    label: str = ""            # filled by the pipeline
    confidence: float = 0.0
    flags: list = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def time_str(self) -> str:
        return f"{_fmt(self.start)} - {_fmt(self.end)}"


def _fmt(t: float) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


def _to_seconds(minutes: str, seconds: str) -> float:
    return int(minutes) * 60 + float(seconds)


def parse_segments_text(text: str) -> list[Segment]:
    """Parse lines containing 'M:SS.s - M:SS.s' (extra text like indices is ignored)."""
    segments = []
    for line in text.splitlines():
        m = LINE_RE.search(line)
        if not m:
            continue
        start = _to_seconds(m.group(1), m.group(2))
        end = _to_seconds(m.group(3), m.group(4))
        segments.append(Segment(index=len(segments) + 1, start=start, end=end))
    if not segments:
        raise SystemExit(
            "No segments found. Expected lines like '0:00.0 - 0:07.0' (one per segment)."
        )
    return segments


def parse_segments_file(path: str | Path) -> list[Segment]:
    return parse_segments_text(Path(path).read_text(encoding="utf-8"))


def parse_segments_image(path: str | Path, router: Router) -> list[Segment]:
    """Read segment timestamps from a screenshot of the annotation platform."""
    img = Path(path).read_bytes()
    prompt = (
        "This is a screenshot of a video-annotation platform listing segments with timestamps.\n"
        "Extract EVERY segment's start and end time, in order. Ignore labels and statuses.\n"
        'Reply with JSON only: {"segments": [{"start": "0:00.0", "end": "0:07.0"}, ...]}'
    )
    data = router.chat_json("ocr", [{"role": "user", "content": [text_part(prompt), image_part(img)]}])
    segments = []
    for item in data.get("segments", []):
        sm = re.fullmatch(TIME_RE, str(item["start"]).strip())
        em = re.fullmatch(TIME_RE, str(item["end"]).strip())
        if not sm or not em:
            continue
        segments.append(
            Segment(
                index=len(segments) + 1,
                start=_to_seconds(sm.group(1), sm.group(2)),
                end=_to_seconds(em.group(1), em.group(2)),
            )
        )
    if not segments:
        raise SystemExit(f"Could not read segment timestamps from {path}")
    return segments


def validate_segments(segments: list[Segment], video_duration: float) -> list[str]:
    """Sanity warnings: overlaps, gaps, out-of-range times."""
    warnings = []
    for i, seg in enumerate(segments):
        if seg.end <= seg.start:
            warnings.append(f"Segment {seg.index}: end <= start ({seg.time_str()})")
        if seg.end > video_duration + 1.0:
            warnings.append(f"Segment {seg.index}: ends past video duration ({seg.time_str()})")
        if i > 0:
            prev = segments[i - 1]
            if seg.start < prev.end - 0.05:
                warnings.append(f"Segments {prev.index}/{seg.index} overlap")
            elif seg.start > prev.end + 0.5:
                warnings.append(f"Gap of {seg.start - prev.end:.1f}s between segments {prev.index} and {seg.index}")
    return warnings
