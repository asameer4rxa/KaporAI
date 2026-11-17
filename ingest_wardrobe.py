"""
ingest_wardrobe.py - Phase 1 ingestion pipeline.

    photo in wardrobe_raw
      -> YOLOv8n       if someone is wearing the garment, crop to that person
      -> rembg / U-2-Net   remove the background -> transparent PNG in wardrobe_cutouts
      -> OpenCLIP ViT-B-32 512-d image vector         -> ChromaDB
      -> qwen3.5:4b        JSON tags ("think": false)  -> SQLite

Usage (PowerShell, in D:\\FashionAI with the venv activated):
    python ingest_wardrobe.py              # ingest every new photo
    python ingest_wardrobe.py --limit 3    # only the first 3 new photos (quick test)
    python ingest_wardrobe.py --force      # redo photos that are already in the wardrobe
    python ingest_wardrobe.py --only IMG_0412   # redo just the photo(s) whose file name contains this text
    python ingest_wardrobe.py --list       # print the wardrobe inventory

Photo tips: ONE garment per photo, laid flat or on a hanger, on a plain
contrasting background, in good light.
"""

from __future__ import annotations

import fashion_core as fc  # keep this the first project import: it pins all model caches to this drive

import argparse
import hashlib
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
try:  # optional iPhone HEIC support:  pip install pillow-heif
    import pillow_heif

    pillow_heif.register_heif_opener()
    IMAGE_EXTS |= {".heic", ".heif"}
except ImportError:
    pass

REMBG_MODEL = os.environ.get("FASHIONAI_REMBG_MODEL", "u2net")
WORK_SIDE = 2048        # photos are downscaled to this longest side before processing
CUTOUT_SIDE = 1024      # longest side of the saved cutout PNG
VLM_SIDE = 512          # longest side of the image sent to the tagger (smaller = fewer image tokens = faster)
PERSON_CONF = 0.5       # YOLO confidence needed to treat the photo as "worn by a person"
PERSON_MIN_AREA = 0.12  # ...and that person must fill at least this share of the frame

# ---------------------------------------------------------------------------
# Tag vocabulary + the JSON schema Ollama enforces on the tagger's answer
# ---------------------------------------------------------------------------
PATTERNS = ["solid", "striped", "checked", "floral", "graphic", "printed", "textured", "colorblock", "other"]
FITS = ["slim", "regular", "relaxed", "oversized", "unknown"]
SEASONS = ["spring", "summer", "autumn", "winter"]
OCCASIONS = ["casual", "work", "smart", "formal", "party", "date", "sport", "lounge", "outdoor", "travel"]

TAG_SCHEMA = {
    "type": "object",
    "properties": {
        # description comes first on purpose: the model "looks" before it classifies
        "description": {"type": "string"},
        "category": {"type": "string", "enum": list(fc.CATEGORIES)},
        "subcategory": {"type": "string"},
        "primary_color": {"type": "string"},
        "secondary_colors": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "pattern": {"type": "string", "enum": PATTERNS},
        "material": {"type": "string"},
        "fit": {"type": "string", "enum": FITS},
        "formality": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "warmth": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
        "seasons": {"type": "array", "items": {"type": "string", "enum": SEASONS}, "minItems": 1, "maxItems": 4},
        "occasions": {"type": "array", "items": {"type": "string", "enum": OCCASIONS}, "minItems": 1, "maxItems": 4},
        "style_tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
    },
    "required": [
        "description", "category", "subcategory", "primary_color", "secondary_colors", "pattern", "material",
        "fit", "formality", "warmth", "seasons", "occasions", "style_tags",
    ],
}

TAGGER_SYSTEM = """You are a meticulous fashion cataloguer. You are shown ONE clothing item with its background removed. Describe only what you can actually see and answer with JSON.

Fields:
- description: one plain sentence, at most 20 words, naming the item, its colour and its notable details.
- category: top = t-shirts, shirts, polos, blouses, knitwear, sweatshirts, pullover hoodies. bottom = jeans, trousers, shorts, skirts, joggers, leggings. one_piece = dresses, jumpsuits, full-length robes (thobe, abaya, kaftan). outerwear = jackets, coats, blazers, cardigans, zip-up hoodies, gilets, overshirts. shoes = all footwear. accessory = bags, belts, hats, scarves, ties, watches, jewellery, sunglasses.
- subcategory: the specific item type in 1-3 lowercase words, e.g. "oxford shirt", "slim jeans", "chelsea boots".
- primary_color: the dominant colour as one common lowercase colour name, e.g. navy, black, white, grey, beige, olive, burgundy.
- secondary_colors: other clearly visible colours; [] if there are none.
- pattern: solid, striped, checked, floral, graphic (logos, text, pictures), printed (other all-over prints), textured (cable knit, ribbing, quilting), colorblock, or other.
- material: best guess in one or two lowercase words (cotton, denim, wool, linen, leather, suede, knit, fleece, nylon, polyester, silk); "unknown" if unclear.
- fit: slim, regular, relaxed or oversized; "unknown" if it cannot be judged (always "unknown" for shoes and accessories).
- formality: 1 = gym or lounge, 2 = casual, 3 = smart casual, 4 = business, 5 = formal / black tie.
- warmth: 1 = very light, for hot weather ... 5 = very warm, for deep winter. Use 2 or 3 for shoes and accessories unless they are clearly seasonal.
- seasons: every season the item suits.
- occasions: the occasions (up to 4) it suits best.
- style_tags: 1-4 lowercase style words, e.g. minimalist, classic, streetwear, sporty, preppy, workwear, bohemian, vintage, elegant."""

_CATEGORY_ALIASES = {
    "tops": "top", "shirt": "top", "bottoms": "bottom", "pants": "bottom", "trousers": "bottom",
    "dress": "one_piece", "onepiece": "one_piece", "jumpsuit": "one_piece",
    "jacket": "outerwear", "coat": "outerwear", "shoe": "shoes", "footwear": "shoes",
    "accessories": "accessory", "bag": "accessory",
}


def _word(value, default: str = "unknown", limit: int = 40) -> str:
    text = " ".join(str(value or "").lower().split())[:limit].strip()
    return text or default


def _words(values, allowed: list[str] | None = None, limit: int = 4) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values else []
    result: list[str] = []
    for value in values:
        word = _word(value, default="")
        if word and word not in result and (allowed is None or word in allowed):
            result.append(word)
    return result[:limit]


def _level(value, default: int) -> int:
    try:
        return min(5, max(1, int(value)))
    except (TypeError, ValueError):
        return default


def normalize_tags(raw: dict) -> dict:
    """Validate and clean the model's JSON (defence in depth on top of the schema)."""
    category = _word(raw.get("category")).replace(" ", "_").replace("-", "_")
    category = _CATEGORY_ALIASES.get(category, category)
    if category not in fc.CATEGORIES:
        raise ValueError(f"unknown category {category!r}")
    description = " ".join(str(raw.get("description") or "").split())[:240]
    if not description:
        raise ValueError("empty description")
    pattern = _word(raw.get("pattern"), "other")
    fit = _word(raw.get("fit"))
    primary = _word(raw.get("primary_color"))
    return {
        "category": category,
        "subcategory": _word(raw.get("subcategory"), default=category.replace("_", " ")),
        "primary_color": primary,
        "secondary_colors": [c for c in _words(raw.get("secondary_colors"), limit=3) if c != primary],
        "pattern": pattern if pattern in PATTERNS else "other",
        "material": _word(raw.get("material")),
        "fit": fit if fit in FITS else "unknown",
        "formality": _level(raw.get("formality"), 2),
        "warmth": _level(raw.get("warmth"), 3),
        "seasons": _words(raw.get("seasons"), SEASONS) or list(SEASONS),
        "occasions": _words(raw.get("occasions"), OCCASIONS) or ["casual"],
        "style_tags": _words(raw.get("style_tags")),
        "description": description,
    }


def tag_garment(flat: Image.Image, worn: bool) -> tuple[dict, dict]:
    """Ask the vision model for JSON tags. "think": false keeps it a short GPU burst."""
    prompt = "Catalogue this clothing item."
    if worn:
        prompt += (" It was cut out of a photo of a person wearing it, so parts of the person or of other clothes may"
                   " still show: catalogue only the single most prominent garment and ignore everything else.")
    messages = [
        {"role": "system", "content": TAGGER_SYSTEM},
        {"role": "user", "content": prompt, "images": [fc.jpeg_b64(flat, max_side=VLM_SIDE)]},
    ]
    last_error: Exception | None = None
    for temperature in (0.2, 0.7):  # the second attempt only happens if the first reply was unusable
        reply = fc.ollama_chat(
            messages, think=False, schema=TAG_SCHEMA,
            options={"temperature": temperature, "presence_penalty": 0.0, "num_predict": 400},
        )
        try:
            return normalize_tags(fc.parse_json(reply["content"])), reply
        except ValueError as exc:  # includes json.JSONDecodeError
            last_error = exc
    raise RuntimeError(f"the tagger returned unusable JSON twice ({last_error})")


# ---------------------------------------------------------------------------
# Vision steps
# ---------------------------------------------------------------------------
class PersonDetector:
    """YOLOv8n (COCO). Only job: notice a person wearing the garment and crop to them."""

    def __init__(self) -> None:
        from ultralytics import YOLO, settings
        from ultralytics.utils import events as yolo_events

        yolo_events.events.enabled = False  # local-first: no usage analytics
        wanted = {
            "sync": False,
            "weights_dir": str(fc.YOLO_WEIGHTS.parent),
            "runs_dir": str(fc.MODELS_DIR / "ultralytics" / "runs"),
            "datasets_dir": str(fc.MODELS_DIR / "ultralytics" / "datasets"),
        }
        changes = {key: value for key, value in wanted.items() if settings.get(key) != value}
        if changes:
            settings.update(changes)
        fc.YOLO_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
        self.model = YOLO(str(fc.YOLO_WEIGHTS))  # first run downloads ~6 MB into models\yolo
        self.device = fc.pick_device()

    def crop_to_person(self, img: Image.Image) -> tuple[Image.Image, bool]:
        """Returns (image, worn). Unchanged image and False when nobody is in the photo."""
        result = self.model.predict(img, classes=[0], conf=PERSON_CONF, imgsz=640,
                                    device=self.device, verbose=False)[0]
        if result.boxes is None or len(result.boxes) == 0:
            return img, False
        boxes = result.boxes.xyxy.cpu().numpy()
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        best = int(areas.argmax())
        if areas[best] < PERSON_MIN_AREA * img.width * img.height:
            return img, False
        x1, y1, x2, y2 = (float(v) for v in boxes[best])
        pad = 0.04 * max(x2 - x1, y2 - y1)
        box = (max(0, int(x1 - pad)), max(0, int(y1 - pad)),
               min(img.width, int(x2 + pad)), min(img.height, int(y2 + pad)))
        return img.crop(box), True


class BackgroundRemover:
    """rembg with the U-2-Net model (CPU, via onnxruntime)."""

    def __init__(self) -> None:
        from rembg import new_session

        self.session = new_session(REMBG_MODEL)  # first run downloads u2net.onnx (~176 MB) into models\rembg

    def cutout(self, img: Image.Image) -> tuple[Image.Image, bool]:
        """Returns (RGBA cutout cropped tight to the garment, background_removed)."""
        from rembg import remove

        rgba = remove(img, session=self.session, post_process_mask=True).convert("RGBA")
        solid = np.asarray(rgba.getchannel("A")) > 32
        removed = bool(solid.mean() >= 0.02)
        if removed:
            rows = np.flatnonzero(solid.any(axis=1))
            cols = np.flatnonzero(solid.any(axis=0))
            pad = int(0.02 * max(rgba.size))
            rgba = rgba.crop((max(0, int(cols[0]) - pad), max(0, int(rows[0]) - pad),
                              min(rgba.width, int(cols[-1]) + 1 + pad), min(rgba.height, int(rows[-1]) + 1 + pad)))
        else:  # the model kept (almost) nothing, e.g. white garment on a white wall: keep the photo as it is
            rgba = img.convert("RGBA")
        rgba.thumbnail((CUTOUT_SIDE, CUTOUT_SIDE), Image.LANCZOS)
        return rgba, removed


class GarmentSegmenter:
    """rembg's clothing model (u2net_cloth_seg): finds the clothes on a person.

    Used for photos of someone wearing the item, so the cutout shows the garment and not
    the person. The model labels upper-body, lower-body and full-body clothing; which of the
    three is "the" item is decided by the item's category (a jacket photo also shows trousers).

    The model is not reliable on its own: it shreds flat product shots that the person
    detector mistook for a person. So its result is only used when it removes something that
    really is a person - a meaningful part of the foreground that contains skin.
    """

    MASK_FOR = {"top": 0, "outerwear": 0, "bottom": 1, "one_piece": 2}  # index into the model's three masks
    MIN_SHARE = 0.03      # the garment must cover at least this share of the photo
    MIN_REMOVED = 0.06    # ... and at least this share of the foreground must be cut away
    MIN_SKIN = 0.05       # ... and at least this share of what is cut away must be skin-toned

    def __init__(self) -> None:
        from rembg import new_session

        self.session = new_session("u2net_cloth_seg")  # first use downloads ~176 MB into models\rembg

    def cutout(self, img: Image.Image, category: str, foreground: np.ndarray) -> Image.Image | None:
        """RGBA cutout of the garment of `category`, or None to keep the ordinary cutout.

        foreground is the plain background-removal mask of the same image (True = person or object).
        """
        from scipy import ndimage

        index = self.MASK_FOR.get(category)
        if index is None:  # shoes and accessories: the clothing model does not know them
            return None
        best = (np.asarray(self.session.predict(img)[index]) > 127) & foreground
        # keep the biggest connected piece (drops stray specks) and close pinholes
        labels, count = ndimage.label(best)
        if count == 0:
            return None
        if count > 1:
            sizes = ndimage.sum(best, labels, range(1, count + 1))
            best = labels == (int(np.argmax(sizes)) + 1)
        best = ndimage.binary_closing(best, iterations=2) & foreground
        removed = foreground & ~best
        if best.mean() < self.MIN_SHARE or removed.sum() < self.MIN_REMOVED * max(1, foreground.sum()):
            return None
        # A real person shows skin (face, hands) in the part that gets cut away. A flat product
        # shot the detector mistook for a person does not, and the model's mask of it is ragged.
        pixels = np.asarray(img.convert("RGB"), dtype=float)
        red, green, blue = pixels[..., 0], pixels[..., 1], pixels[..., 2]
        cr = 128 + 0.5 * red - 0.418688 * green - 0.081312 * blue
        cb = 128 - 0.168736 * red - 0.331264 * green + 0.5 * blue
        skin = (cr > 135) & (cr < 180) & (cb > 85) & (cb < 135) & (red > 60)
        if skin[removed].mean() < self.MIN_SKIN:
            return None

        alpha = Image.fromarray((best * 255).astype("uint8"), mode="L").filter(ImageFilter.GaussianBlur(1.2))
        rgba = img.convert("RGBA")
        rgba.putalpha(alpha)
        rows = np.flatnonzero(best.any(axis=1))
        cols = np.flatnonzero(best.any(axis=0))
        pad = int(0.02 * max(rgba.size))
        rgba = rgba.crop((max(0, int(cols[0]) - pad), max(0, int(rows[0]) - pad),
                          min(rgba.width, int(cols[-1]) + 1 + pad), min(rgba.height, int(rows[-1]) + 1 + pad)))
        rgba.thumbnail((CUTOUT_SIDE, CUTOUT_SIDE), Image.LANCZOS)
        return rgba


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def file_id(path: Path) -> str:
    """Stable item id: first 16 hex chars of the file's SHA-256 (same photo -> same id)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def find_photos() -> list[Path]:
    return sorted(p for p in fc.RAW_DIR.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_EXTS)


def load_photo(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)  # phone photos: apply the EXIF rotation
        if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
            rgba = img.convert("RGBA")  # already-transparent PNG: put it on white, not black
            img = Image.new("RGB", rgba.size, (255, 255, 255))
            img.paste(rgba, mask=rgba.getchannel("A"))
        else:
            img = img.convert("RGB")
    img.thumbnail((WORK_SIDE, WORK_SIDE), Image.LANCZOS)
    return img


class Pipeline:
    """Every model loaded once; process() turns one photo into a stored wardrobe item.

    Shared by this script and by the FastAPI server (which keeps one Pipeline alive).
    """

    def __init__(self, collection=None) -> None:
        self.detector = PersonDetector()
        self.remover = BackgroundRemover()
        self.clip = fc.ClipEmbedder()
        self.collection = collection if collection is not None else fc.open_collection()
        self._segmenter: GarmentSegmenter | None = None  # loaded when the first worn photo shows up

    def garment_only(self, crop: Image.Image, category: str) -> Image.Image | None:
        """For a worn photo: the garment without the person, or None when that cannot be done well."""
        from rembg import remove

        if category not in GarmentSegmenter.MASK_FOR:
            return None
        if self._segmenter is None:
            self._segmenter = GarmentSegmenter()
        mask = remove(crop, session=self.remover.session, only_mask=True, post_process_mask=True)
        return self._segmenter.cutout(crop, category, np.asarray(mask.convert("L")) > 127)

    def recut(self, conn, item_id: str) -> bool:
        """Replace an existing worn item's cutout (and vector) with a garment-only one; tags are kept.

        Returns True when the cutout changed. Used to upgrade worn photos ingested before the
        clothing model was added.
        """
        found = fc.load_items(conn, [item_id])
        if not found or not fc.abs_path(found[0]["raw_path"]).is_file():
            return False
        item = found[0]
        crop, worn = self.detector.crop_to_person(load_photo(fc.abs_path(item["raw_path"])))
        garment = self.garment_only(crop, item["category"]) if worn else None
        if garment is None:
            return False
        garment.save(fc.abs_path(item["cutout_path"]))
        fc.store_item(conn, self.collection, item, self.clip.embed_image(fc.flatten_on_white(garment)))
        return True

    def process(self, conn, path: Path, item_id: str | None = None, *,
                cooldown: float = 0.0, max_temp: int = 80) -> tuple[dict, dict]:
        """Photo -> cutout PNG + CLIP vector + tags, saved to ChromaDB and SQLite.

        Returns (item, timings). Raises fc.OllamaError if Ollama is unavailable.
        """
        item_id = item_id or file_id(path)
        started = time.time()
        crop, worn = self.detector.crop_to_person(load_photo(path))
        cutout, bg_removed = self.remover.cutout(crop)
        flat = fc.flatten_on_white(cutout)
        vision_s = time.time() - started

        fc.cool_down(max_temp=max_temp, pause=cooldown)
        tags, reply = tag_garment(flat, worn)  # tagged from the whole person: that says which garment is the item

        started = time.time()
        if worn:
            garment = self.garment_only(crop, tags["category"])
            if garment is not None:  # show the garment, not the person wearing it
                cutout, flat = garment, fc.flatten_on_white(garment)
        fc.CUTOUT_DIR.mkdir(parents=True, exist_ok=True)
        cutout_path = fc.CUTOUT_DIR / f"{item_id}.png"
        cutout.save(cutout_path)
        vector = self.clip.embed_image(flat)
        vision_s += time.time() - started
        item = {
            **tags,
            "id": item_id,
            "raw_path": fc.rel_path(path),
            "cutout_path": fc.rel_path(cutout_path),
            "worn_photo": int(worn),
            "bg_removed": int(bg_removed),
            "tagger_model": fc.OLLAMA_MODEL,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        fc.store_item(conn, self.collection, item, vector)
        return item, {"vision_s": vision_s, "tag_s": reply["total_s"], "load_s": reply["load_s"]}

    def worn_photo(self, conn, data: bytes, worn_on: str | None = None, outfit_id: int | None = None,
                   entry_id: str | None = None) -> dict:
        """A photo of you in today's outfit -> background removed -> an entry in the worn diary.

        No tagging and no GPU: only the person detector and the background remover run.
        If outfit_id is given, every item of that outfit is also logged as worn that day.
        """
        import uuid

        entry_id = entry_id or uuid.uuid4().hex[:16]
        fc.WORN_DIR.mkdir(parents=True, exist_ok=True)
        photo_path = fc.WORN_DIR / f"{entry_id}.jpg"
        photo_path.write_bytes(data)
        try:
            crop, _ = self.detector.crop_to_person(load_photo(photo_path))
            cutout, _ = self.remover.cutout(crop)
        except Exception:
            photo_path.unlink(missing_ok=True)
            raise
        cutout_path = fc.WORN_DIR / f"{entry_id}.png"
        cutout.save(cutout_path)
        day = worn_on or time.strftime("%Y-%m-%d")
        if outfit_id is not None:
            row = conn.execute("SELECT item_ids FROM outfits WHERE id = ?", (outfit_id,)).fetchone()
            if row is None:
                outfit_id = None
            else:
                import json

                fc.log_wear(conn, json.loads(row["item_ids"] or "[]"), outfit_id, day)
        entry = {"id": entry_id, "worn_on": day, "outfit_id": outfit_id, "photo_path": fc.rel_path(photo_path),
                 "cutout_path": fc.rel_path(cutout_path), "created_at": datetime.now().isoformat(timespec="seconds")}
        fc.save_worn_photo(conn, entry)
        return entry

    def retag(self, conn, item_id: str) -> dict:
        """Ask the tagger again for an existing item (its cutout and vector are kept)."""
        found = fc.load_items(conn, [item_id])
        if not found:
            raise KeyError(item_id)
        item = found[0]
        with Image.open(fc.abs_path(item["cutout_path"])) as cutout:
            flat = fc.flatten_on_white(cutout)
        tags, _ = tag_garment(flat, bool(item["worn_photo"]))
        item.update(tags, tagger_model=fc.OLLAMA_MODEL)
        fc.store_item(conn, self.collection, item)
        return item


def _server_is_running(port: int = 8000) -> bool:
    """True when server.py answers on this PC (it replies even without the API key)."""
    import requests

    try:
        reply = requests.get(f"http://127.0.0.1:{port}/", timeout=2)
        return reply.ok and reply.json().get("app") == "FashionAI"
    except (requests.exceptions.RequestException, ValueError):
        return False


def print_inventory(conn) -> None:
    items = fc.load_items(conn)
    if not items:
        print("The wardrobe is empty. Put photos in", fc.RAW_DIR, "and run:  python ingest_wardrobe.py")
        return
    print(f"{len(items)} items in {fc.DB_PATH}\n")
    print(f"{'id':<17}{'category':<11}{'item':<24}{'colour':<12}{'pattern':<11}{'form':<5}{'warm':<5}photo")
    for item in items:
        print(f"{item['id']:<17}{item['category']:<11}{item['subcategory'][:22]:<24}{item['primary_color'][:10]:<12}"
              f"{item['pattern']:<11}{item['formality']:<5}{item['warmth']:<5}{item['raw_path']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest clothing photos into the local wardrobe.")
    parser.add_argument("--limit", type=int, default=0, help="process at most N new photos")
    parser.add_argument("--force", action="store_true", help="re-process photos that are already ingested")
    parser.add_argument("--list", action="store_true", help="print the wardrobe inventory and exit")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="process only photos whose file name contains NAME, even if already ingested "
                             "(repeatable: --only shirt --only IMG_0412)")
    parser.add_argument("--recut-worn", action="store_true",
                        help="redo the cutouts of items photographed on a person so they show only the garment")
    parser.add_argument("--ignore-server", action="store_true",
                        help="ingest even though the FashionAI server is running (restart the server afterwards)")
    parser.add_argument("--cooldown", type=float, default=1.0,
                        help="seconds to rest the GPU between photos (default 1.0, 0 = no rest)")
    parser.add_argument("--max-temp", type=int, default=80,
                        help="pause when the GPU reaches this temperature in C (default 80, 0 = never pause)")
    args = parser.parse_args()

    for folder in (fc.RAW_DIR, fc.CUTOUT_DIR, fc.CHROMA_DIR):
        folder.mkdir(parents=True, exist_ok=True)
    conn = fc.open_db()
    if args.list:
        print_inventory(conn)
        return 0

    if args.recut_worn:
        if not args.ignore_server and _server_is_running():
            print("ERROR: stop the FashionAI server first (it must not be running while the wardrobe is rewritten).")
            return 1
        worn = [item for item in fc.load_items(conn) if item["worn_photo"]]
        print(f"{len(worn)} item(s) come from worn photos. Loading models ...", flush=True)
        pipeline = Pipeline()
        changed = 0
        for item in worn:
            done = pipeline.recut(conn, item["id"])
            changed += done
            print(f"  {'garment only' if done else 'kept as is  '}  {item['category']} / {item['subcategory']}", flush=True)
        print(f"{changed} of {len(worn)} cutouts now show only the garment. Run  python cloud_sync.py push  to update the cloud.")
        return 0

    photos = find_photos()
    if not photos:
        print(f"No photos found. Copy your clothing photos into {fc.RAW_DIR} and run this again.")
        return 0

    force = args.force
    if args.only:  # redo specific photos: picking them by name implies --force for those photos
        wanted = [name.lower() for name in args.only]
        photos = [p for p in photos if any(name in p.name.lower() for name in wanted)]
        if not photos:
            print(f"No photo in {fc.RAW_DIR} has a file name containing: {', '.join(args.only)}")
            return 1
        force = True

    known = {row["id"] for row in conn.execute("SELECT id FROM items")}
    todo: list[tuple[str, Path]] = []
    skipped = 0
    seen: set[str] = set()
    for path in photos:
        item_id = file_id(path)
        if item_id in seen or (item_id in known and not force):
            skipped += 1
            continue
        seen.add(item_id)
        todo.append((item_id, path))
    if args.limit > 0:
        todo = todo[:args.limit]
    if not todo:
        print(f"Nothing new: all {len(photos)} photos are already in the wardrobe (use --force to redo them).")
        return 0

    try:
        fc.ollama_check()
    except fc.OllamaError as exc:
        print(f"ERROR: {exc}")
        return 1

    # The vector database must only be written by one program at a time. A running server keeps
    # its own view of it and would not see (and could later break on) items written from here.
    if not args.ignore_server and _server_is_running():
        print("ERROR: the FashionAI server is running on this PC. Either add the photos with the app's\n"
              "       'Add photos' button, or stop the server, run this again, and start the server afterwards.")
        return 1

    first_run = not fc.YOLO_WEIGHTS.exists()
    print(f"{len(todo)} photo(s) to ingest. Loading models"
          + (f" (first run: downloading ~750 MB of weights into {fc.MODELS_DIR})" if first_run else "")
          + " ...", flush=True)
    started = time.time()
    pipeline = Pipeline()
    print(f"Models ready in {time.time() - started:.1f} s (vision on {pipeline.clip.device}, tagging on Ollama "
          f"{fc.OLLAMA_MODEL}). The first tag is slow while Ollama loads the model.\n", flush=True)

    done: list[dict] = []
    failed: list[tuple[Path, str]] = []
    batch_start = time.time()
    for number, (item_id, path) in enumerate(todo, 1):
        print(f"[{number}/{len(todo)}] {fc.rel_path(path)}", flush=True)
        try:
            first = number == 1  # nothing to cool down from before the first photo
            item, timing = pipeline.process(conn, path, item_id, cooldown=0.0 if first else args.cooldown,
                                            max_temp=0 if first else args.max_temp)
            done.append(item)
            worn, bg_removed = bool(item["worn_photo"]), bool(item["bg_removed"])

            notes = []
            if worn:
                notes.append("worn photo")
            if not bg_removed:
                notes.append("background NOT removed")
            temp = fc.gpu_temp_c()
            print(f"      {item['category']} / {item['subcategory']} / {item['primary_color']} / {item['pattern']}"
                  f" / formality {item['formality']} / warmth {item['warmth']}"
                  + (f"   [{', '.join(notes)}]" if notes else ""))
            print(f"      {item['description']}")
            print(f"      vision {timing['vision_s']:.1f} s, tagging {timing['tag_s']:.1f} s"
                  + (f" (model load {timing['load_s']:.0f} s)" if timing["load_s"] > 1 else "")
                  + (f", GPU {temp} C" if temp is not None else ""), flush=True)
        except fc.OllamaError as exc:
            print(f"\nERROR: {exc}\nStopping. Photos ingested so far are saved; run again to continue.")
            return 1
        except Exception as exc:  # one bad photo must not kill the batch
            failed.append((path, f"{type(exc).__name__}: {exc}"))
            print(f"      FAILED: {type(exc).__name__}: {exc}", flush=True)

    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    by_category = Counter(row["category"] for row in conn.execute("SELECT category FROM items"))
    print(f"\nDone in {time.time() - batch_start:.0f} s: {len(done)} ingested, {skipped} skipped, {len(failed)} failed.")
    print(f"Wardrobe now holds {total} item(s): "
          + ", ".join(f"{count} {name}" for name, count in sorted(by_category.items())))
    for path, reason in failed:
        print(f"  failed: {path.name}  ({reason})")
    print('Next:  python stylist.py "smart casual dinner, around 15C"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
