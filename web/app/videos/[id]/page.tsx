import { notFound } from "next/navigation";
import Link from "next/link";
import { supabaseServer, signFramePaths } from "@/lib/supabase-server";
import type { VideoRow, SegmentRow, SegmentWithFrames } from "@/lib/types";
import ReviewCards from "./ReviewCards";

export const dynamic = "force-dynamic"; // signed frame URLs expire — never serve a cached page

export default async function VideoReviewPage({ params }: { params: { id: string } }) {
  const sb = supabaseServer();

  const { data: video, error: videoErr } = await sb
    .from("videos")
    .select("*")
    .eq("id", params.id)
    .maybeSingle();
  if (videoErr) throw videoErr;
  if (!video) notFound();

  const { data: segments, error: segErr } = await sb
    .from("segments")
    .select("*")
    .eq("video_id", params.id)
    .order("seg_index", { ascending: true });
  if (segErr) throw segErr;

  const allPaths = (segments as SegmentRow[]).flatMap((s) => s.frame_paths);
  const signedUrls = await signFramePaths(allPaths);
  const urlByPath = new Map(allPaths.map((p, i) => [p, signedUrls[i]]));

  const segmentsWithFrames: SegmentWithFrames[] = (segments as SegmentRow[]).map((s) => ({
    ...s,
    frameUrls: s.frame_paths.map((p) => urlByPath.get(p) ?? ""),
  }));

  return (
    <main style={{ maxWidth: 900, margin: "0 auto", padding: "16px" }}>
      <div style={{ marginBottom: 12 }}>
        <Link href="/" style={{ fontSize: 13, color: "#2b5fd9" }}>
          &larr; all videos
        </Link>
      </div>
      <ReviewCards video={video as VideoRow} initialSegments={segmentsWithFrames} />
    </main>
  );
}
