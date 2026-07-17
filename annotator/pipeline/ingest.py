"""Compile the guidelines PDF into knowledge/rulebook.md, with versioning + update diffs."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from pypdf import PdfReader

from .client import Router, text_part

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"
RULEBOOK_PATH = KNOWLEDGE_DIR / "rulebook.md"
META_PATH = KNOWLEDGE_DIR / "rulebook.meta.json"

COMPILE_PROMPT = """You are compiling video-annotation labeling guidelines into a rulebook that will be
used as the system prompt for an annotation model. The raw text below was extracted from the official
guideline PDF (table layout may be mangled — reconstruct the intent carefully).

Requirements for the rulebook:
1. Preserve EVERY rule, threshold, word list, and example — nothing may be dropped or invented.
2. Organize into clear markdown sections mirroring the source (what to label / boundaries & timestamps /
   labeling standards / forbidden words & verb reference / special labels / quality control).
3. Convert tables into explicit bullet rules with ✓/✗ examples kept verbatim.
4. End with a section "## Auditor failure taxonomy" listing the audit-fail conditions.
5. Write rules as direct instructions to the annotator model ("Write labels as commands", "Never use...").

Reply with the complete markdown rulebook only — no preamble.

--- RAW GUIDELINE TEXT ---
{text}
--- END ---"""

DIFF_PROMPT = """Compare the OLD and NEW versions of annotation guidelines and summarize what changed
(added rules, removed rules, changed thresholds or wording). Be specific and brief. If nothing of
substance changed, say so.

--- OLD RULEBOOK ---
{old}
--- NEW RULEBOOK ---
{new}
--- END ---"""


def pdf_text(path: str | Path) -> str:
    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, 1):
        pages.append(f"[page {i}]\n{page.extract_text() or ''}")
    return "\n\n".join(pages)


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ingest(pdf_path: str | Path, router: Router, force: bool = False) -> Path:
    pdf_path = Path(pdf_path)
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    digest = file_hash(pdf_path)

    meta = {}
    if META_PATH.exists():
        meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    if meta.get("sha256") == digest and RULEBOOK_PATH.exists() and not force:
        print(f"Guidelines unchanged (hash match) — rulebook is up to date: {RULEBOOK_PATH}")
        return RULEBOOK_PATH

    text = pdf_text(pdf_path)
    if len(text.strip()) < 200:
        raise SystemExit("PDF text extraction produced almost nothing — is this a scanned PDF?")

    print(f"Compiling rulebook from {pdf_path.name} ({len(text)} chars) ...")
    old_rulebook = RULEBOOK_PATH.read_text(encoding="utf-8") if RULEBOOK_PATH.exists() else None
    rulebook = router.chat(
        "ingest", [{"role": "user", "content": [text_part(COMPILE_PROMPT.format(text=text))]}]
    ).strip()
    if rulebook.startswith("```"):
        rulebook = rulebook.strip("`").removeprefix("markdown").strip()

    RULEBOOK_PATH.write_text(rulebook, encoding="utf-8")
    META_PATH.write_text(
        json.dumps(
            {
                "sha256": digest,
                "source": str(pdf_path),
                "ingested": date.today().isoformat(),
                "model": router.model_for("ingest"),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Rulebook written: {RULEBOOK_PATH}")

    if old_rulebook:
        print("\n--- What changed in the guidelines ---")
        diff = router.chat(
            "ingest",
            [{"role": "user", "content": [text_part(DIFF_PROMPT.format(old=old_rulebook, new=rulebook))]}],
        )
        print(diff.strip())
    return RULEBOOK_PATH


def load_rulebook() -> str:
    if not RULEBOOK_PATH.exists():
        raise SystemExit(
            "No rulebook found. Run first:\n"
            "  python -m annotator ingest <guidelines.pdf>"
        )
    return RULEBOOK_PATH.read_text(encoding="utf-8")


def load_fewshot() -> str:
    p = KNOWLEDGE_DIR / "fewshot.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def load_vocabulary() -> str:
    p = KNOWLEDGE_DIR / "vocabulary.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""
