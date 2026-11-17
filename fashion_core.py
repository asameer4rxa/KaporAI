"""
fashion_core.py - shared plumbing for the FashionAI wardrobe app.

IMPORTANT: import this module BEFORE torch / ultralytics / open_clip / rembg /
chromadb. Its first job is to pin every model cache to <project>\\models so
nothing is ever downloaded to C:.

Used by ingest_wardrobe.py and stylist.py (Phase 1) and later by the FastAPI
backend (Phase 2).
"""

from __future__ import annotations

import base64
import io
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent  # D:\FashionAI
# Point FASHIONAI_DATA_DIR at another folder to work on a throw-away wardrobe.
DATA_DIR = Path(os.environ.get("FASHIONAI_DATA_DIR") or ROOT).resolve()
MODELS_DIR = ROOT / "models"

RAW_DIR = DATA_DIR / "wardrobe_raw"          # your original photos go here
CUTOUT_DIR = DATA_DIR / "wardrobe_cutouts"   # transparent PNG cutouts
CHROMA_DIR = DATA_DIR / "chroma_db"          # CLIP vectors
OUTFIT_DIR = DATA_DIR / "outfits"            # outfit board images
REMOVED_DIR = DATA_DIR / "wardrobe_removed"  # photos of items deleted through the API are moved here, not erased
WORN_DIR = DATA_DIR / "worn_photos"          # the worn diary: <id>.jpg (photo of you) and <id>.png (background removed)
DB_PATH = DATA_DIR / "wardrobe.db"           # tags + file paths
PROFILE_PATH = DATA_DIR / "user_profile.json"
YOLO_WEIGHTS = MODELS_DIR / "yolo" / "yolov8n.pt"

# ---------------------------------------------------------------------------
# 2. Keep every cache on this drive (must run before the ML libraries import)
# ---------------------------------------------------------------------------
_CACHE_ENV = {
    "HF_HOME": MODELS_DIR / "hf",                  # OpenCLIP weights (Hugging Face hub)
    "TORCH_HOME": MODELS_DIR / "torch",
    "U2NET_HOME": MODELS_DIR / "rembg",            # rembg / U-2-Net weights
    "REMBG_HOME": MODELS_DIR / "rembg",
    "YOLO_CONFIG_DIR": MODELS_DIR / "ultralytics", # Ultralytics settings + fonts
    "MPLCONFIGDIR": MODELS_DIR / "matplotlib",     # matplotlib font cache (pulled in by Ultralytics)
    "XDG_CACHE_HOME": MODELS_DIR / "cache",        # catch-all for anything else
}
for _name, _default in _CACHE_ENV.items():
    # setdefault: a permanent user environment variable, if you set one, wins.
    os.environ.setdefault(_name, str(_default))
    if Path(os.environ[_name]).drive.upper() != ROOT.drive.upper():
        print(f"[warn] {_name}={os.environ[_name]} is not on {ROOT.drive} - model files will be stored there.",
              file=sys.stderr)

CACHE_ENV_VARS = tuple(_CACHE_ENV)  # names of the variables pinned above (check_phase1.py verifies them)

os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("YOLO_AUTOINSTALL", "False")   # never let Ultralytics pip-install things by itself
if YOLO_WEIGHTS.exists():
    os.environ.setdefault("YOLO_OFFLINE", "True")    # weights are local: no network check, no analytics

for _stream in (sys.stdout, sys.stderr):             # avoid UnicodeEncodeError in Windows consoles
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 3. Settings
# ---------------------------------------------------------------------------
OLLAMA_URL = os.environ.get("FASHIONAI_OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("FASHIONAI_OLLAMA_MODEL", "qwen3.5:4b")
# One context size for tagging AND styling: Ollama reloads the model whenever
# num_ctx changes, and a reload is the slowest thing in this whole pipeline.
OLLAMA_NUM_CTX = int(os.environ.get("FASHIONAI_NUM_CTX", "8192"))

CLIP_MODEL = "ViT-B-32"
CLIP_PRETRAINED = "laion2b_s34b_b79k"
CLIP_HF_REPO = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"  # where open_clip downloads those weights from
# The collection name carries the embedding model: vectors from different
# models must never be mixed, so changing the model means a fresh collection.
COLLECTION_NAME = "wardrobe_vit_b_32_laion2b_s34b_b79k"

# Once the CLIP weights are in the local cache, run the Hugging Face hub offline:
# no network round-trip at start-up, and the app keeps working without internet.
_clip_snapshots = Path(os.environ["HF_HOME"]) / "hub" / ("models--" + CLIP_HF_REPO.replace("/", "--")) / "snapshots"
if any(_clip_snapshots.glob("*/open_clip_model.safetensors")) or any(_clip_snapshots.glob("*/open_clip_pytorch_model.bin")):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

CATEGORIES = ("top", "bottom", "one_piece", "outerwear", "shoes", "accessory")


def pick_device() -> str:
    """'cuda' when a CUDA build of torch is installed, otherwise 'cpu'.

    YOLOv8n and CLIP ViT-B-32 are small enough that CPU is fine (well under a
    second per photo), which also leaves the whole GPU to Ollama.
    Override with FASHIONAI_DEVICE=cpu|cuda.
    """
    wanted = os.environ.get("FASHIONAI_DEVICE", "auto").lower()
    if wanted != "auto":
        return wanted
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def rel_path(path: Path) -> str:
    """Path stored in the database: relative to DATA_DIR, forward slashes."""
    path = Path(path).resolve()
    try:
        return path.relative_to(DATA_DIR).as_posix()
    except ValueError:
        return path.as_posix()


def abs_path(stored: str) -> Path:
    """Inverse of rel_path()."""
    path = Path(stored)
    return path if path.is_absolute() else DATA_DIR / path


# ---------------------------------------------------------------------------
# 4. Ollama (native /api/chat, so the top-level "think" switch is available)
# ---------------------------------------------------------------------------
class OllamaError(RuntimeError):
    """Ollama is unreachable or answered with an error."""


_http = None


def _session():
    global _http
    if _http is None:
        import requests

        _http = requests.Session()
        _http.trust_env = False  # talk to localhost directly, ignore system proxy settings
    return _http


def ollama_check() -> None:
    """Fail fast with a helpful message if the server or the model is missing."""
    import requests

    try:
        reply = _session().get(f"{OLLAMA_URL}/api/tags", timeout=5)
        reply.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise OllamaError(
            f"Cannot reach Ollama at {OLLAMA_URL}. Start the Ollama app (or run `ollama serve`) and try again."
        ) from exc
    names = {m.get("name") for m in reply.json().get("models", [])}
    if OLLAMA_MODEL not in names:
        raise OllamaError(f"Model {OLLAMA_MODEL!r} is not pulled. Run:  ollama pull {OLLAMA_MODEL}")


def ollama_chat(messages: list[dict], *, think: bool, schema: dict | None = None,
                options: dict | None = None, timeout: float = 900.0, on_thinking=None) -> dict:
    """One /api/chat call.

    think        top-level "think" flag (False = fast tagging, True = reasoning).
    schema       JSON schema sent as "format": Ollama constrains the answer to valid
                 JSON of exactly that shape (the thinking itself is not constrained).
    on_thinking  optional callback, called with the number of thinking tokens so far.
                 Return True to stop the generation at once; the reply then has
                 "stopped": True and "thinking" holds the reasoning produced so far.

    The reply is streamed internally (that is what makes on_thinking possible) and
    returned as {"content", "thinking", "thinking_tokens", "stopped", "done_reason",
    "tokens_in", "tokens_out", "load_s", "gen_s", "total_s"}.
    keep_alive is deliberately not sent, so your OLLAMA_KEEP_ALIVE=2m applies.
    """
    import requests

    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": True,
        "think": think,
        "options": {"num_ctx": OLLAMA_NUM_CTX, **(options or {})},
    }
    if schema is not None:
        payload["format"] = schema

    content: list[str] = []
    thinking: list[str] = []
    final: dict = {}
    stopped = False
    started = time.time()
    try:
        with _session().post(f"{OLLAMA_URL}/api/chat", json=payload, stream=True, timeout=(5, timeout)) as reply:
            if reply.status_code != 200:
                try:
                    detail = reply.json().get("error", reply.text)
                except ValueError:
                    detail = reply.text
                raise OllamaError(f"Ollama returned HTTP {reply.status_code}: {detail}")
            for line in reply.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):
                    raise OllamaError(f"Ollama error: {chunk['error']}")
                message = chunk.get("message") or {}
                if message.get("thinking"):
                    thinking.append(message["thinking"])
                    if on_thinking is not None and on_thinking(len(thinking)):
                        stopped = True
                        break  # leaving the with-block closes the connection, which makes Ollama stop generating
                if message.get("content"):
                    content.append(message["content"])
                if chunk.get("done"):
                    final = chunk
                    break
    except requests.exceptions.ConnectionError as exc:
        raise OllamaError(
            f"Cannot reach Ollama at {OLLAMA_URL}. Start the Ollama app (or run `ollama serve`) and try again."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise OllamaError(f"Ollama did not answer within {timeout:.0f} s.") from exc
    except requests.exceptions.RequestException as exc:
        raise OllamaError(f"The connection to Ollama broke: {exc}") from exc
    if not final and not stopped:
        raise OllamaError("Ollama closed the connection before finishing its answer.")

    ns = 1e9
    total_s = (final.get("total_duration") or 0) / ns
    return {
        "content": "".join(content).strip(),
        "thinking": "".join(thinking).strip(),
        "thinking_tokens": len(thinking),
        "stopped": stopped,
        "done_reason": "stopped" if stopped else final.get("done_reason"),
        "tokens_in": final.get("prompt_eval_count") or 0,
        "tokens_out": final.get("eval_count") or (len(thinking) + len(content)),
        "load_s": (final.get("load_duration") or 0) / ns,
        "gen_s": (final.get("eval_duration") or 0) / ns,
        "total_s": total_s if total_s > 0 else time.time() - started,
    }


def parse_json(text: str) -> dict:
    """Parse a model reply into a dict, tolerating code fences or stray prose."""
    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("model reply contains no JSON object") from None
        data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply is not a JSON object")
    return data


# ---------------------------------------------------------------------------
# 5. GPU thermal pacing
# ---------------------------------------------------------------------------
_nvml = None  # None = not tried yet, False = unavailable, else (module, handle)


def gpu_temp_c() -> int | None:
    """NVIDIA GPU temperature in Celsius, or None when it cannot be read."""
    global _nvml
    if _nvml is None:
        try:
            import pynvml  # provided by the nvidia-ml-py package

            pynvml.nvmlInit()
            _nvml = (pynvml, pynvml.nvmlDeviceGetHandleByIndex(0))
        except Exception:
            _nvml = False
    if _nvml:
        try:
            lib, handle = _nvml
            return int(lib.nvmlDeviceGetTemperature(handle, lib.NVML_TEMPERATURE_GPU))
        except Exception:
            return None
    try:  # fallback: nvidia-smi
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
        return int(out[0]) if out else None
    except Exception:
        return None


def cool_down(max_temp: int = 80, pause: float = 0.0, max_wait: float = 180.0) -> float:
    """Thermal pacing between LLM calls.

    Sleeps `pause` seconds, then - if the GPU is at or above `max_temp` - waits
    until it is 8 C cooler (or `max_wait` seconds have passed). max_temp=0
    disables the temperature check. Returns the seconds spent waiting.
    """
    start = time.time()
    if pause > 0:
        time.sleep(pause)
    if max_temp > 0:
        temp = gpu_temp_c()
        if temp is not None and temp >= max_temp:
            target = max_temp - 8
            print(f"      GPU at {temp} C - pausing until it cools to {target} C ...", flush=True)
            while temp is not None and temp > target and time.time() - start < max_wait:
                time.sleep(2.0)
                temp = gpu_temp_c()
    return time.time() - start


# ---------------------------------------------------------------------------
# 6. Images
# ---------------------------------------------------------------------------
def flatten_on_white(cutout, margin: float = 0.06):
    """RGBA cutout -> RGB on a white square canvas.

    Square padding matters: CLIP's preprocessing centre-crops, which would cut
    the top and bottom off long garments such as trousers or dresses.
    """
    from PIL import Image

    rgba = cutout.convert("RGBA")
    side = max(1, int(round(max(rgba.size) * (1 + 2 * margin))))
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2), rgba)
    return canvas


def jpeg_b64(img, max_side: int = 768, quality: int = 90) -> str:
    """Downscale + JPEG-encode an image as base64 for Ollama's "images" field."""
    from PIL import Image

    img = img.convert("RGB")
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.LANCZOS)
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=quality)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# 7. OpenCLIP embeddings
# ---------------------------------------------------------------------------
class ClipEmbedder:
    """OpenCLIP ViT-B-32 image + text encoder returning unit-length 512-d vectors."""

    def __init__(self) -> None:
        import open_clip
        import torch

        self._torch = torch
        self.device = pick_device()
        model, _, preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL,
            pretrained=CLIP_PRETRAINED,
            device=self.device,
            cache_dir=str(Path(os.environ["HF_HOME"]) / "hub"),
        )
        self.model = model.eval()
        self.preprocess = preprocess
        self.tokenizer = open_clip.get_tokenizer(CLIP_MODEL)

    @staticmethod
    def _unit(features) -> list[list[float]]:
        features = features / features.norm(dim=-1, keepdim=True)
        return features.float().cpu().tolist()

    def embed_image(self, img) -> list[float]:
        with self._torch.inference_mode():
            batch = self.preprocess(img.convert("RGB")).unsqueeze(0).to(self.device)
            return self._unit(self.model.encode_image(batch))[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        with self._torch.inference_mode():
            tokens = self.tokenizer(list(texts)).to(self.device)
            return self._unit(self.model.encode_text(tokens))


# ---------------------------------------------------------------------------
# 8. SQLite (tags + paths) and ChromaDB (vectors)
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id               TEXT PRIMARY KEY,            -- first 16 hex chars of the photo's SHA-256
    raw_path         TEXT NOT NULL,               -- relative to the data folder
    cutout_path      TEXT NOT NULL,
    category         TEXT NOT NULL,               -- top | bottom | one_piece | outerwear | shoes | accessory
    subcategory      TEXT NOT NULL,
    primary_color    TEXT NOT NULL,
    secondary_colors TEXT NOT NULL DEFAULT '[]',  -- JSON list
    pattern          TEXT NOT NULL,
    material         TEXT NOT NULL,
    fit              TEXT NOT NULL,
    formality        INTEGER NOT NULL,            -- 1 lounge/gym .. 5 formal
    warmth           INTEGER NOT NULL,            -- 1 very light .. 5 very warm
    seasons          TEXT NOT NULL DEFAULT '[]',  -- JSON list
    occasions        TEXT NOT NULL DEFAULT '[]',  -- JSON list
    style_tags       TEXT NOT NULL DEFAULT '[]',  -- JSON list
    description      TEXT NOT NULL,
    worn_photo       INTEGER NOT NULL DEFAULT 0,  -- 1 = a person was wearing it in the photo
    bg_removed       INTEGER NOT NULL DEFAULT 1,  -- 0 = background removal failed, original kept
    tagger_model     TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category);

CREATE TABLE IF NOT EXISTS outfits (              -- every outfit the stylist has suggested
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    occasion   TEXT NOT NULL,
    preset     TEXT NOT NULL,
    name       TEXT NOT NULL,
    item_ids   TEXT NOT NULL,                     -- JSON list of items.id
    why        TEXT NOT NULL DEFAULT '',
    tips       TEXT NOT NULL DEFAULT '',
    thought    INTEGER NOT NULL DEFAULT 0,        -- 1 = produced with "think": true
    liked      INTEGER,                           -- NULL = no feedback, 1 = liked, 0 = disliked
    worn_on    TEXT                               -- YYYY-MM-DD of the day you last wore this outfit
);

CREATE TABLE IF NOT EXISTS wears (                -- one row per item per day it was worn
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id    TEXT NOT NULL,
    outfit_id  INTEGER,
    worn_on    TEXT NOT NULL                      -- YYYY-MM-DD
);
CREATE INDEX IF NOT EXISTS idx_wears_item ON wears(item_id);

CREATE TABLE IF NOT EXISTS worn_photos (          -- the worn diary: a photo of you in that day's outfit
    id          TEXT PRIMARY KEY,
    worn_on     TEXT NOT NULL,                    -- YYYY-MM-DD
    outfit_id   INTEGER,                          -- the suggested outfit it shows, if any
    photo_path  TEXT NOT NULL,
    cutout_path TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_worn_photos_day ON worn_photos(worn_on);
"""

_ITEM_COLUMNS = (
    "id", "raw_path", "cutout_path", "category", "subcategory", "primary_color", "secondary_colors",
    "pattern", "material", "fit", "formality", "warmth", "seasons", "occasions", "style_tags",
    "description", "worn_photo", "bg_removed", "tagger_model", "created_at",
)
_LIST_COLUMNS = ("secondary_colors", "seasons", "occasions", "style_tags")


def open_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    # databases created before the wear log existed lack this column
    if "worn_on" not in {row[1] for row in conn.execute("PRAGMA table_info(outfits)")}:
        conn.execute("ALTER TABLE outfits ADD COLUMN worn_on TEXT")
        conn.commit()
    return conn


def log_wear(conn: sqlite3.Connection, item_ids: list[str], outfit_id: int | None = None,
             day: str | None = None) -> str:
    """Record that these items were worn on `day` (default today). Returns the day (YYYY-MM-DD)."""
    day = day or time.strftime("%Y-%m-%d")
    for item_id in item_ids:
        if not conn.execute("SELECT 1 FROM wears WHERE item_id = ? AND worn_on = ?", (item_id, day)).fetchone():
            conn.execute("INSERT INTO wears (item_id, outfit_id, worn_on) VALUES (?, ?, ?)", (item_id, outfit_id, day))
    if outfit_id is not None:
        conn.execute("UPDATE outfits SET worn_on = ? WHERE id = ?", (day, outfit_id))
    conn.commit()
    return day


def save_worn_photo(conn: sqlite3.Connection, entry: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO worn_photos (id, worn_on, outfit_id, photo_path, cutout_path, created_at) "
        "VALUES (:id, :worn_on, :outfit_id, :photo_path, :cutout_path, :created_at)", entry)
    conn.commit()


def load_worn_photos(conn: sqlite3.Connection) -> list[dict]:
    """The worn diary's photo entries, newest day first."""
    return [dict(row) for row in conn.execute("SELECT * FROM worn_photos ORDER BY worn_on DESC, created_at DESC")]


def delete_worn_photo(conn: sqlite3.Connection, entry_id: str) -> bool:
    """Remove a diary photo and its two files. The wear log itself is left alone."""
    row = conn.execute("SELECT * FROM worn_photos WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        return False
    for stored in (row["photo_path"], row["cutout_path"]):
        abs_path(stored).unlink(missing_ok=True)
    conn.execute("DELETE FROM worn_photos WHERE id = ?", (entry_id,))
    conn.commit()
    return True


def wear_stats(conn: sqlite3.Connection) -> dict[str, dict]:
    """{item_id: {"wear_count": n, "last_worn": "YYYY-MM-DD"}} for every item worn at least once."""
    rows = conn.execute("SELECT item_id, COUNT(*), MAX(worn_on) FROM wears GROUP BY item_id").fetchall()
    return {row[0]: {"wear_count": row[1], "last_worn": row[2]} for row in rows}


def save_item(conn: sqlite3.Connection, item: dict) -> None:
    """Insert or replace one wardrobe item (the caller commits)."""
    row = {column: item.get(column) for column in _ITEM_COLUMNS}
    for column in _LIST_COLUMNS:
        row[column] = json.dumps(row[column] or [])
    columns = ", ".join(_ITEM_COLUMNS)
    marks = ", ".join(f":{column}" for column in _ITEM_COLUMNS)
    conn.execute(f"INSERT OR REPLACE INTO items ({columns}) VALUES ({marks})", row)


def load_items(conn: sqlite3.Connection, ids: list[str] | None = None) -> list[dict]:
    """All items, or just `ids` (returned in the same order as `ids`)."""
    if ids is None:
        rows = conn.execute("SELECT * FROM items ORDER BY category, subcategory, id").fetchall()
    elif not ids:
        return []
    else:
        marks = ", ".join("?" * len(ids))
        rows = conn.execute(f"SELECT * FROM items WHERE id IN ({marks})", list(ids)).fetchall()
    worn = wear_stats(conn)
    items = []
    for row in rows:
        item = dict(row)
        for column in _LIST_COLUMNS:
            item[column] = json.loads(item[column] or "[]")
        item.update(worn.get(item["id"], {"wear_count": 0, "last_worn": None}))
        items.append(item)
    if ids is not None:
        order = {item_id: n for n, item_id in enumerate(ids)}
        items.sort(key=lambda item: order[item["id"]])
    return items


def store_item(conn: sqlite3.Connection, collection, item: dict, vector: list[float] | None = None) -> None:
    """Write one item to ChromaDB and SQLite and commit.

    vector=None keeps the vector already stored (used when only the tags changed).
    ChromaDB is written first, so a row in SQLite always has its vector.
    """
    metadata = {key: item[key] for key in
                ("category", "subcategory", "primary_color", "pattern", "formality", "warmth")}
    for key in ("seasons", "occasions"):
        if item.get(key):
            metadata[key] = item[key]
    if vector is None:
        # Re-use the stored vector: a plain collection.update() with a new document would make
        # ChromaDB try to embed that text itself, and this collection deliberately has no embedder.
        stored = collection.get(ids=[item["id"]], include=["embeddings"])["embeddings"]
        if stored is None or len(stored) == 0:
            raise RuntimeError(f"item {item['id']} has no stored vector - ingest its photo again")
        vector = [float(value) for value in stored[0]]
    collection.upsert(ids=[item["id"]], embeddings=[vector], metadatas=[metadata],
                      documents=[item["description"]])
    save_item(conn, item)
    conn.commit()


def delete_item(conn: sqlite3.Connection, collection, item_id: str) -> dict | None:
    """Remove one item from SQLite and ChromaDB. Returns the deleted item (files are left to the caller)."""
    found = load_items(conn, [item_id])
    if not found:
        return None
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.execute("DELETE FROM wears WHERE item_id = ?", (item_id,))
    conn.commit()
    collection.delete(ids=[item_id])
    return found[0]


def remove_item(conn: sqlite3.Connection, collection, item_id: str) -> tuple[dict | None, str | None]:
    """Remove an item from this PC completely: database row, vector and cutout file.

    The original photo is not erased: it is moved to wardrobe_removed, so the ingest script
    does not pick it up again. Returns (deleted item or None, new photo path or None).
    """
    import shutil

    item = delete_item(conn, collection, item_id)
    if item is None:
        return None, None
    abs_path(item["cutout_path"]).unlink(missing_ok=True)
    moved_to = None
    photo = abs_path(item["raw_path"])
    if photo.is_file() and RAW_DIR in photo.resolve().parents:
        REMOVED_DIR.mkdir(parents=True, exist_ok=True)
        target = REMOVED_DIR / photo.name
        if target.exists():
            target = REMOVED_DIR / f"{item_id}_{photo.name}"
        shutil.move(str(photo), str(target))
        moved_to = rel_path(target)
    return item, moved_to


def save_outfit(conn: sqlite3.Connection, *, occasion: str, preset: str, name: str, item_ids: list[str],
                why: str = "", tips: str = "", thought: bool = False) -> int:
    """Record a suggested outfit in the history and return its id."""
    cursor = conn.execute(
        "INSERT INTO outfits (created_at, occasion, preset, name, item_ids, why, tips, thought) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (time.strftime("%Y-%m-%dT%H:%M:%S"), occasion, preset, name, json.dumps(item_ids), why, tips, int(thought)),
    )
    conn.commit()
    return int(cursor.lastrowid)


def load_outfits(conn: sqlite3.Connection, limit: int = 50, liked: bool | None = None) -> list[dict]:
    """Outfit history, newest first. liked=True/False filters on the feedback given."""
    query, params = "SELECT * FROM outfits", []
    if liked is not None:
        query += " WHERE liked = ?"
        params.append(int(liked))
    rows = conn.execute(query + " ORDER BY id DESC LIMIT ?", [*params, limit]).fetchall()
    outfits = []
    for row in rows:
        outfit = dict(row)
        outfit["item_ids"] = json.loads(outfit["item_ids"] or "[]")
        outfit["thought"] = bool(outfit["thought"])
        outfit["liked"] = None if outfit["liked"] is None else bool(outfit["liked"])
        outfits.append(outfit)
    return outfits


def set_outfit_feedback(conn: sqlite3.Connection, outfit_id: int, liked: bool | None) -> bool:
    """Store like / dislike (None clears it). Returns False if the outfit does not exist."""
    cursor = conn.execute("UPDATE outfits SET liked = ? WHERE id = ?",
                          (None if liked is None else int(liked), outfit_id))
    conn.commit()
    return cursor.rowcount > 0


def open_collection():
    """The ChromaDB collection holding one CLIP vector per wardrobe item."""
    import chromadb
    from chromadb.config import Settings

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR), settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=None,  # we always pass CLIP vectors ourselves; Chroma must never embed (or download) anything
        configuration={"hnsw": {"space": "cosine"}},
    )
