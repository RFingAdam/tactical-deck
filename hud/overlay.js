(() => {
  const Q = new URLSearchParams(location.search);
  const POS = ["tl", "tr", "bl", "br", "bar"].includes(Q.get("pos")) ? Q.get("pos") : "tl";
  const HUD_SCALE = parseFloat(Q.get("scale") || "1") || 1;
  const VOL = Math.max(0, Math.min(1, parseFloat(Q.get("vol") ?? "0.55")));
  const MUTE = Q.get("mute") === "1";
  const SHOW_HUD = Q.get("hud") !== "0";
  const SHOW_BIG = Q.get("big") !== "0";
  // stage=WxH changes the design canvas (e.g. 1440x1072 for the TikTok game panel)
  const _st = (Q.get("stage") || "1920x1080").split("x").map(Number);
  const SW = _st[0] > 0 ? _st[0] : 1920, SH = _st[1] > 0 ? _st[1] : 1080;

  const $ = (id) => document.getElementById(id);
  const stage = $("stage"), hud = $("hud"), feed = $("feed");
  hud.classList.add("pos-" + POS);
  document.body.classList.add("layout-" + POS);
  hud.style.transform = `scale(${HUD_SCALE})`;
  if (!SHOW_HUD) hud.querySelectorAll(".brand,.bar,.sub").forEach((e) => (e.style.display = "none"));

  let S = 1;
  stage.style.width = SW + "px"; stage.style.height = SH + "px";
  { const f = $("fx"); f.width = SW; f.height = SH; f.style.width = SW + "px"; f.style.height = SH + "px"; }
  function fit() {
    S = Math.min(innerWidth / SW, innerHeight / SH);
    stage.style.transform = `scale(${S})`;
  }
  addEventListener("resize", fit);
  fit();

  // ---------------- sound ----------------
  let AC = null, master = null;
  function ac() {
    if (!AC) {
      AC = new (window.AudioContext || window.webkitAudioContext)();
      master = AC.createGain();
      master.gain.value = VOL;
      const comp = AC.createDynamicsCompressor();
      master.connect(comp).connect(AC.destination);
    }
    if (AC.state === "suspended") AC.resume();
    return AC;
  }
  function osc(type, f0, f1, t, dur, vol) {
    const a = ac(), o = a.createOscillator(), g = a.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f0, t);
    if (f1) o.frequency.exponentialRampToValueAtTime(f1, t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(master);
    o.start(t);
    o.stop(t + dur + 0.05);
  }
  function noise(t, dur, vol, freq, q) {
    const a = ac(), len = Math.floor(a.sampleRate * dur);
    const b = a.createBuffer(1, len, a.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * (1 - i / len);
    const s = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
    s.buffer = b; f.type = "bandpass"; f.frequency.value = freq; f.Q.value = q; g.gain.value = vol;
    s.connect(f).connect(g).connect(master);
    s.start(t);
  }
  function lowpassed(freq) {
    const f = ac().createBiquadFilter();
    f.type = "lowpass"; f.frequency.value = freq; f.connect(master);
    return f;
  }
  function tone(type, f0, f1, t, dur, vol, dest, attack = 0.005) {
    const a = ac(), o = a.createOscillator(), g = a.createGain();
    o.type = type;
    o.frequency.setValueAtTime(f0, t);
    if (f1) o.frequency.exponentialRampToValueAtTime(f1, t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + attack);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(g).connect(dest || master);
    o.start(t); o.stop(t + dur + 0.05);
  }
  function hiss(t, dur, vol, type, f0, f1) {
    const a = ac(), len = Math.floor(a.sampleRate * dur);
    const b = a.createBuffer(1, len, a.sampleRate), d = b.getChannelData(0);
    for (let i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    const s = a.createBufferSource(), f = a.createBiquadFilter(), g = a.createGain();
    s.buffer = b; f.type = type; f.Q.value = 0.7;
    f.frequency.setValueAtTime(f0, t);
    if (f1) f.frequency.exponentialRampToValueAtTime(f1, t + dur);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(vol, t + dur * 0.15);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    s.connect(f).connect(g).connect(master);
    s.start(t);
  }
  const SYNTH = {
    // tight "confirmed" tick: soft sub thump + crisp click + faint high ping
    kill() {
      const t = ac().currentTime;
      tone("sine", 120, 50, t, 0.14, 0.55);
      hiss(t, 0.035, 0.35, "highpass", 3500);
      tone("sine", 1480, null, t + 0.01, 0.12, 0.035);
    },
    // same tick, doubled with a second ping a fifth up
    multi(n) {
      const t = ac().currentTime;
      tone("sine", 120, 45, t, 0.18, 0.65);
      hiss(t, 0.04, 0.4, "highpass", 3200);
      tone("sine", 1480, null, t + 0.01, 0.14, 0.04);
      tone("sine", 2217, null, t + 0.07, 0.16, 0.03 + 0.005 * Math.min(n, 4));
    },
    // cinematic low impact with a short air swell
    spree() {
      const t = ac().currentTime;
      hiss(t, 0.45, 0.18, "bandpass", 300, 2400);
      const lp = lowpassed(420);
      tone("sawtooth", 55, 52, t + 0.32, 1.1, 0.10, lp, 0.02);
      tone("sawtooth", 55.6, 52.5, t + 0.32, 1.1, 0.10, lp, 0.02);
      tone("sine", 70, 32, t + 0.32, 0.8, 0.8);
    },
    // muffled low hit
    death() {
      const t = ac().currentTime;
      tone("sine", 95, 38, t, 0.55, 0.6);
      hiss(t, 0.5, 0.25, "lowpass", 380, 120);
    },
    // warm lowpassed chord swell + soft impact
    win() {
      const t = ac().currentTime, lp = lowpassed(1600);
      tone("sine", 60, 30, t, 0.9, 0.7);
      hiss(t, 0.6, 0.12, "bandpass", 500, 4000);
      [220, 277.18, 329.63, 440].forEach((f, i) => {
        tone("triangle", f, null, t + 0.05 + i * 0.03, 2.6, 0.07, lp, 0.25);
        tone("sine", f * 2, null, t + 0.05 + i * 0.03, 2.2, 0.015, lp, 0.3);
      });
    },
  };
  // Sound packs live in sounds/<pack>/<event>.wav and are decoded up front for zero-latency playback.
  // Pack "synth" (or a missing file) falls back to the built-in synth sounds.
  let BUFS = {}, PACK = "synth";
  async function loadPack() {
    try {
      const info = await (await fetch("/api/sounds")).json();
      const next = {};
      await Promise.all((info.files || []).map(async (f) => {
        const data = await (await fetch(`/sounds/${info.pack}/${encodeURIComponent(f)}`)).arrayBuffer();
        next[f.replace(/\.[^.]+$/, "").toLowerCase()] = await ac().decodeAudioData(data);
      }));
      BUFS = next; PACK = info.pack;
    } catch (e) { console.warn("sound pack load failed", e); }
  }
  loadPack();
  // Server-controlled: when the Tactical Deck Director plays game sounds through the
  // soundboard, overlay sounds go quiet so nothing doubles.
  let SERVER_SOUND = true;
  function play(name, fallback) {
    if (MUTE || !SERVER_SOUND) return;
    try {
      const b = BUFS[name];
      if (b) {
        const s = ac().createBufferSource();
        s.buffer = b;
        s.connect(master);
        s.start();
      } else if (fallback && (PACK === "synth" || !Object.keys(BUFS).length)) fallback();
    } catch (e) { /* audio is best-effort */ }
  }

  // ---------------- particles ----------------
  const cv = $("fx"), cx = cv.getContext("2d");
  let parts = [], raf = null;
  function spawn(x, y, n, colors, speed, grav, spread) {
    for (let i = 0; i < n; i++) {
      const a = spread ? -Math.PI / 2 + (Math.random() - 0.5) * spread : Math.random() * Math.PI * 2;
      const sp = speed * (0.4 + Math.random() * 0.8);
      parts.push({ x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, g: grav,
        w: 4 + Math.random() * 8, h: 2 + Math.random() * 5, r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4,
        c: colors[i % colors.length], life: 70 + Math.random() * 90 });
    }
    if (!raf) raf = requestAnimationFrame(loop);
  }
  function rain(n, colors) {
    for (let i = 0; i < n; i++) {
      parts.push({ x: Math.random() * SW, y: -20 - Math.random() * 400, vx: (Math.random() - 0.5) * 2, vy: 2 + Math.random() * 3,
        g: 0.04, w: 6 + Math.random() * 8, h: 3 + Math.random() * 5, r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.3,
        c: colors[i % colors.length], life: 260 + Math.random() * 60 });
    }
    if (!raf) raf = requestAnimationFrame(loop);
  }
  function loop() {
    cx.clearRect(0, 0, SW, SH);
    parts = parts.filter((p) => p.life-- > 0 && p.y < SH + 80);
    for (const p of parts) {
      p.vx *= 0.985; p.vy = p.vy * 0.985 + p.g; p.x += p.vx; p.y += p.vy; p.r += p.vr;
      cx.save();
      cx.translate(p.x, p.y); cx.rotate(p.r);
      cx.globalAlpha = Math.min(1, p.life / 40);
      cx.fillStyle = p.c;
      cx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      cx.restore();
    }
    raf = parts.length ? requestAnimationFrame(loop) : null;
  }
  function centerOf(el) {
    const r = el.getBoundingClientRect();
    return [(r.left + r.width / 2) / S, (r.top + r.height / 2) / S];
  }

  // ---------------- UI helpers ----------------
  const MULTI = { 2: "DOUBLE", 3: "TRIPLE", 4: "QUAD" };
  const SPREE = { 5: "LETHAL", 8: "RELENTLESS", 12: "UNTOUCHABLE", 20: "APEX" };
  const COLORS = { kill: "var(--kill)", death: "var(--death)", win: "var(--win)", info: "#9fb3c8", revive: "#4be3a0" };

  function restart(el, cls) { el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls); }

  function setNum(el, v, animate) {
    const s = String(v);
    if (el.textContent === s) return false;
    el.textContent = s;
    if (animate) restart(el, "tick");
    return true;
  }

  function render(st, animate) {
    const ck = setNum($("nk"), st.kills, animate);
    const cd = setNum($("nd"), st.deaths, animate);
    const cw = setNum($("nw"), st.wins, animate);
    $("kd").textContent = Number(st.kd || 0).toFixed(2);
    $("sv").textContent = st.streak;
    $("bv").textContent = st.best_streak;
    $("stk").className = st.streak >= 3 ? "hot" : "dim";
    return { ck, cd, cw };
  }

  function pop(kind, title, sub, value, ms = 2800) {
    if (!SHOW_BIG && kind !== "kill") return;
    const el = document.createElement("div");
    el.className = "pop";
    el.style.setProperty("--c", COLORS[kind] || COLORS.info);
    el.innerHTML = `<div><div class="pt"></div><div class="ps"></div></div><div class="pv"></div>`;
    el.querySelector(".pt").textContent = title;
    el.querySelector(".ps").textContent = sub || "";
    el.querySelector(".pv").textContent = value || "";
    feed.prepend(el);
    while (feed.children.length > (POS === "bar" ? 3 : 4)) feed.lastElementChild.remove();
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 420); }, ms);
  }

  function callout(text, sub, kind) {
    if (!SHOW_BIG) return;
    const c = $("callout");
    c.style.setProperty("--c", COLORS[kind]);
    c.querySelector(".t").textContent = text;
    c.querySelector(".s").textContent = sub || "";
    restart(c, "on");
  }

  // ---------------- event handlers ----------------
  function onKill(evt) {
    const st = evt.state;
    if (!evt.test) render(st, true);
    restart($("tk"), "hit");
    const [x, y] = centerOf($("tk"));
    spawn(x, y, 14, ["#ff5a2a", "#ffb199", "#ffffff"], 6, 0.22, 0);
    const multi = st.multi || 1, streak = st.streak || 1;
    const title = multi >= 5 ? "MULTI KILL" : (MULTI[multi] ? MULTI[multi] + " KILL" : "KILL CONFIRMED");
    pop("kill", title, streak >= 2 ? `STREAK ${streak}` : "ENEMY DOWN", "+1");
    if (SPREE[streak]) {
      callout(SPREE[streak], `${streak} KILL STREAK`, "kill");
      play("spree", () => SYNTH.spree());
    } else if (multi >= 3) {
      callout(multi >= 5 ? "MULTI KILL" : MULTI[multi] + " KILL", `${multi} RAPID KILLS`, "kill");
      play(multi === 3 ? "triple" : "multi", () => SYNTH.multi(multi));
    } else if (multi === 2) {
      play("double", () => SYNTH.multi(2));
    } else {
      play("kill", () => SYNTH.kill());
    }
  }

  function onDeath(evt) {
    const st = evt.state;
    if (!evt.test) render(st, true);
    restart($("td"), "hit");
    restart($("vig"), "on");
    const ended = evt.ended_streak || 0;
    $("kia").querySelector(".s").textContent = ended >= 3 ? `STREAK ENDED · ${ended}` : `K/D ${Number(st.kd).toFixed(2)}`;
    if (SHOW_BIG) restart($("kia"), "on");
    play("death", () => SYNTH.death());
  }

  function onWin(evt) {
    const st = Object.assign({}, evt.state);
    if (evt.test) st.wins += 1; else render(st, true);
    restart($("tw"), "hit");
    $("win").querySelector(".ws").textContent = `WIN #${st.wins}  ·  ${st.kills} KILLS  ·  K/D ${Number(st.kd).toFixed(2)}`;
    if (SHOW_BIG) {
      restart($("win"), "on");
      const gold = ["#ffd24a", "#ffe89a", "#fff6d8", "#c98a00"];
      spawn(SW / 2, SH * 0.48, 70, gold, 13, 0.2, 0);
      setTimeout(() => rain(50, gold), 300);
    } else {
      pop("win", "VICTORY", `WIN #${st.wins}`, "+1", 4000);
    }
    play("win", () => SYNTH.win());
  }

  function handle(evt) {
    switch (evt.type) {
      case "hello":
        if (window.__build && evt.build && evt.build !== window.__build) { location.reload(); return; }
        window.__build = evt.build;
        if (typeof evt.overlay_sounds === "boolean") SERVER_SOUND = evt.overlay_sounds;
        render(evt.state, false);
        break;
      case "undo": render(evt.state, false); break;
      case "kill": onKill(evt); break;
      case "death": onDeath(evt); break;
      case "win": onWin(evt); break;
      case "reset":
        render(evt.state, true);
        pop("info", "NEW SESSION", evt.auto ? "AUTO RESET" : "COUNTERS ZEROED", "0", 3200);
        break;
      case "downed":
        pop("death", "DOWNED", "AWAITING REVIVE", "", 3500);
        play("downed");
        break;
      case "revived":
        pop("revive", "REVIVED", "BACK IN THE FIGHT", "", 2600);
        play("revived");
        break;
      case "soundpack":
        loadPack();
        break;
      case "overlay_sounds":
        SERVER_SOUND = !!evt.on;
        break;
    }
  }

  // ---------------- live connection ----------------
  function connect() {
    const es = new EventSource("/events");
    es.onmessage = (m) => { try { handle(JSON.parse(m.data)); } catch (e) { console.error(e); } };
    es.onerror = () => { /* EventSource retries on its own */ };
  }
  fetch("/api/state").then((r) => r.json()).then((st) => render(st, false)).catch(() => {});
  connect();
})();
