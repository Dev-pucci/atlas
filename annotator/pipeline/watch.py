"""Local watcher: polls Supabase for videos uploaded through the web UI and processes them
with the same pipeline as `annotate`, then pushes the result back.

This is what lets the hosted app be the only thing a user interacts with — video ingestion
still runs on this machine (Vercel can't run OpenCV or a multi-minute multi-call pipeline),
but the trigger is now a queue row instead of a typed CLI command.
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime
from pathlib import Path

from . import ingest as ingest_mod
from .client import Router, load_config
from .push import _client, push
from .run import run_pipeline

ATLAS_DIR = Path(__file__).resolve().parent.parent.parent
UPLOADS_DIR = ATLAS_DIR / "web_uploads"
UPLOADS_BUCKET = "uploads"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _process_job(job: dict, sb, router, cfg: dict, rulebook: str, fewshot: str) -> None:
    job_id = job["id"]
    print(f"\n=== Processing job {job_id}: {job['original_filename']} ===")
    sb.table("jobs").update({"status": "processing", "started_at": _now()}).eq("id", job_id).execute()

    try:
        folder = UPLOADS_DIR / job_id
        folder.mkdir(parents=True, exist_ok=True)
        # original_filename comes from an unauthenticated web request — .name strips any
        # directory components (relative "../../" traversal AND, on Windows, a full
        # absolute path, which `folder / "C:\\..."` would otherwise silently overwrite
        # instead of joining) so the download can only ever land inside `folder`.
        safe_filename = Path(job["original_filename"]).name or "upload.mp4"
        video_path = folder / safe_filename

        print("Downloading upload...")
        blob = sb.storage.from_(UPLOADS_BUCKET).download(job["storage_path"])
        video_path.write_bytes(blob)

        def on_progress(note: str) -> None:
            sb.table("jobs").update({"progress_note": note}).eq("id", job_id).execute()

        out_dir = folder / "annotator_out"
        out_dir.mkdir(exist_ok=True)
        run_pipeline(video_path, out_dir, router, cfg, rulebook, fewshot,
                     segments=None, on_progress=on_progress)

        on_progress("Pushing to Supabase")
        video_id, _url = push(folder)

        sb.table("jobs").update(
            {"status": "done", "video_id": video_id, "finished_at": _now(), "progress_note": None}
        ).eq("id", job_id).execute()

        sb.storage.from_(UPLOADS_BUCKET).remove([job["storage_path"]])
        print(f"Job {job_id} done -> video {video_id}")

    except Exception as e:
        print(f"Job {job_id} failed: {e}")
        traceback.print_exc()
        sb.table("jobs").update({"status": "error", "error_message": str(e)}).eq("id", job_id).execute()


def watch(poll_seconds: float = 10.0) -> None:
    cfg = load_config()
    router = Router(cfg)
    rulebook = ingest_mod.load_rulebook()
    fewshot = (ingest_mod.load_fewshot() + "\n\n" + ingest_mod.load_vocabulary()).strip()
    sb = _client()

    # A job stuck 'processing' means a previous watcher run died mid-job (crash, PC sleep,
    # Ctrl+C) — requeue it rather than leaving it stuck forever. Not resumable mid-pipeline,
    # just retried from scratch, which is fine at this volume.
    stuck = sb.table("jobs").update({"status": "queued", "progress_note": None}).eq("status", "processing").execute()
    if stuck.data:
        print(f"Requeued {len(stuck.data)} job(s) left 'processing' by a previous run.")

    print(f"Watching for queued jobs every {poll_seconds:.0f}s... (Ctrl+C to stop)")
    while True:
        jobs = (
            sb.table("jobs").select("*").eq("status", "queued").order("created_at").limit(1).execute().data
        )
        if not jobs:
            time.sleep(poll_seconds)
            continue
        _process_job(jobs[0], sb, router, cfg, rulebook, fewshot)
