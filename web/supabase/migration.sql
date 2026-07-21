-- Atlas Annotator — hosted review schema.
-- Run this once in the Supabase SQL Editor (Project -> SQL Editor -> New query -> paste -> Run).
-- Then create two Storage buckets, both set to PRIVATE (not public):
--   "frames"  — review-frame thumbnails
--   "uploads" — raw videos uploaded through the web UI, awaiting the local watcher

create extension if not exists pgcrypto;

create table if not exists videos (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  folder_name text not null,
  duration_seconds double precision,
  task_summary text,
  environment text,
  hands_overview text,
  objects jsonb default '[]',
  video_notes text,
  cost_summary text,
  cost_usd double precision,
  cost_detail jsonb default '{}',
  label_accuracy jsonb,
  segmentation_accuracy jsonb,
  pushed_at timestamptz default now(),
  created_at timestamptz default now()
);

create table if not exists segments (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references videos(id) on delete cascade,
  seg_index int not null,
  start_seconds double precision not null,
  end_seconds double precision not null,
  label text not null default '',
  original_label text not null default '',
  confidence double precision default 0,
  flags jsonb default '[]',
  evidence jsonb default '{}',
  frame_paths jsonb default '[]',
  edited boolean not null default false,
  finalize_verdict text,
  finalize_notes jsonb default '[]',
  finalized_at timestamptz,
  unique (video_id, seg_index)
);

create table if not exists knowledge (
  key text primary key,
  content text not null default '',
  updated_at timestamptz default now()
);

-- One row per video uploaded through the web UI, from queued through processed.
-- The local watcher (`python -m annotator watch`) polls this table.
create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  status text not null default 'queued',  -- queued | processing | done | error
  original_filename text not null,
  storage_path text not null,             -- path in the "uploads" bucket
  progress_note text,
  error_message text,
  video_id uuid references videos(id) on delete set null,
  created_at timestamptz default now(),
  started_at timestamptz,
  finished_at timestamptz
);

create index if not exists segments_video_id_idx on segments(video_id);
create index if not exists jobs_status_idx on jobs(status);

-- Row Level Security: locked down by default. The local push script and the
-- Vercel app both use the service_role key, which bypasses RLS entirely — so
-- these tables are simply inaccessible to anyone using the public anon key.
alter table videos enable row level security;
alter table segments enable row level security;
alter table knowledge enable row level security;
alter table jobs enable row level security;
-- (No policies are created — service_role bypasses RLS; anon key gets nothing.
--  Browser uploads to the "uploads" bucket are authorized by the signed upload
--  URL/token itself, not by any table or bucket policy.)
