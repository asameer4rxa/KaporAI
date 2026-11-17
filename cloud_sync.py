"""
cloud_sync.py - keeps a private cloud copy of the wardrobe in Supabase and does the AI
work for whatever the phone app leaves there.

    python cloud_sync.py check    # are the settings, tables and storage bucket in place?
    python cloud_sync.py push     # one-time: upload the wardrobe that is already on this PC
    python cloud_sync.py run      # work until Ctrl+C   (or:  python server.py --cloud)

How it fits together:
    phone app  --photo-->  Supabase inbox  --this worker-->  cutout + tags  -->  Supabase
    phone app  --"outfits for ..."--> Supabase --this worker--> qwen3.5:4b --> Supabase
The phone never talks to this PC directly, so it works on any network. All AI still runs
here; when this PC is off, the app can browse and upload, and requests wait in the cloud.

Settings live in .supabase.env next to this file (see the comments in it). The SECRET key
in there gives full access to your Supabase project and must never leave this PC.
The cloud tables are created by cloud/schema.sql (run once in the Supabase SQL Editor).
"""

from __future__ import annotations

import fashion_core as fc  # keep this the first project import: it pins all model caches to this drive

import argparse
import hashlib
import io
import json
import mimetypes
import os
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import ingest_wardrobe as iw
import stylist as st

CONFIG_PATH = fc.ROOT / ".supabase.env"
STATE_PATH = fc.DATA_DIR / ".cloud_state.json"   # remembers what was already synced
BUCKET = "wardrobe"
TABLES = ("items", "uploads", "style_requests", "outfits", "profiles", "worker_status")
POLL_SECONDS = 4.0
HEARTBEAT_SECONDS = 30.0
THUMB_SIDE = 400

TAG_FIELDS = ("category", "subcategory", "primary_color", "secondary_colors", "pattern", "material", "fit",
              "formality", "warmth", "seasons", "occasions", "style_tags", "description")


class CloudError(RuntimeError):
    """Supabase is unreachable, misconfigured, or answered with an error."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_thumbnail(path: Path) -> bytes:
    """A small WebP preview of a cutout: keeps the app fast and the downloads tiny."""
    from PIL import Image

    with Image.open(path) as image:
        thumb = image.convert("RGBA")
        thumb.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
        buffer = io.BytesIO()
        thumb.save(buffer, format="WEBP", quality=82)
    return buffer.getvalue()


def load_config() -> dict[str, str]:
    """Read .supabase.env (environment variables of the same name win)."""
    values: dict[str, str] = {}
    if CONFIG_PATH.is_file():
        for line in CONFIG_PATH.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "SUPABASE_SECRET_KEY", "SUPABASE_OWNER_EMAIL"):
        values[key] = os.environ.get(key, "").strip() or values.get(key, "")
    missing = [key for key in ("SUPABASE_URL", "SUPABASE_SECRET_KEY", "SUPABASE_OWNER_EMAIL") if not values[key]]
    if missing:
        raise CloudError(f"Fill in {', '.join(missing)} in {CONFIG_PATH}")
    if not values["SUPABASE_URL"].startswith("https://"):
        raise CloudError("SUPABASE_URL must look like https://abcdefghijkl.supabase.co")
    return values


# ---------------------------------------------------------------------------
# A thin Supabase client (REST, Storage and Auth admin) on top of requests
# ---------------------------------------------------------------------------
class Cloud:
    def __init__(self, url: str, secret_key: str) -> None:
        import requests

        self._requests = requests
        self.url = url.rstrip("/")
        self.http = requests.Session()
        self.http.headers["apikey"] = secret_key
        if not secret_key.startswith("sb_"):
            # legacy service_role keys (long JWTs starting with eyJ) must also be sent as a bearer token
            self.http.headers["Authorization"] = f"Bearer {secret_key}"

    def _call(self, method: str, path: str, **kwargs):
        try:
            reply = self.http.request(method, self.url + path, timeout=90, **kwargs)
        except self._requests.exceptions.RequestException as exc:
            raise CloudError(f"Cannot reach Supabase ({type(exc).__name__}). Is this PC online?") from exc
        if reply.status_code >= 400:
            try:
                body = reply.json()
                detail = body.get("message") or body.get("msg") or body.get("error") or reply.text
            except ValueError:
                detail = reply.text
            raise CloudError(f"Supabase {method} {path.split('?')[0]} failed ({reply.status_code}): {str(detail)[:300]}",
                             reply.status_code)
        return reply

    # --- database rows (PostgREST) -------------------------------------------------
    def select(self, table: str, **filters: str) -> list[dict]:
        """filters are PostgREST query parameters, e.g. status="eq.pending", order="created_at", limit="5"."""
        return self._call("GET", f"/rest/v1/{table}", params={"select": "*", **filters}).json()

    def insert(self, table: str, rows: list[dict], on_conflict: str | None = None) -> list[dict]:
        """Insert rows; with on_conflict (comma-separated key columns) existing rows are updated instead."""
        headers = {"Prefer": "return=representation" + (",resolution=merge-duplicates" if on_conflict else "")}
        params = {"on_conflict": on_conflict} if on_conflict else None
        return self._call("POST", f"/rest/v1/{table}", json=rows, headers=headers, params=params).json()

    def update(self, table: str, match: dict[str, str], values: dict) -> None:
        self._call("PATCH", f"/rest/v1/{table}", params=match, json=values)

    def delete(self, table: str, match: dict[str, str]) -> None:
        self._call("DELETE", f"/rest/v1/{table}", params=match)

    # --- files (Storage) -----------------------------------------------------------
    def download(self, path: str) -> bytes:
        return self._call("GET", f"/storage/v1/object/{BUCKET}/{path}").content

    def upload(self, path: str, data: bytes, content_type: str) -> None:
        self._call("POST", f"/storage/v1/object/{BUCKET}/{path}", data=data,
                   headers={"Content-Type": content_type, "x-upsert": "true"})

    def remove(self, paths: list[str]) -> None:
        paths = [path for path in paths if path]
        if paths:
            self._call("DELETE", f"/storage/v1/object/{BUCKET}", json={"prefixes": paths})

    def bucket_exists(self) -> bool:
        try:
            self._call("GET", f"/storage/v1/bucket/{BUCKET}")
            return True
        except CloudError as exc:
            if exc.status in (400, 404):
                return False
            raise

    # --- users (Auth admin) --------------------------------------------------------
    def find_user_id(self, email: str) -> str | None:
        page = 1
        while page <= 20:
            users = self._call("GET", "/auth/v1/admin/users", params={"page": page, "per_page": 200}).json().get("users", [])
            for user in users:
                if (user.get("email") or "").lower() == email.lower():
                    return user["id"]
            if len(users) < 200:
                return None
            page += 1
        return None


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------
class Worker:
    """One pass of tick() handles everything that is waiting in the cloud for this PC."""

    def __init__(self, cloud: Cloud, owner_id: str, get_pipeline, get_collection, lock: threading.Lock) -> None:
        self.cloud = cloud
        self.owner = owner_id
        self.get_pipeline = get_pipeline      # loads YOLO + U-2-Net + CLIP on first use
        self.get_collection = get_collection  # the ChromaDB collection (cheap)
        self.lock = lock                      # shared with the API server: one model job at a time
        self._mine = {"user_id": f"eq.{owner_id}"}
        self._last_heartbeat = 0.0
        self._state = self._load_state()
        self._has_wear: bool | None = None    # does the cloud have the wear-log columns yet?
        self._has_worn_photos: bool | None = None  # ... and the worn_photos table?

    def has_wear(self) -> bool:
        """True once cloud/schema.sql has been re-run with the wear-log columns (checked again until it is)."""
        if not self._has_wear:
            try:
                self.cloud._call("GET", "/rest/v1/outfits", params={"select": "worn_synced", "limit": "1"})
                self.cloud._call("GET", "/rest/v1/items", params={"select": "wear_count,last_worn", "limit": "1"})
                self._has_wear = True
            except CloudError as exc:
                if exc.status == 0:
                    raise
                if self._has_wear is None:
                    print("[cloud] the wear log is not synced yet: run cloud/schema.sql again in the Supabase SQL Editor",
                          flush=True)
                self._has_wear = False
        return bool(self._has_wear)

    # --- small helpers -----------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        STATE_PATH.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

    def _item_match(self, item_id: str) -> dict[str, str]:
        return {**self._mine, "id": f"eq.{item_id}"}

    def _row_for(self, item: dict) -> dict:
        row = {field: item[field] for field in TAG_FIELDS}
        row.update(user_id=self.owner, id=item["id"], worn_photo=bool(item["worn_photo"]),
                   bg_removed=bool(item["bg_removed"]), tagger_model=item["tagger_model"])
        if self.has_wear():
            row.update(wear_count=item.get("wear_count", 0), last_worn=item.get("last_worn"))
        return row

    def push_item(self, item: dict, files: bool = True) -> None:
        """Copy one local item to the cloud: (optionally) its three image files, then its row."""
        row = self._row_for(item)
        stale: list[str] = []
        if files:
            cutout = fc.abs_path(item["cutout_path"])
            photo = fc.abs_path(item["raw_path"])
            base = f"{self.owner}"
            existing = self.cloud.select("items", **self._item_match(item["id"]), limit="1")
            before = existing[0] if existing else {}
            if cutout.is_file():
                # The file name carries the cutout's timestamp: a changed cutout gets a new address,
                # so the app (which caches images by address) shows it at once, and an unchanged
                # one is not uploaded again.
                stamp = int(cutout.stat().st_mtime)
                row["cutout_path"] = f"{base}/cutouts/{item['id']}-{stamp}.png"
                row["thumb_path"] = f"{base}/thumbs/{item['id']}-{stamp}.webp"
                if before.get("cutout_path") != row["cutout_path"]:
                    self.cloud.upload(row["cutout_path"], cutout.read_bytes(), "image/png")
                    self.cloud.upload(row["thumb_path"], make_thumbnail(cutout), "image/webp")
                    stale += [before.get("cutout_path"), before.get("thumb_path")]
            if photo.is_file():
                row["photo_path"] = f"{base}/photos/{item['id']}{photo.suffix.lower() or '.jpg'}"
                if before.get("photo_path") != row["photo_path"]:
                    self.cloud.upload(row["photo_path"], photo.read_bytes(),
                                      mimetypes.guess_type(photo.name)[0] or "application/octet-stream")
        self.cloud.insert("items", [row], on_conflict="user_id,id")
        self.cloud.remove(stale)

    # --- the jobs ----------------------------------------------------------------
    def heartbeat(self, conn, force: bool = False) -> None:
        if not force and time.time() - self._last_heartbeat < HEARTBEAT_SECONDS:
            return
        items = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        self.cloud.insert("worker_status", [{"user_id": self.owner, "last_seen": now_iso(),
                                             "info": {"items": items, "model": fc.OLLAMA_MODEL,
                                                      # the app has no other way to learn the style presets
                                                      "presets": [{"name": name, "brief": preset["brief"]}
                                                                  for name, preset in st.PRESETS.items()]}}],
                          on_conflict="user_id")
        self._last_heartbeat = time.time()

    def reset_interrupted(self) -> None:
        """Jobs left 'processing' by a crash or shutdown go back in the queue."""
        for table in ("uploads", "style_requests") + (("worn_photos",) if self.has_worn_photos() else ()):
            self.cloud.update(table, {**self._mine, "status": "eq.processing"}, {"status": "pending"})

    def handle_uploads(self, conn) -> int:
        rows = self.cloud.select("uploads", **self._mine, status="eq.pending", order="created_at", limit="5")
        for row in rows:
            match = {"id": f"eq.{row['id']}"}
            self.cloud.update("uploads", match, {"status": "processing"})
            try:
                data = self.cloud.download(row["path"])
                item_id = hashlib.sha256(data).hexdigest()[:16]
                known = fc.load_items(conn, [item_id])
                if known:
                    item, status = known[0], "duplicate"
                else:
                    suffix = Path(row["path"]).suffix.lower() or ".jpg"
                    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(row.get("file_name") or "photo").stem).strip("_")[:40] or "photo"
                    fc.RAW_DIR.mkdir(parents=True, exist_ok=True)
                    photo = fc.RAW_DIR / f"{item_id}_{stem}{suffix}"
                    photo.write_bytes(data)
                    try:
                        with self.lock:
                            item, _ = self.get_pipeline().process(conn, photo, item_id)
                    except Exception:
                        photo.unlink(missing_ok=True)
                        (fc.CUTOUT_DIR / f"{item_id}.png").unlink(missing_ok=True)
                        raise
                    status = "done"
                    print(f"[cloud] new item {item['category']} / {item['subcategory']} / {item['primary_color']}", flush=True)
                self.push_item(item)
                self.cloud.update("uploads", match, {"status": status, "item_id": item["id"], "finished_at": now_iso()})
                self.cloud.remove([row["path"]])
            except (fc.OllamaError, CloudError):
                self.cloud.update("uploads", match, {"status": "pending"})  # not the photo's fault: retry later
                raise
            except Exception as exc:
                self.cloud.update("uploads", match, {"status": "failed", "finished_at": now_iso(),
                                                     "error": f"{type(exc).__name__}: {exc}"[:400]})
                print(f"[cloud] upload {row.get('file_name') or row['path']} failed: {exc}", flush=True)
        return len(rows)

    def handle_item_requests(self, conn) -> int:
        rows = self.cloud.select(
            "items", **self._mine, limit="20",
            **{"or": "(edited_in_app.is.true,retag_requested.is.true,delete_requested.is.true)"})
        for row in rows:
            item_id = row["id"]
            local = fc.load_items(conn, [item_id])
            if row["delete_requested"]:
                with self.lock:
                    fc.remove_item(conn, self.get_collection(), item_id)
                self.cloud.remove([row.get("photo_path"), row.get("cutout_path"), row.get("thumb_path")])
                self.cloud.delete("items", self._item_match(item_id))
                print(f"[cloud] removed item {item_id}", flush=True)
                continue
            if not local:  # the cloud knows an item this PC does not have: nothing to edit or retag here
                self.cloud.update("items", self._item_match(item_id), {"edited_in_app": False, "retag_requested": False})
                continue
            item = local[0]
            flags: dict = {}
            if row["edited_in_app"]:
                try:
                    item.update(iw.normalize_tags({**item, **{field: row[field] for field in TAG_FIELDS}}),
                                tagger_model="manual edit")
                    with self.lock:
                        fc.store_item(conn, self.get_collection(), item)
                except ValueError as exc:
                    print(f"[cloud] ignored an invalid edit of item {item_id}: {exc}", flush=True)
                flags["edited_in_app"] = False
            if row["retag_requested"]:
                with self.lock:
                    item = self.get_pipeline().retag(conn, item_id)
                flags["retag_requested"] = False
            self.cloud.update("items", self._item_match(item_id), {**self._row_for(item), **flags})
        return len(rows)

    def handle_style_requests(self, conn) -> int:
        rows = self.cloud.select("style_requests", **self._mine, status="eq.pending", order="created_at", limit="3")
        for row in rows:
            match = {"id": f"eq.{row['id']}"}
            self.cloud.update("style_requests", match, {"status": "processing"})
            try:
                preset = row["preset"] if row["preset"] in st.PRESETS else "personal"
                think_budget = st.THINK_BUDGET if row.get("think") else 0  # thinking stays opt-in
                with self.lock:
                    pipeline = self.get_pipeline()
                    candidates = st.retrieve_candidates(conn, pipeline.clip, pipeline.collection, row["occasion"], preset, 6)
                    if len(candidates) < 2:
                        raise ValueError("the wardrobe needs at least two items first")
                    result = st.compose_outfits(conn, candidates, row["occasion"], preset,
                                                st.load_profile(announce=False), int(row["outfits"]), think_budget, 80)
                info = result["info"]
                self.cloud.insert("outfits", [{
                    "user_id": self.owner, "local_id": outfit["id"], "request_id": row["id"],
                    "occasion": row["occasion"], "preset": preset, "name": outfit["name"],
                    "item_ids": [item["id"] for item in outfit["items"]], "why": outfit["why"], "tips": outfit["tips"],
                    "missing": outfit["missing"], "thought": think_budget > 0,
                } for outfit in result["outfits"]], on_conflict="user_id,local_id")
                self.cloud.update("style_requests", match, {
                    "status": "done", "finished_at": now_iso(),
                    "result": {"brief": result["brief"], "wardrobe_gap": result["wardrobe_gap"],
                               "seconds": round(info["seconds"], 1),
                               "thinking": {"used": think_budget > 0, "tokens": info["thinking_tokens"],
                                            "cut_short": info["cut"]}},
                })
                print(f"[cloud] styled \"{row['occasion'][:50]}\" in {info['seconds']:.0f} s", flush=True)
            except (fc.OllamaError, CloudError):
                self.cloud.update("style_requests", match, {"status": "pending"})
                raise
            except Exception as exc:
                self.cloud.update("style_requests", match, {"status": "failed", "finished_at": now_iso(),
                                                            "error": f"{exc}"[:400]})
        return len(rows)

    def handle_feedback(self, conn) -> int:
        rows = self.cloud.select("outfits", **self._mine, liked_synced="is.false", limit="50")
        for row in rows:
            if row.get("local_id") is not None:
                fc.set_outfit_feedback(conn, int(row["local_id"]), row["liked"])
            self.cloud.update("outfits", {"id": f"eq.{row['id']}"}, {"liked_synced": True})
        return len(rows)

    def handle_wear(self, conn) -> int:
        """"I wore this" from the app: log it here, then publish the items' new wear counts."""
        if not self.has_wear():
            return 0
        rows = self.cloud.select("outfits", **self._mine, worn_synced="is.false", limit="50")
        for row in rows:
            item_ids = [item_id for item_id in row.get("item_ids") or []]
            local_id = int(row["local_id"]) if row.get("local_id") is not None else None
            if row.get("worn_on") and item_ids:
                fc.log_wear(conn, item_ids, local_id, day=row["worn_on"])
                for item in fc.load_items(conn, item_ids):
                    self.cloud.update("items", self._item_match(item["id"]),
                                      {"wear_count": item["wear_count"], "last_worn": item["last_worn"]})
            self.cloud.update("outfits", {"id": f"eq.{row['id']}"}, {"worn_synced": True})
        return len(rows)

    def has_worn_photos(self) -> bool:
        """True once cloud/schema.sql has been re-run with the worn_photos table."""
        if not self._has_worn_photos:
            try:
                self.cloud.select("worn_photos", limit="1")
                self._has_worn_photos = True
            except CloudError as exc:
                if exc.status == 0:
                    raise
                if self._has_worn_photos is None:
                    print("[cloud] the worn diary is not synced yet: run cloud/schema.sql again in the Supabase SQL Editor",
                          flush=True)
                self._has_worn_photos = False
        return bool(self._has_worn_photos)

    def handle_worn_photos(self, conn) -> int:
        """Outfit photos from the app: remove the background here, publish the cutout, file it in the diary."""
        if not self.has_worn_photos():
            return 0
        done = 0
        for row in self.cloud.select("worn_photos", **self._mine, delete_requested="is.true", limit="20"):
            fc.delete_worn_photo(conn, row["id"].replace("-", "")[:16])
            self.cloud.remove([row.get("inbox_path"), row.get("cutout_path"), row.get("thumb_path")])
            self.cloud.delete("worn_photos", {"id": f"eq.{row['id']}"})
            done += 1
        for row in self.cloud.select("worn_photos", **self._mine, status="eq.pending", order="created_at", limit="5"):
            match = {"id": f"eq.{row['id']}"}
            self.cloud.update("worn_photos", match, {"status": "processing"})
            try:
                outfit = (self.cloud.select("outfits", id=f"eq.{row['outfit_id']}", limit="1") or [None])[0] \
                    if row.get("outfit_id") else None
                local_outfit = int(outfit["local_id"]) if outfit and outfit.get("local_id") is not None else None
                data = self.cloud.download(row["inbox_path"])
                with self.lock:
                    entry = self.get_pipeline().worn_photo(conn, data, row["worn_on"], local_outfit,
                                                           entry_id=row["id"].replace("-", "")[:16])
                cutout = fc.abs_path(entry["cutout_path"])
                paths = {"cutout_path": f"{self.owner}/worn/{row['id']}.png",
                         "thumb_path": f"{self.owner}/worn/{row['id']}.webp"}
                self.cloud.upload(paths["cutout_path"], cutout.read_bytes(), "image/png")
                self.cloud.upload(paths["thumb_path"], make_thumbnail(cutout), "image/webp")
                self.cloud.update("worn_photos", match, {**paths, "status": "done", "inbox_path": None})
                self.cloud.remove([row["inbox_path"]])
                if outfit and local_outfit is not None:  # the photo also counts as "I wore this"
                    self.cloud.update("outfits", {"id": f"eq.{outfit['id']}"}, {"worn_on": row["worn_on"]})
                    if self.has_wear():
                        for item in fc.load_items(conn, list(outfit.get("item_ids") or [])):
                            self.cloud.update("items", self._item_match(item["id"]),
                                              {"wear_count": item["wear_count"], "last_worn": item["last_worn"]})
                print(f"[cloud] outfit photo filed under {row['worn_on']}", flush=True)
            except CloudError:
                self.cloud.update("worn_photos", match, {"status": "pending"})
                raise
            except Exception as exc:
                self.cloud.update("worn_photos", match, {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:400]})
            done += 1
        return done

    def sync_profile(self, conn) -> int:
        rows = self.cloud.select("profiles", **self._mine, limit="1")
        if not rows:  # first run: the cloud gets this PC's profile
            created = self.cloud.insert("profiles", [{"user_id": self.owner, "data": st.load_profile(announce=False)}],
                                        on_conflict="user_id")
            self._state["profile_updated_at"] = created[0]["updated_at"] if created else ""
            self._save_state()
            return 1
        if rows[0]["updated_at"] != self._state.get("profile_updated_at"):
            st.save_profile(rows[0]["data"] or {})   # the app edited the profile
            self._state["profile_updated_at"] = rows[0]["updated_at"]
            self._save_state()
            return 1
        return 0

    def tick(self) -> int:
        """Do everything that is waiting. Returns how many jobs were handled (0 = idle)."""
        conn = fc.open_db()
        try:
            self.heartbeat(conn)
            done = self.handle_item_requests(conn)
            done += self.handle_uploads(conn)
            done += self.handle_style_requests(conn)
            done += self.handle_feedback(conn)
            done += self.handle_wear(conn)
            done += self.handle_worn_photos(conn)
            done += self.sync_profile(conn)
            return done
        finally:
            conn.close()

    def push_everything(self) -> None:
        """One-time upload of the wardrobe and outfit history that already exist on this PC."""
        conn = fc.open_db()
        try:
            items = fc.load_items(conn)
            for number, item in enumerate(items, 1):
                print(f"  [{number}/{len(items)}] {item['subcategory']} ({item['primary_color']})", flush=True)
                self.push_item(item)
            outfits = fc.load_outfits(conn, limit=1000)
            if outfits:
                self.cloud.insert("outfits", [{
                    "user_id": self.owner, "local_id": outfit["id"], "occasion": outfit["occasion"],
                    "preset": outfit["preset"], "name": outfit["name"], "item_ids": outfit["item_ids"],
                    "why": outfit["why"], "tips": outfit["tips"], "thought": outfit["thought"], "liked": outfit["liked"],
                } for outfit in outfits], on_conflict="user_id,local_id")
            self.sync_profile(conn)
            self.heartbeat(conn, force=True)
            print(f"Pushed {len(items)} item(s) and {len(outfits)} outfit(s).")
        finally:
            conn.close()


def connect() -> tuple[Cloud, str]:
    """Open the cloud connection and look up the wardrobe owner's user id."""
    config = load_config()
    cloud = Cloud(config["SUPABASE_URL"], config["SUPABASE_SECRET_KEY"])
    owner_id = cloud.find_user_id(config["SUPABASE_OWNER_EMAIL"])
    if not owner_id:
        raise CloudError(f"No user {config['SUPABASE_OWNER_EMAIL']} in this Supabase project. "
                         f"Create it under Authentication -> Users -> Add user.")
    return cloud, owner_id


def run_forever(worker: Worker, stop: threading.Event | None = None) -> None:
    worker.reset_interrupted()
    print("[cloud] sync is running - waiting for uploads and outfit requests", flush=True)
    last_problem = ""
    while not (stop and stop.is_set()):
        pause = POLL_SECONDS
        try:
            if worker.tick() > 0:
                pause = 0.5
            last_problem = ""
        except (CloudError, fc.OllamaError) as exc:
            if str(exc) != last_problem:  # say it once, then keep retrying quietly
                print(f"[cloud] waiting: {exc}", flush=True)
                last_problem = str(exc)
            pause = 15.0
        except Exception as exc:  # a bug must not kill the worker
            print(f"[cloud] unexpected {type(exc).__name__}: {exc}", flush=True)
            pause = 15.0
        if stop:
            stop.wait(pause)
        else:
            time.sleep(pause)


def start_background(get_pipeline, get_collection, lock: threading.Lock) -> threading.Thread | None:
    """Used by server.py --cloud: run the worker beside the API, sharing its models and lock."""
    try:
        cloud, owner_id = connect()
    except CloudError as exc:
        print(f"[cloud] not started: {exc}", flush=True)
        return None
    worker = Worker(cloud, owner_id, get_pipeline, get_collection, lock)
    thread = threading.Thread(target=run_forever, args=(worker,), name="cloud-sync", daemon=True)
    thread.start()
    return thread


def _standalone_worker() -> Worker:
    cloud, owner_id = connect()
    holder: dict = {}

    def get_collection():
        if "collection" not in holder:
            holder["collection"] = fc.open_collection()
        return holder["collection"]

    def get_pipeline():
        if "pipeline" not in holder:
            print("[cloud] loading the vision models (about 20 s) ...", flush=True)
            holder["pipeline"] = iw.Pipeline(collection=get_collection())
        return holder["pipeline"]

    return Worker(cloud, owner_id, get_pipeline, get_collection, threading.Lock())


def check() -> int:
    """Report, step by step, whether the cloud side is ready."""
    failures = 0

    def report(ok: bool, text: str) -> None:
        nonlocal failures
        failures += 0 if ok else 1
        print(f"[{' OK ' if ok else 'FAIL'}] {text}")

    try:
        config = load_config()
    except CloudError as exc:
        report(False, str(exc))
        return 1
    report(True, f"settings found in {CONFIG_PATH.name} (project {config['SUPABASE_URL']})")
    if not config["SUPABASE_PUBLISHABLE_KEY"]:
        report(False, "SUPABASE_PUBLISHABLE_KEY is empty - the phone app will need it")
    cloud = Cloud(config["SUPABASE_URL"], config["SUPABASE_SECRET_KEY"])
    try:
        owner_id = cloud.find_user_id(config["SUPABASE_OWNER_EMAIL"])
    except CloudError as exc:
        report(False, f"{exc}  (wrong URL or secret key?)")
        return 1
    report(bool(owner_id), f"app login {config['SUPABASE_OWNER_EMAIL']} "
           + ("exists" if owner_id else "not found - create it under Authentication -> Users -> Add user"))
    for table in TABLES:
        try:
            cloud.select(table, limit="1")
            report(True, f"table {table}")
        except CloudError as exc:
            report(False, f"table {table}: {exc}  -> run cloud/schema.sql in the Supabase SQL Editor")
    try:
        report(cloud.bucket_exists(), f"private storage bucket '{BUCKET}'"
               + ("" if cloud.bucket_exists() else " missing -> run cloud/schema.sql in the Supabase SQL Editor"))
    except CloudError as exc:
        report(False, f"storage: {exc}")
    print("\nReady." if failures == 0 else f"\n{failures} problem(s) to fix.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync the wardrobe with its private Supabase copy.")
    parser.add_argument("command", choices=["check", "push", "run"])
    args = parser.parse_args()
    try:
        if args.command == "check":
            return check()
        worker = _standalone_worker()
        if args.command == "push":
            worker.push_everything()
            return 0
        try:
            run_forever(worker)
        except KeyboardInterrupt:
            print("\n[cloud] stopped")
        return 0
    except CloudError as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
