# Protocol HUD

Live Kills / Deaths / Wins overlay for Streamlabs that counts itself while you play Wardogs. It has streak callouts, a K.I.A. glitch, a victory screen, and sound effects.

## Running
- The server starts automatically when you log in (Startup folder shortcut "Protocol HUD"). To start it by hand, double-click `start-hud.cmd`.
- Control panel: http://127.0.0.1:8790/control
- Log: `hud.log`

## In Streamlabs
The source **"Protocol HUD"** sits at the top of **Live Scene** (DarkMode collection) with URL `http://127.0.0.1:8790/overlay?pos=bar`, so the tiles sit in the black letterbox strip above the game.
Its audio goes to the stream only (it appears in the mixer as "Protocol HUD"). To hear it yourself, open Advanced Audio Properties and set monitoring to "Monitor and Output".

URL options (combine with `&`):
- `pos=bar|tl|tr|bl|br`: layout (default `tl`; Streamlabs uses `bar`)
- `scale=0.8`: size
- `vol=0.5`: volume; `mute=1` turns sound off
- `hud=0`: animations only
- `big=0`: no full-screen callouts

## Auto-detect (Wardogs)
While `WardogsClient-Win64-Shipping.exe` is the focused window, the server reads the screen about 4× per second with the built-in Windows OCR. It only reads pixels. It never touches game files or memory, and nothing leaves your PC.

| Event | What it looks for |
|---|---|
| Kill | each "KILL CONFIRMED" line stacked under the crosshair |
| Death | only a real death: the "RESPAWNING IN..." countdown. Getting downed shows a small DOWNED note, and a revive shows REVIVED. Neither counts |
| Win | the end-of-match scoreboard headed **VICTORY**. DEFEAT and the "<TEAM> WINS" splash are ignored |

Pause or resume it from the control panel, or set `auto_detect` to false in `config.json`.

## STREAM PROFIT
`cash_delta = (cash_now - cash_start) + cash_invested`.

One-time unlocks/buys are **invested** (not losses) when the drop is ≥ $5K, not within ~60s of a death/respawn, and either a menu OCR reading or a round-thousand amount. Per-life kits (~$16K after death) stay as costs in `cash_kits`.

Manual override: control-panel **Mark last drop invested** / hotkey Ctrl+Alt+5. Undo invest: Ctrl+Alt+6. APIs: `/api/cash/invest`, `/api/cash/invest/undo`.

## Manual overrides
Hotkeys that work in-game: Ctrl+Alt+1 kill · Ctrl+Alt+2 death · Ctrl+Alt+3 win · Ctrl+Alt+4 undo · Ctrl+Alt+5 mark last drop invested · Ctrl+Alt+6 undo invest · Ctrl+Alt+0 twice = reset.
Companion / Stream Deck: HTTP GET `http://127.0.0.1:8790/api/kill` (also `death`, `win`, `undo`, `reset`, `invest`, `session/start`).
Test animations without changing counts: `/api/test/kill?n=5`, `/api/test/death`, `/api/test/win`.

## New stream = zero
Idle auto-reset is **off** by default (`new_session_after_hours: 0`). Hit **Start session** on the control panel (or `/api/session/start`) at go-live so the baseline is intentional and unlocks stay invested. Hold RESET to zero counters by hand.

## Tests
`python test_stream_profit.py` — unit tests for invest/kit classification and a calibrated 2026-10-28 replay (~+$43K stream profit vs deeply-negative raw delta).

## Sound design
Built-in sounds are short and restrained: a soft thump and click per kill, a low cinematic hit on 5/8/12/20 streaks, a muffled hit on death, and a warm chord swell on VICTORY. Default volume is 0.55. Streak callouts are LETHAL (5), RELENTLESS (8), UNTOUCHABLE (12), and APEX (20). Doubles only show in the feed. Triples and up get a callout.

## Sound packs
Rendered 48 kHz stereo packs live in sounds/<pack>/ (built by 	ools/gen_sounds.py): **tactical** (default: tight hit-marker tick and thud, metallic ring on streaks, muffled death with a faint ear-ring, warm chord on victory, heartbeat when downed) and **cinematic** (deeper punches, a low 'braam' on streaks, big reverb tails). Switch packs or audition them on the control panel. The change applies to the stream instantly. **synth** uses the old built-in sounds.

## Custom sounds
Make a folder `sounds/<yourpack>/` containing any of `kill`, `double`, `triple`, `multi`, `spree`, `death`, `win`, `downed`, or `revived` (.wav/.mp3/.ogg). It appears as a pack on the control panel.

## Tools
- `tools/analyze_clips.py`: accuracy test against Discord clips
- `tools/analyze_segment.py`: test against a slice of a stream recording
- `tools/add_to_streamlabs.py`: re-adds the source (close Streamlabs first; it backs up first)

## Game Boost
While Wardogs runs, the game and Streamlabs (obs64) get above-normal priority. Background apps (G HUB Replay/Highlighter, Streamlabs AI vision, Syncthing, Steam web helper, SteelSeries GG/Prism, Logitech LCore/G HUB, password managers, NordVPN UI, OneDrive, Teams, Word, Brave, Docker, and similar) are pinned to the 4 E-cores at below-normal priority with Windows EcoQoS. Everything is restored when the game closes. Audio (Voicemeeter, SteelSeries Sonar), Discord, TikTok LIVE Studio and Companion are never touched. Check it at /api/boost; turn it off with game_boost: false in config.json.

## Performance log
While Wardogs is focused, 	ools/perf.csv records GPU load/clock/encoder, total CPU, and CPU for Wardogs, Streamlabs, TikTok LIVE Studio and Discord every 2 s.
