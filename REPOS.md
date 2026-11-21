# Where streaming work goes

This is a public repo. Do not commit LAN IPs, internal hostnames, widget or chat URLs, tokens, stream keys, private song packs, or retired/vendor overlay art.

- **Haven Deck** kiosk UI, themes, and generic Companion/Director wiring: public [haven-deck](https://github.com/RFingAdam/haven-deck).
- **Stingers, Director, the public local sound engine, and Protocol HUD:** public [tactical-deck](https://github.com/RFingAdam/tactical-deck). The HUD source of truth is `hud/`.
- **Private song packs, PTT maps, and private layout:** a private deck add-on. New playback code talks to Tactical Deck Director, never straight to the private sound-insert port.
- **Homelab / network panels:** private [homelab-dash](https://github.com/RFingAdam/homelab-dash). Do not grow a second deck UI there.
- **Live mic-insert / voice FX / song library:** the private sound-insert repo. Songs never go public (streamer license is CC BY-NC with credit; commercial use is paid).
- **Stream overlay art:** private [swampbewdy-overlay](https://github.com/RFingAdam/swampbewdy-overlay). Do not add vendor overlay packs or retired squad-theme art.
- **Editing:** director-core / rushcut. **haven-router** is Opscast, not streaming.

If a change does not have a row here, do not invent a new repo. The private `ai-kb` map (`docs/streaming/REPOS.md`) is the full agent source of truth.
