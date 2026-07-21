import { notFound } from "next/navigation";
import Link from "next/link";
import { supabaseServer, signFramePaths } from "@/lib/supabase-server";
import type { VideoRow, SegmentRow, SegmentWithFrames } from "@/lib/types";
import ReviewCards from "./ReviewCards";

export const dynamic = "force-dynamic"; // signed frame URLs expire — never serve a cached page

export default async function VideoReviewPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const sb = supabaseServer();

  const { data: videoData, error: videoErr } = await sb
    .from("videos")
    .select("*")
    .eq("id", id)
    .maybeSingle();
  if (videoErr) throw videoErr;
  if (!videoData) notFound();
  const video = videoData as VideoRow;

  const { data: segmentsData, error: segErr } = await sb
    .from("segments")
    .select("*")
    .eq("video_id", id)
    .order("seg_index", { ascending: true });
  if (segErr) throw segErr;
  const segments = (segmentsData ?? []) as SegmentRow[];

  const allPaths = segments.flatMap((s) => s.frame_paths);
  const signedUrls = await signFramePaths(allPaths);
  const urlByPath = new Map(allPaths.map((p, i) => [p, signedUrls[i]]));

  const segmentsWithFrames: SegmentWithFrames[] = segments.map((s) => ({
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
      <ReviewCards video={video} initialSegments={segmentsWithFrames} />
    </main>
  );
}
