# Contributing to Tactical Deck

Thanks for helping make streams sound better. The most wanted contributions are:

1. **Game profiles** for Protocol HUD (kill, down, death and win detection for more games).
2. **New synthesized sounds** for `synth/tactical_pack.py`. They must be generated from code and released CC0; no samples or recordings.
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

## Adding a game profile

1. Record 2–3 matches (OBS/Streamlabs recording or highlight clips).
2. Run `python hud/tools/scan_recording.py <recording>` and note the exact on-screen text for kills, downs, deaths and wins, and where it appears.
3. Copy `hud/wardogs_detect.py` to `hud/<game>_detect.py` and adjust the regions, keywords and timing rules.
4. Iterate with `analyze_segment.py` until the detected timeline matches what really happened. Include the before/after timeline in your PR description.

## Adding a sound

Add a builder function to `synth/tactical_pack.py` and an entry in `PACK` (id `t_<name>`, label, category, builder, usage). Then run `python synth/tactical_pack.py /tmp/pack --preview` and listen to `preview_all.wav`. Attach a short clip or a waveform screenshot to the PR.

## Licensing of contributions

By opening a PR you agree that your contribution is licensed under this repo's licenses: code under [MIT](LICENSE), and sounds under [CC0](sounds/LICENSE). Only contribute code and sounds you wrote yourself. No ripped game assets, samples or copyrighted audio.
