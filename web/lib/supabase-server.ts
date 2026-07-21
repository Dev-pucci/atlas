import "server-only";
import { createClient } from "@supabase/supabase-js";

/**
 * Server-only Supabase client using the service_role key. NEVER import this file
 * from a "use client" component — the service role key must never reach the browser.
 */
function getEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

// Note: intentionally untyped (no Database generic). This supabase-js version's schema-name
// resolution generics are elaborate enough that a hand-written Database type risks subtle
// mismatches without live schema introspection (`supabase gen types`, which needs a deployed
// project). We already have hand-written Row types in ./types.ts — callers cast query results
// to those immediately after destructuring instead of relying on builder-inferred types.
let cached: ReturnType<typeof createClient> | null = null;

export function supabaseServer() {
  if (cached) return cached;
  cached = createClient(getEnv("SUPABASE_URL"), getEnv("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false },
  });
  return cached;
}

export const FRAMES_BUCKET = "frames";
export const UPLOADS_BUCKET = "uploads";

/** Sign a batch of storage paths for temporary browser access (default 1 hour). */
export async function signFramePaths(paths: string[], expiresInSeconds = 3600): Promise<string[]> {
  if (paths.length === 0) return [];
  const { data, error } = await supabaseServer()
    .storage.from(FRAMES_BUCKET)
    .createSignedUrls(paths, expiresInSeconds);
  if (error) throw error;
  return (data ?? []).map((d) => d.signedUrl ?? "");
}

/**
 * Mint a one-time signed upload URL for the browser to PUT a raw video directly to
 * Supabase Storage — bypassing Vercel's API route body-size limits entirely. The
 * service_role key never reaches the browser; the returned token is itself the
 * (single-use, path-scoped) authorization for that one upload.
 */
export async function createUploadTarget(path: string): Promise<{ signedUrl: string; token: string; path: string }> {
  const { data, error } = await supabaseServer().storage.from(UPLOADS_BUCKET).createSignedUploadUrl(path);
  if (error) throw error;
  return { signedUrl: data.signedUrl, token: data.token, path: data.path };
}
