# Phase 1 tests still to run (real clothing photos)

Phase 1 was only verified on drawn test images and one stock photo. Phase 2 (the
FastAPI backend) was built on top before these were run, so tick them off when you
can. Anything that fails here is fixed in `ingest_wardrobe.py` / `stylist.py` and
the server picks the fix up automatically.

Start every session with:

    cd D:\FashionAI; .\venv\Scripts\Activate.ps1
    python check_phase1.py

## Stage 1 - first 3 photos (a top, a bottom, a pair of shoes)

- [ ] `python check_phase1.py --deep` ends with 0 failures
- [ ] 3 photos in `wardrobe_raw` (one garment each, plain contrasting background)
- [ ] `python ingest_wardrobe.py --limit 3` -> `3 ingested, 0 failed`
- [ ] printed category / subcategory / colour match each photo
- [ ] cutouts in `wardrobe_cutouts` are clean: transparent background, whole garment, no holes
- [ ] running ingest again says "Nothing new"
- [ ] `python check_phase1.py` says "SQLite and ChromaDB agree: 3 item(s)"
- [ ] 2-3 minutes after the last call, `ollama ps` is empty

If a cutout is bad: `$env:FASHIONAI_REMBG_MODEL='isnet-general-use'`, then
`python ingest_wardrobe.py --only <file name>`.
If a tag is wrong: raise `VLM_SIDE` to 768 in `ingest_wardrobe.py`, or fix it by
hand through the API (`PATCH /api/items/{id}`).

## Stage 2 - edge cases (one photo each)

- [ ] you wearing a garment -> `[worn photo]`, the main garment is tagged
- [ ] white garment on a white wall -> tags still right (may say `background NOT removed`)
- [ ] striped / checked / graphic item -> correct `pattern`
- [ ] belt, watch or bag -> `accessory`
- [ ] pullover hoodie -> `top`; zip-up hoodie -> `outerwear`
- [ ] a .txt renamed to .jpg -> `FAILED` for that file only, the batch continues

## Stage 3 - full wardrobe

- [ ] `python ingest_wardrobe.py` on everything: 0 FAILED (about 6-8 s per photo)
- [ ] `python ingest_wardrobe.py --list`, spot-check 10 items: >= 9 right category, >= 8 right colour
- [ ] GPU stayed under 80 C (printed per photo); if not, add `--cooldown 2`

## Stage 4 - retrieval (no GPU)

- [ ] `python stylist.py "gym session" --retrieve-only` -> sporty items first
- [ ] `python stylist.py "job interview" --retrieve-only` -> shirt / trousers / leather shoes first
- [ ] `python stylist.py "beach day, 32C" --retrieve-only` -> shorts / linen / sandals first

## Stage 5 - styling (thinking off)

Five requests, two outfits each: casual day, office day, formal event, cold rainy
evening, hot summer day.

- [ ] every outfit is complete (top + bottom or one-piece, plus shoes)
- [ ] formality and warmth fit the occasion and weather
- [ ] at most 3 colours, at most 1 bold pattern, the two outfits differ
- [ ] `--preset minimalist` vs `--preset streetwear` give visibly different outfits
- [ ] `user_profile.json` (`colours_to_avoid`, `fit_preferences`) is respected
- [ ] worst-scoring request again with `--think`: is it actually better?
- [ ] average "would I wear this?" score >= 3.5 out of 5

## Stage 6 - robustness

- [ ] `$env:FASHIONAI_OLLAMA_URL='http://localhost:1'; python ingest_wardrobe.py --only <name>; Remove-Item Env:FASHIONAI_OLLAMA_URL`
      prints one `ERROR: Cannot reach Ollama` line, no traceback
- [ ] `Test-Path "$env:USERPROFILE\.cache"` is still `False`

When done, paste the ingest summary, the `--list` output, two stylist results and any
FAILED lines to Claude.

# Cloud wardrobe tests still to run (Supabase)

The PC worker passed an end-to-end test against the real project (upload -> item, duplicate,
edit, retag, outfit request, like, profile, delete). Everything that needs YOU to be signed in
is untested, because only you can type your password:

    Terminal 1:  cd D:\FashionAI; .\venv\Scripts\Activate.ps1; python server.py --lan --cloud
    Terminal 2:  cd D:\FashionAI\mobile; npm start

- [ ] sign in on the app's first screen with the app login (Supabase -> Authentication -> Users)
- [ ] "Add photos": the photo shows up in the wardrobe within about 15 s while the PC is on
- [ ] Settings -> "Is my PC on?" says it is on and syncing
- [ ] Stylist returns outfits (the request travels phone -> cloud -> PC -> cloud -> phone)
- [ ] edit an item's tags, tag it again, remove it
- [ ] like an outfit, then find it under Outfits -> Liked
- [ ] stop the server: the wardrobe still opens, Stylist says the PC is off, a new photo says "waiting for your PC"
- [ ] start the server again: the waiting photo turns into an item
- [ ] phone on mobile data (not the home Wi-Fi): the wardrobe still opens

# Phase 3 tests still to run (the app on a real phone)

The app was verified in a desktop browser only (wardrobe, item edit/delete, stylist,
outfit history, likes, settings). These need a phone with Expo Go on the same Wi-Fi:

    Terminal 1:  cd D:\FashionAI; .\venv\Scripts\Activate.ps1; python server.py --lan
    Terminal 2:  cd D:\FashionAI\mobile; npm start        (scan the QR code with Expo Go)

- [ ] Windows Firewall prompts for Python and Node.js were allowed on private networks
- [ ] the app opens in Expo Go and Settings -> "Save and test connection" says Connected
- [ ] "Add photos" picks several library photos and they appear in the wardrobe
- [ ] "Camera" takes a photo and it is ingested (NOT tested anywhere yet)
- [ ] an iPhone HEIC photo uploads fine (the app converts to JPEG first)
- [ ] photo upload in the browser version ("Add photos" on http://localhost:8081) - also untested
- [ ] the keyboard does not cover the fields on the Stylist and Item screens
- [ ] text and buttons are comfortable to read and tap on your screen size
