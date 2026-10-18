# Changelog

## v1.2.0 (2026-10-18)

- **HUD: DEBT PAID tile.** Shows the money earned this stream, read off the in-game balance. Increases add up, spending never takes the total back down, and it resets with the session. Readings have to repeat before they count (longer for big moves and for a balance with its leading digit chopped off), a drop that bounces back within 4 minutes counts as a misread, and single jumps over $150k are ignored. `GET /api/cash/<balance>` corrects it by hand.
- **HUD: five overlay themes** (`protocol`, `fieldkit`, `nvg`, `stencil`, `killfeed`), switched live from the control panel or `GET /api/theme/<name>`, or pinned per source with `?theme=`.
- **HUD: lighter on the game.** Screen capture moved from a GDI grab (80-140 ms, could stall the compositor and cause hitches) to Desktop Duplication via `dxcam` (~14 ms), with GDI kept as a fallback. Only the regions the detector reads are converted. The perf log reads NVML and per-app CPU in-process instead of spawning `nvidia-smi` and PowerShell every 2 s. With Game Boost on, the HUD runs itself on the E-cores while the game is up.
- **HUD: quieter defaults.** VICTORY and K.I.A. now pop in the feed. Use `?winfx=full` / `?deathfx=full` for the old full-screen versions.
- **HUD: new requirements** `dxcam`, `psutil` and `nvidia-ml-py`. Run `pip install -r hud/requirements.txt` again.
- **Tests:** cash reader layouts and misreads, the acceptance rules, and the DEBT PAID total.

## v1.1.0 (2026-10-02)

- **Sounds relicensed to CC BY 4.0.** Still free for any use, including commercial, with a credit: *Tactical Pack by SwampBewdy (twitch.tv/swampbewdy), CC BY 4.0*. The credit is also embedded in every WAV's metadata. Copies taken from v1.0.0 remain CC0.
- **HUD:** opening the inventory, map or a menu while downed (or alt-tabbing) no longer fires a false "revived". A second "downed" no longer fires when the prompt comes back, and stale revives more than 120 s after a down are dropped.
- **Director:** Stop All now restores the voice if a toggle like Comms had switched it to radio.

## v1.0.0 (2026-10-02)

First public release.

- **Tactical Pack:** 24 synthesized, loudness-matched CC0 stingers (impacts, braam, reverse swell, comms squelch, sonar, jet flyby, airstrike, heartbeat, countdown, objective secured/lost, and more).
- **Director:** combos (sequences, toggles and loops), stream ducking through the Streamlabs Desktop API with crash-safe restore, game-reactive rules with priority and cooldowns, a live settings UI and an HTTP API.
- **Engines:** `local` (sounddevice), `haven` (Voicemeeter insert with voice FX and sample-accurate headphone ducking) and `demo` (silent, for trying it out and for CI).
- **Protocol HUD:** a self-counting K/D/W overlay with streak callouts and a victory screen, OCR auto-detect (Wardogs profile) and a calibration toolkit.
- **Companion installer:** adds TACTICAL and TACTICAL FX pages without touching existing pages.
