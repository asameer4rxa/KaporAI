// Shapes shared by both ways the app can reach the wardrobe:
//   direct - straight to server.py on the PC (same Wi-Fi)
//   cloud  - through the private Supabase copy (any network; the PC answers when it is on)

export type Category = 'top' | 'bottom' | 'one_piece' | 'outerwear' | 'shoes' | 'accessory';

// Keep these lists in step with fashion_core.py / ingest_wardrobe.py.
export const CATEGORIES: Category[] = ['top', 'bottom', 'one_piece', 'outerwear', 'shoes', 'accessory'];
export const CATEGORY_LABELS: Record<Category, string> = {
  top: 'Tops',
  bottom: 'Bottoms',
  one_piece: 'One-pieces',
  outerwear: 'Outerwear',
  shoes: 'Shoes',
  accessory: 'Accessories',
};
export const PATTERNS = ['solid', 'striped', 'checked', 'floral', 'graphic', 'printed', 'textured', 'colorblock', 'other'];
export const FITS = ['slim', 'regular', 'relaxed', 'oversized', 'unknown'];
export const SEASONS = ['spring', 'summer', 'autumn', 'winter'];

export type Item = {
  id: string;
  category: Category;
  subcategory: string;
  primary_color: string;
  secondary_colors: string[];
  pattern: string;
  material: string;
  fit: string;
  formality: number;
  warmth: number;
  seasons: string[];
  occasions: string[];
  style_tags: string[];
  description: string;
  worn_photo: boolean;
  bg_removed: boolean;
  tagger_model: string;
  created_at: string;
  /** direct: a path on the PC server; cloud: a complete signed address */
  cutout_url: string;
  /** small preview (cloud only) - use it in grids when present */
  thumb_url?: string;
  photo_url: string | null;
  /** how many days this item was logged as worn, and the last such day (YYYY-MM-DD) */
  wear_count?: number;
  last_worn?: string | null;
};

export type ItemPatch = Partial<
  Pick<Item, 'category' | 'subcategory' | 'primary_color' | 'pattern' | 'material' | 'fit' | 'formality' | 'warmth' | 'seasons' | 'description'>
>;

export type Outfit = {
  /** a number on the PC server, a uuid in the cloud */
  id: number | string | null;
  name: string;
  why: string;
  tips: string;
  liked: boolean | null;
  /** the day you last marked this outfit as worn (YYYY-MM-DD) */
  worn_on?: string | null;
  items: Item[];
  // only on fresh recommendations
  complete?: boolean;
  missing?: string[];
  // only in the history
  occasion?: string;
  preset?: string;
  created_at?: string;
  thought?: boolean;
};

export type Recommendation = {
  occasion: string;
  preset: string;
  brief: string;
  wardrobe_gap: string;
  seconds: number;
  thinking: { used: boolean; tokens: number; cut_short: string };
  outfits: Outfit[];
};

export type Preset = { name: string; brief: string };

export type Health = {
  status: string;
  models_ready: boolean;
  models_error: string | null;
  ollama: { ok: boolean; model: string; error?: string };
  items: number;
  outfits: number;
  gpu_temp_c: number | null;
};

/** queued = the photo is safely in the cloud but the PC has not processed it yet */
export type UploadResult = { duplicate: boolean; item?: Item; queued?: boolean };

export type Profile = Record<string, unknown>;

export type Uploadable = { uri: string; name: string };

export type RecommendOptions = {
  occasion: string;
  preset: string;
  outfits: number;
  /** Opt-in reasoning. Off by default: it costs about 30 s of full GPU load on the PC. */
  think: boolean;
};

/** One day in the worn diary: an outfit you marked as worn and / or a photo of you in it. */
export type WornEntry = {
  id: string;
  worn_on: string; // YYYY-MM-DD
  has_photo: boolean;
  /** direct: a path on the PC server; cloud: a complete signed address */
  photo_url: string | null;
  outfit: Outfit | null;
};

/** Is the PC (which does all the AI work) reachable right now? */
export type PcStatus = { online: boolean; detail: string };

/** Everything the screens need, whichever way the wardrobe is reached. */
export interface Backend {
  kind: 'direct' | 'cloud';
  /** Turns an item's cutout_url / thumb_url into something an <Image> can load. */
  mediaUrl(path: string | null | undefined): string | undefined;
  status(): Promise<PcStatus>;
  items(): Promise<{ count: number; items: Item[] }>;
  item(id: string): Promise<Item>;
  editItem(id: string, patch: ItemPatch): Promise<Item>;
  retagItem(id: string): Promise<Item>;
  deleteItem(id: string): Promise<unknown>;
  upload(photo: Uploadable): Promise<UploadResult>;
  presets(): Promise<{ default: string; presets: Preset[] }>;
  profile(): Promise<Profile>;
  saveProfile(profile: Profile): Promise<Profile>;
  recommend(options: RecommendOptions): Promise<Recommendation>;
  outfits(liked?: boolean): Promise<{ count: number; outfits: Outfit[] }>;
  feedback(outfitId: number | string, liked: boolean | null): Promise<unknown>;
  /** "I wore this today": logs a wear for every item in the outfit. Returns the day (YYYY-MM-DD). */
  wearOutfit(outfitId: number | string): Promise<string>;
  /** The worn diary, newest day first. */
  wornDiary(): Promise<WornEntry[]>;
  /** Add a photo of you in an outfit (background removed on the PC). queued = the PC has not done it yet. */
  addWornPhoto(photo: Uploadable, outfitId?: number | string | null): Promise<{ queued?: boolean }>;
  deleteWornPhoto(entryId: string): Promise<unknown>;
}
