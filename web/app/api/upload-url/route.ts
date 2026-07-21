import { NextRequest, NextResponse } from "next/server";
import { createUploadTarget } from "@/lib/supabase-server";

export const runtime = "nodejs";

interface Body {
  filename: string;
}

/** Mints a one-time signed upload URL so the browser can PUT the video straight to
 * Supabase Storage, bypassing Vercel's API route body-size limits. */
export async function POST(req: NextRequest) {
  let body: Body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.filename) {
    return NextResponse.json({ error: "filename is required" }, { status: 400 });
  }

  // Strip any directory components — filename is attacker-controlled on this unauthenticated
  // route, and Supabase Storage paths with embedded "/" would otherwise create nested "folders"
  // (or, combined with "..", something an over-clever client-side path join could misresolve).
  const safeName = body.filename.replace(/^.*[\\/]/, "").replace(/\.\./g, "") || "upload.mp4";
  const path = `${crypto.randomUUID()}-${safeName}`;
  try {
    const target = await createUploadTarget(path);
    return NextResponse.json(target);
  } catch (e) {
    return NextResponse.json({ error: e instanceof Error ? e.message : "failed to mint upload URL" }, { status: 500 });
  }
}
