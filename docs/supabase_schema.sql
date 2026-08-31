-- Automatic Video Editor - Supabase schema
-- Run this file in Supabase SQL Editor.

create extension if not exists pgcrypto;

do $$ begin
  create type public.job_status as enum ('queued', 'downloading', 'transcribing', 'summarizing', 'editing', 'completed', 'failed');
exception when duplicate_object then null;
end $$;

do $$ begin
  create type public.transcript_source as enum ('youtube_caption', 'whisper', 'manual');
exception when duplicate_object then null;
end $$;

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  created_at timestamptz not null default now()
);

create table if not exists public.videos (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source_url text not null,
  title text,
  channel_name text,
  duration_sec integer check (duration_sec is null or duration_sec >= 0),
  storage_path text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists public.processing_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  video_id uuid references public.videos(id) on delete cascade,
  kind text not null check (kind in ('import', 'transcribe', 'summarize', 'edit')),
  status public.job_status not null default 'queued',
  progress smallint not null default 0 check (progress between 0 and 100),
  error_message text,
  started_at timestamptz,
  completed_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.transcripts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  video_id uuid not null references public.videos(id) on delete cascade,
  language text not null default 'ko',
  source public.transcript_source not null,
  content text,
  segments jsonb not null default '[]'::jsonb,
  storage_path text,
  created_at timestamptz not null default now()
);

create table if not exists public.video_comments (
  id uuid primary key default gen_random_uuid(),
  video_id uuid not null references public.videos(id) on delete cascade,
  external_id text,
  author_name text,
  content text not null,
  timestamp_ms bigint,
  created_at timestamptz not null default now()
);

create table if not exists public.video_segments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  video_id uuid not null references public.videos(id) on delete cascade,
  transcript_id uuid references public.transcripts(id) on delete set null,
  segment_index integer not null check (segment_index >= 0),
  start_ms bigint not null check (start_ms >= 0),
  end_ms bigint not null check (end_ms > start_ms),
  content text not null default '',
  llm_score numeric(5, 4) check (llm_score is null or llm_score between 0 and 1),
  llm_score_version text,
  comment_timestamp_count integer not null default 0 check (comment_timestamp_count >= 0),
  comment_density numeric(12, 6) not null default 0 check (comment_density >= 0),
  average_volume_dbfs numeric(7, 3),
  final_score numeric(5, 4) check (final_score is null or final_score between 0 and 1),
  created_at timestamptz not null default now(),
  unique (video_id, segment_index)
);

create table if not exists public.edit_jobs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  source_video_id uuid not null references public.videos(id) on delete cascade,
  processing_job_id uuid references public.processing_jobs(id) on delete set null,
  selected_segment_ids uuid[] not null default '{}',
  edit_plan jsonb not null default '{}'::jsonb,
  result_storage_path text,
  status public.job_status not null default 'queued',
  error_message text,
  created_at timestamptz not null default now(),
  completed_at timestamptz
);

create index if not exists videos_user_id_idx on public.videos(user_id);
create index if not exists jobs_user_status_idx on public.processing_jobs(user_id, status);
create index if not exists transcripts_video_id_idx on public.transcripts(video_id);
create index if not exists comments_video_timestamp_idx on public.video_comments(video_id, timestamp_ms);
create index if not exists segments_video_start_idx on public.video_segments(video_id, start_ms);
create index if not exists edit_jobs_user_status_idx on public.edit_jobs(user_id, status);

alter table public.profiles enable row level security;
alter table public.videos enable row level security;
alter table public.processing_jobs enable row level security;
alter table public.transcripts enable row level security;
alter table public.video_comments enable row level security;
alter table public.video_segments enable row level security;
alter table public.edit_jobs enable row level security;

create policy "profiles: own rows" on public.profiles
  for all using (auth.uid() = id) with check (auth.uid() = id);

create policy "videos: own rows" on public.videos
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "processing jobs: own rows" on public.processing_jobs
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "transcripts: own rows" on public.transcripts
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "comments: owner through video" on public.video_comments
  for all using (
    exists (select 1 from public.videos v where v.id = video_id and v.user_id = auth.uid())
  ) with check (
    exists (select 1 from public.videos v where v.id = video_id and v.user_id = auth.uid())
  );

create policy "segments: own rows" on public.video_segments
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

create policy "edit jobs: own rows" on public.edit_jobs
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

insert into storage.buckets (id, name, public)
values ('longform-media', 'longform-media', false)
on conflict (id) do nothing;

create policy "media: own objects" on storage.objects
  for all using (
    bucket_id = 'longform-media'
    and (storage.foldername(name))[1] = auth.uid()::text
  ) with check (
    bucket_id = 'longform-media'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- Keep profiles synchronized with Supabase Auth users.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', new.email))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute procedure public.handle_new_user();
