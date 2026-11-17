# Discovery & Moodboards - plan

Status: **plan only, nothing built yet** (2026-11-17). The code below is a draft written
for this project's stack; it has not been run. Built on request, it goes in as described
in "Build steps" at the end.

## 1. What it is

A Pinterest / Instagram-Explore style feed of fashion photos from Unsplash. You scroll a
two-column masonry grid, tap a photo to see it large, and tap **Save** to put it in a
board (Instagram calls them Collections, Pinterest calls them Boards). A **Boards** page
shows your boards as cover tiles; tapping one shows what you saved in it.

It is separate from the wardrobe on purpose: these are other people's photos used for
inspiration, not your clothes.

## 2. Facts that shape the design

From Unsplash's API documentation (checked 2026-11-17):

| Fact | Consequence |
|---|---|
| Free, but needs an **Access Key** (register an app at unsplash.com/developers) | You create the account; the key lives in a local file like the others |
| **50 requests per hour** in demo mode, 1,000 after Unsplash approves the app | The feed is cached and fetched 30 photos at a time; one request serves a full screen and more |
| Image files (`images.unsplash.com`) do **not** count against the limit | Scrolling and viewing are free; only fetching new pages costs a request |
| Images must be **hotlinked** (use the returned URLs, keep the `ixid` parameter) | Boards store image *addresses* and photo info, never copies of the files |
| Attribution required: photographer and Unsplash, with links | Every enlarged photo shows "Photo by NAME on Unsplash" |
| `GET /photos/:id/download` must be called when an app "downloads" a photo | Called once when a photo is saved to a board (the conservative reading) |
| **No videos** on Unsplash | `media_type` is kept in the schema; videos would need Pexels (free, 200 requests/hour, has a video API). Left out of v1 |

Two facts from this project:

- **The iPhone app is cloud-only** (an https page cannot call the PC's http server), and the
  PC is often off. So the feed must not depend on the PC. Boards must live in the cloud too,
  or the iPhone could not see them.
- The user's rules: $0, nothing on C:, no secrets in the app or in chat.

## 3. Architecture

```
iPhone / browser app
   |  Discover tab: GET feed  ------------------>  feed proxy  ---> api.unsplash.com
   |                                                (holds the Access Key, caches 30 min)
   |  Boards: read/write folders + saved media ---> Supabase tables (cloud mode)
   |                                            or server.py + SQLite (direct mode)
   |  <img> straight from images.unsplash.com (hotlinked, free)
```

The feed proxy exists in two forms that share one contract:

1. **`GET /api/discover` on `server.py`** - used in direct mode (same Wi-Fi), and the
   reference implementation. Key in `D:\FashionAI\.unsplash.env`.
2. **A Supabase Edge Function `discover`** - used in cloud mode, so the feed works while the
   PC is off. Free plan includes Edge Functions. The key is stored as a Supabase secret,
   never in the app.

Recommendation: build (1) first, test it, then deploy (2) by pasting the same logic
(TypeScript) into the Supabase dashboard's Edge Function editor - no new tools on the PC.

Why not call Unsplash straight from the app: the key would ship inside the public web
build, and anyone could use up the 50 requests/hour.

## 4. Requirement 1 - Database schema

Raw SQLite in `fashion_core.py`, matching the rest of the project (it does not use
SQLAlchemy; adding it for two tables is not worth it). Added to `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS moodboard_folders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    cover_image_url TEXT,                     -- NULL = use the most recently saved image
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS saved_media (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id         INTEGER NOT NULL REFERENCES moodboard_folders(id) ON DELETE CASCADE,
    source            TEXT NOT NULL DEFAULT 'unsplash',   -- unsplash | pexels
    source_id         TEXT NOT NULL,                      -- the photo's id at the source
    media_type        TEXT NOT NULL DEFAULT 'image' CHECK (media_type IN ('image', 'video')),
    media_url         TEXT NOT NULL,                      -- hotlinked display URL (urls.regular, ixid kept)
    thumb_url         TEXT,                               -- urls.small, for grids
    source_url        TEXT NOT NULL,                      -- the photo's page on Unsplash
    width             INTEGER,                            -- lets the grid reserve the right height
    height            INTEGER,
    color             TEXT,                               -- average colour: placeholder while loading
    description       TEXT,
    photographer_name TEXT NOT NULL,
    photographer_url  TEXT NOT NULL,
    saved_at          TEXT NOT NULL,
    UNIQUE (folder_id, source, source_id)                 -- saving twice into one board is a no-op
);
CREATE INDEX IF NOT EXISTS idx_saved_media_folder ON saved_media(folder_id, saved_at);
CREATE INDEX IF NOT EXISTS idx_saved_media_source ON saved_media(source, source_id);
```

`PRAGMA foreign_keys = ON` must be set in `open_db()` for the cascade to work (SQLite
leaves it off by default).

The cloud mirror (appended to `cloud/schema.sql`) has the same columns plus `user_id`,
row level security "own rows only" like the other tables, `uuid` ids, and
`unique (user_id, name)` on folders.

## 5. Requirement 2 - Backend endpoints (FastAPI)

New file `discover.py` (keeps `server.py` from growing further), included with
`app.include_router(discover.router)`. Same API-key protection as the rest.

```python
"""discover.py - Discovery feed (Unsplash) and moodboards."""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import fashion_core as fc

UNSPLASH = "https://api.unsplash.com"
QUERIES = [  # rotated so the feed stays varied; a style preset or a search replaces them
    "street style outfit", "menswear outfit", "minimalist fashion", "smart casual outfit",
    "streetwear", "fashion editorial", "layered outfit", "linen summer outfit",
    "tailoring", "denim outfit", "knitwear", "outfit inspiration",
]
CACHE_SECONDS = 30 * 60
_cache: dict[tuple, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def access_key() -> str:
    """From D:\\FashionAI\\.unsplash.env (UNSPLASH_ACCESS_KEY=...). Never logged."""
    ...


def _photo(raw: dict) -> dict:
    """Unsplash JSON -> the app's media shape (only what the feed and boards need)."""
    return {
        "source": "unsplash", "source_id": raw["id"], "media_type": "image",
        "media_url": raw["urls"]["regular"], "thumb_url": raw["urls"]["small"],
        "source_url": raw["links"]["html"] + "?utm_source=fashionai&utm_medium=referral",
        "width": raw["width"], "height": raw["height"], "color": raw.get("color"),
        "description": raw.get("alt_description") or raw.get("description"),
        "photographer_name": raw["user"]["name"],
        "photographer_url": raw["user"]["links"]["html"] + "?utm_source=fashionai&utm_medium=referral",
        "download_location": raw["links"].get("download_location"),
    }


def _search(query: str, page: int) -> list[dict]:
    key = (query, page)
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    reply = requests.get(f"{UNSPLASH}/search/photos", timeout=15,
                         headers={"Authorization": f"Client-ID {access_key()}", "Accept-Version": "v1"},
                         params={"query": query, "page": page, "per_page": 30,
                                 "orientation": "portrait", "content_filter": "high"})
    if reply.status_code == 403:
        raise HTTPException(429, "Unsplash's hourly limit is used up - try again later.")
    reply.raise_for_status()
    photos = [_photo(raw) for raw in reply.json().get("results", [])]
    with _lock:
        _cache[key] = (time.time(), photos)
    return photos


router = APIRouter(prefix="/api", tags=["discover"])  # server.py adds the API-key dependency


@router.get("/discover")
def discover(page: int = 1, q: str | None = None, seed: int = 0) -> dict:
    """A page of fashion photos. Without q, two rotating queries are mixed (seed keeps a
    scroll session stable). Each page costs at most 2 Unsplash requests; repeats hit the cache."""
    rng = random.Random(seed * 1000 + page)
    queries = [q] if q else rng.sample(QUERIES, 2)
    seen, items = set(), []
    for query in queries:
        for photo in _search(query, page):
            if photo["source_id"] not in seen:
                seen.add(photo["source_id"])
                items.append(photo)
    rng.shuffle(items)
    with fc.open_db() as conn:  # mark what is already saved, for the filled bookmark icon
        saved = {row[0] for row in conn.execute("SELECT DISTINCT source_id FROM saved_media")}
    for item in items:
        item["saved"] = item["source_id"] in saved
    return {"page": page, "next_page": page + 1, "items": items}


class NewFolder(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class SaveMedia(BaseModel):
    folder_id: int
    source: str = "unsplash"
    source_id: str
    media_type: str = Field("image", pattern="^(image|video)$")
    media_url: str
    thumb_url: str | None = None
    source_url: str
    width: int | None = None
    height: int | None = None
    color: str | None = None
    description: str | None = None
    photographer_name: str
    photographer_url: str
    download_location: str | None = None


@router.get("/folders")
def list_folders() -> dict:
    """Boards with their item count and cover (the chosen one, or the latest save)."""
    with fc.open_db() as conn:
        rows = conn.execute("""
            SELECT f.*, COUNT(m.id) AS count,
                   COALESCE(f.cover_image_url,
                            (SELECT thumb_url FROM saved_media WHERE folder_id = f.id
                             ORDER BY saved_at DESC LIMIT 1)) AS cover
            FROM moodboard_folders f LEFT JOIN saved_media m ON m.folder_id = f.id
            GROUP BY f.id ORDER BY f.updated_at DESC""").fetchall()
    return {"folders": [dict(row) for row in rows]}


@router.post("/folders", status_code=201)
def create_folder(body: NewFolder) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    with fc.open_db() as conn:
        try:
            cur = conn.execute("INSERT INTO moodboard_folders (name, created_at, updated_at) VALUES (?, ?, ?)",
                               (body.name.strip(), now, now))
        except fc.sqlite3.IntegrityError:
            raise HTTPException(409, f'You already have a board called "{body.name.strip()}".') from None
        conn.commit()
        return dict(conn.execute("SELECT * FROM moodboard_folders WHERE id = ?", (cur.lastrowid,)).fetchone())


@router.get("/folders/{folder_id}")
def get_folder(folder_id: int) -> dict:
    with fc.open_db() as conn:
        folder = conn.execute("SELECT * FROM moodboard_folders WHERE id = ?", (folder_id,)).fetchone()
        if folder is None:
            raise HTTPException(404, "No such board.")
        media = conn.execute("SELECT * FROM saved_media WHERE folder_id = ? ORDER BY saved_at DESC",
                             (folder_id,)).fetchall()
    return {"folder": dict(folder), "items": [dict(row) for row in media]}


@router.post("/save-media", status_code=201)
def save_media(body: SaveMedia) -> dict:
    """Save a photo into a board. Saving it again into the same board changes nothing."""
    now = datetime.now().isoformat(timespec="seconds")
    data = body.model_dump(exclude={"download_location"})
    with fc.open_db() as conn:
        if not conn.execute("SELECT 1 FROM moodboard_folders WHERE id = ?", (body.folder_id,)).fetchone():
            raise HTTPException(404, "No such board.")
        cur = conn.execute(f"""INSERT OR IGNORE INTO saved_media ({', '.join(data)}, saved_at)
                               VALUES ({', '.join('?' * len(data))}, ?)""", (*data.values(), now))
        conn.execute("UPDATE moodboard_folders SET updated_at = ? WHERE id = ?", (now, body.folder_id))
        conn.commit()
    if cur.rowcount and body.download_location:  # Unsplash asks to be told when a photo is "downloaded"
        threading.Thread(target=_track_download, args=(body.download_location,), daemon=True).start()
    return {"saved": True, "duplicate": cur.rowcount == 0}


def _track_download(url: str) -> None:
    try:
        requests.get(url, headers={"Authorization": f"Client-ID {access_key()}"}, timeout=10)
    except requests.RequestException:
        pass  # counting only; never block a save on it
```

Also planned, small: `DELETE /api/saved-media/{id}` (unsave), `PATCH /api/folders/{id}`
(rename, set cover), `DELETE /api/folders/{id}` (with its saved items), and
`GET /api/saved?source_id=...` (which boards hold a photo, for the Save sheet's ticks).

## 6. Requirement 3 - Frontend screens (Expo / React Native)

Placement: a new **Discover** tab (between Stylist and Outfits) with a two-way switch at the
top: **For you** (the feed) and **Boards** (the library). Five tabs stay readable on a phone;
a sixth would not.

Library choices:
- **Masonry**: no extra package. `react-native-masonry-list` is unmaintained and has native
  parts that may not fit Expo SDK 58 or the web build. A two-column layout that places each
  photo in the shorter column (heights known from `width`/`height`) is about 40 lines, works on
  iPhone, Android and web, and never jumps while images load. (FlashList's masonry mode is the
  upgrade if the feed ever gets slow; check its Expo SDK 58 support first.)
- **Bottom sheet**: React Native's own `Modal` with `animationType="slide"`. `@gorhom/bottom-sheet`
  needs Reanimated and gesture-handler; not worth it for one sheet.
- **Images**: `expo-image` (an Expo module, has `placeholder` + caching) if it is in SDK 58's
  bundled modules; otherwise `Image` with the photo's average `color` as background.

New `Backend` methods (both modes): `discover(page, q?, seed)`, `folders()`, `createFolder(name)`,
`folder(id)`, `saveMedia(media, folderId)`, `unsave(id)`. Cloud mode calls the Edge Function for
the feed and the Supabase tables for boards.

### `src/components/Masonry.tsx`

```tsx
import { Image, Pressable, ScrollView, StyleSheet, View, type NativeScrollEvent } from 'react-native';
import type { Media } from '@/lib/types';
import { space } from '@/theme';

type Props = { items: Media[]; width: number; onPress: (m: Media) => void; onEndReached?: () => void };

/** Two columns; each photo goes into the currently shorter one, so the grid stays balanced. */
export function Masonry({ items, width, onPress, onEndReached }: Props) {
  const column = (width - space.sm) / 2;
  const cols: Media[][] = [[], []];
  const heights = [0, 0];
  for (const item of items) {
    const h = column * ((item.height ?? 4) / (item.width ?? 3));
    const target = heights[0] <= heights[1] ? 0 : 1;
    cols[target].push(item);
    heights[target] += h + space.sm;
  }
  const nearEnd = ({ layoutMeasurement, contentOffset, contentSize }: NativeScrollEvent) =>
    layoutMeasurement.height + contentOffset.y > contentSize.height - 800;
  return (
    <ScrollView onScroll={(e) => nearEnd(e.nativeEvent) && onEndReached?.()} scrollEventThrottle={250}>
      <View style={styles.row}>
        {cols.map((col, i) => (
          <View key={i} style={{ width: column, gap: space.sm }}>
            {col.map((item) => (
              <Pressable key={item.source_id} onPress={() => onPress(item)} accessibilityRole="imagebutton"
                accessibilityLabel={item.description ?? `Photo by ${item.photographer_name}`}>
                <Image source={{ uri: item.thumb_url ?? item.media_url }}
                  style={{ width: column, height: column * ((item.height ?? 4) / (item.width ?? 3)),
                           borderRadius: 12, backgroundColor: item.color ?? '#ddd' }} />
              </Pressable>
            ))}
          </View>
        ))}
      </View>
    </ScrollView>
  );
}
const styles = StyleSheet.create({ row: { flexDirection: 'row', gap: space.sm, padding: space.sm } });
```

### `src/app/(tabs)/discover.tsx` (feed + boards switch)

```tsx
export default function DiscoverScreen() {
  const { api } = useSettings();
  const { width } = useWindowDimensions();
  const [mode, setMode] = useState<'feed' | 'boards'>('feed');
  const [items, setItems] = useState<Media[]>([]);
  const [page, setPage] = useState(1);
  const [seed] = useState(() => Math.floor(Math.random() * 1e6)); // one shuffle per visit
  const [open, setOpen] = useState<Media | null>(null);           // photo shown in the viewer
  const loading = useRef(false);

  const loadMore = useCallback(async () => {
    if (loading.current) return;
    loading.current = true;
    try {
      const data = await api.discover(page, undefined, seed);
      setItems((cur) => [...cur, ...data.items.filter((n) => !cur.some((c) => c.source_id === n.source_id))]);
      setPage(data.next_page);
    } finally { loading.current = false; }
  }, [api, page, seed]);

  useEffect(() => { loadMore(); }, []); // first page

  return (
    <Page>
      <SegmentedChips value={mode} onChange={setMode} options={[['feed', 'For you'], ['boards', 'Boards']]} />
      {mode === 'feed'
        ? <Masonry items={items} width={Math.min(width, MAX_WIDTH)} onPress={setOpen} onEndReached={loadMore} />
        : <BoardsLibrary />}
      <PhotoViewer media={open} onClose={() => setOpen(null)} />
    </Page>
  );
}
```

### `src/components/PhotoViewer.tsx` (tap a photo)

Full-screen `Modal` with the photo at `media_url`, **"Photo by NAME on Unsplash"** (both
names are links: `Linking.openURL(photographer_url)` / `source_url`), and a **Save** button
(bookmark icon, filled when `saved`) that opens the Save sheet.

### `src/components/SaveSheet.tsx` (Instagram-style save)

```tsx
/** Slides up from the bottom: your boards (tap to save, tick when already in it),
    and "New board" which asks for a name, creates it and saves into it in one go. */
export function SaveSheet({ media, visible, onClose, onSaved }: Props) {
  const { api } = useSettings();
  const [folders, setFolders] = useState<Folder[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  useEffect(() => { if (visible) api.folders().then((d) => setFolders(d.folders)); }, [visible]);

  async function saveTo(folderId: number | string) {
    await api.saveMedia(media, folderId);
    onSaved(folderId);
    onClose();
  }
  async function createAndSave() {
    const folder = await api.createFolder(name.trim());
    await saveTo(folder.id);
  }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose} />
      <View style={styles.sheet}>
        <Text style={styles.title}>Save to board</Text>
        <FlatList data={folders} keyExtractor={(f) => String(f.id)} horizontal
          renderItem={({ item }) => (
            <Pressable onPress={() => saveTo(item.id)} style={styles.board}>
              <Image source={{ uri: item.cover ?? undefined }} style={styles.cover} />
              <Text numberOfLines={1}>{item.name}</Text>
            </Pressable>
          )} />
        {creating ? (
          <View style={styles.newRow}>
            <TextInput value={name} onChangeText={setName} placeholder="Board name" autoFocus style={ui.input} />
            <Button label="Create and save" onPress={createAndSave} disabled={!name.trim()} />
          </View>
        ) : (
          <Button label="New board" icon="add" kind="secondary" onPress={() => setCreating(true)} />
        )}
      </View>
    </Modal>
  );
}
```

### `src/components/BoardsLibrary.tsx` + `src/app/board/[id].tsx`

The library is a two-column grid of board tiles: cover image (the latest save unless one is
chosen), name, item count, plus a "New board" tile. Tapping a tile opens `board/[id]`, which
reuses `Masonry` on the saved items. Long-press a saved photo: **Remove from board** /
**Use as cover**. Board screen header menu: **Rename**, **Delete board**.

## 7. Build steps (when you say go)

1. **You**: create a free Unsplash developer account, register an app ("FashionAI", demo),
   and put its Access Key in `D:\FashionAI\.unsplash.env` as `UNSPLASH_ACCESS_KEY=...`.
   Do not paste it in chat.
2. SQLite tables + `discover.py` + router in `server.py`; tests with a throw-away data folder
   and a mocked Unsplash (no real requests spent on tests), then 2-3 real requests.
3. App: types, `Backend` methods (direct mode), Masonry, Discover tab, viewer, Save sheet,
   Boards library, board screen. Typecheck + browser check.
4. Cloud: tables in `cloud/schema.sql` (run in the SQL Editor), Edge Function `discover`
   (pasted in the dashboard, key added as a Supabase secret), cloud `Backend` methods.
5. Rebuild the web app; you redeploy `mobile\dist` on Netlify.

Rough size: 2 and 3 are one session each; 4 is a session plus your two dashboard steps.

## 8. Open questions for you

1. **Unsplash only, or Pexels too?** Pexels adds videos and a higher limit (200 / hour) but
   means a second account and key. Recommendation: Unsplash only for v1.
2. **Should boards feed the stylist?** Later, saved photos could be compared (with the CLIP
   model already on the PC) to your wardrobe: "find pieces like this in my wardrobe", or a
   board used as a style direction for outfits. Not in v1, but the schema allows it.
3. **Apply for Unsplash production access** (1,000 requests/hour)? Not needed for one person;
   the 30-minute cache keeps normal use well under 50 an hour.
