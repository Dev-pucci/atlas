import Link from "next/link";
import { supabaseServer } from "@/lib/supabase-server";
import { fmtPct, fmtUsd } from "@/lib/format";
import type { VideoRow, SegmentRow, JobRow } from "@/lib/types";
import { UploadVideo } from "./UploadVideo";

export const dynamic = "force-dynamic";

const JOB_STATUS_COLOR: Record<JobRow["status"], string> = {
  queued: "#898781",
  processing: "#2a78d6",
  error: "#d03b3b",
  done: "#0ca30c",
};

interface VideoWithStats extends VideoRow {
  segmentCount: number;
  needsReviewCount: number;
  finalizedCount: number;
}

// Status palette (fixed, never themed) — meter fill carries severity.
function severityColor(ratio: number): string {
  if (ratio >= 0.75) return "#0ca30c"; // good
  if (ratio >= 0.5) return "#fab219"; // warning
  return "#d03b3b"; // critical
}

function Meter({ label, ratio }: { label: string; ratio: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5 }}>
      <span style={{ color: "#52514e", minWidth: 88 }}>{label}</span>
      <div style={{ width: 60, height: 6, borderRadius: 3, background: "#e1e0d9", overflow: "hidden" }}>
        <div
          style={{
            width: `${Math.round(ratio * 100)}%`,
            height: "100%",
            borderRadius: 3,
            background: severityColor(ratio),
          }}
        />
      </div>
      <span style={{ color: "#52514e" }}>{fmtPct(ratio)}</span>
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div
      style={{
        background: "#fff",
        borderRadius: 8,
        padding: "10px 16px",
        boxShadow: "0 1px 3px rgba(0,0,0,.1)",
        minWidth: 130,
      }}
    >
      <div style={{ fontSize: 12.5, color: "#52514e" }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color: "#0b0b0b" }}>{value}</div>
    </div>
  );
}

type SegmentStats = Pick<SegmentRow, "video_id" | "flags" | "confidence" | "finalize_verdict">;

async function loadVideos(): Promise<VideoWithStats[]> {
  const sb = supabaseServer();
  const { data: videosData, error } = await sb
    .from("videos")
    .select("*")
    .order("pushed_at", { ascending: false });
  if (error) throw error;
  const videos = (videosData ?? []) as VideoRow[];
  if (!videos.length) return [];

  const { data: segmentsData, error: segErr } = await sb
    .from("segments")
    .select("video_id, flags, confidence, finalize_verdict")
    .in(
      "video_id",
      videos.map((v) => v.id)
    );
  if (segErr) throw segErr;
  const segments = (segmentsData ?? []) as SegmentStats[];

  return videos.map((v) => {
    const own = segments.filter((s) => s.video_id === v.id);
    return {
      ...v,
      segmentCount: own.length,
      needsReviewCount: own.filter((s) => (s.flags?.length ?? 0) > 0 || s.confidence < 0.7).length,
      finalizedCount: own.filter((s) => s.finalize_verdict && s.finalize_verdict !== "unchanged").length,
    };
  });
}

async function loadQueuedJobs(): Promise<JobRow[]> {
  const sb = supabaseServer();
  const { data, error } = await sb
    .from("jobs")
    .select("*")
    .neq("status", "done")
    .order("created_at", { ascending: true });
  if (error) throw error;
  return (data ?? []) as JobRow[];
}

export default async function VideoListPage() {
  const [videos, jobs] = await Promise.all([loadVideos(), loadQueuedJobs()]);

  const totalCost = videos.reduce((sum, v) => sum + (v.cost_usd ?? 0), 0);
  const withCost = videos.filter((v) => v.cost_usd != null);
  const withLabelAcc = videos.filter((v) => v.label_accuracy != null);
  const withSegAcc = videos.filter((v) => v.segmentation_accuracy != null);
  const avgF1 = withLabelAcc.length
    ? withLabelAcc.reduce((s, v) => s + v.label_accuracy!.mean_f1, 0) / withLabelAcc.length
    : null;
  const avgRecall = withSegAcc.length
    ? withSegAcc.reduce((s, v) => s + v.segmentation_accuracy!.recall_at_tol, 0) / withSegAcc.length
    : null;

  return (
    <main style={{ maxWidth: 960, margin: "0 auto", padding: "24px 16px" }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>Atlas Annotator — Review Queue</h1>

      <UploadVideo />

      {jobs.length > 0 && (
        <div style={{ marginBottom: 20 }}>
          <div style={{ fontWeight: 600, marginBottom: 8, fontSize: 14 }}>Processing Queue</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {jobs.map((j) => (
              <div
                key={j.id}
                style={{
                  background: "#fff",
                  borderRadius: 8,
                  padding: 12,
                  boxShadow: "0 1px 3px rgba(0,0,0,.1)",
                  borderLeft: `5px solid ${JOB_STATUS_COLOR[j.status]}`,
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                  <span style={{ fontWeight: 600 }}>{j.original_filename}</span>
                  <span style={{ fontSize: 12.5, color: JOB_STATUS_COLOR[j.status], textTransform: "uppercase" }}>
                    {j.status}
                  </span>
                </div>
                {j.status === "processing" && j.progress_note && (
                  <div style={{ fontSize: 13, color: "#52514e", marginTop: 4 }}>{j.progress_note}</div>
                )}
                {j.status === "error" && j.error_message && (
                  <div style={{ fontSize: 13, color: "#d03b3b", marginTop: 4 }}>{j.error_message}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {videos.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 20 }}>
          <StatTile label="Videos" value={String(videos.length)} />
          <StatTile label="Total cost" value={fmtUsd(totalCost)} />
          <StatTile label="Avg cost / video" value={withCost.length ? fmtUsd(totalCost / withCost.length) : "—"} />
          <StatTile label="Avg label accuracy" value={avgF1 != null ? `${fmtPct(avgF1)} F1` : "no gold data"} />
          <StatTile label="Avg segment recall" value={avgRecall != null ? fmtPct(avgRecall) : "no gold data"} />
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {videos.map((v) => (
          <Link
            key={v.id}
            href={`/videos/${v.id}`}
            style={{
              display: "block",
              background: "#fff",
              borderRadius: 8,
              padding: 14,
              boxShadow: "0 1px 3px rgba(0,0,0,.1)",
              textDecoration: "none",
              color: "inherit",
              borderLeft: `5px solid ${v.needsReviewCount > 0 ? "#fab219" : "#0ca30c"}`,
            }}
          >
            <div style={{ fontWeight: 600 }}>{v.name}</div>
            <div style={{ fontSize: 13, color: "#666", marginTop: 4 }}>
              {v.segmentCount} segments · {v.needsReviewCount} to review · {v.finalizedCount} finalized
              {v.duration_seconds ? ` · ${v.duration_seconds.toFixed(0)}s` : ""}
              {v.cost_usd != null ? ` · ${fmtUsd(v.cost_usd)}` : ""}
            </div>
            {(v.label_accuracy || v.segmentation_accuracy) ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 14, marginTop: 8 }}>
                {v.label_accuracy && <Meter label="Label F1" ratio={v.label_accuracy.mean_f1} />}
                {v.segmentation_accuracy && (
                  <Meter label="Segment recall" ratio={v.segmentation_accuracy.recall_at_tol} />
                )}
              </div>
            ) : (
              <div style={{ fontSize: 12.5, color: "#898781", marginTop: 8 }}>no gold data</div>
            )}
          </Link>
        ))}
      </div>
    </main>
  );
}
