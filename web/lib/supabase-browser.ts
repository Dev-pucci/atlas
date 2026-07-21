"use client";

import { createClient } from "@supabase/supabase-js";

/**
 * Client-side Supabase instance, used ONLY to perform the direct-to-storage upload PUT
 * against a signed URL minted server-side. Uses the publishable key — safe to expose in
 * the browser bundle, same as the old "anon" key it replaces. It has zero table/bucket
 * grants of its own (see migration.sql); the signed URL's token is what actually
 * authorizes the upload, independent of this key.
 */
let cached: ReturnType<typeof createClient> | null = null;

export function supabaseBrowser() {
  if (cached) return cached;
  cached = createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL as string,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY as string
  );
  return cached;
}

export const UPLOADS_BUCKET = "uploads";
