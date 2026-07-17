"""Atlas annotator CLI.

Commands:
  python -m annotator ingest <guidelines.pdf>          compile/refresh the rulebook
  python -m annotator annotate <video-or-folder> --segments <file>|--segments-image <png>
  python -m annotator lint "<label>"                   quick offline label check
  python -m annotator eval                             score pipeline vs gold data
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline.client import Router, load_config
from .pipeline.frames import find_video, video_info
from .pipeline import ingest as ingest_mod
from .pipeline.label import build_context, escalate_segments, label_segments
from .pipeline.audit import audit_segments
from .pipeline.lint import lint_label, repair_label
from .pipeline.report import write_report
from .pipeline.segments import (
    parse_segments_file,
    parse_segments_image,
    parse_segments_text,
    validate_segments,
)


def cmd_ingest(args) -> None:
    router = Router()
    ingest_mod.ingest(args.pdf, router, force=args.force)
    print("\n" + router.cost.summary())


def cmd_annotate(args) -> None:
    cfg = load_config()
    router = Router(cfg)
    rulebook = ingest_mod.load_rulebook()
    # vocabulary rides along with the few-shot examples into every labeling prompt
    fewshot = (ingest_mod.load_fewshot() + "\n\n" + ingest_mod.load_vocabulary()).strip()

    video_path = find_video(args.video)
    info = video_info(video_path)
    out_dir = video_path.parent / "annotator_out"
    out_dir.mkdir(exist_ok=True)
    print(f"Video: {video_path.name} — {info.duration:.1f}s @ {info.fps:.1f}fps {info.width}x{info.height}")

    if args.segments:
        segments = parse_segments_file(args.segments)
    elif args.segments_image:
        segments = parse_segments_image(args.segments_image, router)
    elif args.segments_text:
        segments = parse_segments_text(args.segments_text)
    else:
        raise SystemExit("Provide segments via --segments <file>, --segments-image <png>, or --segments-text '...'")
    print(f"Segments: {len(segments)} ({segments[0].time_str()} ... {segments[-1].time_str()})")
    for w in validate_segments(segments, info.duration):
        print(f"  ! {w}")

    # Pass 0 — context & glossary
    context = build_context(video_path, router, cfg, out_dir, ingest_mod.load_vocabulary())
    print(f"Task: {context.get('task_summary', '')[:120]}...")

    # Pass 1 — batched per-segment labeling
    print("Pass 1: labeling segments (batched)")
    segments = label_segments(video_path, segments, context, rulebook, fewshot, router, cfg)

    # Pass 2 — deterministic lint + repair
    print("Pass 2: lint + repair")
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

    # Pass 3 — audit (text-only by default: fixes grammar/naming, FLAGS visual doubts)
    segments, video_notes = audit_segments(video_path, segments, context, router, cfg, rulebook)

    # Pass 4 — escalate audit-suspect / low-confidence segments to the stronger model
    segments = escalate_segments(video_path, segments, context, rulebook, fewshot, router, cfg)

    # final lint on anything rewritten by audit or escalation
    for seg in segments:
        for v in lint_label(seg.label, seg.duration):
            if v.severity == "error":
                fixed, _ = repair_label(seg.label, [v], router, seg.duration)
                seg.label = fixed

    write_report(out_dir, video_path.name, segments, context, video_notes, router.cost.summary(),
                 video_path=video_path, cfg=cfg)


def cmd_lint(args) -> None:
    violations = lint_label(args.label)
    if not violations:
        print("OK — no violations")
    for v in violations:
        print(v)


def cmd_lint_file(args) -> None:
    """Lint every label line of an (edited) annotations file before submitting."""
    problems = 0
    for n, line in enumerate(Path(args.file).read_text(encoding="utf-8").splitlines(), 1):
        label = line.split("\t")[-1].strip()  # tolerate "time<TAB>label" or bare label
        if not label:
            continue
        for v in lint_label(label):
            print(f"line {n}: {v}   -> {label}")
            problems += 1 if v.severity == "error" else 0
    print("OK — every label passes" if problems == 0 else f"\n{problems} error(s) — fix before submitting")


LEARN_PROMPT = """This screenshot shows audited video-annotation segments. Some are Rejected with a
correction; some are Approved. Extract EVERY rejected segment. Reply with JSON only:
{"rejections": [{"wrong": "<original label>", "reasons": ["Missed action", ...],
                 "corrected": "<auditor's corrected label>"}],
 "vocabulary_hints": ["object or verb terms the auditors used that a labeler should prefer"]}
If nothing is rejected in the image, return empty lists."""


def cmd_learn(args) -> None:
    """Feed an audit-rejection screenshot back into the few-shot bank + vocabulary."""
    from datetime import date
    from .pipeline.client import image_part, text_part

    router = Router()
    img = Path(args.image).read_bytes()
    data = router.chat_json("ocr", [{"role": "user",
                                     "content": [text_part(LEARN_PROMPT), image_part(img)]}])
    rejections = data.get("rejections", [])
    hints = [h for h in data.get("vocabulary_hints", []) if h]
    if not rejections:
        print("No rejected segments found in that screenshot.")
        return

    fewshot_path = ingest_mod.KNOWLEDGE_DIR / "fewshot.md"
    block = [f"\n## Learned from audit on {date.today().isoformat()}"]
    for r in rejections:
        block.append(f"\nWRONG: \"{r.get('wrong', '').strip()}\"")
        block.append(f"REASON: {', '.join(r.get('reasons', [])) or 'rejected'}")
        block.append(f"RIGHT: \"{r.get('corrected', '').strip()}\"")
    with open(fewshot_path, "a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")
    print(f"Added {len(rejections)} rejection example(s) to {fewshot_path}")

    if hints:
        vocab_path = ingest_mod.KNOWLEDGE_DIR / "vocabulary.md"
        with open(vocab_path, "a", encoding="utf-8") as f:
            f.write(f"\n## Learned {date.today().isoformat()}\n")
            f.writelines(f"- {h}\n" for h in hints)
        print(f"Added {len(hints)} vocabulary hint(s) to {vocab_path}: {', '.join(hints)}")
    print("Future annotations will use these examples automatically.")


def cmd_eval(args) -> None:
    from .eval import run_eval

    run_eval(live=args.live)


def cmd_push(args) -> None:
    from .pipeline.push import push

    push(args.video, app_url=args.app_url)


def main() -> None:
    p = argparse.ArgumentParser(prog="annotator", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    p_ing = sub.add_parser("ingest", help="compile guidelines PDF into the rulebook")
    p_ing.add_argument("pdf")
    p_ing.add_argument("--force", action="store_true", help="recompile even if the PDF is unchanged")
    p_ing.set_defaults(func=cmd_ingest)

    p_ann = sub.add_parser("annotate", help="annotate a video")
    p_ann.add_argument("video", help="video file or folder containing one")
    p_ann.add_argument("--segments", help="text/csv file with 'M:SS.s - M:SS.s' lines")
    p_ann.add_argument("--segments-image", help="screenshot of the platform's segment list")
    p_ann.add_argument("--segments-text", help="segment lines passed inline")
    p_ann.set_defaults(func=cmd_annotate)

    p_lint = sub.add_parser("lint", help="lint a single label offline")
    p_lint.add_argument("label")
    p_lint.set_defaults(func=cmd_lint)

    p_lf = sub.add_parser("lint-file", help="lint every label in an annotations file (pre-submission check)")
    p_lf.add_argument("file")
    p_lf.set_defaults(func=cmd_lint_file)

    p_learn = sub.add_parser("learn", help="feed an audit-rejection screenshot back into the tool")
    p_learn.add_argument("image", help="screenshot showing rejected segments with corrections")
    p_learn.set_defaults(func=cmd_learn)

    p_eval = sub.add_parser("eval", help="score generated labels against gold data")
    p_eval.add_argument("--live", action="store_true",
                        help="run the full pipeline on the sample videos first (costs API credits)")
    p_eval.set_defaults(func=cmd_eval)

    p_push = sub.add_parser("push", help="push a completed run to Supabase for hosted review")
    p_push.add_argument("video", help="video file or folder (must already have annotator_out/run.json)")
    p_push.add_argument("--app-url", help="your deployed Vercel URL, to print a direct review link")
    p_push.set_defaults(func=cmd_push)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
