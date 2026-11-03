# Contributing to Tactical Deck

Thanks for helping make streams sound better. The most wanted contributions are:

1. **Game profiles** for Protocol HUD (kill, down, death and win detection for more games).
2. **New synthesized sounds** for `synth/tactical_pack.py`. They must be generated from code (no samples or recordings) and are released CC BY 4.0 like the rest of the pack.
3. **Integrations:** OBS WebSocket ducking, Touch Portal, Stream Deck SDK, and so on.

## Dev setup

```bash
pip install -r requirements.txt pytest ruff
python director/director.py --demo --debug      # UI at http://127.0.0.1:8781, no audio needed
ruff check . && pytest                           # what CI runs (Linux + Windows)
```

## Ground rules

- **Loudness spec:** every clip lands at −22 dBFS RMS with a peak ≤ 0.23 (`master()` handles it). `tests/test_synth.py` enforces this.
- **Deterministic synthesis:** draw randomness only from the module's seeded `RNG`, so a build is byte-reproducible.
- **Localhost only:** the Director must keep binding to `127.0.0.1` and checking Host/Origin.
- **Anti-cheat-safe detection:** game profiles may only read screen pixels. No process memory, no file hooks and no injected overlays.

## Add a game

Protocol HUD loads one JSON file per game from `hud/games/<id>.json`. Wardogs is `hud/games/wardogs.json`; `hud/games/example.json` is a minimal second profile you can switch to from the control page without restarting. The server validates the schema on load and returns a clear error for a bad file.

**What to capture**

1. Record 2–3 matches (OBS/Streamlabs recording or highlight clips) at the same aspect ratio you stream. For a letterboxed ultrawide game on a 16:9 canvas, keep the letterbox in the recording so OCR regions can follow the game rect.
2. Run `python hud/tools/scan_recording.py <recording>` and note the exact on-screen text for kills, downs, deaths and wins, and where it appears (fractions of the *game* area, not the black bars).
3. Copy `hud/games/wardogs.json` to `hud/games/<id>.json`. Set `id` (must match the filename), `name`, `game_exe`, banner text, stat names, colours, callout banners, and `detect.regions` / `detect.keywords`. Regions are `[left, top, right, bottom]` fractions of the game rect (0–1). Keywords are case-insensitive regexes; OCR is noisy, so match loosely like the Wardogs profile does.
4. Point the control page at the new profile (it switches live). Iterate with `hud/tools/analyze_segment.py` until the detected timeline matches what really happened. Include the before/after timeline in your PR description.

**Testing OCR regions**

- `analyze_clips.py` / `analyze_segment.py` feed frames through the same crop path the live detector uses. Set the display layout to `16:9` or `ultrawide-letterbox` so the kill-feed crops follow the game rect.
- A region that works on your monitor but not on a 16:9 capture is usually a canvas-vs-game mixup: keep regions relative to the game rect, never to absolute 1920×1080 pixels.

**Do not include** ripped game assets, UI textures, fonts, audio, or anything you didn't write. Detection is pixels-only: no process memory, no file hooks, no injected overlays.

## Adding a sound

Add a builder function to `synth/tactical_pack.py` and an entry in `PACK` (id `t_<name>`, label, category, builder, usage). Then run `python synth/tactical_pack.py /tmp/pack --preview` and listen to `preview_all.wav`. Attach a short clip or a waveform screenshot to the PR.

## PR checklist

- [ ] `ruff check . && pytest`
- [ ] Tried it in `--demo` mode or on a real stream setup
- [ ] New sounds: generated from code (no samples); listened to `preview_all.wav`; loudness test passes
- [ ] New game profile: `hud/games/<id>.json` validates; `id` matches the filename; no ripped game assets
- [ ] New game profile: OCR regions are fractions of the game rect; before/after detection timeline is attached
- [ ] Display layout: ultrawide-letterbox still matches the original overlay pixels if you touched HUD chrome

## Licensing of contributions

By opening a PR you agree that your contribution is licensed under this repo's licenses: code under [MIT](LICENSE), and sound designs/sounds under [CC BY 4.0](sounds/LICENSE). Only contribute code and sounds you wrote yourself. No ripped game assets, samples or copyrighted audio. Inbound = outbound; there is no CLA.
