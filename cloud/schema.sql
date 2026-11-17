-- FashionAI cloud wardrobe: storage bucket, tables and access rules.
--
-- Run once in the Supabase dashboard:  SQL Editor -> New query -> paste this whole file -> Run.
-- It is safe to run again (nothing is dropped except the access rules it re-creates).
--
-- Who can do what:
--   * You, signed in from the app: read your own wardrobe, edit tags, upload photos into your
--     inbox folder, ask for outfits, like outfits, edit your profile.
--   * The PC worker (cloud_sync.py, using the SECRET key): everything. It is the only thing that
--     creates items and outfits, because only the PC can run the AI models.
--   * Anyone else: nothing. Every table has row level security and the bucket is private.

-- ---------------------------------------------------------------------------
-- Storage: one private bucket, one folder per user
--   <user id>/inbox/<uuid>.jpg     photos uploaded by the app, waiting for the PC
--   <user id>/photos/<item id>.jpg original photos of ingested items
--   <user id>/cutouts/<item id>.png
--   <user id>/thumbs/<item id>.webp small previews for the wardrobe grid
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public)
values ('wardrobe', 'wardrobe', false)
on conflict (id) do nothing;

drop policy if exists "wardrobe: read own files" on storage.objects;
create policy "wardrobe: read own files" on storage.objects
  for select to authenticated
  using (bucket_id = 'wardrobe' and (storage.foldername(name))[1] = (select auth.uid())::text);

drop policy if exists "wardrobe: upload to own inbox" on storage.objects;
create policy "wardrobe: upload to own inbox" on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'wardrobe'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and (storage.foldername(name))[2] = 'inbox'
  );

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- One row per garment. Written by the PC worker; the app may edit tags and set the request flags.
create table if not exists public.items (
  user_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
  id               text not null,                       -- same id as on the PC (hash of the photo)
  category         text not null,
  subcategory      text not null,
  primary_color    text not null,
  secondary_colors jsonb not null default '[]',
  pattern          text not null default 'other',
  material         text not null default 'unknown',
  fit              text not null default 'unknown',
  formality        int  not null default 2 check (formality between 1 and 5),
  warmth           int  not null default 3 check (warmth between 1 and 5),
  seasons          jsonb not null default '[]',
  occasions        jsonb not null default '[]',
  style_tags       jsonb not null default '[]',
  description      text not null default '',
  worn_photo       boolean not null default false,
  bg_removed       boolean not null default true,
  tagger_model     text not null default '',
  photo_path       text,                                -- paths inside the wardrobe bucket
  cutout_path      text,
  thumb_path       text,
  edited_in_app    boolean not null default false,      -- app changed the tags: the PC copies them and clears this
  retag_requested  boolean not null default false,      -- app asks the PC to run the tagger again
  delete_requested boolean not null default false,      -- app asks the PC to remove the item
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  primary key (user_id, id)
);
drop trigger if exists items_touch on public.items;
create trigger items_touch before update on public.items
  for each row execute function public.touch_updated_at();

-- A photo the app uploaded to the inbox; the PC turns it into an item.
create table if not exists public.uploads (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  path        text not null,                            -- object path inside the wardrobe bucket
  file_name   text,
  status      text not null default 'pending'
              check (status in ('pending', 'processing', 'done', 'duplicate', 'failed')),
  item_id     text,
  error       text,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
create index if not exists uploads_pending on public.uploads (user_id, status, created_at);

-- A request for outfits; the PC answers it when it is on.
create table if not exists public.style_requests (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  occasion    text not null check (char_length(occasion) between 2 and 300),
  preset      text not null default 'personal',
  outfits     int  not null default 2 check (outfits between 1 and 4),
  think       boolean not null default false,           -- opt-in reasoning; off unless the app asks
  status      text not null default 'pending'
              check (status in ('pending', 'processing', 'done', 'failed')),
  result      jsonb,                                    -- brief, wardrobe_gap, seconds, thinking
  error       text,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
create index if not exists style_requests_pending on public.style_requests (user_id, status, created_at);

-- Outfits the stylist suggested. Written by the PC; the app sets liked.
create table if not exists public.outfits (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  local_id     bigint,                                  -- the outfit's id in the PC database
  request_id   uuid references public.style_requests (id) on delete set null,
  occasion     text not null,
  preset       text not null,
  name         text not null,
  item_ids     jsonb not null default '[]',
  why          text not null default '',
  tips         text not null default '',
  missing      jsonb not null default '[]',
  thought      boolean not null default false,
  liked        boolean,                                 -- null = no feedback
  liked_synced boolean not null default true,           -- app sets false when it changes liked; the PC copies it
  created_at   timestamptz not null default now(),
  unique (user_id, local_id)
);
create index if not exists outfits_recent on public.outfits (user_id, created_at desc);

-- Build, sizes, fit preferences: the same content as user_profile.json on the PC.
create table if not exists public.profiles (
  user_id    uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  data       jsonb not null default '{}',
  updated_at timestamptz not null default now()
);
drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

-- Heartbeat from the PC so the app can say whether it is on.
create table if not exists public.worker_status (
  user_id   uuid primary key references auth.users (id) on delete cascade,
  last_seen timestamptz not null default now(),
  info      jsonb not null default '{}'
);

-- ---------------------------------------------------------------------------
-- Access rules: a signed-in user sees and changes only their own rows
-- ---------------------------------------------------------------------------
alter table public.items          enable row level security;
alter table public.uploads        enable row level security;
alter table public.style_requests enable row level security;
alter table public.outfits        enable row level security;
alter table public.profiles       enable row level security;
alter table public.worker_status  enable row level security;

drop policy if exists "items: read own" on public.items;
create policy "items: read own" on public.items for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "items: edit own" on public.items;
create policy "items: edit own" on public.items for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "uploads: read own" on public.uploads;
create policy "uploads: read own" on public.uploads for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "uploads: add own" on public.uploads;
create policy "uploads: add own" on public.uploads for insert to authenticated
  with check (user_id = (select auth.uid()));

drop policy if exists "style_requests: read own" on public.style_requests;
create policy "style_requests: read own" on public.style_requests for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "style_requests: add own" on public.style_requests;
create policy "style_requests: add own" on public.style_requests for insert to authenticated
  with check (user_id = (select auth.uid()));

drop policy if exists "outfits: read own" on public.outfits;
create policy "outfits: read own" on public.outfits for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "outfits: rate own" on public.outfits;
create policy "outfits: rate own" on public.outfits for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "profiles: read own" on public.profiles;
create policy "profiles: read own" on public.profiles for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "profiles: add own" on public.profiles;
create policy "profiles: add own" on public.profiles for insert to authenticated
  with check (user_id = (select auth.uid()));
drop policy if exists "profiles: edit own" on public.profiles;
create policy "profiles: edit own" on public.profiles for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "worker_status: read own" on public.worker_status;
create policy "worker_status: read own" on public.worker_status for select to authenticated
  using (user_id = (select auth.uid()));

-- Table privileges (row level security above still applies to "authenticated").
grant usage on schema public to authenticated, service_role;
grant select, update          on public.items          to authenticated;
grant select, insert          on public.uploads        to authenticated;
grant select, insert          on public.style_requests to authenticated;
grant select, update          on public.outfits        to authenticated;
grant select, insert, update  on public.profiles       to authenticated;
grant select                  on public.worker_status  to authenticated;
grant all on public.items, public.uploads, public.style_requests, public.outfits,
             public.profiles, public.worker_status to service_role;

-- ---------------------------------------------------------------------------
-- Added with the wear log (Phase 4). Safe to run on a project that already has the tables.
-- ---------------------------------------------------------------------------
alter table public.items   add column if not exists wear_count  int not null default 0;
alter table public.items   add column if not exists last_worn   date;
alter table public.outfits add column if not exists worn_on     date;                           -- the app sets this for "I wore this"
alter table public.outfits add column if not exists worn_synced boolean not null default true;  -- app sets false; the PC logs the wear

-- ---------------------------------------------------------------------------
-- Added with the worn diary. A photo of you in that day's outfit: the app uploads it to the
-- inbox, the PC removes the background and stores the cutout under <user id>/worn/.
-- ---------------------------------------------------------------------------
create table if not exists public.worn_photos (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
  worn_on          date not null default current_date,
  outfit_id        uuid references public.outfits (id) on delete set null,
  inbox_path       text,                                -- where the app put the photo
  cutout_path      text,                                -- set by the PC when it is done
  thumb_path       text,
  status           text not null default 'pending'
                   check (status in ('pending', 'processing', 'done', 'failed')),
  error            text,
  delete_requested boolean not null default false,      -- app asks the PC to remove the photo
  created_at       timestamptz not null default now()
);
create index if not exists worn_photos_days on public.worn_photos (user_id, worn_on desc);

alter table public.worn_photos enable row level security;
drop policy if exists "worn_photos: read own" on public.worn_photos;
create policy "worn_photos: read own" on public.worn_photos for select to authenticated
  using (user_id = (select auth.uid()));
drop policy if exists "worn_photos: add own" on public.worn_photos;
create policy "worn_photos: add own" on public.worn_photos for insert to authenticated
  with check (user_id = (select auth.uid()));
drop policy if exists "worn_photos: edit own" on public.worn_photos;
create policy "worn_photos: edit own" on public.worn_photos for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
grant select, insert, update on public.worn_photos to authenticated;
grant all on public.worn_photos to service_role;

select 'FashionAI cloud schema is ready' as result;
