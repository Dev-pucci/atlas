"""Pull finalized web-review edits into fewshot.md — a second source of real, human-verified
corrections beyond `learn`'s platform audit screenshots.

Every label you edit and finalize on the hosted review page is a genuine correction (a human
said the original was wrong and this is right), but it previously just sat in Supabase and
never fed back into what the model learns from. This closes that gap using data the finalize
workflow already captures.
"""

from __future__ import annotations

from datetime import date

from .ingest import KNOWLEDGE_DIR
from .push import _client

# Only verdicts that mean the final label is actually validated: "ok" (accepted as typed) or
# "revise" (server corrected phrasing on top of the edit). "suspect" means the server itself
# doubts it, and "lint_error" means it's still mechanically broken — neither is a trustworthy
# RIGHT example.
_TRUSTED_VERDICTS = ("ok", "revise")


def harvest_finalized_edits(write: bool = False) -> int:
    """Dry-run by default: a finalize verdict of "ok" is the recheck model's own judgment,
    not a guarantee — it can and does approve genuinely bad edits (observed directly: a
    burst of 18 finalize calls landing within a 4-second window produced several verdict="ok"
    corrections that were clearly scrambled/mismatched labels, not real fixes). Print
    candidates for a human to actually read; only write to fewshot.md with write=True,
    after you've looked at them."""
    sb = _client()
    rows = (
        sb.table("segments")
        .select("id,label,original_label,finalize_verdict,finalize_notes")
        .eq("edited", True)
        .execute()
        .data
    )
    corrections = [
        r for r in rows
        if r["label"] != r["original_label"] and r.get("finalize_verdict") in _TRUSTED_VERDICTS
    ]
    if not corrections:
        print("No finalized web-review edits to harvest.")
        return 0

    fewshot_path = KNOWLEDGE_DIR / "fewshot.md"
    existing = fewshot_path.read_text(encoding="utf-8") if fewshot_path.exists() else ""

    new_corrections = []
    for r in corrections:
        marker = f'WRONG: "{r["original_label"].strip()}"'
        if marker not in existing:
            new_corrections.append(r)

    if not new_corrections:
        print(f"All {len(corrections)} finalized edit(s) were already harvested.")
        return 0

    if not write:
        print(f"{len(new_corrections)} candidate correction(s) — DRY RUN, nothing written. "
              f"Read these before trusting them (verdict='ok' is the recheck model's own "
              f"opinion, not proof):\n")
        for r in new_corrections:
            print(f"  WRONG: \"{r['original_label'].strip()}\"")
            print(f"  RIGHT: \"{r['label'].strip()}\"")
            print(f"  (verdict={r['finalize_verdict']})\n")
        print("Re-run with write=True (CLI: --write) to append all of the above to fewshot.md.")
        return len(new_corrections)

    block = [f"\n## Learned from web review edits on {date.today().isoformat()}"]
    for r in new_corrections:
        reason = "; ".join(r.get("finalize_notes") or []) or "corrected during web review"
        block.append(f"\nWRONG: \"{r['original_label'].strip()}\"")
        block.append(f"REASON: {reason}")
        block.append(f"RIGHT: \"{r['label'].strip()}\"")

    with open(fewshot_path, "a", encoding="utf-8") as f:
        f.write("\n".join(block) + "\n")
    print(f"Added {len(new_corrections)} web-review correction(s) to {fewshot_path}")
    return len(new_corrections)
