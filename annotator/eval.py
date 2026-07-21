"""Score pipeline output against the gold labels transcribed from the audit screenshots.

Offline mode (default): scores existing annotator_out/run.json files + lints the gold labels.
Live mode (--live): runs the full pipeline on each sample video first (costs API credits).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import mean, median

from rich.console import Console
from rich.table import Table

GOLD_DIR = Path(__file__).resolve().parent / "gold"
ATLAS_DIR = Path(__file__).resolve().parent.parent


def _norm(label: str) -> str:
    return re.sub(r"\s+", " ", label.lower().strip().rstrip("."))


def _tokens(label: str) -> set[str]:
    return set(re.findall(r"[a-z]+", label.lower()))


def token_f1(pred: str, gold: str) -> float:
    p, g = _tokens(pred), _tokens(gold)
    if not p or not g:
        return 0.0
    tp = len(p & g)
    if tp == 0:
        return 0.0
    prec, rec = tp / len(p), tp / len(g)
    return 2 * prec * rec / (prec + rec)


def _time_to_s(t: str) -> float:
    m, s = t.split(":")
    return int(m) * 60 + float(s)


def score_labels_against_gold(run_segments: list[dict], gold_segments: list[dict]) -> dict:
    """Pure comparison math, no API calls / file I/O — works directly on run.json's
    segments list (or any equivalently-shaped dict list) so push.py can score an
    already-completed run with zero extra cost."""
    pred_by_time = {(round(s["start"], 1), round(s["end"], 1)): s for s in run_segments}
    exact, f1s = 0, []
    for g in gold_segments:
        key = (round(_time_to_s(g["start"]), 1), round(_time_to_s(g["end"]), 1))
        pred = pred_by_time.get(key)
        if pred is None:
            f1s.append(0.0)
            continue
        f1 = token_f1(pred["label"], g["label"])
        exact += _norm(pred["label"]) == _norm(g["label"])
        f1s.append(f1)
    return {"exact": exact, "n": len(gold_segments), "mean_f1": sum(f1s) / len(f1s) if f1s else 0.0}


def score_video(gold_file: Path, console: Console) -> dict | None:
    gold = json.loads(gold_file.read_text(encoding="utf-8"))
    folder = ATLAS_DIR / gold["video_folder"]
    run_file = folder / "annotator_out" / "run.json"
    if not run_file.exists():
        console.print(f"[yellow]{gold['video_folder']}: no run.json yet — run annotate first[/yellow]")
        return None
    run = json.loads(run_file.read_text(encoding="utf-8"))
    pred_by_time = {(round(s["start"], 1), round(s["end"], 1)): s for s in run["segments"]}

    rows = []
    for g in gold["segments"]:
        key = (round(_time_to_s(g["start"]), 1), round(_time_to_s(g["end"]), 1))
        pred = pred_by_time.get(key)
        if pred is None:
            rows.append((g["index"], "—", g["label"], "MISSING", 0.0))
            continue
        f1 = token_f1(pred["label"], g["label"])
        is_exact = _norm(pred["label"]) == _norm(g["label"])
        rows.append((g["index"], pred["label"], g["label"], "EXACT" if is_exact else f"F1={f1:.2f}", f1))

    result = score_labels_against_gold(run["segments"], gold["segments"])
    table = Table(title=f"{gold['video_folder']} — {result['exact']}/{result['n']} exact, "
                        f"mean F1 {result['mean_f1']:.3f}")
    table.add_column("#", justify="right", width=3)
    table.add_column("Predicted", overflow="fold")
    table.add_column("Gold", overflow="fold")
    table.add_column("Match", width=9)
    for idx, pred_l, gold_l, match, f1 in rows:
        style = "green" if match == "EXACT" else ("red" if f1 < 0.6 else "yellow")
        table.add_row(str(idx), pred_l, gold_l, f"[{style}]{match}[/{style}]")
    console.print(table)
    return {"video": gold["video_folder"], **result}


def lint_gold(console: Console) -> None:
    """Sanity check: the gold labels themselves should produce zero lint errors."""
    from .pipeline.lint import lint_errors

    problems = 0
    for gf in sorted(GOLD_DIR.glob("video_*.json")):
        gold = json.loads(gf.read_text(encoding="utf-8"))
        for g in gold["segments"]:
            dur = _time_to_s(g["end"]) - _time_to_s(g["start"])
            for v in lint_errors(g["label"], dur):
                console.print(f"[red]gold lint[/red] {gf.stem} #{g['index']}: {v} — '{g['label']}'")
                problems += 1
    if problems == 0:
        console.print("[green]Gold labels pass the linter (0 errors) — linter is calibrated.[/green]")
    else:
        console.print(f"[yellow]{problems} lint errors on gold labels — review linter rules for false positives.[/yellow]")


def score_boundaries_against_gold(prop_points: list[float], gold_points: list[float],
                                   n_proposed: int, n_gold: int, duration: float,
                                   tolerance: float = 2.0) -> dict:
    """Pure nearest-point boundary comparison, no API calls / file I/O. prop_points/
    gold_points are each the sorted set of segment start+end times; n_proposed/n_gold are
    SEGMENT counts (not point counts), used only for count_delta. duration is the fallback
    error distance when one side has no points at all."""
    gold_err = [min(abs(t - p) for p in prop_points) for t in gold_points] if prop_points else [duration] * len(gold_points)
    prop_err = [min(abs(t - g) for g in gold_points) for t in prop_points] if gold_points else [duration] * len(prop_points)
    recall = sum(e <= tolerance for e in gold_err) / len(gold_err) if gold_err else 0.0
    precision = sum(e <= tolerance for e in prop_err) / len(prop_err) if prop_err else 0.0
    return {
        "n_gold": n_gold,
        "n_proposed": n_proposed,
        "count_delta": n_proposed - n_gold,
        "mean_err": mean(gold_err) if gold_err else 0.0,
        "median_err": median(gold_err) if gold_err else 0.0,
        "max_err": max(gold_err) if gold_err else 0.0,
        "recall_at_tol": recall,
        "precision_at_tol": precision,
    }


def score_segmentation_video(gold_file: Path, router, cfg: dict, rulebook: str, tolerance: float) -> dict | None:
    """Runs ONLY build_context (cached) + propose_segments — no labeling/lint/audit/escalate —
    so this is cheap to re-run repeatedly while tuning config.yaml's sampling.segment_* values."""
    from .pipeline.boundaries import propose_segments
    from .pipeline.frames import find_video, video_info
    from .pipeline.ingest import load_vocabulary
    from .pipeline.label import build_context
    from .pipeline.segments import validate_segments

    gold = json.loads(gold_file.read_text(encoding="utf-8"))
    folder = ATLAS_DIR / gold["video_folder"]
    video_path = find_video(folder)
    out_dir = folder / "annotator_out"
    out_dir.mkdir(exist_ok=True)

    context = build_context(video_path, router, cfg, out_dir, load_vocabulary())
    proposed = propose_segments(video_path, context, rulebook, router, cfg)
    duration = video_info(video_path).duration
    warnings = validate_segments(proposed, duration)

    gold_points = sorted({round(_time_to_s(g["start"]), 2) for g in gold["segments"]}
                         | {round(_time_to_s(g["end"]), 2) for g in gold["segments"]})
    prop_points = sorted({round(s.start, 2) for s in proposed} | {round(s.end, 2) for s in proposed})

    result = score_boundaries_against_gold(prop_points, gold_points, len(proposed),
                                            len(gold["segments"]), duration, tolerance)

    return {
        "video": gold["video_folder"],
        **result,
        "invariant_warnings": len(warnings),
        "proposed_segments": proposed,
        "gold_segments": gold["segments"],
    }


def score_segmentation(tolerance: float = 2.0) -> None:
    """Validate the auto-segmenter (Pass 0.5) against videos 1-5's REAL platform-approved
    boundaries before it's ever trusted blind on a video with no given segments."""
    from .pipeline.client import Router, load_config

    console = Console()
    cfg = load_config()
    router = Router(cfg)
    from .pipeline import ingest as ingest_mod
    rulebook = ingest_mod.load_rulebook()

    results = []
    for gf in sorted(GOLD_DIR.glob("video_*.json")):
        console.print(f"\n[bold]{gf.stem}[/bold]")
        r = score_segmentation_video(gf, router, cfg, rulebook, tolerance)
        if r is None:
            continue
        results.append(r)

        table = Table(title=f"{r['video']} — {r['n_gold']} gold / {r['n_proposed']} proposed "
                            f"(delta {r['count_delta']:+d}) — recall {r['recall_at_tol']:.0%} / "
                            f"precision {r['precision_at_tol']:.0%} @ {tolerance}s — "
                            f"mean err {r['mean_err']:.2f}s — "
                            f"{'[red]' if r['invariant_warnings'] else '[green]'}"
                            f"{r['invariant_warnings']} invariant warning(s)"
                            f"{'[/red]' if r['invariant_warnings'] else '[/green]'}")
        table.add_column("Proposed", width=17)
        table.add_column("Activity", overflow="fold")
        table.add_column("Nearest gold label", overflow="fold")
        gold_segs = r["gold_segments"]
        for seg in r["proposed_segments"]:
            nearest = min(gold_segs, key=lambda g: abs(_time_to_s(g["start"]) - seg.start)) if gold_segs else None
            time_col = seg.time_str() + (f" [yellow]({seg.flags[0]})[/yellow]" if seg.flags else "")
            table.add_row(time_col, seg.evidence.get("activity", ""), nearest["label"] if nearest else "")
        console.print(table)

    if results:
        console.print(
            f"\n[bold]Overall ({tolerance}s tolerance): mean recall "
            f"{mean(r['recall_at_tol'] for r in results):.0%}, mean precision "
            f"{mean(r['precision_at_tol'] for r in results):.0%}, mean boundary error "
            f"{mean(r['mean_err'] for r in results):.2f}s, "
            f"{sum(r['invariant_warnings'] for r in results)} total invariant warning(s)[/bold]"
        )


def run_eval(live: bool = False) -> None:
    console = Console()
    lint_gold(console)

    if live:
        from .pipeline.client import Router, load_config
        from .cli import cmd_annotate
        import argparse

        for gf in sorted(GOLD_DIR.glob("video_*.json")):
            gold = json.loads(gf.read_text(encoding="utf-8"))
            folder = ATLAS_DIR / gold["video_folder"]
            seg_lines = "\n".join(f'{g["start"]} - {g["end"]}' for g in gold["segments"])
            console.print(f"\n[bold]Running pipeline on {gold['video_folder']}[/bold]")
            args = argparse.Namespace(video=str(folder), segments=None,
                                      segments_image=None, segments_text=seg_lines, auto_segment=False)
            cmd_annotate(args)

    results = []
    for gf in sorted(GOLD_DIR.glob("video_*.json")):
        r = score_video(gf, console)
        if r:
            results.append(r)
    if results:
        total_exact = sum(r["exact"] for r in results)
        total_n = sum(r["n"] for r in results)
        mean_f1 = sum(r["mean_f1"] for r in results) / len(results)
        console.print(f"\n[bold]Overall: {total_exact}/{total_n} exact ({100 * total_exact / total_n:.0f}%), "
                      f"mean token-F1 {mean_f1:.3f}[/bold]")
