# FashionAI - handoff for the next engineer (human or LLM)

Written 2026-11-16. Read this first, then `TODO.md` and `PHASE1_TESTS_TODO.md`.
Everything below was true when written; section 5 gives one-line commands to re-verify it.

## 1. What this is

A free ($0) personal wardrobe and outfit recommender for one user.
Photos of the user's clothes -> background-removed cutouts + AI tags -> a stylist that
suggests outfits for an occasion, a style preset, the user's profile, their likes and what
they wore recently. A phone app shows the wardrobe and talks to the stylist.

All AI runs on the user's Windows laptop. A private Supabase project holds a cloud copy of
the wardrobe so the phone works on any network.

## 2. Rules the user set (do not break these)

1. **Everything on D:.** Code, venv, npm cache, model weights and every library cache.
   C: must stay clean. `fashion_core.py`, `mobile/.npmrc`, `mobile/scripts/expo.js` and
   `mobile/metro.config.js` enforce this; keep using them.
2. **$0.** No paid services. Free cloud storage for the wardrobe is allowed (the user chose
   Supabase's free plan); a paid Apple developer account is not.
3. **GPU load light and bursty** (thermals / longevity). Model "thinking" is OFF everywhere
   by default and only used when the user opts in ("only use by choice and for complex
   design tasks"). Never add auto-detection that turns it on.
4. **Windows 11 + PowerShell 5.1** for every command given to the user.
5. **Never print or ask for secrets.** They live in `D:\FashionAI\.supabase.env`
   (Supabase secret key) and `D:\FashionAI\.api_key` (local API key). Read them from code,
   never echo them, never ask the user to paste them into a chat.
6. The user wants to be guided step by step with exact commands and complete files, and
   wants a summary before big steps. They are not an expert: explain plainly.

## 3. Machine facts

- Windows 11, NVIDIA RTX 5060 Laptop GPU (8 GB), AMD Radeon 780M. C: has about 19 GB free.
- Python 3.13 venv: `D:\FashionAI\venv\Scripts\python.exe`. torch is the **CPU-only** build,
  so YOLO / U-2-Net / CLIP run on CPU (2-3 s per photo). Only Ollama uses the GPU.
- Ollama 0.40.16 at `D:\Ollama\ollama.exe`, model `qwen3.5:4b` (vision + thinking),
  `OLLAMA_KEEP_ALIVE=2m`. Cold model load 10-40 s; warm tagging call about 2.5 s.
- Node 26 at `D:\Node.js`. Git 2.56 is Portable Git at `D:\Git\PortableGit\cmd\git.exe`
  (not on PATH). Repository: https://github.com/asameer4rxa/KaporAI (public, branch `main`);
  commits use the GitHub private address `asameer4rxa@users.noreply.github.com`.
  Before every commit, check `git diff --cached` contains no secrets or wardrobe data.
- User-level env vars already set: `PIP_CACHE_DIR=D:\pip_cache`, `HF_HOME`, `TORCH_HOME`,
  `U2NET_HOME`, `REMBG_HOME`, `OLLAMA_MODELS`. An agent's shell may not inherit them:
  set `$env:PIP_CACHE_DIR='D:\pip_cache'` / `$env:npm_config_cache='D:\npm_cache'` per command.
- The Wi-Fi is a shared network (Windows "Public" profile), which may block devices from
  reaching each other. The PC's address changes; `start.ps1` prints the current one.
  Firewall already allows Python and Node inbound.

## 4. Architecture and file map

```
phone app (Expo / web)  <-- cloud mode -->  Supabase (private bucket + tables)
        |                                          ^
        +---- direct mode (same Wi-Fi) ---> server.py (FastAPI, port 8000)
                                               |  cloud_sync.py worker (inside server with --cloud)
                                               v
                       ingest pipeline + stylist  ->  SQLite + ChromaDB + image folders
                                               |
                                            Ollama qwen3.5:4b
```

Backend (`D:\FashionAI`):

| File | Role |
|---|---|
| `fashion_core.py` | Import FIRST. Pins caches to D:, paths, Ollama client (streamed, `think` flag, JSON-schema `format`), GPU temperature pacing, CLIP embedder, SQLite schema + helpers (items, outfits, wears), ChromaDB collection, `store_item` / `remove_item` / `log_wear`. |
| `ingest_wardrobe.py` | `Pipeline`: YOLOv8n person crop -> rembg `u2net` cutout -> OpenCLIP ViT-B-32 (`laion2b_s34b_b79k`) vector -> qwen tags (`think: false`, schema-enforced). CLI: `--limit`, `--force`, `--only NAME`, `--list`, `--ignore-server`. |
| `stylist.py` | CLIP text retrieval per category, taste re-ranking from likes, wear rotation, `compose_outfits` (budgeted opt-in thinking), 9 style presets, profile, outfit board PNG. CLI flags `--think`, `--think-budget`, `--retrieve-only`, `--show-candidates`. |
| `server.py` | FastAPI. `/api/health, items, items/{id} (GET/PATCH/DELETE), items/{id}/retag, presets, profile, outfits/recommend, outfits, outfits/{id}/feedback, outfits/{id}/worn`, `/media/...`. Flags `--lan`, `--cloud`, `--port`. Key from `.api_key`. |
| `cloud_sync.py` | Supabase worker: `check`, `push`, `run`. Handles inbox uploads, edit / retag / delete flags, outfit requests, likes, wear, profile, heartbeat. |
| `cloud/schema.sql` | Supabase bucket, tables, row level security, grants. Idempotent. |
| `check_phase1.py` | Self-test (`--deep` runs every model once). |
| `start.ps1` | Starts API (`--lan --cloud`) and the Expo dev server in two windows. |
| `wardrobe.db`, `chroma_db/`, `wardrobe_raw/`, `wardrobe_cutouts/`, `wardrobe_removed/`, `outfits/`, `user_profile.json` | Data. `FASHIONAI_DATA_DIR` relocates all of it (use for tests). |
| `models/` | Weights and caches (about 750 MB). |
| `.claude/launch.json` | Preview configs: `fashionai-api`, `fashionai-api-lan` (`--lan --cloud`), `fashionai-api-test` (port 8001, throw-away data), `fashionai-web-build` (8090), `fashionai-app` (Expo, 8081). |

App (`D:\FashionAI\mobile`, Expo SDK 58, Expo Router, TypeScript, React Native 0.88):

| File | Role |
|---|---|
| `src/lib/types.ts` | Shared types and the `Backend` interface both modes implement. |
| `src/lib/api.ts` | Direct backend (server.py). |
| `src/lib/cloudApi.ts` | Cloud backend (Supabase): signed image URLs cached 7 days, polling for PC answers. |
| `src/lib/supabase.ts`, `settings.tsx`, `photos.ts` | Client, mode + session context, pick / convert photos to JPEG. |
| `src/app/_layout.tsx`, `sign-in.tsx`, `(tabs)/index|stylist|outfits|settings.tsx`, `item/[id].tsx` | Screens. |
| `src/components/ui.tsx`, `OutfitCard.tsx` | Shared UI, outfit card (like, dislike, "I wore this"). |
| `public/` | Web / home-screen assets: `index.html`, `manifest.json`, icons, `_redirects`. |
| `scripts/expo.js`, `scripts/serve_dist.py` | Expo wrapper (state on D:, telemetry off), local static server for `dist`. |
| `.env` | `EXPO_PUBLIC_SUPABASE_URL`, `EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY` (public values only). |
| `AGENTS.md` | Expo's own note: its APIs change every SDK, read the versioned docs before touching them. Follow it. |

## 5. What was built, in order

1. **Phase 1 - MVP scripts.** `fashion_core.py`, `ingest_wardrobe.py`, `stylist.py`. Verified
   on generated test images. Benchmarks decided the design: schema-constrained JSON with
   `think: false` is fastest (about 2.3 s); thinking on this model runs 2,500+ tokens and
   40-60 s at about 82 C, so it is streamed, capped by `--think-budget` and GPU temperature,
   and the answer is then produced from the partial reasoning notes.
2. **Test helpers.** `check_phase1.py`, `--only`, `--show-candidates`, `--retrieve-only`.
3. **Phase 2 - FastAPI backend.** `server.py`; 32 endpoint checks passed. Outfit history
   with like / dislike. Models load once and stay resident.
4. **Phase 3 - Expo app.** Five screens; verified in a desktop browser on test data.
5. **API key** for LAN use (`.api_key`).
6. **Cloud wardrobe (Supabase).** Schema, worker, app cloud mode with sign-in. The worker
   passed a 15-check end-to-end test against the user's real project. The user signed in
   and confirmed it works.
7. **Phase 4 - daily use.** Wear log + rotation (enforced in retrieval, not left to the
   model), taste re-ranking (per category, mean-centred CLIP vectors), "Today" shortcut,
   `start.ps1`.
8. **Real data.** 42 photos ingested, then one more through the app. Two wrong tags were
   corrected by hand.
9. **Installable iPhone version.** Home-screen web app: `npm run build:web` -> `mobile/dist`
   (2 MB). The user published it on Netlify and confirmed it works.
10. **Garment-only cutouts for worn photos** (2026-11-17). `GarmentSegmenter` in
    `ingest_wardrobe.py` uses rembg `u2net_cloth_seg`, picks the mask by the item's category,
    and is only applied when the removed part contains skin (otherwise it shreds product shots
    the person detector mistook for a person). 8 of 16 worn items changed; the others keep the
    old cutout. `--recut-worn` redoes them. Backups in `backups\`.
11. **Worn collection** (2026-11-17). Outfits tab -> Worn: Days / Months views, photo of the
    day's outfit with background removed (`worn_photos` table locally and in the cloud,
    `/api/worn`, files in `worn_photos/`), plus outfits marked "I wore this".
12. **Discovery & Moodboards** - planned only, see `DISCOVERY_PLAN.md`.

## 6. State right now

- Local wardrobe: 43 items (8 top, 12 bottom, 5 outerwear, 11 shoes, 7 accessory), 6 outfits,
  1 liked, 0 wears. 15 items come from photos of a person wearing them.
- Update 2026-11-17: 44 items locally and in the cloud. The cloud now has the wear-log columns
  and the `worn_photos` table (added through the SQL Editor; its parser rejects
  `add column if not exists`, so plain statements were used).
- Three servers were left running by the previous agent: API on 8000 (`--lan --cloud`),
  Expo dev server on 8081, static build on 8090. They die when that session ends.

Re-verify:

```powershell
cd D:\FashionAI; .\venv\Scripts\Activate.ps1
python check_phase1.py            # environment, weights, Ollama, local consistency
python cloud_sync.py check        # Supabase settings, tables, bucket
python ingest_wardrobe.py --list  # the wardrobe
cd mobile; npx tsc --noEmit       # app typecheck (set $env:npm_config_cache='D:\npm_cache' first)
```

Run it: `.\start.ps1` (or `python server.py --lan --cloud` plus `cd mobile; npm start`).
App in a browser: http://localhost:8081. API docs: http://127.0.0.1:8000/docs.

## 7. To do

Waiting on the user:

- [ ] Re-run `cloud\schema.sql` in the Supabase SQL Editor (adds wear-log columns; until then
      "I wore this" fails in cloud mode).
- [ ] Publish `mobile\dist` on a free static host (walkthrough given for Netlify drag and
      drop), then on the iPhone: Safari -> Share -> Add to Home Screen -> sign in.
- [ ] Skim the wardrobe and fix wrong tags.
- [ ] Use it for a real day (Today shortcut, like / dislike, "I wore this").
- [ ] Say whether the app runs on the phone at all. Expo Go showed a warning asking to run
      `npx expo login`; it was never confirmed resolved (use `npm run login` in `mobile` so the
      session is stored on D:, or sign out of Expo Go).

Build next (the user's stated order of interest):

- [ ] Finish the iPhone install and fix whatever a real iPhone shows.
- [ ] Garment-only cutouts for worn photos (rembg `u2net_cloth_seg` or a fashion-trained YOLO).
- [ ] Better tagging accuracy (raise `VLM_SIDE` from 512 to 768, re-tag wrong items).
- [ ] Automatic weather for the Today shortcut (needs an online service; ask first).

## 8. Known problems and gaps to fix

Correctness and sync:

1. **PC -> cloud sync is partial.** The worker pushes an item only when it came through the
   app inbox, or after an app edit / retag. Items ingested by the CLI, tag edits made through
   the direct API, deletes made in direct mode, and outfits created in direct mode or by the
   CLI reach the cloud only via `python cloud_sync.py push`, and **push never deletes cloud
   rows**. A proper fix: have the worker reconcile (hash or `updated_at` per item) each loop.
2. **Tagging quality on real photos is unverified.** Of three items inspected closely, two
   were wrong (a shirt on a model tagged as shoes; a sweatshirt tagged as an outerwear
   "pullover hoodie", which the tagger prompt says is a top). Expect more among the 43.
3. **Worn photos** produce a cutout of the whole person (two people in one test photo).
4. Many source photos are small web images (about 300 px).
5. The app re-encodes photos before upload, so the same picture added by the app and by the
   CLI gets two different ids (duplicate detection is by file hash).
6. Signed image URLs are cached for 7 days in the app; a re-ingested cutout with the same
   path may show the old image until the cache entry expires.
7. Default (no-thinking) outfits sometimes break small rules (for example a brown belt with
   black shoes, or no outerwear at 12 C). `--think` did better in the one comparison made.

Never tested by the previous agent (only the user can sign in):

8. Photo upload from the app's "Add photos" button (the user did add one item this way, so
   it works at least once), the camera, HEIC photos.
9. The "I wore this" button in the app, in either mode.
10. Row level security as a signed-in user (only "not signed in sees nothing" was checked).
11. Anything on a real phone; the keyboard covering inputs; safe-area insets.
12. `start.ps1` (syntax-checked only).
13. The published web build: whether Netlify honours `public/_redirects` for deep links.
    If not, switch Expo's web output or add a host-specific fallback.

Operational:

14. **Only one process may write ChromaDB.** A running server keeps a stale view if another
    process ingests ("Error creating hnsw segment reader: Nothing found on disk"). The CLI now
    refuses to ingest while the server is up; restart the server after any out-of-band write.
15. The published web app only supports cloud mode (an https page cannot call the PC's http
    API). Rebuild and republish after every app change.
16. `/media/...` on the local API is not protected by the API key (ids are unguessable).
17. `npm audit` reports 3 moderate issues (not reviewed). `expo-doctor` passes 19 / 20; the
    failing check wrongly says `.expo` is not git-ignored because git is not installed.
18. The venv has unused packages (`boto3`, `psycopg2-binary`); harmless.
19. A native iPhone build (TestFlight) is out of scope unless the user accepts Apple's
    99 USD a year fee.

## 9. Things learned the hard way

- qwen3.5:4b ignores "think briefly" instructions and can burn tokens debating them. Budget
  thinking in code. Put free-text fields before enum fields in JSON schemas so the model
  "looks" before classifying.
- A 4B model also ignored a prompt rule about rotation; anything that must hold is enforced
  in Python (`clean_outfits`, rotation and taste in `retrieve_candidates`).
- Raw CLIP vectors of clothes are all alike; taste scoring only discriminates after
  subtracting the category mean.
- ChromaDB `collection.update()` with a document tries to embed it; this collection has no
  embedder on purpose, so `store_item` re-reads the stored vector and upserts.
- Ultralytics sends usage analytics and auto-installs packages unless told not to; both are
  switched off in `fashion_core.py` / `PersonDetector`.
- Supabase: new keys (`sb_publishable_`, `sb_secret_`) go in the `apikey` header only. The
  secret key bypasses row level security.
- Expo SDK 58: `Stack.Protected` guards the sign-in route; `tabBarIcon` colour is not a plain
  string (use `focused`); Metro's file-map cache folder must be created by hand.
- When testing, set `FASHIONAI_DATA_DIR` to a throw-away folder (`D:\FashionAI\_smoketest`)
  and delete it afterwards. Never put test items in the real wardrobe. For cloud tests, record
  the row counts first and remove only the rows the test created.
- Do not press "I wore this" or like outfits on the user's real data to test; it changes
  their taste and rotation history.

## 10. Where the user's own notes are

- `TODO.md` - the short list. `PHASE1_TESTS_TODO.md` - detailed, mostly unticked checklists
  (real photos, cloud, phone).
- Claude Code memory (if you are Claude Code): `%USERPROFILE%\.claude\projects\D--FashionAI\memory\`.
