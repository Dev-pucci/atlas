"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { supabaseBrowser, UPLOADS_BUCKET } from "@/lib/supabase-browser";

export function UploadVideo() {
  const [status, setStatus] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();

  async function handleFile(file: File) {
    setBusy(true);
    try {
      setStatus("Requesting upload slot...");
      const urlResp = await fetch("/api/upload-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ filename: file.name }),
      });
      const urlData = await urlResp.json();
      if (!urlResp.ok) throw new Error(urlData.error ?? "failed to get upload slot");
      const { token, path } = urlData as { token: string; path: string };

      setStatus(`Uploading ${file.name}...`);
      const { error: uploadError } = await supabaseBrowser()
        .storage.from(UPLOADS_BUCKET)
        .uploadToSignedUrl(path, token, file);
      if (uploadError) throw uploadError;

      setStatus("Queuing job...");
      const jobResp = await fetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, original_filename: file.name }),
      });
      const jobData = await jobResp.json();
      if (!jobResp.ok) throw new Error(jobData.error ?? "failed to queue job");

      setStatus("Queued — processing starts once your local watcher picks it up.");
      router.refresh();
    } catch (e) {
      setStatus(`Failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div
      style={{
        background: "#fff",
        borderRadius: 8,
        padding: 14,
        boxShadow: "0 1px 3px rgba(0,0,0,.1)",
        marginBottom: 20,
      }}
    >
      <div style={{ fontWeight: 600, marginBottom: 6 }}>Upload a video</div>
      <input
        ref={inputRef}
        type="file"
        accept="video/*"
        disabled={busy}
        onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
      />
      {status && <div style={{ fontSize: 13, color: "#52514e", marginTop: 8 }}>{status}</div>}
    </div>
  );
}
