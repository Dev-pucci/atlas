import { NextRequest, NextResponse } from "next/server";
import { supabaseServer, signFramePaths } from "@/lib/supabase-server";
import { lintErrors, normalizeLabel } from "@/lib/lint";
import { chatJson, imagePart, textPart, type ChatMessage } from "@/lib/openrouter";
import { FINALIZE_MODEL } from "@/lib/config";
import type { FinalizeRequestSegment, FinalizeResultItem, SegmentRow, VideoRow, KnowledgeRow } from "@/lib/types";

export const runtime = "nodejs";

const RECHECK_PROMPT = `You are rechecking ONE video-annotation segment that a HUMAN just edited by hand,
using the segment's own frames as ground truth. The human already visually verified this segment — you
are not drafting from scratch and must not re-judge content you cannot actually see disproven in the
frames. Your job is narrow:

- If the label has a grammar / forbidden-word / vocabulary-naming issue the human's edit didn't fix,
  correct ONLY that (verdict "revise", with "corrected_label") — never change the described actions or
  objects, only the phrasing.
- If something in the label looks factually contradicted by the frames (wrong hand, an object that isn't
  present, an action that clearly isn't happening), do NOT rewrite it — flag it (verdict "suspect") with
  a short note explaining the doubt, so a human looks again.
- Otherwise: verdict "ok", leave the label exactly as the human wrote it.

## Rulebook
{rulebook}

## Vocabulary preferences
{vocabulary}

## Video context
Objects: {objects}
Hands overview: {handsOverview}
Neighboring segments (context only — do not describe or judge these): previous="{prevLabel}", next="{nextLabel}"

## This segment: {start}s to {end}s
Human's label: "{label}"

Reply with JSON only:
{"verdict": "ok" | "revise" | "suspect", "corrected_label": "<only if revise>", "notes": ["<short note>", ...]}`;

interface Body {
  videoId: string;
  segments: FinalizeRequestSegment[];
}

export async function POST(req: NextRequest) {
  let body: Body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.videoId || !Array.isArray(body.segments) || body.segments.length === 0) {
    return NextResponse.json({ error: "videoId and a non-empty segments[] are required" }, { status: 400 });
  }

  const sb = supabaseServer();

  const { data: videoData, error: videoErr } = await sb
    .from("videos")
    .select("*")
    .eq("id", body.videoId)
    .maybeSingle();
  if (videoErr) return NextResponse.json({ error: videoErr.message }, { status: 500 });
  if (!videoData) return NextResponse.json({ error: "video not found" }, { status: 404 });
  const video = videoData as VideoRow;

  const { data: allSegmentsData, error: segErr } = await sb
    .from("segments")
    .select("*")
    .eq("video_id", body.videoId)
    .order("seg_index", { ascending: true });
  if (segErr) return NextResponse.json({ error: segErr.message }, { status: 500 });
  const allSegments = (allSegmentsData ?? []) as SegmentRow[];

  const { data: knowledgeRowsData } = await sb.from("knowledge").select("*").in("key", ["rulebook", "vocabulary"]);
  const knowledgeRows = (knowledgeRowsData ?? []) as KnowledgeRow[];
  const knowledge = new Map(knowledgeRows.map((k) => [k.key, k.content]));

  const segByIndex = new Map(allSegments.map((s) => [s.seg_index, s]));
  const segById = new Map(allSegments.map((s) => [s.id, s]));

  const results = await Promise.all(
    body.segments.map((req) => processOne(req, video, segById, segByIndex, knowledge, sb))
  );

  return NextResponse.json({ results });
}

async function processOne(
  reqSeg: FinalizeRequestSegment,
  video: VideoRow,
  segById: Map<string, SegmentRow>,
  segByIndex: Map<number, SegmentRow>,
  knowledge: Map<string, string>,
  sb: ReturnType<typeof supabaseServer>
): Promise<FinalizeResultItem> {
  const stored = segById.get(reqSeg.id);
  if (!stored) {
    return { id: reqSeg.id, verdict: "unchanged", label: reqSeg.label, notes: ["segment not found"] };
  }

  // Server-side re-verification: only re-check labels that actually differ from what's stored.
  if (normalizeLabel(reqSeg.label) === normalizeLabel(stored.label)) {
    return { id: reqSeg.id, verdict: "unchanged", label: stored.label, notes: [] };
  }

  const duration = stored.end_seconds - stored.start_seconds;

  // Stage A — deterministic, free. Block on mechanical errors; don't silently rewrite mid-edit.
  const errors = lintErrors(reqSeg.label, duration);
  if (errors.length > 0) {
    return {
      id: reqSeg.id,
      verdict: "lint_error",
      label: reqSeg.label,
      notes: errors.map((e) => e.message),
    };
  }

  // Stage B — one recheck call, only for segments that passed Stage A.
  const framePaths = stored.frame_paths ?? [];
  const frameUrls = await signFramePaths(framePaths, 300); // short TTL, used immediately server-side
  const frameBytes = await Promise.all(frameUrls.map((u) => fetchAsBase64(u)));

  const prev = segByIndex.get(stored.seg_index - 1);
  const next = segByIndex.get(stored.seg_index + 1);

  const prompt = RECHECK_PROMPT.replace("{rulebook}", knowledge.get("rulebook") ?? "(none synced)")
    .replace("{vocabulary}", knowledge.get("vocabulary") ?? "(none synced)")
    .replace("{objects}", JSON.stringify(video.objects ?? []))
    .replace("{handsOverview}", video.hands_overview ?? "")
    .replace("{prevLabel}", prev?.label ?? "(start of video)")
    .replace("{nextLabel}", next?.label ?? "(end of video)")
    .replace("{start}", String(stored.start_seconds))
    .replace("{end}", String(stored.end_seconds))
    .replace("{label}", reqSeg.label);

  const content: ChatMessage["content"] = [textPart(prompt), ...frameBytes.map((b64) => imagePart(b64))];

  let verdict: "ok" | "revise" | "suspect" = "ok";
  let notes: string[] = [];
  let finalLabel = normalizeLabel(reqSeg.label);
  try {
    const data = await chatJson(FINALIZE_MODEL, [{ role: "user", content }]);
    const v = String(data.verdict ?? "ok").toLowerCase();
    notes = Array.isArray(data.notes) ? (data.notes as string[]) : [];
    if (v === "revise" && data.corrected_label) {
      verdict = "revise";
      finalLabel = normalizeLabel(String(data.corrected_label));
      const postErrors = lintErrors(finalLabel, duration);
      if (postErrors.length > 0) {
        // the model's own correction still fails the linter — don't trust it, fall back
        verdict = "suspect";
        finalLabel = normalizeLabel(reqSeg.label);
        notes = [...notes, "auto-correction still failed the linter — kept your text"];
      }
    } else if (v === "suspect") {
      verdict = "suspect";
    }
  } catch (e) {
    verdict = "suspect";
    notes = [e instanceof Error ? e.message : "recheck call failed"];
  }

  // Cast to `any` for this one call: the untyped client (see supabase-server.ts) resolves
  // .update()'s payload type to `never` without a generated Database type; we already
  // constructed `finalLabel`/`verdict`/`notes` with our own hand-checked types above.
  await (sb as any)
    .from("segments")
    .update({
      label: finalLabel,
      edited: true,
      finalize_verdict: verdict,
      finalize_notes: notes,
      finalized_at: new Date().toISOString(),
    })
    .eq("id", reqSeg.id);

  return { id: reqSeg.id, verdict, label: finalLabel, notes };
}

async function fetchAsBase64(url: string): Promise<string> {
  const resp = await fetch(url);
  const buf = await resp.arrayBuffer();
  return Buffer.from(buf).toString("base64");
}
