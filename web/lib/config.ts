/** Model used for the finalize recheck — matches the local pipeline's `audit` tier
 * (cheap, vision-capable), not the `escalate` tier: this is a narrow recheck of a
 * single already-human-verified segment, not a hard re-label. */
export const FINALIZE_MODEL = "google/gemini-2.5-flash";
