# Protocol HUD

A live Kills / Deaths / Wins overlay for Streamlabs or OBS that counts itself while you play. It has streak callouts, K.I.A. and victory pops, a **DEBT PAID** tile (money earned this stream, read off the in-game balance), five overlay themes and optional sounds. Every event is published on an SSE stream (`/events`) so other tools (like the Tactical Deck Director) can react.

## Running
```
pip install -r requirements.txt
copy config.example.json config.json   (optional; defaults are used if missing)
start-hud.cmd                            (or: pythonw server.py)
```
- Control panel: http://127.0.0.1:8790/control
- Overlay: add a Browser Source with `http://127.0.0.1:8790/overlay?pos=bar`

Overlay URL options (combine with `&`):
- `pos=bar|tl|tr|bl|br`: layout
- `scale=0.8`: size
- `vol=0.5` / `mute=1`: overlay sound volume, or no sound
- `hud=0`: animations only
- `big=0`: no big callouts
- `winfx=full` / `deathfx=full`: the full-screen VICTORY banner / K.I.A. glitch instead of the default pop in the feed
- `cash=0`: hide the DEBT PAID tile
- `theme=<name>`: pin this source to one theme (otherwise it follows the control panel)
- `stage=1080x1920`: lay out for a vertical canvas

## Themes
`protocol` (angled glass plates, the default), `fieldkit` (matches the game's menu boxes), `nvg` (green phosphor, scanlines), `stencil` (olive drab, stencil type) and `killfeed` (no plates, most minimal). Pick one on the control panel or with `GET /api/theme/<name>`: every overlay switches live, no Browser Source change needed. Tile and pop positions are the same in every theme.

## Auto-detect (game profile: Wardogs)
While the configured `game_exe` is the focused window, the server reads the screen about 4× per second with the built-in Windows OCR. It only reads pixels. It never touches game files or memory, and nothing leaves your PC.

| Event | What it looks for |
|---|---|
| kill | each "KILL CONFIRMED" line stacked under the crosshair |
| downed / revived | the give-up / call-for-help prompt appearing, then clearing without a respawn |
| death | the "RESPAWNING IN..." countdown (a revive does not count) |
| win | the end-of-match scoreboard headed VICTORY |
| cash | the account balance in the top-right money readout (matches and menus), every 2 s |

To support another game, see "About the scene detection" in the top-level README.

## DEBT PAID (money earned this stream)
Every increase in the in-game balance adds to the total; spending lowers the balance but never takes the total back down. It resets with the session (see `new_session_after_hours`) or the RESET button.

OCR on small header text misreads sometimes (a "$1" read as "5", a leading digit lost, the session delta taken for the balance), so a reading has to earn its way in:
- the balance is the right-most `$` amount of the header row, and a lone amount only counts when its position says it's the balance;
- a new value must read the same on 2 checks in a row, 4 for a move over $50k, and about a minute when it looks like the old balance with its leading digit chopped off;
- a big drop that comes back to the old level within 4 minutes was a misread, so only what ends up above the old level counts;
- a single jump over $150k is ignored.

Feed or correct the balance by hand with `GET /api/cash/<balance>`. `GET /api/test/cash?n=5000` plays the animation without changing anything.

## Manual overrides
- Hotkeys: Ctrl+Alt+1 kill · Ctrl+Alt+2 death · Ctrl+Alt+3 win · Ctrl+Alt+4 undo · Ctrl+Alt+0 twice to reset.
- HTTP: `GET /api/kill` (also `death`, `win`, `undo`, `reset`).
- Test without changing counts: `/api/test/kill?n=5`, `/api/test/death`, `/api/test/win`, `/api/test/cash?n=5000`.
- `GET /api/overlaysounds/on|off` toggles the overlay's own sounds. The Director turns them off when it handles sound.
- `GET /api/theme/<name>` switches the theme, `GET /api/cash/<balance>` sets the balance.

## Sound packs
`python tools/gen_sounds.py` renders the `tactical` and `cinematic` packs into `sounds/`. For a custom pack, create `sounds/<name>/` with any of `kill`, `double`, `triple`, `multi`, `spree`, `death`, `win`, `downed`, `revived` (.wav/.mp3/.ogg).

## Staying out of the game's way
- Screen capture uses Desktop Duplication (`dxcam`, the same API as OBS display capture), about 14 ms a grab. The old GDI grab of a 3440×1440 window took 80-140 ms and could stall the compositor, which showed up as hitches. GDI (`mss`) is only a fallback.
- Only the regions the detector looks at are converted from the grab, not the whole frame.
- With Game Boost on (and a CPU with E-cores), the HUD moves itself to the E-cores at below-normal priority while the game runs.

## Game Boost (optional, `game_boost` in config)
While the game runs, it and the streaming app get above-normal priority. A list of known background apps is moved to E-cores with EcoQoS, and everything is restored when the game closes. Edit `boost.py` to change the list. Audio, chat and streaming apps are never touched.

## Performance log
While the game is focused, `perflog.py` writes GPU/CPU usage to `tools/perf.csv` every 2 s. GPU stats come from NVML (`nvidia-ml-py`) and per-app CPU from `psutil`, both in-process, so the logger doesn't spawn anything while you play. `tools/perf_report.py` summarizes it.
