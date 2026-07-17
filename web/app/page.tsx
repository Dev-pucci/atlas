import Link from "next/link";
import { supabaseServer } from "@/lib/supabase-server";
import type { VideoRow, SegmentRow } from "@/lib/types";

export const dynamic = "force-dynamic";

interface VideoWithStats extends VideoRow {
  segmentCount: number;
  needsReviewCount: number;
  finalizedCount: number;
}

async function loadVideos(): Promise<VideoWithStats[]> {
  const sb = supabaseServer();
  const { data: videos, error } = await sb
    .from("videos")
    .select("*")
    .order("pushed_at", { ascending: false });
  if (error) throw error;
  if (!videos?.length) return [];

  const { data: segments, error: segErr } = await sb
    .from("segments")
    .select("video_id, flags, confidence, finalize_verdict")
    .in(
      "video_id",
      videos.map((v) => v.id)
    );
  if (segErr) throw segErr;

  return (videos as VideoRow[]).map((v) => {
    const own = (segments as Pick<SegmentRow, "video_id" | "flags" | "confidence" | "finalize_verdict">[])
      .filter((s) => s.video_id === v.id);
    return {
      ...v,
      segmentCount: own.length,
      needsReviewCount: own.filter((s) => (s.flags?.length ?? 0) > 0 || s.confidence < 0.7).length,
      finalizedCount: own.filter((s) => s.finalize_verdict && s.finalize_verdict !== "unchanged").length,
    };
  });
}

export default async function VideoListPage() {
  const videos = await loadVideos();

  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: "24px 16px" }}>
      <h1 style={{ fontSize: 22, marginBottom: 4 }}>Atlas Annotator — Review Queue</h1>
      <p style={{ color: "#666", marginTop: 0, marginBottom: 24, fontSize: 14 }}>
        Videos pushed from the local pipeline (<code>python -m annotator push &lt;folder&gt;</code>).
      </p>

      {videos.length === 0 && (
        <p style={{ color: "#777" }}>
          No videos yet. Run <code>python -m annotator push &lt;video_folder&gt;</code> from your PC.
        </p>
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
              borderLeft: `5px solid ${v.needsReviewCount > 0 ? "#e0a800" : "#3cb371"}`,
            }}
          >
            <div style={{ fontWeight: 600 }}>{v.name}</div>
            <div style={{ fontSize: 13, color: "#666", marginTop: 4 }}>
              {v.segmentCount} segments · {v.needsReviewCount} to review · {v.finalizedCount} finalized
              {v.duration_seconds ? ` · ${v.duration_seconds.toFixed(0)}s` : ""}
            </div>
          </Link>
        ))}
      </div>
    </main>
  );
}
