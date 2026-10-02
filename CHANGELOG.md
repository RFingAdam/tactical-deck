# Changelog

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
