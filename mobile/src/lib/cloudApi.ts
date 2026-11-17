import AsyncStorage from '@react-native-async-storage/async-storage';
import type { SupabaseClient } from '@supabase/supabase-js';

import { ApiError, localDay } from './api';
import { BUCKET } from './supabase';
import type {
  Backend, Item, ItemPatch, Outfit, PcStatus, Preset, Profile, Recommendation, RecommendOptions, Uploadable, UploadResult,
  WornEntry,
} from './types';

const SIGNED_URL_SECONDS = 7 * 24 * 60 * 60;
const URL_CACHE_KEY = 'fashionai.signedUrls';
const PC_ONLINE_WITHIN_MS = 90_000; // the PC writes a heartbeat every 30 s
const FALLBACK_PRESETS: Preset[] = [{ name: 'personal', brief: 'Follow your own profile and taste.' }];

type Row = Record<string, any>;
type SignedUrl = { url: string; expires: number };

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const randomName = () => `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}${Math.random().toString(36).slice(2, 10)}`;

function check<T>(result: { data: T; error: { message: string } | null }): T {
  if (result.error) {
    const offline = /fetch|network/i.test(result.error.message);
    throw new ApiError(offline ? 0 : 500, offline ? 'Cannot reach the cloud. Check your internet connection.' : result.error.message);
  }
  return result.data;
}

/**
 * The "cloud" backend: the wardrobe lives in a private Supabase project.
 * The app reads it directly; anything that needs AI (new photos, retagging, outfits) is left
 * there as a request and picked up by cloud_sync.py on the PC.
 */
export function createCloudApi(sb: SupabaseClient): Backend {
  // Signed image addresses are reused for days: a stable address lets the phone cache the picture
  // instead of downloading it again (which would eat into the free plan's download allowance).
  let urlCache: Record<string, SignedUrl> | null = null;

  async function signedUrls(paths: (string | null | undefined)[]): Promise<Map<string, string>> {
    if (urlCache === null) {
      try {
        urlCache = JSON.parse((await AsyncStorage.getItem(URL_CACHE_KEY)) ?? '{}') as Record<string, SignedUrl>;
      } catch {
        urlCache = {};
      }
    }
    const cache = urlCache;
    const soon = Date.now() + 24 * 60 * 60 * 1000;
    const wanted = [...new Set(paths.filter((path): path is string => !!path))];
    const missing = wanted.filter((path) => !cache[path] || cache[path].expires < soon);
    if (missing.length > 0) {
      const signed = check(await sb.storage.from(BUCKET).createSignedUrls(missing, SIGNED_URL_SECONDS));
      for (const entry of signed ?? []) {
        if (entry.path && entry.signedUrl) cache[entry.path] = { url: entry.signedUrl, expires: Date.now() + SIGNED_URL_SECONDS * 1000 };
      }
      AsyncStorage.setItem(URL_CACHE_KEY, JSON.stringify(cache)).catch(() => undefined);
    }
    return new Map(wanted.filter((path) => cache[path]).map((path) => [path, cache[path].url]));
  }

  async function toItems(rows: Row[], withCutouts: boolean): Promise<Item[]> {
    const urls = await signedUrls(rows.flatMap((row) => [row.thumb_path, withCutouts ? row.cutout_path : null]));
    return rows.map((row) => ({
      ...(row as Item),
      thumb_url: urls.get(row.thumb_path),
      cutout_url: urls.get(row.cutout_path) ?? urls.get(row.thumb_path) ?? '',
      photo_url: null,
    }));
  }

  async function userId(): Promise<string> {
    const { data } = await sb.auth.getSession();
    const id = data.session?.user.id;
    if (!id) throw new ApiError(401, 'Please sign in again.');
    return id;
  }

  async function status(): Promise<PcStatus> {
    const row = check(await sb.from('worker_status').select('last_seen').maybeSingle());
    if (!row) return { online: false, detail: 'Your PC has never connected. Start it with "python server.py --cloud".' };
    const age = Date.now() - new Date(row.last_seen).getTime();
    if (age < PC_ONLINE_WITHIN_MS) return { online: true, detail: 'Your PC is on and syncing.' };
    const minutes = Math.round(age / 60_000);
    const ago = minutes < 90 ? `${Math.max(1, minutes)} min` : minutes < 2880 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} days`;
    return { online: false, detail: `Your PC is off (last seen ${ago} ago).` };
  }

  /** Poll one row until `finished` says so; undefined when the PC did not answer in time. */
  async function waitFor(table: string, id: string, finished: (row: Row) => boolean, timeoutMs: number, stepMs = 2000): Promise<Row | undefined> {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      await sleep(stepMs);
      const row = check(await sb.from(table).select('*').eq('id', id).maybeSingle());
      if (row && finished(row)) return row;
    }
    return undefined;
  }

  async function itemsById(ids: string[]): Promise<Map<string, Item>> {
    if (ids.length === 0) return new Map();
    const rows = check(await sb.from('items').select('*').in('id', [...new Set(ids)]).eq('delete_requested', false)) ?? [];
    return new Map((await toItems(rows, false)).map((item) => [item.id, item]));
  }

  async function toOutfits(rows: Row[]): Promise<Outfit[]> {
    const items = await itemsById(rows.flatMap((row) => row.item_ids as string[]));
    return rows.map((row) => ({
      id: row.id,
      name: row.name,
      why: row.why,
      tips: row.tips,
      liked: row.liked,
      worn_on: row.worn_on ?? null,
      occasion: row.occasion,
      preset: row.preset,
      created_at: row.created_at,
      thought: row.thought,
      missing: row.missing ?? [],
      complete: (row.missing ?? []).length === 0,
      items: (row.item_ids as string[]).map((id) => items.get(id)).filter((item): item is Item => !!item),
    }));
  }

  return {
    kind: 'cloud',
    mediaUrl: (path) => (path && path.startsWith('http') ? path : undefined),
    status,

    items: async () => {
      const rows = check(await sb.from('items').select('*').eq('delete_requested', false).order('category').order('subcategory')) ?? [];
      const items = await toItems(rows, false);
      return { count: items.length, items };
    },

    item: async (id) => {
      const row = check(await sb.from('items').select('*').eq('id', id).maybeSingle());
      if (!row) throw new ApiError(404, 'This item is no longer in the wardrobe.');
      return (await toItems([row], true))[0];
    },

    editItem: async (id, patch: ItemPatch) => {
      // The PC copies the edit into its own database on its next sync and clears the flag.
      const row = check(await sb.from('items').update({ ...patch, edited_in_app: true }).eq('id', id).select().single());
      return (await toItems([row], true))[0];
    },

    retagItem: async (id) => {
      check(await sb.from('items').update({ retag_requested: true }).eq('id', id));
      const deadline = Date.now() + 60_000;
      while (Date.now() < deadline) {
        await sleep(2000);
        const row = check(await sb.from('items').select('*').eq('id', id).maybeSingle());
        if (row && !row.retag_requested) return (await toItems([row], true))[0];
      }
      throw new ApiError(504, 'Your PC has not answered yet. The item will be tagged again as soon as it is on.');
    },

    deleteItem: async (id) => {
      check(await sb.from('items').update({ delete_requested: true }).eq('id', id));
    },

    upload: async (photo: Uploadable): Promise<UploadResult> => {
      const owner = await userId();
      const path = `${owner}/inbox/${randomName()}.jpg`;
      const body = await (await fetch(photo.uri)).arrayBuffer();
      check(await sb.storage.from(BUCKET).upload(path, body, { contentType: 'image/jpeg' }));
      const upload = check(await sb.from('uploads').insert({ path, file_name: photo.name }).select().single());

      const done = await waitFor('uploads', upload.id, (row) => ['done', 'duplicate', 'failed'].includes(row.status), 75_000);
      if (!done) return { duplicate: false, queued: true }; // safe in the cloud; the PC will get to it
      if (done.status === 'failed') throw new ApiError(422, done.error || 'The PC could not process this photo.');
      const item = done.item_id ? (await itemsById([done.item_id])).get(done.item_id) : undefined;
      return { duplicate: done.status === 'duplicate', item };
    },

    presets: async () => {
      const row = check(await sb.from('worker_status').select('info').maybeSingle());
      const presets = (row?.info?.presets as Preset[] | undefined) ?? [];
      return { default: 'personal', presets: presets.length > 0 ? presets : FALLBACK_PRESETS };
    },

    profile: async () => {
      const row = check(await sb.from('profiles').select('data').maybeSingle());
      return (row?.data as Profile | undefined) ?? {};
    },

    saveProfile: async (profile: Profile) => {
      const owner = await userId();
      check(await sb.from('profiles').upsert({ user_id: owner, data: profile }, { onConflict: 'user_id' }));
      return profile;
    },

    recommend: async (options: RecommendOptions): Promise<Recommendation> => {
      const pc = await status();
      if (!pc.online) throw new ApiError(503, `${pc.detail} The stylist runs on your PC, so it cannot answer right now.`);
      const request = check(await sb.from('style_requests').insert(options).select().single());
      const answered = await waitFor('style_requests', request.id, (row) => ['done', 'failed'].includes(row.status),
        options.think ? 180_000 : 100_000, 1500);
      if (!answered) throw new ApiError(504, 'Your PC is taking longer than expected. Look under Outfits in a minute.');
      if (answered.status === 'failed') throw new ApiError(502, answered.error || 'The stylist could not answer. Please try again.');

      const rows = check(await sb.from('outfits').select('*').eq('request_id', request.id).order('created_at')) ?? [];
      const result = (answered.result ?? {}) as Partial<Recommendation>;
      return {
        occasion: options.occasion,
        preset: options.preset,
        brief: result.brief ?? '',
        wardrobe_gap: result.wardrobe_gap ?? '',
        seconds: result.seconds ?? 0,
        thinking: result.thinking ?? { used: options.think, tokens: 0, cut_short: '' },
        outfits: await toOutfits(rows),
      };
    },

    outfits: async (liked?: boolean) => {
      let query = sb.from('outfits').select('*').order('created_at', { ascending: false }).limit(50);
      if (liked !== undefined) query = query.eq('liked', liked);
      const outfits = await toOutfits(check(await query) ?? []);
      return { count: outfits.length, outfits };
    },

    feedback: async (outfitId, liked) => {
      // liked_synced: false tells the PC to copy the rating into its own database
      check(await sb.from('outfits').update({ liked, liked_synced: false }).eq('id', outfitId));
    },

    wearOutfit: async (outfitId) => {
      const day = localDay(); // the phone's own date, not UTC
      // worn_synced: false tells the PC to log the wear and update the items' wear counts
      check(await sb.from('outfits').update({ worn_on: day, worn_synced: false }).eq('id', outfitId));
      return day;
    },

    wornDiary: async (): Promise<WornEntry[]> => {
      // A project whose schema predates the diary has no worn_photos table yet: treat that as "no photos".
      const photoResult = await sb.from('worn_photos').select('*').eq('delete_requested', false)
        .neq('status', 'failed').order('worn_on', { ascending: false });
      const photos: Row[] = photoResult.error ? [] : (photoResult.data ?? []);
      const wornRows = check(await sb.from('outfits').select('*').not('worn_on', 'is', null)) ?? [];
      const linkedIds = photos.map((photo: Row) => photo.outfit_id).filter(Boolean);
      const extraRows = linkedIds.length > 0 ? (check(await sb.from('outfits').select('*').in('id', linkedIds)) ?? []) : [];
      const outfits = new Map((await toOutfits([...wornRows, ...extraRows])).map((outfit) => [outfit.id as string, outfit]));
      const urls = await signedUrls(photos.map((photo: Row) => photo.cutout_path));

      const withPhoto = new Set<string>();
      const entries: WornEntry[] = photos.map((photo: Row) => {
        if (photo.outfit_id) withPhoto.add(`${photo.outfit_id}|${photo.worn_on}`);
        return {
          id: photo.id,
          worn_on: photo.worn_on,
          has_photo: true,
          photo_url: urls.get(photo.cutout_path) ?? null, // null while the PC is still working on it
          outfit: (photo.outfit_id && outfits.get(photo.outfit_id)) || null,
        };
      });
      for (const row of wornRows as Row[]) {
        if (!withPhoto.has(`${row.id}|${row.worn_on}`)) {
          entries.push({ id: `outfit-${row.id}`, worn_on: row.worn_on, has_photo: false, photo_url: null, outfit: outfits.get(row.id) ?? null });
        }
      }
      return entries.sort((a, b) => (a.worn_on < b.worn_on ? 1 : a.worn_on > b.worn_on ? -1 : 0));
    },

    addWornPhoto: async (photo: Uploadable, outfitId) => {
      const owner = await userId();
      const path = `${owner}/inbox/${randomName()}.jpg`;
      const body = await (await fetch(photo.uri)).arrayBuffer();
      check(await sb.storage.from(BUCKET).upload(path, body, { contentType: 'image/jpeg' }));
      const row = check(await sb.from('worn_photos')
        .insert({ inbox_path: path, worn_on: localDay(), outfit_id: outfitId ?? null }).select().single());
      const done = await waitFor('worn_photos', row.id, (current) => ['done', 'failed'].includes(current.status), 60_000);
      if (!done) return { queued: true };
      if (done.status === 'failed') throw new ApiError(422, done.error || 'The PC could not process this photo.');
      return {};
    },

    deleteWornPhoto: async (entryId) => {
      check(await sb.from('worn_photos').update({ delete_requested: true }).eq('id', entryId));
    },
  };
}
