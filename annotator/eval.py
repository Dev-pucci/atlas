"""Score pipeline output against the gold labels transcribed from the audit screenshots.

Offline mode (default): scores existing annotator_out/run.json files + lints the gold labels.
Live mode (--live): runs the full pipeline on each sample video first (costs API credits).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

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


def score_video(gold_file: Path, console: Console) -> dict | None:
    gold = json.loads(gold_file.read_text(encoding="utf-8"))
    folder = ATLAS_DIR / gold["video_folder"]
    run_file = folder / "annotator_out" / "run.json"
    if not run_file.exists():
        console.print(f"[yellow]{gold['video_folder']}: no run.json yet — run annotate first[/yellow]")
        return None
    run = json.loads(run_file.read_text(encoding="utf-8"))
    pred_by_time = {(round(s["start"], 1), round(s["end"], 1)): s for s in run["segments"]}

    rows, exact, f1s = [], 0, []
    for g in gold["segments"]:
        key = (round(_time_to_s(g["start"]), 1), round(_time_to_s(g["end"]), 1))
        pred = pred_by_time.get(key)
        if pred is None:
            rows.append((g["index"], "—", g["label"], "MISSING", 0.0))
            f1s.append(0.0)
            continue
        f1 = token_f1(pred["label"], g["label"])
        is_exact = _norm(pred["label"]) == _norm(g["label"])
        exact += is_exact
        f1s.append(f1)
        rows.append((g["index"], pred["label"], g["label"], "EXACT" if is_exact else f"F1={f1:.2f}", f1))

    table = Table(title=f"{gold['video_folder']} — {exact}/{len(gold['segments'])} exact, "
                        f"mean F1 {sum(f1s) / len(f1s):.3f}")
    table.add_column("#", justify="right", width=3)
    table.add_column("Predicted", overflow="fold")
    table.add_column("Gold", overflow="fold")
    table.add_column("Match", width=9)
    for idx, pred_l, gold_l, match, f1 in rows:
        style = "green" if match == "EXACT" else ("red" if f1 < 0.6 else "yellow")
        table.add_row(str(idx), pred_l, gold_l, f"[{style}]{match}[/{style}]")
    console.print(table)
    return {"video": gold["video_folder"], "exact": exact, "n": len(gold["segments"]),
            "mean_f1": sum(f1s) / len(f1s)}


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
                                      segments_image=None, segments_text=seg_lines)
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
