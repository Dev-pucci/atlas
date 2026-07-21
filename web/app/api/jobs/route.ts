import { NextRequest, NextResponse } from "next/server";
import { supabaseServer } from "@/lib/supabase-server";

export const runtime = "nodejs";

interface Body {
  path: string;
  original_filename: string;
}

/** Queues a job row AFTER the browser's direct-to-storage upload has already succeeded,
 * so a failed/abandoned upload never leaves a dangling queued job for the watcher to fail on. */
export async function POST(req: NextRequest) {
  let body: Body;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  if (!body.path || !body.original_filename) {
    return NextResponse.json({ error: "path and original_filename are required" }, { status: 400 });
  }

  const sb = supabaseServer();
  // Cast to `any`: the untyped client (see supabase-server.ts) resolves .insert()'s payload
  // type to `never` without a generated Database type, same as the .update() call in
  // api/finalize/route.ts.
  const { data, error } = await (sb as any)
    .from("jobs")
    .insert({ status: "queued", storage_path: body.path, original_filename: body.original_filename })
    .select()
    .single();
  if (error) return NextResponse.json({ error: error.message }, { status: 500 });

  return NextResponse.json(data);
}
