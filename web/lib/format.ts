/** Matches annotator/pipeline/frames.py::fmt_ts / segments.py::Segment.time_str exactly. */
export function fmtTs(seconds: number): string {
  const t = Math.max(seconds, 0);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}

export function timeStr(start: number, end: number): string {
  return `${fmtTs(start)} - ${fmtTs(end)}`;
}
