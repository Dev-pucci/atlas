/**
 * TypeScript port of annotator/pipeline/lint.py — must stay in lock-step with it.
 * Used both client-side (live typing feedback) and server-side (finalize Stage A),
 * from the same module, so there is exactly one definition of "what's a violation"
 * on the web side (unlike the throwaway JS embedded in the old static review.html).
 */

export type Severity = "error" | "warn";

export interface Violation {
  rule: string;
  severity: Severity;
  message: string;
}

const v = (rule: string, severity: Severity, message: string): Violation => ({
  rule,
  severity,
  message,
});

export const FORBIDDEN_WORDS: Record<string, string> = {
  adjust:
    "use shift/reposition/center/align/slide/tilt/fold/turn/rotate/flatten/tighten/loosen/smoothen/straighten",
  adjusts: "see 'adjust'",
  manipulate: "use grip/hold/push/pull/press/work/twist/squeeze/flip/pinch/apply/assemble",
  manipulates: "see 'manipulate'",
  move: "use pick up / place / reposition instead",
  moves: "see 'move'",
  transfer: "use pick up and place; for hand exchanges use hand over/pass/put/switch/set",
  transfers: "see 'transfer'",
  handover: "use 'hand over' / pass / put / switch / set",
  inspect: "do not label looking/inspecting/checking",
  inspects: "see 'inspect'",
  check: "do not label looking/inspecting/checking",
  checks: "see 'check'",
  examine: "do not label looking/inspecting/checking",
  examines: "see 'examine'",
  reach: "fix timestamps instead of labeling reaching",
  reaches: "see 'reach'",
};

export const ARTICLES = new Set(["the", "a", "an"]);
export const PRONOUNS = new Set(["it", "them", "they"]);

const LABEL_VERBS = [
  "pick", "place", "hold", "wipe", "slide", "rotate", "turn", "cut", "roll", "flatten",
  "press", "pour", "shake", "open", "close", "loosen", "tighten", "fold", "unfold",
  "align", "shift", "reposition", "center", "tilt", "tuck", "smoothen", "straighten",
  "grip", "push", "pull", "work", "twist", "squeeze", "flip", "pinch", "apply",
  "assemble", "gather", "scrape", "portion", "draw", "dispense", "etch", "release",
  "remove", "pass", "put", "switch", "set", "hand", "move", "transfer", "adjust",
  "manipulate", "reach", "inspect", "check", "examine", "iron", "fill", "sift",
  "drop", "lift", "insert", "attach", "detach", "wash", "clean", "dry", "mix",
  "stir", "spread", "wrap", "unwrap", "peel", "spray", "sweep", "scrub", "hang",
];

const VOWELS = "aeiou";

function ingForms(): Set<string> {
  const forms = new Set<string>();
  for (const w of LABEL_VERBS) {
    forms.add(w + "ing"); // holding, picking
    if (w.endsWith("e") && !w.endsWith("ee")) {
      forms.add(w.slice(0, -1) + "ing"); // place -> placing, wipe -> wiping
    }
    const last = w[w.length - 1], mid = w[w.length - 2], prev = w[w.length - 3];
    if (w.length >= 3 && !"aeiouwy".includes(last) && VOWELS.includes(mid) && !VOWELS.includes(prev)) {
      forms.add(w + last + "ing"); // cut -> cutting, set -> setting
    }
  }
  return forms;
}

export const ING_VERBS = ingForms();

export const ING_NOUN_WHITELIST = new Set([
  "ring", "string", "spring", "fishing", "railing", "awning", "ceiling", "casing", "piping",
]);

const NON_NOUN_FOLLOWERS = new Set([
  "with", "in", "on", "from", "to", "into", "onto", "and", "of", "off", "up", "down", "at", "by",
]);

export const EXCHANGE_OK = ["hand over", "pass", "put", "switch", "set"];
const EXCHANGE_BAD = ["transfer", "handover", "give", "gives"];

const WEAK_VERBS: Record<string, string> = {
  take: "use 'pick up'",
  grasp: "use 'pick up' or 'grip'",
  grab: "use 'pick up' or 'grip'",
};

const MAX_SEGMENT_SECONDS = 10.0;

function words(label: string): string[] {
  return label.toLowerCase().match(/[a-z]+/g) || [];
}

function clauses(label: string): string[][] {
  return label
    .toLowerCase()
    .split(/,|\band\b/)
    .map((p) => words(p))
    .filter((w) => w.length > 0);
}

function ingViolations(label: string): Violation[] {
  const out: Violation[] = [];
  for (const clause of clauses(label)) {
    for (let i = 0; i < clause.length; i++) {
      const w = clause[i];
      if (!w.endsWith("ing") || w.length <= 4 || ING_NOUN_WHITELIST.has(w)) continue;
      const follower = i + 1 < clause.length ? clause[i + 1] : undefined;
      const isModifier = follower !== undefined && !NON_NOUN_FOLLOWERS.has(follower);
      if (i === 0) {
        const sev: Severity = ING_VERBS.has(w) ? "error" : "warn";
        out.push(v("ing-verb", sev, `'${w}' leads a clause — use imperative form`));
      } else if (!isModifier) {
        const sev: Severity = ING_VERBS.has(w) ? "error" : "warn";
        out.push(v("ing-verb", sev, `'${w}' reads as an -ing verb — use imperative form`));
      }
    }
  }
  return out;
}

/** Deterministic cleanup applied after every model pass: whitespace, case, separators. */
export function normalizeLabel(label: string): string {
  let s = label.trim().replace(/\s+/g, " ");
  s = s.replace(/;/g, ",");
  s = s.replace(/\s*,\s*/g, ", ");
  s = s.replace(/[ .,]+$/, "");
  if (s.length && /[A-Z]/.test(s[0])) s = s[0].toLowerCase() + s.slice(1);
  return s;
}

export function lintLabel(label: string, duration?: number): Violation[] {
  const out: Violation[] = [];
  const low = label.toLowerCase().trim();
  const w = words(label);

  for (const word of w) {
    if (ARTICLES.has(word)) out.push(v("articles", "error", `'${word}' is forbidden (no the/a/an)`));
  }
  for (const word of w) {
    if (PRONOUNS.has(word))
      out.push(v("pronouns", "error", `pronoun '${word}' is forbidden — repeat the object name`));
  }
  for (const word of w) {
    if (word in FORBIDDEN_WORDS)
      out.push(v("forbidden-word", "error", `'${word}' is forbidden — ${FORBIDDEN_WORDS[word]}`));
  }
  out.push(...ingViolations(label));

  if (/\d/.test(label)) {
    out.push(v("numerals", "error", "digits are forbidden — spell out numbers or use plural noun"));
  }
  for (const word of w) {
    if (word in WEAK_VERBS) out.push(v("weak-verb", "warn", `'${word}' — ${WEAK_VERBS[word]}`));
  }
  if (/\bpick\b(?!\s+up)/.test(low)) {
    out.push(v("weak-verb", "warn", "bare 'pick' — the reference verb is 'pick up'"));
  }
  for (const word of w) {
    if (["tool", "object", "tools", "objects"].includes(word)) {
      out.push(
        v("generic-noun", "warn", `generic '${word}' — name the actual object (ok only for technical items)`)
      );
    }
  }

  if (!w.includes("hand") && !w.includes("hands")) {
    out.push(v("hand-spec", "error", "label never specifies left/right/both hand(s)"));
  } else {
    const cls = low.split(/,| and (?=[a-z]+ )/);
    for (const c of cls) {
      const trimmed = c.trim();
      if (trimmed && !trimmed.includes("hand")) {
        out.push(v("hand-spec", "warn", `clause '${trimmed}' does not name a hand — verify it is covered`));
      }
    }
  }

  if (low.includes("no action") && w.length > 2) {
    out.push(v("no-action", "error", "'no action' must not be combined with any action"));
  }

  if (/\bto (left|right) hand\b/.test(low)) {
    const cls = low.split(",").filter((c) => /\bto (left|right) hand\b/.test(c));
    for (const c of cls) {
      if (!EXCHANGE_OK.some((ok) => c.includes(ok))) {
        out.push(
          v("exchange-verb", "error", `hand exchange '${c.trim()}' must use hand over/pass/put/switch/set`)
        );
      }
      for (const bad of EXCHANGE_BAD) {
        if (new RegExp(`\\b${bad}\\b`).test(c)) {
          out.push(v("exchange-verb", "error", `'${bad}' is not allowed for hand exchanges`));
        }
      }
    }
  }

  if (duration !== undefined && duration > MAX_SEGMENT_SECONDS + 0.05) {
    out.push(v("duration", "error", `segment is ${duration.toFixed(1)}s — max is 10s, split it`));
  }
  if (!low) out.push(v("empty", "error", "label is empty"));

  return out;
}

export function lintErrors(label: string, duration?: number): Violation[] {
  return lintLabel(label, duration).filter((x) => x.severity === "error");
}
