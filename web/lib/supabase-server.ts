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

let cached: ReturnType<typeof createClient> | null = null;

export function supabaseServer() {
  if (cached) return cached;
  cached = createClient(getEnv("SUPABASE_URL"), getEnv("SUPABASE_SERVICE_ROLE_KEY"), {
    auth: { persistSession: false },
  });
  return cached;
}

export const FRAMES_BUCKET = "frames";

/** Sign a batch of storage paths for temporary browser access (default 1 hour). */
export async function signFramePaths(paths: string[], expiresInSeconds = 3600): Promise<string[]> {
  if (paths.length === 0) return [];
  const { data, error } = await supabaseServer()
    .storage.from(FRAMES_BUCKET)
    .createSignedUrls(paths, expiresInSeconds);
  if (error) throw error;
  return (data ?? []).map((d) => d.signedUrl ?? "");
}
