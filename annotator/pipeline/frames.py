"""Frame extraction with OpenCV: single sequential pass, timestamp overlay, JPEG encoding."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2


@dataclass
class VideoInfo:
    path: Path
    fps: float
    frame_count: int
    duration: float
    width: int
    height: int


def video_info(path: str | Path) -> VideoInfo:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    info = VideoInfo(
        path=Path(path),
        fps=fps,
        frame_count=count,
        duration=count / fps if fps else 0.0,
        width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )
    cap.release()
    return info


def sample_times(start: float, end: float, fps: float, max_frames: int) -> list[float]:
    """Evenly spaced timestamps in [start, end] at ~fps, capped at max_frames."""
    span = max(end - start, 0.01)
    n = min(max(int(span * fps) + 1, 2), max_frames)
    step = span / (n - 1) if n > 1 else span
    return [round(start + i * step, 3) for i in range(n)]


def fmt_ts(t: float) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


def extract_frames(
    path: str | Path,
    times: list[float],
    max_width: int = 854,
    jpeg_quality: int = 80,
) -> list[tuple[float, bytes]]:
    """Grab the nearest frame for each requested timestamp in ONE sequential pass.

    Returns [(requested_time, jpeg_bytes)] in input order. Each frame gets a
    timestamp banner so the model can reference exact times.
    """
    if not times:
        return []
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    order = sorted(range(len(times)), key=lambda i: times[i])
    results: dict[int, bytes] = {}
    ti = 0  # index into sorted targets
    frame_idx = 0
    ok, frame = cap.read()
    while ok and ti < len(order):
        t = frame_idx / fps
        # serve every target that this frame has reached
        while ti < len(order) and t >= times[order[ti]] - (0.5 / fps):
            results[order[ti]] = _encode(frame, times[order[ti]], max_width, jpeg_quality)
            ti += 1
        frame_idx += 1
        ok, frame = cap.read()
    # targets past the last frame get the final frame
    if frame is not None:
        last = frame
    else:
        last = None
    while ti < len(order) and last is not None:
        results[order[ti]] = _encode(last, times[order[ti]], max_width, jpeg_quality)
        ti += 1
    cap.release()
    return [(times[i], results[i]) for i in range(len(times)) if i in results]


def _encode(frame, t: float, max_width: int, quality: int) -> bytes:
    h, w = frame.shape[:2]
    if w > max_width:
        scale = max_width / w
        frame = cv2.resize(frame, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
    frame = frame.copy()
    label = f"t={fmt_ts(t)}"
    cv2.rectangle(frame, (0, 0), (150, 32), (0, 0, 0), thickness=-1)
    cv2.putText(frame, label, (8, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


def find_video(folder: str | Path) -> Path:
    """Given a folder (or a file), return the video file to annotate."""
    p = Path(folder)
    if p.is_file():
        return p
    vids = sorted(p.glob("*.mp4")) + sorted(p.glob("*.mov")) + sorted(p.glob("*.avi")) + sorted(p.glob("*.mkv"))
    if not vids:
        raise SystemExit(f"No video file found in {p}")
    if len(vids) > 1:
        print(f"Multiple videos in {p}, using {vids[0].name}")
    return vids[0]
