"""Push a completed local run to Supabase, for review on the hosted Vercel frontend.

This is a separate, explicit step from `annotate` — the local CLI + review.html workflow
remains fully standalone with no new dependency or network call. `supabase-py` is imported
lazily inside this module only (same pattern as lint.py's local import in repair_label).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ..eval import GOLD_DIR, _time_to_s, score_boundaries_against_gold, score_labels_against_gold
from .frames import find_video, video_info
from .ingest import KNOWLEDGE_DIR
from .report import extract_review_frames
from .segments import Segment

BUCKET = "frames"


def _score_against_gold(video_folder_name: str, run: dict, duration: float) -> tuple[dict | None, dict | None]:
    """label_accuracy is scored whenever gold data exists for this video; segmentation_accuracy
    only when the run used --auto-segment (given segments trivially match gold boundaries, so
    recall/precision there would be a meaningless ~100%). No API calls — run.json already has
    everything needed."""
    gold_path = GOLD_DIR / f"{video_folder_name}.json"
    if not gold_path.exists():
        return None, None
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    label_accuracy = score_labels_against_gold(run["segments"], gold["segments"])

    segmentation_accuracy = None
    if run.get("segmentation_mode") == "auto":
        gold_points = sorted({round(_time_to_s(g["start"]), 2) for g in gold["segments"]}
                             | {round(_time_to_s(g["end"]), 2) for g in gold["segments"]})
        prop_points = sorted({round(s["start"], 2) for s in run["segments"]}
                             | {round(s["end"], 2) for s in run["segments"]})
        segmentation_accuracy = score_boundaries_against_gold(
            prop_points, gold_points, len(run["segments"]), len(gold["segments"]), duration
        )
    return label_accuracy, segmentation_accuracy


def _client():
    try:
        from supabase import create_client
    except ImportError as e:
        raise SystemExit(
            "The 'supabase' package is required for push. Install it with:\n"
            "  pip install supabase"
        ) from e

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit(
            "Missing SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY environment variables.\n"
            "  PowerShell: $env:SUPABASE_URL = \"https://xxxx.supabase.co\"\n"
            '             $env:SUPABASE_SERVICE_ROLE_KEY = "..."\n'
            "  (Project Settings -> API in the Supabase dashboard)"
        )
    return create_client(url, key)


def _cloud_id_path(out_dir: Path) -> Path:
    return out_dir / "cloud.json"


def _load_cloud_id(out_dir: Path) -> str | None:
    p = _cloud_id_path(out_dir)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8")).get("video_id")


def _save_cloud_id(out_dir: Path, video_id: str) -> None:
    from datetime import datetime

    _cloud_id_path(out_dir).write_text(
        json.dumps({"video_id": video_id, "pushed_at": datetime.now().isoformat(timespec="seconds")}, indent=2),
        encoding="utf-8",
    )


def _read_knowledge_file(name: str) -> str:
    p = KNOWLEDGE_DIR / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def push(video_folder: str | Path, app_url: str | None = None) -> str:
    """Push one video's run.json + review frames to Supabase. Returns the hosted review URL."""
    video_folder = Path(video_folder)
    video_path = find_video(video_folder)
    out_dir = video_folder / "annotator_out"
    run_path = out_dir / "run.json"
    if not run_path.exists():
        raise SystemExit(f"No {run_path} — run `annotate` on this video first.")

    run = json.loads(run_path.read_text(encoding="utf-8"))
    context = run.get("context", {})
    segments = [
        Segment(
            index=s["index"], start=s["start"], end=s["end"], label=s["label"],
            confidence=s.get("confidence", 0.0), flags=s.get("flags", []),
            evidence=s.get("evidence", {}),
        )
        for s in run["segments"]
    ]

    sb = _client()
    info = video_info(video_path)

    cost = run.get("cost", {})
    label_accuracy, segmentation_accuracy = _score_against_gold(video_folder.name, run, info.duration)

    video_row = {
        "name": run.get("video", video_path.name),
        "folder_name": video_folder.name,
        "duration_seconds": info.duration,
        "task_summary": context.get("task_summary", ""),
        "environment": context.get("environment", ""),
        "hands_overview": context.get("hands_overview", ""),
        "objects": context.get("objects", []),
        "video_notes": run.get("video_notes", ""),
        "cost_summary": cost.get("summary_text", ""),
        "cost_usd": cost.get("total_usd"),
        "cost_detail": cost,
        "label_accuracy": label_accuracy,
        "segmentation_accuracy": segmentation_accuracy,
    }

    existing_id = _load_cloud_id(out_dir)
    if existing_id:
        sb.table("videos").update(video_row).eq("id", existing_id).execute()
        video_id = existing_id
        sb.table("segments").delete().eq("video_id", video_id).execute()
        print(f"Updating existing hosted video {video_id}")
    else:
        inserted = sb.table("videos").insert(video_row).execute()
        video_id = inserted.data[0]["id"]
        _save_cloud_id(out_dir, video_id)
        print(f"Created hosted video {video_id}")

    print("Extracting review frames...")
    frames_by_seg = extract_review_frames(video_path, segments)

    print(f"Uploading {sum(len(v) for v in frames_by_seg.values())} frames + {len(segments)} segments...")
    for seg in segments:
        frame_paths = []
        for n, jpeg in enumerate(frames_by_seg.get(seg.index, [])):
            path = f"{video_id}/{seg.index}_{n}.jpg"
            sb.storage.from_(BUCKET).upload(
                path, jpeg, {"content-type": "image/jpeg", "upsert": "true"}
            )
            frame_paths.append(path)

        sb.table("segments").insert(
            {
                "video_id": video_id,
                "seg_index": seg.index,
                "start_seconds": seg.start,
                "end_seconds": seg.end,
                "label": seg.label,
                "original_label": seg.label,
                "confidence": seg.confidence,
                "flags": seg.flags,
                "evidence": seg.evidence,
                "frame_paths": frame_paths,
            }
        ).execute()

    for key, filename in (("rulebook", "rulebook.md"), ("vocabulary", "vocabulary.md")):
        content = _read_knowledge_file(filename)
        if content:
            sb.table("knowledge").upsert({"key": key, "content": content}).execute()

    url = f"{app_url.rstrip('/')}/videos/{video_id}" if app_url else f"<your-app>.vercel.app/videos/{video_id}"
    print(f"\nPushed. Hosted review URL: {url}")
    return url
