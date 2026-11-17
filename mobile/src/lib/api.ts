import { Platform } from 'react-native';

import type {
  Backend, Health, Item, ItemPatch, Outfit, PcStatus, Preset, Profile, Recommendation, RecommendOptions, Uploadable, UploadResult,
  WornEntry,
} from './types';

/** Today's date on this device as YYYY-MM-DD (not UTC: "today" is the user's day). */
export function localDay(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** The "direct" backend: a small client for server.py on the PC. Create it with the address from Settings. */
export function createApi(baseUrl: string, apiKey: string) {
  const root = baseUrl.trim().replace(/\/+$/, '');

  async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json', ...(init.headers as Record<string, string>) };
    if (apiKey) headers['X-API-Key'] = apiKey;

    let response: Response;
    try {
      response = await fetch(root + path, { ...init, headers });
    } catch {
      throw new ApiError(0, `Cannot reach the server at ${root}. Is it running, and is the address in Settings right?`);
    }
    const text = await response.text();
    let data: any = null;
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      // not JSON: handled below
    }
    if (!response.ok) {
      const detail = data?.detail;
      const message =
        typeof detail === 'string'
          ? detail
          : Array.isArray(detail)
            ? detail.map((entry: { msg?: string }) => entry.msg).filter(Boolean).join('; ')
            : `The server answered with error ${response.status}.`;
      throw new ApiError(response.status, message);
    }
    return data as T;
  }

  const json = (method: string, body: unknown): RequestInit => ({
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const health = () => request<Health>('/api/health');

  const api = {
    kind: 'direct' as const,
    root,
    /** Turns "/media/cutouts/abc.png" from the API into a full image address. */
    mediaUrl: (path: string | null | undefined) => (path ? (path.startsWith('http') ? path : root + path) : undefined),

    health,
    status: async (): Promise<PcStatus> => {
      try {
        const state = await health();
        return { online: true, detail: state.ollama.ok ? 'The PC is on.' : 'The PC is on, but Ollama is not running.' };
      } catch (problem) {
        return { online: false, detail: (problem as Error).message };
      }
    },

    items: () => request<{ count: number; items: Item[] }>('/api/items'),
    item: (id: string) => request<Item>(`/api/items/${id}`),
    editItem: (id: string, patch: ItemPatch) => request<Item>(`/api/items/${id}`, json('PATCH', patch)),
    retagItem: (id: string) => request<Item>(`/api/items/${id}/retag`, { method: 'POST' }),
    deleteItem: (id: string) => request<{ deleted: string }>(`/api/items/${id}`, { method: 'DELETE' }),

    /** Uploads one JPEG. Takes about 6 s, longer when the PC has to load the tagging model first. */
    upload: async (photo: Uploadable) => {
      const form = new FormData();
      if (Platform.OS === 'web') {
        const blob = await (await fetch(photo.uri)).blob();
        form.append('file', blob, photo.name);
      } else {
        // React Native's FormData takes a file description instead of a Blob.
        form.append('file', { uri: photo.uri, name: photo.name, type: 'image/jpeg' } as unknown as Blob);
      }
      return request<UploadResult>('/api/items', { method: 'POST', body: form });
    },

    presets: () => request<{ default: string; presets: Preset[] }>('/api/presets'),
    profile: () => request<Profile>('/api/profile'),
    saveProfile: (profile: Profile) => request<Profile>('/api/profile', json('PUT', profile)),

    recommend: (options: RecommendOptions) => request<Recommendation>('/api/outfits/recommend', json('POST', options)),
    outfits: (liked?: boolean) =>
      request<{ count: number; outfits: Outfit[] }>('/api/outfits?limit=50' + (liked === undefined ? '' : `&liked=${liked}`)),
    feedback: (outfitId: number | string, liked: boolean | null) =>
      request<{ id: number; liked: boolean | null }>(`/api/outfits/${outfitId}/feedback`, json('POST', { liked })),
    wearOutfit: async (outfitId: number | string) =>
      (await request<{ worn_on: string }>(`/api/outfits/${outfitId}/worn`, { method: 'POST' })).worn_on,

    wornDiary: async () => (await request<{ entries: WornEntry[] }>('/api/worn')).entries,
    addWornPhoto: async (photo: Uploadable, outfitId?: number | string | null) => {
      const form = new FormData();
      if (Platform.OS === 'web') {
        form.append('file', await (await fetch(photo.uri)).blob(), photo.name);
      } else {
        form.append('file', { uri: photo.uri, name: photo.name, type: 'image/jpeg' } as unknown as Blob);
      }
      form.append('worn_on', localDay());
      if (outfitId !== undefined && outfitId !== null) form.append('outfit_id', String(outfitId));
      await request<WornEntry>('/api/worn', { method: 'POST', body: form });
      return {};
    },
    deleteWornPhoto: (entryId: string) => request<{ deleted: string }>(`/api/worn/${entryId}`, { method: 'DELETE' }),
  };
  return api satisfies Backend;
}

export type DirectApi = ReturnType<typeof createApi>;
