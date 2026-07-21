export interface VideoRow {
  id: string;
  name: string;
  folder_name: string;
  duration_seconds: number | null;
  task_summary: string | null;
  environment: string | null;
  hands_overview: string | null;
  objects: { name: string; descriptors?: string; role?: string }[];
  video_notes: string | null;
  cost_summary: string | null;
  cost_usd: number | null;
  cost_detail: { total_usd?: number; calls?: number; input_tokens?: number; output_tokens?: number;
                 by_pass?: Record<string, { calls: number; in: number; out: number; usd: number }> } | null;
  label_accuracy: { exact: number; n: number; mean_f1: number } | null;
  segmentation_accuracy: { n_gold: number; n_proposed: number; count_delta: number; mean_err: number;
                            median_err: number; max_err: number; recall_at_tol: number; precision_at_tol: number } | null;
  pushed_at: string;
  created_at: string;
}

export interface SegmentRow {
  id: string;
  video_id: string;
  seg_index: number;
  start_seconds: number;
  end_seconds: number;
  label: string;
  original_label: string;
  confidence: number;
  flags: string[];
  evidence: { left_hand?: string; right_hand?: string; boundary_note?: string; uncertain_about?: string };
  frame_paths: string[];
  edited: boolean;
  finalize_verdict: "ok" | "revise" | "suspect" | "lint_error" | "unchanged" | null;
  finalize_notes: string[];
  finalized_at: string | null;
}

export interface KnowledgeRow {
  key: "rulebook" | "vocabulary";
  content: string;
  updated_at: string;
}

/** What the review page passes down to the client component — segment + already-signed frame URLs. */
export interface SegmentWithFrames extends SegmentRow {
  frameUrls: string[];
}

export interface FinalizeRequestSegment {
  id: string;
  label: string;
}

export interface FinalizeResultItem {
  id: string;
  verdict: "ok" | "revise" | "suspect" | "lint_error" | "unchanged";
  label: string;
  notes: string[];
}
