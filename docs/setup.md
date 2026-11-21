# Common setups

Haven Deck is the touchscreen or browser panel. [Tactical Deck](https://github.com/RFingAdam/tactical-deck)'s Director is the soundboard that plays on the stream computer. [Bitfocus Companion](https://bitfocus.io/companion) can sit alongside them for scene and stream buttons.

They talk over HTTP. This guide covers three layouts people actually run:

- **A.** Everything in a browser on one PC
- **B.** All on one PC, with Streamlabs or OBS
- **C.** The deck on a Pi or tablet, talking to a stream PC on the LAN

Addresses below are examples. `192.168.1.50` and `<stream-pc>` mean *your* stream computer. Replace them with the IP or hostname you actually use. Do not commit a machine-specific `config.json` in either repo.

A copy of this guide lives in [Haven Deck](https://github.com/RFingAdam/haven-deck/blob/main/docs/setup.md) and [Tactical Deck](https://github.com/RFingAdam/tactical-deck/blob/main/docs/setup.md).

## Safe defaults

- Leave Director bound to `127.0.0.1`, with `allowed_hosts` and `allowed_origins` empty, unless a second device must call it.
- Bind to `0.0.0.0` only on a private LAN (`10.0.0.0/8`, `172.16.0.0/12`, or `192.168.0.0/16`). Do not put Director, Companion, or the kiosk on the public internet.
- If you open a Windows firewall port, allow only your local subnet, and only on a Private network profile.
- Open Haven Deck in the browser and let it proxy `/director` and `/companion`. Do not point the browser at Director directly.

## Settings you might change

**Director** (`director/config.json` in Tactical Deck; copy from `director/config.default.json` or the snippets in the README):

| Key | Default | When to change it |
|---|---|---|
| `host` | `127.0.0.1` | Set to `0.0.0.0` only if another machine must reach Director. |
| `port` | `8781` | Leave unless that port is taken. |
| `allowed_hosts` | `[]` | Host header values to accept, such as `"192.168.1.50"` or `"<stream-pc>"`. Hostname or IP, optional port. Loopback is always allowed. No wildcards and no URLs. |
| `allowed_origins` | `[]` | Bare origins such as `"http://127.0.0.1:4173"`. Needed when a browser — or Haven Deck's proxy forwarding that browser's `Origin` — posts to Director. No paths, no wildcards. |

Restart Director after you edit this file.

**Haven Deck** (`config.json`, copied from `config.example.json`):

| Key | Default | When to change it |
|---|---|---|
| `directorBase` | `http://127.0.0.1:8781` | The Director URL as seen from the machine running the kiosk server. |
| `companionBase` | `http://127.0.0.1:8000` | Companion's HTTP API, as seen from that same machine. |
| `bind` / `port` | `127.0.0.1` / `4173` | Set `bind` to `0.0.0.0` only if another device will open the deck in a browser. |
| `simulate` | `true` | Leave on until your buttons are mapped, then turn Demo mode off. |

`directorBase` and `companionBase` must be loopback or a private LAN URL. The kiosk server (and the Vite dev proxy) forward `/director` and `/companion` so the browser stays on one origin.

**Companion:** the HTTP API defaults to `http://127.0.0.1:8000`. If the kiosk server runs on the same PC as Companion, leave it on localhost. If the kiosk runs on another device, allow Companion's HTTP API to listen on your LAN (in Companion's settings) and point `companionBase` at the stream PC.

## A. Everything in a browser on one PC

Use this to try the UIs. No firewall changes, and no LAN bind.

1. Tactical Deck: `pip install -r requirements.txt`, then `python director/director.py --demo --debug`. Open http://127.0.0.1:8781.
2. Haven Deck: `npm install && npm run dev`. Open the URL Vite prints, usually http://127.0.0.1:5173. Leave Demo mode on.

Test:

```bash
curl -s http://127.0.0.1:8781/api/status
curl -s -X POST http://127.0.0.1:8781/play/t_explosion
```

The status call should return JSON. In demo mode the play call is silent, but the Director log and UI should show it. Taps on the Haven Deck demo board should update the keys.

`npm run dev` proxies `/director` and `/companion` to localhost only. For a real stream, or a second device, use `npm start` as in B or C.

## B. All on one PC with Streamlabs or OBS

Director, Companion, and optionally Haven Deck all run on the stream PC. Keep everything on loopback.

1. Follow [Tactical Deck's Quick start, Option B](https://github.com/RFingAdam/tactical-deck#quick-start): local engine, a virtual cable into OBS or Streamlabs, Director at http://127.0.0.1:8781.
2. Leave Director `host` at `127.0.0.1` and both allowlists empty.
3. Leave Companion on localhost, port 8000. Optionally install the pre-built Tactical pages from that same Quick start.
4. Haven Deck: `npm run build`, copy `config.example.json` to `config.json`, keep `directorBase` and `companionBase` on `127.0.0.1`, keep `bind` at `127.0.0.1`, then `npm start`. Open http://127.0.0.1:4173. Map Companion coordinates to your pages, then turn Demo mode off.
5. Optional: add Protocol HUD as a Browser Source at http://127.0.0.1:8790/overlay?pos=bar.

Test (you should hear the clip through the virtual-cable path):

```bash
curl -s http://127.0.0.1:8781/api/status
curl -s -X POST http://127.0.0.1:8781/play/t_explosion
curl -s -X POST http://127.0.0.1:4173/director/play/t_explosion
```

The third call goes through Haven Deck's proxy. A JSON `{"ok": true}` means the kiosk can reach Director.

## C. Deck on a Pi or tablet, stream PC on the LAN

Two ways. Prefer **C1** when you can: Director and Companion stay on loopback, so you only expose the kiosk UI.

### C1. Kiosk server on the stream PC (safer)

1. On the stream PC, run Director and Companion as in B, still bound to `127.0.0.1`.
2. On the stream PC, run Haven Deck (`npm run build && npm start`) with `bind` set to `0.0.0.0` and `port` `4173`. Keep `directorBase` and `companionBase` on `127.0.0.1`.
3. Open a Windows firewall rule for TCP 4173 from your local subnet only, Private profile (see below).
4. On the tablet or Pi, open `http://192.168.1.50:4173` or `http://<stream-pc>:4173`.

Director's allowlists stay empty. The browser talks to the kiosk; the kiosk talks to Director and Companion on localhost.

### C2. Kiosk on the Pi, Director on the stream PC

Use this when the Pi itself runs the kiosk (`npm start` and `bash pi-setup/kiosk.sh`).

On the stream PC, in `director/config.json`:

```json
{
  "host": "0.0.0.0",
  "port": 8781,
  "allowed_hosts": ["192.168.1.50"],
  "allowed_origins": ["http://127.0.0.1:4173"]
}
```

`allowed_hosts` must include the Host header Director will see. If `directorBase` is `http://192.168.1.50:8781`, that is `192.168.1.50`. If you use `http://<stream-pc>:8781`, add `"<stream-pc>"` as well.

`allowed_origins` must include the exact origin of the Haven Deck page, because the kiosk proxy forwards the browser's `Origin` header:

- Pi kiosk at the default local URL: `http://127.0.0.1:4173`
- A tablet opening the deck on the Pi: also add `http://<pi-address>:4173`

If Companion should be reachable from the Pi, allow its HTTP API to listen on the LAN and open TCP 8000 the same way as 8781.

On the Pi, `config.json`:

```json
{
  "simulate": true,
  "companionBase": "http://192.168.1.50:8000",
  "directorBase": "http://192.168.1.50:8781",
  "bind": "127.0.0.1",
  "port": 4173
}
```

Leave `bind` on `127.0.0.1` for the Pi's own kiosk window. Set it to `0.0.0.0` only if another device will open the Pi's deck in a browser. Turn Demo mode off after you map buttons.

Windows firewall on the stream PC (Private profile, local subnet only):

```powershell
New-NetFirewallRule -DisplayName "Tactical Deck Director" -Direction Inbound -Protocol TCP -LocalPort 8781 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

If Companion is also listening on the LAN, add a second rule for TCP 8000 with the same profile and `LocalSubnet`. For C1, use those same flags on TCP 4173 instead.

Restart Director after the config change.

Test from the Pi:

```bash
curl -s http://192.168.1.50:8781/api/status
curl -s -X POST http://192.168.1.50:8781/play/t_explosion
curl -s -X POST -H "Origin: http://127.0.0.1:4173" http://192.168.1.50:8781/play/t_explosion
curl -s -X POST http://127.0.0.1:4173/director/play/t_explosion
```

You should hear the clip on the stream PC. If the first two work but the Origin or proxy call returns `{"error": "local requests only"}`, add the exact page origin to `allowed_origins`.

## Common failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `403` with `{"error": "local requests only"}` | Director rejected the `Host` or `Origin` header | Add the exact host you used (`192.168.1.50` or `<stream-pc>`, optional port) to `allowed_hosts`. Add the exact page origin (`http://...` with port, no path) to `allowed_origins`. Restart Director. If you meant to stay local-only, call `127.0.0.1`, not the LAN IP. |
| Connection refused or a timeout | Still bound to loopback, or the firewall blocked the port | Set Director `host` or the kiosk `bind` to `0.0.0.0` only if a remote device must connect. Add a subnet-scoped firewall rule. Confirm `curl` to `/api/status` works from the other device. |
| Mixed HTTP/HTTPS, a blank page, or requests blocked in the browser | The page is `https` and Director or the kiosk is `http` | Serve both over HTTP on the LAN, or both over HTTPS. Browsers block mixed content. |
| CORS error in the browser console | The page called Director or Companion directly | Open Haven Deck (the kiosk or Vite URL) and use its `/director` and `/companion` proxy. Director does not send CORS headers. |
| `npm run dev` on a Pi cannot reach Director on the stream PC | Vite's proxy is fixed to `127.0.0.1` | Use `npm run build && npm start` with `directorBase` and `companionBase` set in `config.json` (C2). |
| Companion taps do nothing | Companion is localhost-only, or the page/row/column is wrong | Reach `companionBase` from the machine running the kiosk. Keep Demo mode on until the coordinates match your Companion pages. |

## Adapting this to your network

If your network looks different, paste this guide into an AI assistant and ask it to adapt the addresses, ports, and firewall commands to your setup.
