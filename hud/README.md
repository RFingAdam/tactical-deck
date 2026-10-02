# Protocol HUD

A live Kills / Deaths / Wins overlay for Streamlabs or OBS that counts itself while you play. It has streak callouts, a K.I.A. glitch, a victory screen and optional sounds, and every event is published on an SSE stream (`/events`) so other tools (like the Tactical Deck Director) can react.

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
- `big=0`: no full-screen callouts
- `stage=1080x1920`: lay out for a vertical canvas

## Auto-detect (game profile: Wardogs)
While the configured `game_exe` is the focused window, the server reads the screen about 4× per second with the built-in Windows OCR. It only reads pixels. It never touches game files or memory, and nothing leaves your PC.

| Event | What it looks for |
|---|---|
| kill | each "KILL CONFIRMED" line stacked under the crosshair |
| downed / revived | the give-up / call-for-help prompt appearing, then clearing without a respawn |
| death | the "RESPAWNING IN..." countdown (a revive does not count) |
| win | the end-of-match scoreboard headed VICTORY |

To support another game, see "About the scene detection" in the top-level README.

## Manual overrides
- Hotkeys: Ctrl+Alt+1 kill · Ctrl+Alt+2 death · Ctrl+Alt+3 win · Ctrl+Alt+4 undo · Ctrl+Alt+0 twice to reset.
- HTTP: `GET /api/kill` (also `death`, `win`, `undo`, `reset`).
- Test without changing counts: `/api/test/kill?n=5`, `/api/test/death`, `/api/test/win`.
- `GET /api/overlaysounds/on|off` toggles the overlay's own sounds. The Director turns them off when it handles sound.

## Sound packs
`python tools/gen_sounds.py` renders the `tactical` and `cinematic` packs into `sounds/`. For a custom pack, create `sounds/<name>/` with any of `kill`, `double`, `triple`, `multi`, `spree`, `death`, `win`, `downed`, `revived` (.wav/.mp3/.ogg).

## Game Boost (optional, `game_boost` in config)
While the game runs, it and the streaming app get above-normal priority. A list of known background apps is moved to E-cores with EcoQoS, and everything is restored when the game closes. Edit `boost.py` to change the list. Audio, chat and streaming apps are never touched.

## Performance log
While the game is focused, `perflog.py` writes GPU/CPU usage to `tools/perf.csv`. `tools/perf_report.py` summarizes it.
