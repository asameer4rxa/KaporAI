# FashionAI to-do

Written 2026-11-16. Detailed test checklists are in `PHASE1_TESTS_TODO.md`.

## Yours (about 10 minutes)

- [x] Wear-log columns added to the Supabase project (done 2026-11-16 through the SQL Editor)
- [ ] Skim the Wardrobe tab and fix wrong tags on the item screen
      (2 of the 3 items checked closely were wrong, so expect more among the 42)
- [ ] Use it for a real day: "Dress me for today", like / dislike, mark what you wore
- [ ] Confirm whether the app runs on your phone, and whether the Expo Go sign-in warning is gone

## Installable iPhone app (home-screen web app)

A real App Store / TestFlight build needs Apple's paid developer programme (99 USD a year),
so the free route is a web app added to the iPhone home screen. Built and tested locally.

- [x] icons, manifest and iPhone meta tags (`mobile\public`)
- [x] build: `cd D:\FashionAI\mobile; npm run build:web`  -> `mobile\dist` (about 2 MB)
- [x] try the build on this PC: `python mobile\scripts\serve_dist.py` -> http://localhost:8090
- [ ] publish `mobile\dist` on a free static host with HTTPS (needs an account you create)
- [ ] on the iPhone: open the address in Safari -> Share -> Add to Home Screen -> sign in
- [ ] check on the iPhone: Add photos (library and camera), the tab bar clear of the home indicator
- Rebuild and publish again whenever the app's code changes.
- The published app only works in cloud mode ("straight to the PC" is blocked from an https page).

## Done 2026-11-17

- [x] Worn photos: cutouts now show only the garment where a person was detected and the
      clothing model is confident (8 of 16 changed; old cutouts backed up in
      `backups\cutouts_before_recut`). Redo with `python ingest_wardrobe.py --recut-worn`
      (server stopped).
- [x] Worn collection: Outfits tab -> Worn. Days list or Months grid, "Add a photo of today's
      outfit" (background removed on the PC), entries from "I wore this". Cloud table added.
- [ ] Redeploy `mobile\dist` on Netlify so the iPhone gets the Worn collection

## Discovery & Moodboards

- [ ] Read `DISCOVERY_PLAN.md`, answer its 3 questions, create the Unsplash Access Key

## Build next

- [ ] Garment-only cutouts for the 8 worn photos the clothing model could not separate
      (hoodies, joggers, polo, yellow t-shirt): a stronger human-parsing model would be needed
- [ ] Better tagging accuracy: send the tagger a larger image, re-tag the items that look wrong
- [ ] Automatic weather for the Today shortcut (needs a free online weather service)

## Known gaps

- Photo upload from the app and the "I wore this" button were never clicked through by Claude
- `start.ps1` was only syntax-checked
- Deleting a photo from `wardrobe_raw` by hand does not remove the item (delete it in the app)
- Do not run `ingest_wardrobe.py` while the server is running (it now refuses to)
