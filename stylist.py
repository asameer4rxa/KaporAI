"""
stylist.py - Phase 1 outfit recommender.

    your request --OpenCLIP text encoder--> ChromaDB: best matches per category
                 --qwen3.5:4b ("think": false unless you ask)--> outfits as validated JSON
                 --> console summary + one "outfit board" PNG per outfit in .\\outfits

Usage (PowerShell, in D:\\FashionAI with the venv activated):
    python stylist.py "smart casual dinner, around 15C and windy"
    python stylist.py "gym then coffee with friends" --preset athleisure --outfits 3
    python stylist.py "wedding guest, garden ceremony, 28C" --think          # opt-in reasoning
    python stylist.py "5-day work trip, pack light" --think --think-budget 3000
    python stylist.py "gym session" --show-candidates          # also print what CLIP retrieved
    python stylist.py "gym session" --retrieve-only            # print the retrieval and stop (no LLM, no GPU)
    python stylist.py --presets                                # list the style presets
    python stylist.py                                          # asks you for the occasion

Personalise it by editing user_profile.json (created on the first run).

Thinking is OFF by default ("think": false - about 5 s of GPU per request).
Add --think only when you choose to, for complex styling problems: qwen3.5:4b
thinks at length (thousands of tokens if you let it), which means up to a
minute of full GPU load. So with --think the reasoning is streamed and cut off
at --think-budget tokens (or when the GPU reaches --max-temp); the model then
writes its final answer from the reasoning it has done so far.
"""

from __future__ import annotations

import fashion_core as fc  # keep this the first project import: it pins all model caches to this drive

import argparse
import json
import sys
import time
from datetime import datetime
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

THINK_BUDGET = 1500    # cap on thinking tokens when --think is used: ~25 s on an RTX 5060 Laptop GPU, ~79 C
ANSWER_BUDGET = 900    # cap on the tokens of the JSON answer itself

# code prefix per category, in the order items are shown
SLOTS = {"outerwear": "O", "top": "T", "one_piece": "D", "bottom": "B", "shoes": "S", "accessory": "A"}
SLOT_TITLES = {"outerwear": "OUTERWEAR", "top": "TOPS", "one_piece": "ONE-PIECES (dresses, jumpsuits, robes)",
               "bottom": "BOTTOMS", "shoes": "SHOES", "accessory": "ACCESSORIES"}

PRESETS = {
    "personal": {
        "brief": "No fixed aesthetic: follow the wearer's profile and the taste their wardrobe already shows.",
        "clip": "",
    },
    "contemporary": {
        "brief": "Current mainstream fashion: relaxed tailoring, clean lines, tonal or neutral palettes, quality "
                 "basics, subtle layering.",
        "clip": "modern contemporary",
    },
    "minimalist": {
        "brief": "Minimalist: neutral colours, no loud patterns or logos, clean silhouettes, very few accessories.",
        "clip": "minimalist",
    },
    "smart-casual": {
        "brief": "Smart casual: polished but relaxed - collared shirts or fine knitwear, chinos or dark denim, "
                 "loafers or clean sneakers, optional blazer.",
        "clip": "smart casual",
    },
    "business": {
        "brief": "Business: office-appropriate tailoring, shirts, trousers, blazers, leather shoes, restrained colours.",
        "clip": "business office formal",
    },
    "streetwear": {
        "brief": "Streetwear: relaxed or oversized fits, sneakers, hoodies, graphic pieces, caps, confident layering.",
        "clip": "streetwear",
    },
    "old-money": {
        "brief": "Old money / quiet luxury: preppy classics - knit polos, oxford shirts, pleated trousers, loafers; "
                 "navy, cream, beige, forest green; no visible logos.",
        "clip": "old money preppy classic",
    },
    "athleisure": {
        "brief": "Athleisure: sporty comfort - joggers, technical fabrics, hoodies, trainers - but still put together.",
        "clip": "athleisure sporty",
    },
    "bold": {
        "brief": "Bold: build each outfit around one statement colour or pattern and keep everything else quiet.",
        "clip": "bold colourful statement",
    },
}

PROFILE_TEMPLATE = {
    "_help": "Fill in what you like and leave the rest empty - the stylist reads every non-empty field. "
             "Examples: style_of_dress 'menswear'; body_build 'athletic, broad shoulders'; "
             "fit_preferences 'relaxed tops, tapered trousers'; style_notes 'I like earth tones, no big logos'.",
    "style_of_dress": "",
    "height_cm": None,
    "weight_kg": None,
    "body_build": "",
    "sizes": {"top": "", "bottom": "", "shoes": ""},
    "fit_preferences": "",
    "favourite_colours": [],
    "colours_to_avoid": [],
    "style_notes": "",
}

# No "think briefly" instructions in here on purpose: in testing the model ignored
# them or burned tokens debating them. The token budget is enforced in code instead.
STYLIST_SYSTEM = """You are a personal stylist. Build outfits ONLY from the wardrobe items the user lists, referring to them by their codes (T1, B2, S1 ...). Never invent items.

Rules:
1. A complete outfit is one TOP + one BOTTOM (or one ONE-PIECE) + SHOES. Add OUTERWEAR only when the weather, season or formality calls for it. Accessories are optional, at most 2. A second top only for real layering (a tee under knitwear).
2. Formality: every piece within one level of the others, and right for the occasion.
3. Colour: anchor on neutrals, at most three colours, one accent. Tonal looks are welcome; no clashing saturated colours.
4. Pattern: at most one bold pattern per outfit.
5. Fit: balance volume (relaxed with fitted) and respect the wearer's build and fit preferences.
6. Weather: use the warmth and season tags; never under- or over-dress for the stated conditions.
7. The outfits must be clearly different from each other.
8. If the wardrobe lacks something the occasion needs, still give the best outfit and name what is missing in "wardrobe_gap" (use "" when nothing is missing).
9. Taste: lean towards combinations like the ones the wearer liked before and away from the ones they disliked, without simply repeating an old outfit.
10. Rotation: items marked "worn today", "worn yesterday" or "worn N days ago" were worn recently. Prefer other pieces when a good alternative exists; shoes and outerwear may repeat.

Style direction: {style}
Wearer profile: {profile}
Taste history: {taste}

Answer with JSON:
- occasion_needs: one sentence on the formality level and warmth this occasion calls for.
- name: a short title for the outfit.
- items: the codes of the items in the outfit.
- why_it_works: one or two sentences on colour, proportion and suitability for the occasion.
- styling_tips: one sentence on how to wear it (tuck, roll, layer, accessories)."""


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------
def save_profile(profile: dict) -> None:
    fc.PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


def load_profile(announce: bool = True) -> dict:
    if not fc.PROFILE_PATH.exists():
        save_profile(PROFILE_TEMPLATE)
        if announce:
            print(f"Created {fc.PROFILE_PATH} - fill in your build, sizes and fit preferences to personalise results.\n")
    try:
        profile = json.loads(fc.PROFILE_PATH.read_text(encoding="utf-8-sig"))
        return profile if isinstance(profile, dict) else {}
    except ValueError as exc:
        print(f"[warn] {fc.PROFILE_PATH.name} is not valid JSON ({exc}); continuing without a profile.\n")
        return {}


def profile_text(profile: dict) -> str:
    """Flatten the non-empty profile fields into one line for the prompt."""
    parts = []
    for key, value in profile.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict):
            value = ", ".join(f"{k} {v}" for k, v in value.items() if v not in ("", None))
        elif isinstance(value, list):
            value = ", ".join(str(v) for v in value if v not in ("", None))
        if value not in ("", None):
            parts.append(f"{key.replace('_', ' ')}: {value}")
    return "; ".join(parts) if parts else "not provided - dress for a general adult audience"


# ---------------------------------------------------------------------------
# Retrieval: CLIP text query -> nearest wardrobe items per category
# ---------------------------------------------------------------------------
def taste_text(conn) -> str:
    """One line for the prompt built from the outfits the wearer liked / disliked (see the outfits table)."""
    def describe(outfit: dict) -> str:
        items = fc.load_items(conn, outfit["item_ids"])
        if not items:
            return ""
        pieces = " + ".join(
            item["subcategory"] if item["primary_color"] in item["subcategory"]
            else f"{item['primary_color']} {item['subcategory']}" for item in items)
        return f"{pieces} (for: {outfit['occasion'][:60]})"

    liked = [text for text in map(describe, fc.load_outfits(conn, 5, liked=True)) if text]
    disliked = [text for text in map(describe, fc.load_outfits(conn, 3, liked=False)) if text]
    parts = []
    if liked:
        parts.append("liked before: " + "; ".join(liked))
    if disliked:
        parts.append("disliked before: " + "; ".join(disliked))
    return ". ".join(parts) if parts else "none yet"


def retrieve_candidates(conn, clip, collection, occasion: str, preset: str, per_category: int) -> dict[str, dict]:
    """Returns {code: item}, e.g. {"T1": {...}, "T2": {...}, "B1": {...}}.

    clip is a fc.ClipEmbedder and collection the ChromaDB collection (the caller owns them,
    so the server can keep both loaded between requests).
    """
    import numpy as np

    style_words = PRESETS[preset]["clip"]
    query = f"an outfit for {occasion}" + (f", {style_words} style" if style_words else "")
    vector = clip.embed_texts([query])[0]
    liked_ids = {i for outfit in fc.load_outfits(conn, 30, liked=True) for i in outfit["item_ids"]}
    disliked_ids = {i for outfit in fc.load_outfits(conn, 30, liked=False) for i in outfit["item_ids"]} - liked_ids

    def unit(rows):
        lengths = np.linalg.norm(rows, axis=-1, keepdims=True)
        return rows / np.where(lengths == 0, 1.0, lengths)

    candidates: dict[str, dict] = {}
    for category, prefix in SLOTS.items():
        # Fetch more than needed, then let rotation and your likes decide which ones make the cut.
        found = collection.query(query_embeddings=[vector], n_results=per_category * 2 + 2,
                                 where={"category": category}, include=["distances", "embeddings"])
        ids = found["ids"][0] if found["ids"] else []
        if not ids:
            continue
        distances = dict(zip(ids, (float(d) for d in found["distances"][0])))
        scores = dict(distances)

        # Taste: compare within the category, after removing what all its items have in common
        # (raw CLIP vectors of clothes are all alike, which would make every item look "liked").
        vectors = np.asarray(found["embeddings"][0], dtype=float)
        if len(ids) > 1:
            centred = unit(vectors - vectors.mean(axis=0))
            for group, sign in ((liked_ids, -1.0), (disliked_ids, 1.0)):
                rows = [n for n, item_id in enumerate(ids) if item_id in group]
                if rows:
                    direction = unit(centred[rows].mean(axis=0))
                    for n, item_id in enumerate(ids):
                        scores[item_id] += sign * TASTE_WEIGHT * float(centred[n] @ direction)

        items = fc.load_items(conn, ids)
        # Rotation: tops, bottoms and one-pieces worn today or yesterday sit out when there is an alternative.
        if category in ROTATED:
            rested = [item for item in items if (days := _days_since(item.get("last_worn"))) is None or days >= REST_DAYS]
            if rested:
                items = rested
        items.sort(key=lambda item: scores[item["id"]])
        for number, item in enumerate(items[:per_category], 1):
            item["clip_distance"] = distances.get(item["id"], 0.0)  # cosine distance to the request: lower = closer
            item["taste_score"] = scores.get(item["id"], 0.0)       # the same, adjusted by your likes and dislikes
            candidates[f"{prefix}{number}"] = item
    return candidates


TASTE_WEIGHT = 0.05           # how strongly likes / dislikes move an item in the ranking (0 = ignore taste)
ROTATED = ("top", "bottom", "one_piece")
REST_DAYS = 2                 # days an item of those categories rests after being worn


def taste_vectors(conn, collection):
    """(liked, disliked): the average CLIP vector of the items in outfits you liked / disliked, or None.

    An item that looks like things you liked scores closer to `liked`; retrieval uses that to
    prefer it. This costs no GPU time - the vectors are already in ChromaDB.
    """
    import numpy as np

    def centroid(want_liked: bool):
        ids = sorted({item_id for outfit in fc.load_outfits(conn, 30, liked=want_liked) for item_id in outfit["item_ids"]})
        if not ids:
            return None
        stored = collection.get(ids=ids, include=["embeddings"])["embeddings"]
        if stored is None or len(stored) == 0:
            return None
        mean = np.asarray(stored, dtype=float).mean(axis=0)
        length = float(np.linalg.norm(mean))
        return mean / length if length else None

    return centroid(True), centroid(False)


def _days_since(day: str | None) -> int | None:
    from datetime import date

    try:
        return (date.today() - date.fromisoformat(day)).days if day else None
    except ValueError:
        return None


def wardrobe_text(candidates: dict[str, dict]) -> str:
    lines: list[str] = []
    for category in SLOTS:
        rows = [(code, item) for code, item in candidates.items() if item["category"] == category]
        if not rows:
            continue
        lines.append(SLOT_TITLES[category])
        for code, item in rows:
            colours = "/".join([item["primary_color"], *item["secondary_colors"]])
            facts = [code, item["subcategory"], colours, item["pattern"], item["material"]]
            if item["fit"] != "unknown":
                facts.append(f"{item['fit']} fit")
            facts += [f"formality {item['formality']}/5", f"warmth {item['warmth']}/5", "/".join(item["seasons"])]
            days = _days_since(item.get("last_worn"))
            if days is not None and days <= 7:
                facts.append("worn today" if days <= 0 else "worn yesterday" if days == 1 else f"worn {days} days ago")
            lines.append(" | ".join(facts) + f" | {item['description']}")
        lines.append("")
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# The stylist call
# ---------------------------------------------------------------------------
def outfit_schema(codes: list[str], n_outfits: int) -> dict:
    """JSON schema for the answer. The enum makes it impossible to name an item that is not a candidate."""
    return {
        "type": "object",
        "properties": {
            # first on purpose: the model states what the occasion needs before it picks any item
            "occasion_needs": {"type": "string"},
            "outfits": {
                "type": "array",
                "minItems": n_outfits,
                "maxItems": n_outfits,
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "items": {"type": "array", "items": {"type": "string", "enum": codes},
                                  "minItems": 2, "maxItems": min(7, len(codes))},
                        "why_it_works": {"type": "string"},
                        "styling_tips": {"type": "string"},
                    },
                    "required": ["name", "items", "why_it_works", "styling_tips"],
                },
            },
            "wardrobe_gap": {"type": "string"},
        },
        "required": ["occasion_needs", "outfits", "wardrobe_gap"],
    }


class ThinkingWatch:
    """Called for every thinking token: shows progress and decides when the thinking has to stop."""

    def __init__(self, budget: int, max_temp: int) -> None:
        self.budget = budget
        self.max_temp = max_temp
        self.reason = ""           # why the thinking was cut short ("" = it was not)
        self.peak: int | None = None
        self._started = time.time()
        self._live = sys.stdout.isatty()

    def __call__(self, tokens: int) -> bool:
        if tokens >= self.budget:
            self.reason = f"{self.budget}-token budget reached"
            return True
        if tokens % 25 == 0:
            temp = fc.gpu_temp_c()
            if temp is not None:
                self.peak = temp if self.peak is None else max(self.peak, temp)
                if 0 < self.max_temp <= temp and tokens >= 200:
                    self.reason = f"GPU reached {temp} C"
                    return True
            if self._live:
                print(f"\r  thinking ... {tokens}/{self.budget} tokens, {time.time() - self._started:.0f} s"
                      + (f", GPU {temp} C" if temp is not None else "") + "   ", end="", flush=True)
        return False

    def clear(self) -> None:
        if self._live:
            print("\r" + " " * 72 + "\r", end="", flush=True)


def ask_stylist(occasion: str, preset: str, profile: dict, candidates: dict[str, dict],
                n_outfits: int, think_budget: int, max_temp: int, taste: str = "none yet") -> tuple[dict, dict]:
    """Returns (answer, info). think_budget=0 skips the thinking entirely ("think": false)."""
    system = STYLIST_SYSTEM.format(style=PRESETS[preset]["brief"], profile=profile_text(profile), taste=taste)
    user = f"Occasion: {occasion}\nOutfits wanted: {n_outfits}\n\nWARDROBE\n{wardrobe_text(candidates)}"
    schema = outfit_schema(list(candidates), n_outfits)
    info = {"thinking": "", "thinking_tokens": 0, "cut": "", "load_s": 0.0, "peak": None}
    started = time.time()

    if think_budget > 0:
        watch = ThinkingWatch(think_budget, max_temp)
        try:
            # no sampling overrides: the model's own defaults are the ones tuned for thinking mode
            reply = fc.ollama_chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                think=True, schema=schema, options={"num_predict": think_budget + ANSWER_BUDGET},
                on_thinking=watch,
            )
        finally:
            watch.clear()
        info.update(thinking=reply["thinking"], thinking_tokens=reply["thinking_tokens"],
                    load_s=reply["load_s"], peak=watch.peak)
        if not reply["stopped"] and reply["done_reason"] != "length":
            try:
                answer = fc.parse_json(reply["content"])
                info["seconds"] = time.time() - started
                return answer, info
            except ValueError:
                pass
        info["cut"] = watch.reason or "token limit reached"

        # The thinking was cut short: hand the model its own notes and let it answer without thinking further.
        prompt_tokens = (len(system) + len(user)) // 3
        room_chars = max(1500, (fc.OLLAMA_NUM_CTX - prompt_tokens - ANSWER_BUDGET - 300) * 3)
        notes = reply["thinking"][-room_chars:]
        if notes:
            user += ("\n\nYour reasoning notes so far (they may be unfinished):\n" + notes
                     + "\n\nUsing these notes, give the final JSON answer now.")

    reply = fc.ollama_chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        think=False, schema=schema,
        options={"temperature": 0.7, "top_p": 0.8, "presence_penalty": 0.0, "num_predict": ANSWER_BUDGET},
    )
    info["load_s"] = max(info["load_s"], reply["load_s"])
    info["seconds"] = time.time() - started
    return fc.parse_json(reply["content"]), info


def compose_outfits(conn, candidates: dict[str, dict], occasion: str, preset: str, profile: dict,
                    n_outfits: int = 2, think_budget: int = 0, max_temp: int = 80, save: bool = True) -> dict:
    """Ask the stylist, validate the answer and (by default) record the outfits in the history.

    Returns {"brief", "outfits", "wardrobe_gap", "info"}; every outfit has
    "id" (history id, None when not saved), "name", "codes", "items", "why", "tips", "missing".
    Raises fc.OllamaError if Ollama is unavailable and ValueError if the answer is unusable.
    """
    answer, info = ask_stylist(occasion, preset, profile, candidates, n_outfits, think_budget, max_temp,
                               taste_text(conn))
    outfits = clean_outfits(answer, candidates)
    if not outfits:
        raise ValueError("the model returned no usable outfit")
    for outfit in outfits:
        outfit["items"] = [candidates[code] for code in outfit["codes"]]
        outfit["id"] = fc.save_outfit(
            conn, occasion=occasion, preset=preset, name=outfit["name"],
            item_ids=[item["id"] for item in outfit["items"]], why=outfit["why"], tips=outfit["tips"],
            thought=think_budget > 0,
        ) if save else None
    gap = " ".join(str(answer.get("wardrobe_gap") or "").split())
    if gap.lower().strip(".") in ("none", "n/a", "nothing"):
        gap = ""
    return {
        "brief": " ".join(str(answer.get("occasion_needs") or "").split()),
        "outfits": outfits,
        "wardrobe_gap": gap,
        "info": info,
    }


def clean_outfits(answer: dict, candidates: dict[str, dict]) -> list[dict]:
    """Validate the model's outfits: known codes only, no duplicates, one bottom / one-piece / pair of shoes."""
    order = list(SLOTS)
    outfits: list[dict] = []
    seen_sets: set[frozenset] = set()
    for raw in answer.get("outfits") or []:
        if not isinstance(raw, dict):
            continue
        codes: list[str] = []
        singles: set[str] = set()
        for code in raw.get("items") or []:
            code = str(code).strip().upper()
            if code not in candidates or code in codes:
                continue
            category = candidates[code]["category"]
            if category in ("bottom", "one_piece", "shoes"):
                if category in singles:
                    continue
                singles.add(category)
            codes.append(code)
        if not codes or frozenset(codes) in seen_sets:
            continue
        seen_sets.add(frozenset(codes))
        codes.sort(key=lambda c: order.index(candidates[c]["category"]))
        categories = {candidates[c]["category"] for c in codes}
        missing = []
        if "one_piece" not in categories and not {"top", "bottom"} <= categories:
            missing.append("a top + bottom (or a one-piece)")
        if "shoes" not in categories:
            missing.append("shoes")
        outfits.append({
            "name": " ".join(str(raw.get("name") or "Outfit").split())[:60],
            "codes": codes,
            "why": " ".join(str(raw.get("why_it_works") or "").split()),
            "tips": " ".join(str(raw.get("styling_tips") or "").split()),
            "missing": missing,
        })
    return outfits


# ---------------------------------------------------------------------------
# Outfit board (a simple collage of the cutouts)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _font(size: int):
    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_board(outfit: dict, candidates: dict[str, dict], path) -> None:
    tile, pad, title_h, label_h = 300, 24, 70, 44
    codes = outfit["codes"]
    board = Image.new("RGB", (pad + len(codes) * (tile + pad), title_h + tile + label_h + pad), (250, 250, 247))
    draw = ImageDraw.Draw(board)
    draw.text((pad, 20), outfit["name"], font=_font(26), fill=(30, 30, 30))
    for n, code in enumerate(codes):
        item = candidates[code]
        x = pad + n * (tile + pad)
        draw.rounded_rectangle((x, title_h, x + tile, title_h + tile), radius=14,
                               fill=(255, 255, 255), outline=(226, 226, 220))
        try:
            with Image.open(fc.abs_path(item["cutout_path"])) as cutout:
                cutout = cutout.convert("RGBA")
                cutout.thumbnail((tile - 30, tile - 30), Image.LANCZOS)
                board.paste(cutout, (x + (tile - cutout.width) // 2, title_h + (tile - cutout.height) // 2), cutout)
        except OSError:
            draw.text((x + 20, title_h + tile // 2), "(cutout missing)", font=_font(16), fill=(150, 150, 150))
        draw.text((x + 4, title_h + tile + 8), f"{code}  {item['subcategory']}"[:32], font=_font(17), fill=(70, 70, 70))
    board.save(path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _budget(text: str) -> int:
    value = int(text)
    if not 200 <= value <= 4000:
        raise argparse.ArgumentTypeError("must be between 200 and 4000")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Recommend outfits from your local wardrobe.")
    parser.add_argument("occasion", nargs="*", help='e.g. "smart casual dinner, around 15C and windy"')
    parser.add_argument("--preset", choices=sorted(PRESETS), default="personal", help="style direction")
    parser.add_argument("--outfits", type=int, default=2, choices=range(1, 5), metavar="N",
                        help="how many outfits to suggest, 1-4 (default 2)")
    parser.add_argument("--k", type=int, default=6, choices=range(2, 11), metavar="K",
                        help="candidates retrieved per category, 2-10 (default 6)")
    parser.add_argument("--think", action="store_true",
                        help='opt in to reasoning ("think": true) for complex styling problems; off by default')
    parser.add_argument("--think-budget", type=_budget, default=None, metavar="TOKENS",
                        help=f"with --think: max thinking tokens, 200-4000 (default {THINK_BUDGET}, roughly 25 s of GPU)")
    parser.add_argument("--max-temp", type=int, default=80,
                        help="with --think: stop thinking when the GPU reaches this temperature in C "
                             "(default 80, 0 = ignore)")
    parser.add_argument("--show-candidates", action="store_true",
                        help="print the items CLIP retrieved for the stylist, with their match distance")
    parser.add_argument("--retrieve-only", action="store_true",
                        help="print the retrieved candidates and stop: no LLM call, no GPU use")
    parser.add_argument("--show-thinking", action="store_true", help="print the model's reasoning")
    parser.add_argument("--no-board", action="store_true", help="do not save outfit board images")
    parser.add_argument("--presets", action="store_true", help="list the style presets and exit")
    args = parser.parse_args()

    if args.presets:
        for name, preset in PRESETS.items():
            print(f"{name:<13} {preset['brief']}")
        return 0

    occasion = " ".join(args.occasion).strip()
    if not occasion:
        occasion = input("Occasion, plans and weather (e.g. dinner date, 15C, windy): ").strip()
    if not occasion:
        print("No occasion given.")
        return 1

    conn = fc.open_db()
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    if total < 2:
        print(f"The wardrobe has {total} item(s). Put photos in {fc.RAW_DIR} and run:  python ingest_wardrobe.py")
        return 1
    if not args.retrieve_only:
        try:
            fc.ollama_check()
        except fc.OllamaError as exc:
            print(f"ERROR: {exc}")
            return 1
    profile = load_profile()

    print(f"Occasion: {occasion}\nStyle   : {args.preset}\n\nSearching {total} wardrobe items ...", flush=True)
    started = time.time()
    candidates = retrieve_candidates(conn, fc.ClipEmbedder(), fc.open_collection(), occasion, args.preset, args.k)
    if len(candidates) < 2:
        print("Not enough matching items were found. Run  python ingest_wardrobe.py --force  to rebuild the index.")
        return 1
    counts = ", ".join(f"{sum(1 for i in candidates.values() if i['category'] == c)} {c}"
                       for c in SLOTS if any(i["category"] == c for i in candidates.values()))
    print(f"Candidates: {counts}  ({time.time() - started:.1f} s)")
    if args.show_candidates or args.retrieve_only:
        print("\n----- retrieved by CLIP (best match first within each category; lower distance = closer) -----")
        for code, item in candidates.items():
            print(f"  {code:<4}{item['category']:<10}{item['subcategory'][:24]:<26}{item['primary_color'][:12]:<14}"
                  f"distance {item['clip_distance']:.3f}   {item['raw_path']}")
        print()
    if args.retrieve_only:
        return 0

    # Thinking is opt-in: off unless --think (or an explicit --think-budget) is given.
    think_budget = (args.think_budget or THINK_BUDGET) if (args.think or args.think_budget) else 0
    mode = (f"thinking, up to {think_budget} tokens (about {think_budget // 60 + 8} s)" if think_budget
            else "no thinking, a few seconds")
    print(f"Asking {fc.OLLAMA_MODEL}: {mode}. Add 10-40 s if Ollama has to load the model first ...", flush=True)
    try:
        result = compose_outfits(conn, candidates, occasion, args.preset, profile, args.outfits,
                                 think_budget, args.max_temp)
    except fc.OllamaError as exc:
        print(f"ERROR: {exc}")
        return 1
    except ValueError as exc:
        print(f"ERROR: the model's answer was not usable ({exc}). Please try again.")
        return 1
    outfits, info = result["outfits"], result["info"]
    if args.show_thinking and info["thinking"]:
        print("\n----- model reasoning -----\n" + info["thinking"] + "\n---------------------------")

    if think_budget == 0:
        detail = "no thinking"
    else:
        detail = f"{info['thinking_tokens']} thinking tokens"
        if info["cut"]:
            detail += f", cut short: {info['cut']}"
    if info["load_s"] > 1:
        detail += f"; model load {info['load_s']:.0f} s"
    temp = info["peak"] if info["peak"] is not None else fc.gpu_temp_c()
    if temp is not None:
        detail += f"; GPU {'peaked at' if info['peak'] is not None else 'at'} {temp} C"
    print(f"Answered in {info['seconds']:.0f} s ({detail})\n")
    if result["brief"]:
        print(f"Brief: {result['brief']}\n")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for number, outfit in enumerate(outfits, 1):
        print(f"=== Outfit {number}: {outfit['name']} ===")
        for code in outfit["codes"]:
            item = candidates[code]
            print(f"  {code:<4}{item['category']:<10}{item['subcategory']} ({item['primary_color']})"
                  f"  -> {item['cutout_path']}")
        if outfit["why"]:
            print(f"  Why : {outfit['why']}")
        if outfit["tips"]:
            print(f"  Tips: {outfit['tips']}")
        if outfit["missing"]:
            print(f"  Note: incomplete - no {' and no '.join(outfit['missing'])} chosen.")
        if not args.no_board:
            fc.OUTFIT_DIR.mkdir(parents=True, exist_ok=True)
            board_path = fc.OUTFIT_DIR / f"{stamp}_{number}.png"
            render_board(outfit, candidates, board_path)
            print(f"  Board: {board_path}")
        print()
    if result["wardrobe_gap"]:
        print(f"Wardrobe gap: {result['wardrobe_gap']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
