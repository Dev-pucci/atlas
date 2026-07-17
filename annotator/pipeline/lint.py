"""Deterministic linter enforcing the Atlas Capture labeling guidelines.

Catches every mechanical rule from the guideline PDF (Part 4 forbidden words,
Part 5 label structure, Part 2 boundaries) without any API call. Violations of
severity "error" would be audit failures; "warn" items deserve a human glance.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- word lists (from the guideline PDF; extend when guidelines update) ---

FORBIDDEN_WORDS = {
    "adjust": "use shift/reposition/center/align/slide/tilt/fold/turn/rotate/flatten/tighten/loosen/smoothen/straighten",
    "adjusts": "see 'adjust'",
    "manipulate": "use grip/hold/push/pull/press/work/twist/squeeze/flip/pinch/apply/assemble",
    "manipulates": "see 'manipulate'",
    "move": "use pick up / place / reposition instead",
    "moves": "see 'move'",
    "transfer": "use pick up and place; for hand exchanges use hand over/pass/put/switch/set",
    "transfers": "see 'transfer'",
    "handover": "use 'hand over' / pass / put / switch / set",
    "inspect": "do not label looking/inspecting/checking",
    "inspects": "see 'inspect'",
    "check": "do not label looking/inspecting/checking",
    "checks": "see 'check'",
    "examine": "do not label looking/inspecting/checking",
    "examines": "see 'examine'",
    "reach": "fix timestamps instead of labeling reaching",
    "reaches": "see 'reach'",
}

ARTICLES = {"the", "a", "an"}
PRONOUNS = {"it", "them", "they"}

# verbs whose -ing form indicates present-participle phrasing (forbidden)
LABEL_VERBS = [
    "pick", "place", "hold", "wipe", "slide", "rotate", "turn", "cut", "roll", "flatten",
    "press", "pour", "shake", "open", "close", "loosen", "tighten", "fold", "unfold",
    "align", "shift", "reposition", "center", "tilt", "tuck", "smoothen", "straighten",
    "grip", "push", "pull", "work", "twist", "squeeze", "flip", "pinch", "apply",
    "assemble", "gather", "scrape", "portion", "draw", "dispense", "etch", "release",
    "remove", "pass", "put", "switch", "set", "hand", "move", "transfer", "adjust",
    "manipulate", "reach", "inspect", "check", "examine", "iron", "fill", "sift",
    "drop", "lift", "insert", "attach", "detach", "wash", "clean", "dry", "mix",
    "stir", "spread", "wrap", "unwrap", "peel", "spray", "sweep", "scrub", "hang",
]

def _ing_forms() -> set[str]:
    forms = set()
    for v in LABEL_VERBS:
        forms.add(v + "ing")                      # holding, picking
        if v.endswith("e") and not v.endswith("ee"):
            forms.add(v[:-1] + "ing")             # place -> placing, wipe -> wiping
        if len(v) >= 3 and v[-1] not in "aeiouwy" and v[-2] in "aeiou" and v[-3] not in "aeiou":
            forms.add(v + v[-1] + "ing")          # cut -> cutting, set -> setting
    return forms

ING_VERBS = _ing_forms()

# nouns ending in -ing that are fine in this domain
ING_NOUN_WHITELIST = {"ring", "string", "spring", "fishing", "railing", "awning", "ceiling", "casing", "piping"}

# words that may follow an -ing word WITHOUT making it a compound noun modifier
# ("wiping with cloth" = verb; "rolling pin" = compound noun)
NON_NOUN_FOLLOWERS = {"with", "in", "on", "from", "to", "into", "onto", "and", "of", "off", "up", "down", "at", "by"}

EXCHANGE_OK = {"hand over", "pass", "put", "switch", "set"}
EXCHANGE_BAD = {"transfer", "handover", "give", "gives"}

WEAK_VERBS = {"take": "use 'pick up'", "grasp": "use 'pick up' or 'grip'", "grab": "use 'pick up' or 'grip'"}

MAX_SEGMENT_SECONDS = 10.0


@dataclass
class Violation:
    rule: str
    severity: str  # "error" | "warn"
    message: str

    def __str__(self):
        return f"[{self.severity}] {self.rule}: {self.message}"


def _words(label: str) -> list[str]:
    return re.findall(r"[a-zA-Z]+", label.lower())


def _clauses(label: str) -> list[list[str]]:
    """Split a label into clauses (comma / ' and ' separated) as word lists."""
    parts = re.split(r",|\band\b", label.lower())
    return [w for w in (_words(p) for p in parts) if w]


def _ing_violations(label: str) -> list["Violation"]:
    """Flag present-participle verbs while allowing compound nouns like 'rolling pin'.

    A verb in these labels leads its clause; an -ing word directly followed by a
    noun ('rolling pin', 'measuring cylinder', 'engraving pen') is a modifier.
    """
    out: list[Violation] = []
    for clause in _clauses(label):
        for i, w in enumerate(clause):
            if not w.endswith("ing") or len(w) <= 4 or w in ING_NOUN_WHITELIST:
                continue
            follower = clause[i + 1] if i + 1 < len(clause) else None
            is_modifier = follower is not None and follower not in NON_NOUN_FOLLOWERS
            if i == 0:  # verb position
                sev = "error" if w in ING_VERBS else "warn"
                out.append(Violation("ing-verb", sev, f"'{w}' leads a clause — use imperative form"))
            elif not is_modifier:  # mid-clause, not modifying a noun -> gerund/participle
                sev = "error" if w in ING_VERBS else "warn"
                out.append(Violation("ing-verb", sev, f"'{w}' reads as an -ing verb — use imperative form"))
    return out


def normalize_label(label: str) -> str:
    """Deterministic cleanup applied after every model pass: whitespace, case, separators."""
    label = re.sub(r"\s+", " ", label.strip())
    label = label.replace(";", ",")
    label = re.sub(r"\s*,\s*", ", ", label).rstrip(" .,")
    if label[:1].isupper():
        label = label[0].lower() + label[1:]
    return label


def lint_label(label: str, duration: float | None = None) -> list[Violation]:
    v: list[Violation] = []
    low = label.lower().strip()
    words = _words(label)

    # articles
    for w in words:
        if w in ARTICLES:
            v.append(Violation("articles", "error", f"'{w}' is forbidden (no the/a/an)"))
    # pronouns
    for w in words:
        if w in PRONOUNS:
            v.append(Violation("pronouns", "error", f"pronoun '{w}' is forbidden — repeat the object name"))
    # forbidden verbs
    for w in words:
        if w in FORBIDDEN_WORDS:
            v.append(Violation("forbidden-word", "error", f"'{w}' is forbidden — {FORBIDDEN_WORDS[w]}"))
    # -ing verbs (position-aware: allows compound nouns like 'rolling pin')
    v.extend(_ing_violations(label))
    # digits
    if re.search(r"\d", label):
        v.append(Violation("numerals", "error", "digits are forbidden — spell out numbers or use plural noun"))
    # weak verbs
    for w in words:
        if w in WEAK_VERBS:
            v.append(Violation("weak-verb", "warn", f"'{w}' — {WEAK_VERBS[w]}"))
    # bare 'pick' without 'up'
    if re.search(r"\bpick\b(?!\s+up)", low):
        v.append(Violation("weak-verb", "warn", "bare 'pick' — the reference verb is 'pick up'"))
    # generic tool/object
    for w in words:
        if w in {"tool", "object", "tools", "objects"}:
            v.append(Violation("generic-noun", "warn", f"generic '{w}' — name the actual object (ok only for technical items)"))
    # hand specification
    if "hand" not in words and "hands" not in words:
        v.append(Violation("hand-spec", "error", "label never specifies left/right/both hand(s)"))
    else:
        clauses = re.split(r",| and (?=[a-z]+ )", low)
        misses = [c.strip() for c in clauses if c.strip() and "hand" not in c]
        # a trailing clause may inherit the hand from shared phrasing; warn only
        for c in misses:
            v.append(Violation("hand-spec", "warn", f"clause '{c}' does not name a hand — verify it is covered"))
    # 'no action' mixed with an action
    if "no action" in low and len(words) > 2:
        v.append(Violation("no-action", "error", "'no action' must not be combined with any action"))
    # hand-exchange phrasing
    if re.search(r"\bto (left|right) hand\b", low):
        clause = [c for c in re.split(r",", low) if re.search(r"\bto (left|right) hand\b", c)]
        for c in clause:
            if not any(okv in c for okv in EXCHANGE_OK):
                v.append(Violation("exchange-verb", "error",
                                   f"hand exchange '{c.strip()}' must use hand over/pass/put/switch/set"))
            for bad in EXCHANGE_BAD:
                if re.search(rf"\b{bad}\b", c):
                    v.append(Violation("exchange-verb", "error", f"'{bad}' is not allowed for hand exchanges"))
    # duration
    if duration is not None and duration > MAX_SEGMENT_SECONDS + 0.05:
        v.append(Violation("duration", "error", f"segment is {duration:.1f}s — max is 10s, split it"))
    # empty
    if not low:
        v.append(Violation("empty", "error", "label is empty"))
    return v


def lint_errors(label: str, duration: float | None = None) -> list[Violation]:
    return [x for x in lint_label(label, duration) if x.severity == "error"]


REPAIR_PROMPT = """You are fixing a video-annotation label that violates labeling guidelines.

Original label: "{label}"

Violations to fix:
{violations}

Core rules: imperative voice; no articles (the/a/an); no -ing verbs; no pronouns (it/them/they);
no digits (spell numbers out); no adjust/manipulate/move/transfer/inspect/check/examine/reach;
every action names the hand (left hand / right hand / both hands); hand exchanges use
"hand over/pass/put/switch/set X in <hand> to <other> hand"; keep the described actions and
objects EXACTLY the same — only fix the phrasing.

Reply with JSON only: {{"label": "<corrected label>"}}"""


def repair_label(label: str, violations: list[Violation], router, duration: float | None = None,
                 max_rounds: int = 2) -> tuple[str, list[Violation]]:
    """Ask a cheap model to fix rule violations; re-lint after each round."""
    from .client import text_part  # local import to avoid cycle

    current = label
    remaining = violations
    for _ in range(max_rounds):
        errors = [x for x in remaining if x.severity == "error"]
        if not errors:
            break
        prompt = REPAIR_PROMPT.format(
            label=current,
            violations="\n".join(f"- {x}" for x in errors),
        )
        try:
            data = router.chat_json("repair", [{"role": "user", "content": [text_part(prompt)]}])
            fixed = str(data.get("label", "")).strip()
        except Exception as e:
            print(f"  repair call failed: {e}")
            break
        fixed = normalize_label(fixed) if fixed else fixed
        if not fixed or fixed == current:
            break
        current = fixed
        remaining = lint_label(current, duration)
    return current, remaining
