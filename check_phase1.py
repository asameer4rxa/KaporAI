"""
check_phase1.py - self-test for the Phase 1 setup.

    python check_phase1.py           # fast: environment, weights, Ollama, wardrobe consistency (no GPU work)
    python check_phase1.py --deep    # also runs every model once on a generated test image
                                     # (one short GPU burst for the tagger; nothing is saved)

Each line is [ OK ], [WARN] or [FAIL]. Exit code 0 means no failures.
Nothing is created or changed: if there is no wardrobe yet, the wardrobe checks are skipped.
"""

from __future__ import annotations

import fashion_core as fc  # keep this the first project import: it pins all model caches to this drive

import argparse
import os
import sys
import time
from collections import Counter
from importlib import metadata
from pathlib import Path

_counts: Counter = Counter()


def _report(status: str, text: str) -> None:
    _counts[status] += 1
    print(f"[{status:^4}] {text}")


def ok(text: str) -> None:
    _report("OK", text)


def warn(text: str) -> None:
    _report("WARN", text)


def fail(text: str) -> None:
    _report("FAIL", text)


def note(text: str) -> None:
    print(f"       {text}")


def section(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------------------
def check_environment() -> None:
    section("Environment")
    drive = fc.ROOT.drive.upper()
    if Path(sys.prefix).drive.upper() == drive:
        ok(f"Python {sys.version.split()[0]} from {sys.prefix}")
    else:
        warn(f"Python is running from {sys.prefix}, not from the venv on {drive} - activate D:\\FashionAI\\venv first")

    off_drive = [f"{name}={os.environ[name]}" for name in fc.CACHE_ENV_VARS
                 if Path(os.environ[name]).drive.upper() != drive]
    if off_drive:
        fail(f"cache variables pointing off {drive}: " + "; ".join(off_drive))
    else:
        ok(f"all {len(fc.CACHE_ENV_VARS)} model-cache variables point at {drive} ({', '.join(fc.CACHE_ENV_VARS)})")

    pip_cache = os.environ.get("PIP_CACHE_DIR", "")
    if pip_cache and Path(pip_cache).drive.upper() == drive:
        ok(f"PIP_CACHE_DIR={pip_cache}")
    else:
        warn(f"PIP_CACHE_DIR is {'not set' if not pip_cache else pip_cache} in this shell - "
             f"a pip install from here would cache on C:")

    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    strays = [p for p in (home / ".cache" / "huggingface", home / ".cache" / "torch", home / ".cache" / "chroma",
                          home / ".cache" / "clip", home / ".u2net", home / ".rembg", home / ".matplotlib",
                          appdata / "Ultralytics") if p.exists()]
    if strays:
        warn("model caches found on the system drive: " + ", ".join(str(p) for p in strays))
    else:
        ok("no model caches on the system drive")


def check_packages() -> None:
    section("Packages")
    versions, missing = [], []
    for name in ("torch", "torchvision", "open_clip_torch", "ultralytics", "rembg", "onnxruntime",
                 "chromadb", "requests", "pillow", "numpy"):
        try:
            versions.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        fail("not installed: " + ", ".join(missing) + "  ->  pip install " + " ".join(missing))
    else:
        ok(", ".join(versions))
    try:
        if "+cpu" in metadata.version("torch"):
            note("torch is the CPU build: YOLO, U-2-Net and CLIP run on CPU (about 2-3 s per photo); "
                 "only Ollama uses the GPU.")
    except metadata.PackageNotFoundError:
        pass


def check_weights() -> None:
    section("Model weights")
    checks = [
        ("YOLOv8n", [fc.YOLO_WEIGHTS] if fc.YOLO_WEIGHTS.exists() else [], "6 MB"),
        ("U-2-Net", list(Path(os.environ["U2NET_HOME"]).rglob("u2net.onnx")), "176 MB"),
        ("OpenCLIP ViT-B-32", list((Path(os.environ["HF_HOME"]) / "hub").glob(
            "models--" + fc.CLIP_HF_REPO.replace("/", "--") + "/snapshots/*/open_clip_*")), "577 MB"),
    ]
    for label, found, size in checks:
        if found:
            ok(f"{label}: {found[0]} ({found[0].stat().st_size / 1e6:.0f} MB)")
        else:
            warn(f"{label} weights not downloaded yet ({size}) - the first ingest run fetches them (needs internet)")


def check_ollama() -> bool:
    section("Ollama")
    try:
        fc.ollama_check()
    except fc.OllamaError as exc:
        fail(str(exc))
        return False
    ok(f"server reachable at {fc.OLLAMA_URL}, model {fc.OLLAMA_MODEL} is pulled")
    try:
        loaded = fc._session().get(f"{fc.OLLAMA_URL}/api/ps", timeout=5).json().get("models", [])
        if loaded:
            note("in VRAM right now: " + ", ".join(m.get("name", "?") for m in loaded)
                 + " (unloads after your OLLAMA_KEEP_ALIVE idle time)")
        else:
            note("nothing loaded in VRAM right now (good while idle; the next call pays a 10-40 s model load).")
    except Exception:
        pass
    return True


def check_gpu() -> None:
    section("GPU")
    temp = fc.gpu_temp_c()
    if temp is None:
        warn("cannot read the GPU temperature - the thermal pauses (--max-temp) will not work")
    else:
        ok(f"GPU temperature readable: {temp} C")


def check_wardrobe() -> None:
    section("Wardrobe")
    import ingest_wardrobe as iw

    missing_dirs = [str(p) for p in (fc.RAW_DIR, fc.CUTOUT_DIR, fc.CHROMA_DIR) if not p.is_dir()]
    if missing_dirs:
        warn("folders missing (created on the first ingest run): " + ", ".join(missing_dirs))
    photos = iw.find_photos() if fc.RAW_DIR.is_dir() else []
    if not fc.DB_PATH.exists():
        note(f"no wardrobe yet ({len(photos)} photo(s) waiting in {fc.RAW_DIR}). "
             f"Run:  python ingest_wardrobe.py --limit 3")
        return

    conn = fc.open_db()
    items = fc.load_items(conn)
    item_ids = {item["id"] for item in items}
    vector_ids = set(fc.open_collection().get(include=[])["ids"])

    if item_ids == vector_ids:
        ok(f"SQLite and ChromaDB agree: {len(items)} item(s)")
    else:
        no_vector = [i for i in items if i["id"] not in vector_ids]
        if no_vector:
            fail(f"{len(no_vector)} item(s) have tags but no vector (" + ", ".join(Path(i["raw_path"]).name for i in no_vector[:5])
                 + ") - redo them with:  python ingest_wardrobe.py --only <name>")
        orphans = vector_ids - item_ids
        if orphans:
            warn(f"{len(orphans)} vector(s) in ChromaDB have no SQLite row (harmless: the stylist skips them)")

    no_cutout = [i for i in items if not fc.abs_path(i["cutout_path"]).is_file()]
    if no_cutout:
        fail(f"{len(no_cutout)} item(s) lost their cutout PNG (" + ", ".join(Path(i["raw_path"]).name for i in no_cutout[:5])
             + ") - redo them with:  python ingest_wardrobe.py --only <name>")
    elif items:
        ok("every item has its cutout PNG")
    no_photo = [i for i in items if not fc.abs_path(i["raw_path"]).is_file()]
    if no_photo:
        warn(f"{len(no_photo)} item(s) whose original photo was moved or deleted since ingest ("
             + ", ".join(Path(i["raw_path"]).name for i in no_photo[:5]) + ") - they are still in the wardrobe")
    stray_cutouts = [p.name for p in fc.CUTOUT_DIR.glob("*.png") if p.stem not in item_ids] if fc.CUTOUT_DIR.is_dir() else []
    if stray_cutouts:
        warn(f"{len(stray_cutouts)} cutout file(s) belong to no item: " + ", ".join(stray_cutouts[:5]))

    waiting = [p.name for p in photos if iw.file_id(p) not in item_ids]
    if waiting:
        note(f"{len(waiting)} photo(s) not ingested yet ({', '.join(waiting[:5])}{' ...' if len(waiting) > 5 else ''}). "
             f"Run:  python ingest_wardrobe.py")

    if not items:
        return
    by_category = Counter(item["category"] for item in items)
    note("categories: " + ", ".join(f"{by_category.get(c, 0)} {c}" for c in fc.CATEGORIES))
    if not (by_category["one_piece"] or (by_category["top"] and by_category["bottom"])):
        warn("no top + bottom pair (and no one-piece) yet - the stylist cannot build a complete outfit")
    if not by_category["shoes"]:
        warn("no shoes yet - every outfit will be reported as incomplete")
    not_removed = [Path(i["raw_path"]).name for i in items if not i["bg_removed"]]
    if not_removed:
        warn(f"background removal failed on {len(not_removed)} photo(s): " + ", ".join(not_removed[:5])
             + " - reshoot on a contrasting background, or try another FASHIONAI_REMBG_MODEL")
    unsure = [Path(i["raw_path"]).name for i in items if i["primary_color"] == "unknown"]
    if unsure:
        warn(f"{len(unsure)} item(s) have an unknown colour: " + ", ".join(unsure[:5]))
    worn = sum(1 for i in items if i["worn_photo"])
    if worn:
        note(f"{worn} item(s) come from photos of someone wearing them (cutout shows the whole person).")


# ---------------------------------------------------------------------------
def _test_photo():
    """A drawn navy t-shirt on a beige background - enough to exercise every model."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter

    rng = np.random.default_rng(1)
    background = (200, 180, 150)
    pixels = np.clip(np.array(background, dtype=float)[None, None, :] + rng.normal(0, 4, (1100, 900, 3)), 0, 255)
    img = Image.fromarray(pixels.astype("uint8"))
    draw = ImageDraw.Draw(img)
    draw.polygon([(300, 200), (380, 170), (450, 205), (520, 170), (600, 200), (760, 300), (700, 420), (620, 370),
                  (620, 900), (280, 900), (280, 370), (200, 420), (140, 300)], fill=(25, 42, 86), outline=(15, 25, 55))
    draw.ellipse((395, 150, 505, 225), fill=background)
    return img.filter(ImageFilter.GaussianBlur(0.8))


def _timed(label: str, action):
    """Run one deep step; report OK with its duration, or FAIL with the error. Returns the result or None."""
    started = time.time()
    try:
        result, summary = action()
    except Exception as exc:
        fail(f"{label}: {type(exc).__name__}: {exc}")
        return None
    ok(f"{label}: {summary} ({time.time() - started:.1f} s)")
    return result


def check_deep(ollama_up: bool) -> None:
    section("Deep check: every model once on a generated test photo (nothing is saved)")
    import ingest_wardrobe as iw

    photo = _test_photo()

    def yolo():
        crop, worn = iw.PersonDetector().crop_to_person(photo)
        if worn:
            raise RuntimeError("a person was detected in a photo that has none")
        return crop, "YOLOv8n loaded, no person found (correct)"

    def rembg():
        cutout, removed = iw.BackgroundRemover().cutout(photo)
        if not removed:
            raise RuntimeError("the background was not removed")
        return cutout, f"background removed, cutout {cutout.width}x{cutout.height}"

    _timed("person detector", yolo)
    cutout = _timed("background removal", rembg)
    if cutout is None:
        return
    flat = fc.flatten_on_white(cutout)

    def clip():
        embedder = fc.ClipEmbedder()
        image_vec = embedder.embed_image(flat)
        shirt, boots = embedder.embed_texts(["a photo of a navy t-shirt", "a photo of brown leather boots"])
        sim_shirt = sum(a * b for a, b in zip(image_vec, shirt))
        sim_boots = sum(a * b for a, b in zip(image_vec, boots))
        if len(image_vec) != 512 or sim_shirt <= sim_boots:
            raise RuntimeError(f"unexpected embedding (dim {len(image_vec)}, t-shirt {sim_shirt:.3f}, boots {sim_boots:.3f})")
        return image_vec, f"512-d vectors; matches 't-shirt' {sim_shirt:.3f} > 'boots' {sim_boots:.3f}"

    _timed("CLIP embeddings", clip)

    if not ollama_up:
        warn("tagger skipped: Ollama is not available")
        return

    def tagger():
        tags, reply = iw.tag_garment(flat, worn=False)
        summary = (f"{tags['category']} / {tags['subcategory']} / {tags['primary_color']}; "
                   f"model call {reply['total_s']:.1f} s" + (f" incl. {reply['load_s']:.0f} s load" if reply["load_s"] > 1 else ""))
        if tags["category"] != "top":
            raise RuntimeError(f"a t-shirt was tagged as {summary}")
        return tags, summary

    _timed('tagger ("think": false)', tagger)
    temp = fc.gpu_temp_c()
    if temp is not None:
        note(f"GPU after the tagger call: {temp} C")


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-test for the FashionAI Phase 1 setup.")
    parser.add_argument("--deep", action="store_true",
                        help="also run YOLO, U-2-Net, CLIP and the tagger once on a generated test photo")
    args = parser.parse_args()

    print(f"FashionAI Phase 1 check - project {fc.ROOT}, data {fc.DATA_DIR}")
    check_environment()
    check_packages()
    check_weights()
    ollama_up = check_ollama()
    check_gpu()
    try:
        check_wardrobe()
    except Exception as exc:
        fail(f"wardrobe check crashed: {type(exc).__name__}: {exc}")
    if args.deep:
        check_deep(ollama_up)

    print(f"\nResult: {_counts['OK']} ok, {_counts['WARN']} warning(s), {_counts['FAIL']} failure(s)")
    if not args.deep:
        print("Add --deep to run every model once on a generated test photo.")
    return 1 if _counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
