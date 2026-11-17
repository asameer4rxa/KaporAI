"""
server.py - Phase 2: the FashionAI backend (FastAPI).

Run (PowerShell, in D:\\FashionAI with the venv activated):
    python server.py              # http://127.0.0.1:8000  - this PC only
    python server.py --lan        # also reachable from your phone on the same Wi-Fi (needed for Expo Go)
    python server.py --port 8080
    python server.py --cloud      # also sync with the private Supabase copy (see cloud_sync.py)

Then open http://127.0.0.1:8000/docs to try every endpoint from the browser.

Endpoints (all JSON, all under /api):
    GET    /api/health                      server, model and Ollama status
    GET    /api/items                       the wardrobe (optional ?category=top)
    POST   /api/items                       upload one photo (multipart field "file") -> ingested item
    GET    /api/items/{id}
    PATCH  /api/items/{id}                  correct tags by hand
    POST   /api/items/{id}/retag            ask the tagger again
    DELETE /api/items/{id}                  remove an item (its photo is moved to wardrobe_removed, not erased)
    GET    /api/presets                     style presets
    GET    /api/profile   PUT /api/profile  your build, sizes and fit preferences
    POST   /api/outfits/recommend           {"occasion": "...", "preset": "personal", "outfits": 2, "think": false}
    GET    /api/outfits                     history of suggested outfits
    POST   /api/outfits/{id}/feedback       {"liked": true | false | null} - shapes later suggestions
Images: /media/cutouts/<file> and /media/raw/<file>.

The models are loaded once, in the background, when the server starts (about 20 s)
and stay in memory, so requests do not pay the start-up cost the scripts do.
Model work runs one request at a time, which keeps the GPU load short and predictable.
Thinking is OFF unless a request says "think": true.

API key: put a key in the file .api_key next to this script (or in the FASHIONAI_API_KEY
environment variable) and every /api call must send it in an X-API-Key header - worth
doing with --lan on a network you share. Type the same key into the app's Settings.
Delete the file to open the API again. Images under /media are served by their
unguessable id without the key, so the app can show them.
"""

from __future__ import annotations

import fashion_core as fc  # keep this the first project import: it pins all model caches to this drive

import argparse
import hashlib
import io
import os
import re
import secrets
import socket
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, Form, Header, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

import ingest_wardrobe as iw
import stylist as st

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
# The key comes from the FASHIONAI_API_KEY environment variable or, failing that, from the
# file .api_key next to this script (so it survives restarts without retyping). No key = open API.
API_KEY_FILE = fc.ROOT / ".api_key"
API_KEY = os.environ.get("FASHIONAI_API_KEY", "").strip() or (
    API_KEY_FILE.read_text(encoding="utf-8-sig").strip() if API_KEY_FILE.is_file() else "")

for _folder in (fc.RAW_DIR, fc.CUTOUT_DIR, fc.CHROMA_DIR, fc.WORN_DIR):
    _folder.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Shared state: models are loaded once and used by one request at a time
# ---------------------------------------------------------------------------
class Models:
    def __init__(self) -> None:
        self.lock = threading.Lock()        # one model job at a time
        self._load_lock = threading.Lock()
        self._pipeline: iw.Pipeline | None = None
        self._collection = None
        self.error: str | None = None

    @property
    def ready(self) -> bool:
        return self._pipeline is not None

    def collection(self):
        """The ChromaDB collection (cheap; available before the models have finished loading)."""
        with self._load_lock:
            if self._collection is None:
                self._collection = fc.open_collection()
            return self._collection

    def pipeline(self) -> iw.Pipeline:
        """YOLO + U-2-Net + CLIP, loaded on first use (blocks until ready)."""
        collection = self.collection()
        with self._load_lock:
            if self._pipeline is None:
                self._pipeline = iw.Pipeline(collection=collection)
                self.error = None
            return self._pipeline

    def warm_up(self) -> None:
        try:
            started = time.time()
            self.pipeline()
            print(f"[models] ready in {time.time() - started:.0f} s", flush=True)
        except Exception as exc:  # reported by /api/health; the next request retries
            self.error = f"{type(exc).__name__}: {exc}"
            print(f"[models] failed to load: {self.error}", flush=True)


models = Models()


@asynccontextmanager
async def lifespan(_: FastAPI):
    threading.Thread(target=models.warm_up, name="model-warm-up", daemon=True).start()
    yield


@contextmanager
def db():
    """One SQLite connection per request, opened and closed in the request's own thread."""
    conn = fc.open_db()
    try:
        yield conn
    finally:
        conn.close()


def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if API_KEY and not secrets.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "Missing or wrong X-API-Key header.")


app = FastAPI(title="FashionAI", version="0.2.0", lifespan=lifespan,
              description="Local wardrobe + outfit recommender. Everything runs on this PC.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/media/cutouts", StaticFiles(directory=fc.CUTOUT_DIR), name="cutouts")
app.mount("/media/raw", StaticFiles(directory=fc.RAW_DIR), name="raw")
app.mount("/media/worn", StaticFiles(directory=fc.WORN_DIR), name="worn")
api = APIRouter(prefix="/api", dependencies=[Depends(require_key)])


# ---------------------------------------------------------------------------
# JSON shapes
# ---------------------------------------------------------------------------
def item_json(item: dict) -> dict:
    data = {key: value for key, value in item.items() if key not in ("clip_distance", "taste_score")}
    data["worn_photo"] = bool(item["worn_photo"])
    data["bg_removed"] = bool(item["bg_removed"])
    data["cutout_url"] = "/media/cutouts/" + quote(Path(item["cutout_path"]).name)
    raw = Path(item["raw_path"])
    data["photo_url"] = ("/media/raw/" + quote("/".join(raw.parts[1:]))
                         if raw.parts and raw.parts[0] == fc.RAW_DIR.name else None)
    return data


def outfit_json(conn, outfit: dict) -> dict:
    """A row of the outfits table with its items resolved (items deleted since are left out)."""
    data = dict(outfit)
    data["items"] = [item_json(item) for item in fc.load_items(conn, outfit["item_ids"])]
    return data


def get_item_or_404(conn, item_id: str) -> dict:
    found = fc.load_items(conn, [item_id])
    if not found:
        raise HTTPException(404, f"No item with id {item_id}.")
    return found[0]


class ItemPatch(BaseModel):
    """Only the fields you send are changed."""
    category: str | None = None
    subcategory: str | None = None
    primary_color: str | None = None
    secondary_colors: list[str] | None = None
    pattern: str | None = None
    material: str | None = None
    fit: str | None = None
    formality: int | None = Field(None, ge=1, le=5)
    warmth: int | None = Field(None, ge=1, le=5)
    seasons: list[str] | None = None
    occasions: list[str] | None = None
    style_tags: list[str] | None = None
    description: str | None = None


class RecommendRequest(BaseModel):
    occasion: str = Field(min_length=2, max_length=300, examples=["smart casual dinner, around 15C and windy"])
    preset: str = "personal"
    outfits: int = Field(2, ge=1, le=4)
    k: int = Field(6, ge=2, le=10, description="candidates retrieved per category")
    think: bool = Field(False, description='opt in to "think": true for complex styling problems (about 30 s of GPU)')
    think_budget: int = Field(st.THINK_BUDGET, ge=200, le=4000, description="max thinking tokens when think is true")
    max_temp: int = Field(80, ge=0, le=95, description="stop thinking when the GPU reaches this temperature (0 = ignore)")


class Feedback(BaseModel):
    liked: bool | None = Field(description="true = liked, false = disliked, null = clear the feedback")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"app": "FashionAI", "docs": "/docs", "health": "/api/health"}


@api.get("/health", tags=["status"])
def health() -> dict:
    try:
        fc.ollama_check()
        ollama = {"ok": True, "model": fc.OLLAMA_MODEL}
    except fc.OllamaError as exc:
        ollama = {"ok": False, "model": fc.OLLAMA_MODEL, "error": str(exc)}
    with db() as conn:
        items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        outfits = conn.execute("SELECT COUNT(*) FROM outfits").fetchone()[0]
    return {
        "status": "ok" if ollama["ok"] and not models.error else "degraded",
        "models_ready": models.ready,          # false for ~20 s after start-up
        "models_error": models.error,
        "ollama": ollama,
        "items": items,
        "outfits": outfits,
        "gpu_temp_c": fc.gpu_temp_c(),
        "thinking_default": False,
    }


# ---------------------------------------------------------------------------
# Wardrobe items
# ---------------------------------------------------------------------------
@api.get("/items", tags=["wardrobe"])
def list_items(category: str | None = None) -> dict:
    if category is not None and category not in fc.CATEGORIES:
        raise HTTPException(422, f"category must be one of: {', '.join(fc.CATEGORIES)}")
    with db() as conn:
        items = [item_json(i) for i in fc.load_items(conn) if category is None or i["category"] == category]
    return {"count": len(items), "items": items}


@api.get("/items/{item_id}", tags=["wardrobe"])
def get_item(item_id: str) -> dict:
    with db() as conn:
        return item_json(get_item_or_404(conn, item_id))


@api.post("/items", status_code=201, tags=["wardrobe"])
def upload_item(file: UploadFile, response: Response) -> dict:
    """Upload one clothing photo (multipart form field `file`). Takes about 6 s, plus 10-40 s when
    Ollama has to load the model. Uploading the same photo twice returns the existing item."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in iw.IMAGE_EXTS:
        raise HTTPException(415, f"Unsupported file type {suffix or '(none)'}. Use: {', '.join(sorted(iw.IMAGE_EXTS))}")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Photo is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except Exception:
        raise HTTPException(400, "That file is not a readable image.") from None

    item_id = hashlib.sha256(data).hexdigest()[:16]  # same id the ingest script would give this file
    with db() as conn:
        existing = fc.load_items(conn, [item_id])
        if existing:
            response.status_code = 200
            return {"duplicate": True, "item": item_json(existing[0])}

        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(file.filename or "photo").stem).strip("_")[:40] or "photo"
        path = fc.RAW_DIR / f"{item_id}_{stem}{suffix}"
        path.write_bytes(data)
        try:
            with models.lock:
                item, timing = models.pipeline().process(conn, path, item_id)
        except Exception as exc:
            path.unlink(missing_ok=True)
            (fc.CUTOUT_DIR / f"{item_id}.png").unlink(missing_ok=True)
            if isinstance(exc, fc.OllamaError):
                raise HTTPException(503, str(exc)) from exc
            raise HTTPException(422, f"Could not process this photo ({type(exc).__name__}: {exc}).") from exc
    return {"duplicate": False, "item": item_json(item),
            "timing": {key: round(value, 1) for key, value in timing.items()}}


@api.patch("/items/{item_id}", tags=["wardrobe"])
def edit_item(item_id: str, patch: ItemPatch) -> dict:
    """Correct an item's tags by hand. Values are validated the same way the tagger's are."""
    changes = patch.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        raise HTTPException(422, "Send at least one field to change.")
    with db() as conn:
        item = get_item_or_404(conn, item_id)
        try:
            tags = iw.normalize_tags({**item, **changes})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        for key in ("pattern", "fit"):  # normalize_tags quietly replaces unknown values; here that would hide a typo
            if key in changes and tags[key] != str(changes[key]).strip().lower():
                allowed = iw.PATTERNS if key == "pattern" else iw.FITS
                raise HTTPException(422, f"{key} must be one of: {', '.join(allowed)}")
        item.update(tags, tagger_model="manual edit")
        with models.lock:
            fc.store_item(conn, models.collection(), item)
        return item_json(item)


@api.post("/items/{item_id}/retag", tags=["wardrobe"])
def retag_item(item_id: str) -> dict:
    """Run the tagger again on an item's cutout (about 3 s of GPU)."""
    with db() as conn:
        get_item_or_404(conn, item_id)
        try:
            with models.lock:
                item = models.pipeline().retag(conn, item_id)
        except fc.OllamaError as exc:
            raise HTTPException(503, str(exc)) from exc
        except (OSError, RuntimeError) as exc:
            raise HTTPException(422, f"Could not retag ({exc}).") from exc
        return item_json(item)


@api.delete("/items/{item_id}", tags=["wardrobe"])
def delete_item(item_id: str) -> dict:
    """Remove an item. The cutout is deleted; the original photo is moved to wardrobe_removed
    (so the ingest script does not pick it up again and nothing of yours is erased)."""
    with db() as conn:
        get_item_or_404(conn, item_id)
        with models.lock:
            _, moved_to = fc.remove_item(conn, models.collection(), item_id)
    return {"deleted": item_id, "photo_moved_to": moved_to}


# ---------------------------------------------------------------------------
# Profile and presets
# ---------------------------------------------------------------------------
@api.get("/presets", tags=["stylist"])
def presets() -> dict:
    return {"default": "personal",
            "presets": [{"name": name, "brief": preset["brief"]} for name, preset in st.PRESETS.items()]}


@api.get("/profile", tags=["stylist"])
def get_profile() -> dict:
    return st.load_profile(announce=False)


@api.put("/profile", tags=["stylist"])
def put_profile(profile: dict) -> dict:
    """Replace the profile. Any keys work; the stylist reads every non-empty one (keys starting with _ are ignored)."""
    if len(str(profile)) > 4000:
        raise HTTPException(413, "Profile is too long - keep it under about 4000 characters.")
    st.save_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# Outfits
# ---------------------------------------------------------------------------
@api.post("/outfits/recommend", tags=["stylist"])
def recommend(request: RecommendRequest) -> dict:
    """Suggest outfits for an occasion. About 5 s; with `"think": true` about 30 s of GPU."""
    if request.preset not in st.PRESETS:
        raise HTTPException(422, f"preset must be one of: {', '.join(st.PRESETS)}")
    think_budget = request.think_budget if request.think else 0
    with db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        if total < 2:
            raise HTTPException(409, f"The wardrobe has {total} item(s) - upload some clothes first.")
        try:
            with models.lock:
                pipeline = models.pipeline()
                candidates = st.retrieve_candidates(conn, pipeline.clip, pipeline.collection,
                                                    request.occasion, request.preset, request.k)
                if len(candidates) < 2:
                    raise HTTPException(409, "Not enough matching items were found for this request.")
                result = st.compose_outfits(conn, candidates, request.occasion, request.preset,
                                            st.load_profile(announce=False), request.outfits,
                                            think_budget, request.max_temp)
        except fc.OllamaError as exc:
            raise HTTPException(503, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(502, f"The model's answer was not usable ({exc}). Please try again.") from exc

    info = result["info"]
    return {
        "occasion": request.occasion,
        "preset": request.preset,
        "brief": result["brief"],
        "wardrobe_gap": result["wardrobe_gap"],
        "seconds": round(info["seconds"], 1),
        "thinking": {"used": think_budget > 0, "tokens": info["thinking_tokens"], "cut_short": info["cut"]},
        "outfits": [{
            "id": outfit["id"],
            "name": outfit["name"],
            "why": outfit["why"],
            "tips": outfit["tips"],
            "complete": not outfit["missing"],
            "missing": outfit["missing"],
            "liked": None,
            "items": [item_json(item) for item in outfit["items"]],
        } for outfit in result["outfits"]],
    }


@api.get("/outfits", tags=["stylist"])
def outfit_history(limit: int = 20, liked: bool | None = None) -> dict:
    """Outfits suggested so far, newest first. `liked=true` lists your favourites."""
    with db() as conn:
        outfits = [outfit_json(conn, outfit) for outfit in fc.load_outfits(conn, max(1, min(limit, 200)), liked)]
    return {"count": len(outfits), "outfits": outfits}


@api.post("/outfits/{outfit_id}/feedback", tags=["stylist"])
def outfit_feedback(outfit_id: int, feedback: Feedback) -> dict:
    """Like or dislike a suggested outfit. The latest likes and dislikes are shown to the stylist as your taste."""
    with db() as conn:
        if not fc.set_outfit_feedback(conn, outfit_id, feedback.liked):
            raise HTTPException(404, f"No outfit with id {outfit_id}.")
    return {"id": outfit_id, "liked": feedback.liked}


@api.post("/outfits/{outfit_id}/worn", tags=["stylist"])
def outfit_worn(outfit_id: int) -> dict:
    """"I wore this today": logs a wear for every item in the outfit, so the stylist rotates your clothes."""
    with db() as conn:
        row = conn.execute("SELECT item_ids FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
        if row is None:
            raise HTTPException(404, f"No outfit with id {outfit_id}.")
        import json

        day = fc.log_wear(conn, json.loads(row["item_ids"] or "[]"), outfit_id)
    return {"id": outfit_id, "worn_on": day}


# ---------------------------------------------------------------------------
# Worn diary: what you wore on which day, optionally with a photo of you in it
# ---------------------------------------------------------------------------
def worn_entries(conn) -> list[dict]:
    """Diary entries, newest day first: every outfit photo, plus outfits marked worn without a photo."""
    outfits = {outfit["id"]: outfit for outfit in fc.load_outfits(conn, 1000)}
    entries: list[dict] = []
    with_photo: set[tuple] = set()
    for photo in fc.load_worn_photos(conn):
        outfit = outfits.get(photo["outfit_id"])
        if outfit:
            with_photo.add((outfit["id"], photo["worn_on"]))
        entries.append({"id": photo["id"], "worn_on": photo["worn_on"], "has_photo": True,
                        "photo_url": "/media/worn/" + quote(Path(photo["cutout_path"]).name),
                        "outfit": outfit_json(conn, outfit) if outfit else None})
    for outfit in outfits.values():
        if outfit.get("worn_on") and (outfit["id"], outfit["worn_on"]) not in with_photo:
            entries.append({"id": f"outfit-{outfit['id']}", "worn_on": outfit["worn_on"], "has_photo": False,
                            "photo_url": None, "outfit": outfit_json(conn, outfit)})
    entries.sort(key=lambda entry: entry["worn_on"], reverse=True)
    return entries


@api.get("/worn", tags=["worn diary"])
def worn_diary() -> dict:
    """What you wore, newest day first. The app groups it by day and by month."""
    with db() as conn:
        entries = worn_entries(conn)
    return {"count": len(entries), "entries": entries}


@api.post("/worn", status_code=201, tags=["worn diary"])
def add_worn_photo(file: UploadFile, worn_on: Annotated[str | None, Form()] = None,
                   outfit_id: Annotated[int | None, Form()] = None) -> dict:
    """Add a photo of you in an outfit (multipart field `file`). The background is removed and the
    photo is filed under `worn_on` (YYYY-MM-DD, default today). With `outfit_id`, that outfit's
    items are also logged as worn that day. No GPU is used."""
    if worn_on is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", worn_on):
        raise HTTPException(422, "worn_on must look like 2026-11-16")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Photo is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except Exception:
        raise HTTPException(400, "That file is not a readable image.") from None
    with db() as conn:
        try:
            with models.lock:
                entry = models.pipeline().worn_photo(conn, data, worn_on, outfit_id)
        except Exception as exc:
            raise HTTPException(422, f"Could not process this photo ({type(exc).__name__}: {exc}).") from exc
        return next(item for item in worn_entries(conn) if item["id"] == entry["id"])


@api.delete("/worn/{entry_id}", tags=["worn diary"])
def delete_worn_photo(entry_id: str) -> dict:
    """Remove a diary photo. The record that the clothes were worn that day is kept."""
    with db() as conn:
        if not fc.delete_worn_photo(conn, entry_id):
            raise HTTPException(404, f"No diary photo with id {entry_id}.")
    return {"deleted": entry_id}


app.include_router(api)


# ---------------------------------------------------------------------------
def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))  # no packet is sent; this only asks the OS which interface it would use
            return probe.getsockname()[0]
    except OSError:
        return None


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Run the FashionAI backend.")
    parser.add_argument("--lan", action="store_true",
                        help="listen on all network interfaces so a phone on the same Wi-Fi can connect")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--cloud", action="store_true",
                        help="also run the Supabase sync worker (cloud_sync.py), sharing this server's models")
    args = parser.parse_args()

    if args.cloud:
        import cloud_sync

        cloud_sync.start_background(models.pipeline, models.collection, models.lock)

    print(f"FashionAI backend - data in {fc.DATA_DIR}")
    print(f"  this PC : http://127.0.0.1:{args.port}/docs")
    if args.lan:
        ip = _lan_ip()
        print(f"  phone   : http://{ip or '<this PC ip>'}:{args.port}   (same Wi-Fi; allow Python through Windows Firewall when asked)")
    if API_KEY:
        print(f"  API key : required (X-API-Key header) - see {API_KEY_FILE.name} or FASHIONAI_API_KEY")
    elif args.lan:
        print(f"  note    : no API key set - anyone on this network can use the API (create {API_KEY_FILE.name} to lock it)")
    print("  Loading models in the background (about 20 s); Ctrl+C stops the server.\n", flush=True)
    uvicorn.run(app, host="0.0.0.0" if args.lan else "127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
