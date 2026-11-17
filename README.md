# FashionAI

A free, mostly local wardrobe and personal stylist. Photograph your clothes; the app cuts
them out, tags them, and suggests outfits for an occasion, the weather, a style preset, your
build and fit preferences, what you liked before and what you wore recently. A phone app
(installable on an iPhone home screen) shows the wardrobe from anywhere.

All AI runs on your own Windows PC. Nothing is paid: the only online pieces are a free
Supabase project (a private cloud copy of the wardrobe, so the phone works on any network)
and a free static host for the phone app.

> Status (2026-11-17): working and in daily use by its owner. See [TODO.md](TODO.md) for what
> is next and [HANDOFF.md](HANDOFF.md) for the full engineering history, known problems and
> lessons learned.

## Features

**Wardrobe**
- Add photos from the phone (library or camera) or by dropping them in a folder on the PC.
- Each photo is cut out on a transparent background and tagged automatically: category,
  type, colours, pattern, material, fit, formality (1-5), warmth (1-5), seasons, occasions,
  style words and a one-line description.
- Photos of someone *wearing* the item are cut down to the garment itself when the clothing
  model is confident (otherwise the person is kept).
- Edit any tag by hand, re-run the tagger, or remove an item.

**Stylist**
- Ask for outfits for any occasion ("dinner date, 12C, windy") or tap **Dress me for today**.
- 9 style presets: personal, contemporary, minimalist, smart-casual, business, streetwear,
  old-money, athleisure, bold.
- Uses your profile (build, sizes, fit preferences, favourite colours, colours to avoid).
- **Taste:** likes and dislikes re-rank which of your clothes are offered to the stylist.
- **Rotation:** tops and bottoms worn in the last two days sit out when there is an
  alternative; the stylist also sees what you wore in the past week.
- Rules enforced in code, not left to the model: only real items, one bottom / one pair of
  shoes, complete outfits flagged.
- Model "thinking" is **off by default** (about 5 s per answer). Turn on **Think it through**
  for tricky requests (about 30 s of GPU), capped by a token budget and GPU temperature.

**Outfits and the Worn diary**
- History of every suggestion, with **Liked** and **Worn** collections.
- **I wore this** logs the outfit for today.
- **Worn diary:** add a photo of yourself in the day's outfit; the background is removed
  and it is filed by day. View as a **Days** list or a **Months** grid.

**Phone app**
- Expo / React Native, runs in Expo Go, a browser, or as a home-screen web app on iPhone.
- Two connection modes: **Cloud** (sign in; works on any network, the PC processes requests
  when it is on) and **Straight to the PC** (same Wi-Fi, no cloud).

## How it works

```
 phone / browser app ──cloud mode──▶ Supabase: private bucket + tables (row level security)
        │                                       ▲
        └──direct mode (same Wi-Fi)──▶ server.py (FastAPI)   ◀── cloud_sync.py worker
                                          │
          photo ─▶ YOLOv8n (is a person wearing it?) ─▶ rembg U-2-Net / u2net_cloth_seg cutout
                 ─▶ OpenCLIP ViT-B-32 vector ─▶ ChromaDB
                 ─▶ Ollama qwen3.5:4b (vision) JSON tags, schema-enforced ─▶ SQLite
          request ─▶ CLIP text query per category + taste + rotation ─▶ qwen3.5:4b outfits ─▶ checks
```

| Piece | Choice | Runs on |
|---|---|---|
| Person detection | Ultralytics YOLOv8n (COCO) | CPU |
| Background removal | rembg `u2net`; `u2net_cloth_seg` for garments on a person | CPU |
| Embeddings / retrieval | OpenCLIP ViT-B-32 `laion2b_s34b_b79k` + ChromaDB (cosine) | CPU |
| Tagging and styling | Ollama `qwen3.5:4b`, native `/api/chat`, JSON-schema `format` | GPU |
| Data | SQLite (`wardrobe.db`) + image folders | PC |
| API | FastAPI + Uvicorn | PC |
| Cloud copy | Supabase free plan (Postgres, Storage, Auth) | online |
| App | Expo SDK 58, Expo Router, TypeScript, React Native 0.88 | phone / web |

## Repository layout

| Path | What it is |
|---|---|
| `fashion_core.py` | Shared core. **Import first**: pins every model cache to the project drive. Ollama client, CLIP embedder, SQLite schema and helpers, ChromaDB, GPU temperature pacing. |
| `ingest_wardrobe.py` | Photo -> cutout + vector + tags. CLI: `--limit`, `--force`, `--only NAME`, `--list`, `--recut-worn`. |
| `stylist.py` | Retrieval, taste, rotation, outfit composition, presets, outfit boards. CLI for testing. |
| `server.py` | FastAPI backend (`/docs` when running). `--lan`, `--cloud`, `--port`. |
| `cloud_sync.py` | Supabase worker: `check`, `push`, `run`. |
| `cloud/schema.sql` | Supabase bucket, tables and access rules. |
| `check_phase1.py` | Self-test (`--deep` runs every model once). |
| `start.ps1` | Starts the API and the app's dev server. |
| `mobile/` | The Expo app (`src/app` screens, `src/lib` data layer, `public` home-screen assets). |
| `.claude/launch.json` | Dev server configurations used during development. |
| `HANDOFF.md` | Full history, state, known problems, lessons. **Read before changing things.** |
| `TODO.md`, `PHASE1_TESTS_TODO.md` | What is left; detailed test checklists. |
| `DISCOVERY_PLAN.md` | Plan for the next module (Unsplash discovery feed + moodboards). Not built. |

Not in the repository (git-ignored): secrets, the wardrobe data (photos, cutouts, database,
profile), model weights (about 920 MB, downloaded on first run) and the venv.

## Setup (Windows 11, PowerShell)

The project keeps **everything on D:** (code, venv, caches, model weights). Adjust the paths if
you use another drive; `fashion_core.py` follows the folder the code is in.

1. **Prerequisites:** Python 3.13, Node.js, [Ollama](https://ollama.com), Git.
2. **Model for tagging and styling:**
   ```powershell
   $env:OLLAMA_MODELS='D:\Ollama\models'; ollama pull qwen3.5:4b
   ```
   Recommended: set the user environment variable `OLLAMA_KEEP_ALIVE=2m` so the GPU memory is
   freed shortly after use.
3. **Python environment:**
   ```powershell
   cd D:\FashionAI
   python -m venv venv; .\venv\Scripts\Activate.ps1
   $env:PIP_CACHE_DIR='D:\pip_cache'
   python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.15.0 torchvision==0.29.2
   python -m pip install -r requirements.txt
   python check_phase1.py --deep        # downloads ~920 MB of model weights into .\models
   ```
4. **App:**
   ```powershell
   cd D:\FashionAI\mobile
   npm install                          # mobile\.npmrc keeps npm's cache on D:
   ```
5. **Cloud (optional, needed for the phone away from home):** create a free Supabase project,
   run `cloud/schema.sql` in its SQL Editor, add an app login under Authentication -> Users,
   then copy `.supabase.env.example` to `.supabase.env` and `mobile/.env.example` to
   `mobile/.env` and fill them in. Check with `python cloud_sync.py check`.
6. **API key for LAN use (optional):** put any long random string in a file named `.api_key`.

## Use

```powershell
cd D:\FashionAI; .\start.ps1           # API (+ cloud sync) and the app's dev server
```

- App in a browser: http://localhost:8081 · API docs: http://127.0.0.1:8000/docs
- Phone with Expo Go: scan the QR code in the app window (same Wi-Fi).
- Add clothes: **Add photos** in the app, or copy photos into `wardrobe_raw\` and run
  `python ingest_wardrobe.py` **while the server is stopped**, then `python cloud_sync.py push`.

Command-line use without the app:

```powershell
python ingest_wardrobe.py --list
python stylist.py "smart casual dinner, around 15C" --preset smart-casual
python stylist.py "gym session" --retrieve-only     # shows what retrieval picks, no GPU
```

### Install on iPhone (free)

```powershell
cd D:\FashionAI\mobile; npm run build:web          # -> mobile\dist
```

Upload `mobile\dist` to a free static host (for example Netlify: drag the folder onto
app.netlify.com/drop), open the address in Safari, Share -> **Add to Home Screen**, sign in.
Rebuild and upload again after app changes. The home-screen app uses cloud mode only.

## Design rules

- **$0.** Free tiers only; no paid APIs.
- **Light, bursty GPU use** for a laptop's longevity: thinking off unless asked, a token budget
  and a temperature limit when it is on, pauses between photos when the GPU runs hot.
- **Nothing on C:.** All caches are pinned to the project drive.
- **Secrets stay in local files** (`.supabase.env`, `.api_key`), never in the app or in git.
  The phone app only ever holds the Supabase *publishable* key; every table and the bucket
  only answer to the signed-in owner.

## Known limitations

Short version (details and fixes in [HANDOFF.md](HANDOFF.md#8-known-problems-and-gaps-to-fix)):

- PC-to-cloud sync is partial: changes made outside the app reach the cloud only via
  `cloud_sync.py push`, which never deletes cloud rows.
- The tagger makes mistakes on real photos; check the tags.
- About half of on-body photos still show the person (the clothing model is not confident).
- Only one process may write the vector database at a time: stop the server before running
  `ingest_wardrobe.py`.

## Credits

Ultralytics YOLOv8, rembg / U-2-Net, OpenCLIP (LAION), ChromaDB, Ollama and Qwen, FastAPI,
Expo, Supabase. Built with Claude Code.
