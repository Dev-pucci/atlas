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

/** $0.0842 style — per-video costs are small enough that 2dp usually reads as $0.00. */
export function fmtUsd(amount: number): string {
  return `$${amount.toFixed(amount < 1 ? 4 : 2)}`;
}

/** 0.82 -> "82%" */
export function fmtPct(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}
