"use client";

import { useState } from "react";
import { lintLabel, type Violation } from "@/lib/lint";
import { timeStr } from "@/lib/format";
import type { VideoRow, SegmentWithFrames, FinalizeResultItem } from "@/lib/types";

interface CardState {
  id: string;
  segIndex: number;
  start: number;
  end: number;
  frameUrls: string[];
  evidence: SegmentWithFrames["evidence"];
  storedFlags: string[];
  storedConfidence: number;
  draftLabel: string;
  baselineLabel: string;
  verdict: FinalizeResultItem["verdict"] | null;
  notes: string[];
}

function toCardState(s: SegmentWithFrames): CardState {
  return {
    id: s.id,
    segIndex: s.seg_index,
    start: s.start_seconds,
    end: s.end_seconds,
    frameUrls: s.frameUrls,
    evidence: s.evidence ?? {},
    storedFlags: s.flags ?? [],
    storedConfidence: s.confidence,
    draftLabel: s.label,
    baselineLabel: s.label,
    verdict: s.finalize_verdict,
    notes: s.finalize_notes ?? [],
  };
}

const VERDICT_STYLE: Record<string, { bg: string; fg: string; label: (n: string[]) => string }> = {
  ok: { bg: "#e8f7ee", fg: "#1c7c3f", label: () => "confirmed" },
  unchanged: { bg: "#eef0f3", fg: "#666", label: () => "unchanged" },
  revise: { bg: "#e8f0fe", fg: "#1a56cc", label: () => "auto-fixed phrasing" },
  suspect: { bg: "#fff4e0", fg: "#a5680a", label: (n) => `needs a look: ${n.join("; ")}` },
  lint_error: { bg: "#fdeaea", fg: "#c0392b", label: (n) => `fix before finalizing: ${n.join("; ")}` },
};

export default function ReviewCards({
  video,
  initialSegments,
}: {
  video: VideoRow;
  initialSegments: SegmentWithFrames[];
}) {
  const [cards, setCards] = useState<CardState[]>(initialSegments.map(toCardState));
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  function updateLabel(id: string, value: string) {
    setCards((prev) => prev.map((c) => (c.id === id ? { ...c, draftLabel: value } : c)));
  }

  function violationsFor(card: CardState): Violation[] {
    return lintLabel(card.draftLabel, card.end - card.start);
  }

  function isDirty(card: CardState): boolean {
    return card.draftLabel !== card.baselineLabel;
  }

  async function finalizeDirty() {
    const dirty = cards.filter(isDirty);
    if (dirty.length === 0) {
      setMessage("Nothing edited — edit a label first.");
      setTimeout(() => setMessage(""), 2500);
      return;
    }
    setBusy(true);
    setMessage(`Finalizing ${dirty.length} segment(s)...`);
    try {
      const resp = await fetch("/api/finalize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          videoId: video.id,
          segments: dirty.map((c) => ({ id: c.id, label: c.draftLabel })),
        }),
      });
      if (!resp.ok) {
        throw new Error(`Finalize request failed (${resp.status})`);
      }
      const data: { results: FinalizeResultItem[] } = await resp.json();
      const byId = new Map(data.results.map((r) => [r.id, r]));
      setCards((prev) =>
        prev.map((c) => {
          const r = byId.get(c.id);
          if (!r) return c;
          if (r.verdict === "lint_error") {
            return { ...c, verdict: r.verdict, notes: r.notes };
          }
          return {
            ...c,
            draftLabel: r.label,
            baselineLabel: r.label,
            verdict: r.verdict,
            notes: r.notes,
          };
        })
      );
      setMessage(`Done — ${data.results.length} segment(s) processed.`);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Finalize failed");
    } finally {
      setBusy(false);
      setTimeout(() => setMessage(""), 4000);
    }
  }

  async function copyLabels(withTime: boolean) {
    const lines = [...cards]
      .sort((a, b) => a.segIndex - b.segIndex)
      .map((c) => (withTime ? `${timeStr(c.start, c.end)}\t${c.draftLabel.trim()}` : c.draftLabel.trim()));
    await navigator.clipboard.writeText(lines.join("\n"));
    setMessage(`Copied ${lines.length} labels`);
    setTimeout(() => setMessage(""), 2500);
  }

  return (
    <div>
      <div
        style={{
          position: "sticky",
          top: 0,
          background: "#fff",
          padding: 10,
          borderRadius: 8,
          boxShadow: "0 1px 4px rgba(0,0,0,.15)",
          marginBottom: 14,
          zIndex: 5,
        }}
      >
        <b>{video.name}</b> — edit labels below (rules checked as you type), then:
        <div style={{ marginTop: 8, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button style={btnStyle} onClick={finalizeDirty} disabled={busy}>
            Finalize edited segments
          </button>
          <button style={btnStyle} onClick={() => copyLabels(false)}>
            Copy labels
          </button>
          <button style={btnStyle} onClick={() => copyLabels(true)}>
            Copy time + labels
          </button>
          <span style={{ fontSize: 13, color: "#555" }}>{message}</span>
        </div>
      </div>

      {cards.map((card) => {
        const violations = violationsFor(card);
        const errors = violations.filter((v) => v.severity === "error");
        const needsReview = card.storedFlags.length > 0 || card.storedConfidence < 0.7;
        const verdictStyle = card.verdict ? VERDICT_STYLE[card.verdict] : null;

        return (
          <div
            key={card.id}
            style={{
              background: "#fff",
              borderRadius: 8,
              padding: 12,
              marginBottom: 12,
              boxShadow: "0 1px 3px rgba(0,0,0,.1)",
              borderLeft: `5px solid ${needsReview ? "#e0a800" : "#3cb371"}`,
            }}
          >
            <div style={{ marginBottom: 6 }}>
              <b>#{card.segIndex}</b>{" "}
              <span style={{ color: "#555", fontFamily: "monospace", margin: "0 8px" }}>
                {timeStr(card.start, card.end)}
              </span>
              <span style={{ color: "#777", fontSize: 13 }}>conf {card.storedConfidence.toFixed(2)}</span>
              {isDirty(card) && (
                <span style={{ marginLeft: 8, fontSize: 12, color: "#a5680a" }}>● edited</span>
              )}
            </div>

            {card.frameUrls.length > 0 && (
              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
                {card.frameUrls.map((url, i) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img key={i} src={url} alt="frame" style={{ width: 220, borderRadius: 4 }} />
                ))}
              </div>
            )}

            <div style={{ fontSize: 12.5, color: "#666", margin: "6px 0" }}>
              L: {card.evidence.left_hand ?? ""}
              <br />
              R: {card.evidence.right_hand ?? ""}
            </div>

            {card.storedFlags.length > 0 && (
              <ul style={{ fontSize: 12.5, color: "#b26b00", margin: "4px 0 6px 18px", padding: 0 }}>
                {card.storedFlags.map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
              </ul>
            )}

            <textarea
              value={card.draftLabel}
              onChange={(e) => updateLabel(card.id, e.target.value)}
              rows={2}
              style={{
                width: "100%",
                fontSize: 15,
                fontFamily: "Consolas, monospace",
                padding: 8,
                border: "1px solid #ccc",
                borderRadius: 6,
                boxSizing: "border-box",
              }}
            />
            <div style={{ fontSize: 13, minHeight: 16, marginTop: 4, color: errors.length ? "#c0392b" : "#2e8b57" }}>
              {errors.length > 0
                ? "✗ " + errors.map((v) => v.message).join("  |  ")
                : "✓ passes all rules"}
            </div>

            {verdictStyle && (
              <div
                style={{
                  marginTop: 6,
                  display: "inline-block",
                  padding: "3px 8px",
                  borderRadius: 4,
                  fontSize: 12.5,
                  background: verdictStyle.bg,
                  color: verdictStyle.fg,
                }}
              >
                {verdictStyle.label(card.notes)}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const btnStyle: React.CSSProperties = {
  padding: "8px 14px",
  border: 0,
  borderRadius: 6,
  background: "#2b5fd9",
  color: "#fff",
  fontSize: 14,
  cursor: "pointer",
};
