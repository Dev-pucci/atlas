"""Final report: console table, annotations.txt, review.md, run.json, and review.html
(visual review page with embedded frames, live linting, and copy buttons)."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .client import CostTracker
from .frames import extract_frames, video_info
from .lint import ARTICLES, PRONOUNS, FORBIDDEN_WORDS, ING_VERBS, ING_NOUN_WHITELIST
from .segments import Segment


def write_report(
    out_dir: Path,
    video_name: str,
    segments: list[Segment],
    context: dict,
    video_notes: str,
    cost: CostTracker,
    video_path: Path | None = None,
    cfg: dict | None = None,
    segmentation_mode: str = "given",
) -> None:
    console = Console()
    out_dir.mkdir(parents=True, exist_ok=True)

    table = Table(title=f"Annotations — {video_name}", show_lines=False)
    table.add_column("#", justify="right", width=3)
    table.add_column("Time", width=17)
    table.add_column("Label", overflow="fold")
    table.add_column("Conf", justify="right", width=5)
    table.add_column("Review?", width=8)
    for seg in segments:
        needs_review = bool(seg.flags) or seg.confidence < 0.7
        table.add_row(
            str(seg.index),
            seg.time_str(),
            seg.label,
            f"{seg.confidence:.2f}",
            "[red]YES[/red]" if needs_review else "[green]ok[/green]",
        )
    console.print(table)
    if video_notes:
        console.print(f"[yellow]Auditor notes:[/yellow] {video_notes}")
    console.print(cost.summary())

    # copy-paste file: one label per line, in order
    ann = out_dir / "annotations.txt"
    ann.write_text(
        "\n".join(f"{seg.time_str()}\t{seg.label}" for seg in segments) + "\n",
        encoding="utf-8",
    )

    # review file: flagged/low-confidence segments with the model's evidence
    lines = [f"# Review — {video_name}", f"Generated {datetime.now():%Y-%m-%d %H:%M}", ""]
    flagged = [s for s in segments if s.flags or s.confidence < 0.7]
    if not flagged:
        lines.append("No segments flagged. Still skim the labels before submitting.")
    for seg in flagged:
        lines.append(f"## Segment {seg.index} [{seg.time_str()}] — confidence {seg.confidence:.2f}")
        lines.append(f"**Label:** {seg.label}")
        for fl in seg.flags:
            lines.append(f"- {fl}")
        if seg.evidence:
            lines.append(f"- left hand: {seg.evidence.get('left_hand', '')}")
            lines.append(f"- right hand: {seg.evidence.get('right_hand', '')}")
        lines.append("")
    if video_notes:
        lines.append(f"## Video-level auditor notes\n{video_notes}")
    (out_dir / "review.md").write_text("\n".join(lines), encoding="utf-8")

    # machine-readable full run
    run = {
        "video": video_name,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "context": context,
        "video_notes": video_notes,
        "segmentation_mode": segmentation_mode,
        "cost": {
            "total_usd": cost.cost_usd,
            "calls": cost.calls,
            "input_tokens": cost.input_tokens,
            "output_tokens": cost.output_tokens,
            "by_pass": cost.by_pass,
            "summary_text": cost.summary(),
        },
        "segments": [
            {
                "index": s.index,
                "start": s.start,
                "end": s.end,
                "label": s.label,
                "confidence": s.confidence,
                "flags": s.flags,
                "evidence": s.evidence,
            }
            for s in segments
        ],
    }
    (out_dir / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")

    extras = ""
    if video_path is not None and cfg is not None:
        try:
            write_review_html(out_dir, video_name, video_path, segments)
            extras = ", review.html (visual review — open in browser)"
        except Exception as e:
            console.print(f"[yellow]review.html generation failed: {e}[/yellow]")

    console.print(
        f"\nFiles written to [bold]{out_dir}[/bold]: annotations.txt (copy-paste), review.md, run.json{extras}"
    )


def _lint_js_data() -> str:
    """Serialize the Python linter's word lists for the in-browser live linter."""
    return json.dumps(
        {
            "articles": sorted(ARTICLES),
            "pronouns": sorted(PRONOUNS),
            "forbidden": sorted(FORBIDDEN_WORDS),
            "ingVerbs": sorted(ING_VERBS),
            "ingOk": sorted(ING_NOUN_WHITELIST),
            "exchangeOk": ["hand over", "pass", "put", "switch", "set"],
        }
    )


def extract_review_frames(
    video_path: Path, segments: list[Segment], max_width: int = 420, jpeg_quality: int = 70
) -> dict[int, list[bytes]]:
    """Sample 3 frames per segment (10%/50%/90% of its span) for review purposes.

    Single source of truth for both write_review_html (base64-embeds them) and
    push.py (uploads them to Supabase Storage) — so the two never drift apart.
    """
    info = video_info(video_path)
    times, owners = [], []
    for seg in segments:
        span = seg.end - seg.start
        for frac in (0.1, 0.5, 0.9):
            times.append(min(seg.start + span * frac, info.duration - 0.05))
            owners.append(seg.index)
    frames = extract_frames(video_path, times, max_width=max_width, jpeg_quality=jpeg_quality)
    frames_by_seg: dict[int, list[bytes]] = {}
    for (_, jpeg), owner in zip(frames, owners):
        frames_by_seg.setdefault(owner, []).append(jpeg)
    return frames_by_seg


def write_review_html(
    out_dir: Path, video_name: str, video_path: Path, segments: list[Segment]
) -> None:
    """Self-contained review page: frames + editable labels + live linting + copy buttons."""
    frames_by_seg_bytes = extract_review_frames(video_path, segments)
    frames_by_seg: dict[int, list[str]] = {
        idx: [base64.b64encode(jpeg).decode("ascii") for jpeg in jpegs]
        for idx, jpegs in frames_by_seg_bytes.items()
    }

    cards = []
    for seg in segments:
        imgs = "".join(
            f'<img src="data:image/jpeg;base64,{b64}" alt="frame">'
            for b64 in frames_by_seg.get(seg.index, [])
        )
        flags = "".join(f"<li>{_esc(f)}</li>" for f in seg.flags)
        flags_html = f'<ul class="flags">{flags}</ul>' if flags else ""
        review_cls = "warn" if (seg.flags or seg.confidence < 0.7) else "ok"
        ev = seg.evidence or {}
        cards.append(f"""
<div class="card {review_cls}">
  <div class="head"><b>#{seg.index}</b> <span class="time">{seg.time_str()}</span>
    <span class="conf">conf {seg.confidence:.2f}</span></div>
  <div class="frames">{imgs}</div>
  <div class="ev">L: {_esc(str(ev.get('left_hand', '')))}<br>R: {_esc(str(ev.get('right_hand', '')))}</div>
  {flags_html}
  <textarea class="label" data-idx="{seg.index}" rows="2">{_esc(seg.label)}</textarea>
  <div class="viol" id="viol-{seg.index}"></div>
</div>""")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Review — {_esc(video_name)}</title>
<style>
 body{{font-family:Segoe UI,Arial,sans-serif;margin:16px;background:#f4f5f7;color:#1a1a2e}}
 .toolbar{{position:sticky;top:0;background:#fff;padding:10px;border-radius:8px;
   box-shadow:0 1px 4px rgba(0,0,0,.15);margin-bottom:14px;z-index:5}}
 button{{padding:8px 14px;margin-right:8px;border:0;border-radius:6px;background:#2b5fd9;
   color:#fff;font-size:14px;cursor:pointer}}
 button:hover{{background:#1e4bb8}}
 .card{{background:#fff;border-radius:8px;padding:12px;margin-bottom:12px;
   box-shadow:0 1px 3px rgba(0,0,0,.1);border-left:5px solid #3cb371}}
 .card.warn{{border-left-color:#e0a800}}
 .head{{margin-bottom:6px}} .time{{color:#555;font-family:monospace;margin:0 8px}}
 .conf{{color:#777;font-size:13px}}
 .frames img{{width:220px;margin:2px;border-radius:4px}}
 .ev{{font-size:12.5px;color:#666;margin:6px 0}}
 .flags{{font-size:12.5px;color:#b26b00;margin:4px 0 6px 18px;padding:0}}
 textarea.label{{width:100%;font-size:15px;font-family:Consolas,monospace;padding:8px;
   border:1px solid #ccc;border-radius:6px;box-sizing:border-box}}
 .viol{{font-size:13px;color:#c0392b;min-height:16px;margin-top:4px}}
 .viol.clean{{color:#2e8b57}}
</style></head><body>
<div class="toolbar">
  <b>{_esc(video_name)}</b> — edit labels below (rules are checked as you type), then:
  <button onclick="copyLabels(false)">Copy labels</button>
  <button onclick="copyLabels(true)">Copy time + labels</button>
  <span id="copymsg"></span>
</div>
{''.join(cards)}
<script>
const L = {_lint_js_data()};
const TIMES = {json.dumps({seg.index: seg.time_str() for seg in segments})};
function lint(t) {{
  const v = [], low = t.toLowerCase().trim();
  const words = low.match(/[a-z]+/g) || [];
  for (const w of words) {{
    if (L.articles.includes(w)) v.push(`forbidden article "${{w}}"`);
    if (L.pronouns.includes(w)) v.push(`pronoun "${{w}}" — repeat the object name`);
    if (L.forbidden.includes(w)) v.push(`forbidden word "${{w}}"`);
  }}
  for (const clause of low.split(/,|\\band\\b/)) {{
    const cw = clause.match(/[a-z]+/g) || [];
    if (cw.length && cw[0].endsWith("ing") && !L.ingOk.includes(cw[0])) v.push(`-ing verb "${{cw[0]}}" — use imperative`);
  }}
  if (/\\d/.test(low)) v.push("digits are forbidden — spell numbers out");
  if (low && !words.includes("hand") && !words.includes("hands")) v.push("no hand specified");
  if (/to (left|right) hand/.test(low) && !L.exchangeOk.some(x => low.includes(x)))
    v.push("hand exchange must use: hand over / pass / put / switch / set");
  if (/;/.test(t)) v.push("use commas, not semicolons");
  if (/^[A-Z]/.test(t.trim())) v.push("start lowercase");
  return v;
}}
function check(el) {{
  const out = document.getElementById("viol-" + el.dataset.idx);
  const v = lint(el.value);
  out.textContent = v.length ? "✗ " + v.join("  |  ") : "✓ passes all rules";
  out.className = v.length ? "viol" : "viol clean";
}}
document.querySelectorAll("textarea.label").forEach(el => {{
  el.addEventListener("input", () => check(el)); check(el);
}});
function copyLabels(withTime) {{
  const lines = [...document.querySelectorAll("textarea.label")].map(el =>
    (withTime ? TIMES[el.dataset.idx] + "\\t" : "") + el.value.trim());
  navigator.clipboard.writeText(lines.join("\\n")).then(() => {{
    document.getElementById("copymsg").textContent = "copied " + lines.length + " labels";
    setTimeout(() => document.getElementById("copymsg").textContent = "", 2500);
  }});
}}
</script></body></html>"""
    (out_dir / "review.html").write_text(html, encoding="utf-8")


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))
