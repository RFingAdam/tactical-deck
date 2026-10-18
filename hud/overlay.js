(() => {
  // window.HUD_QUERY lets a preview page (the theme lab) drive the overlay without a URL
  const Q = new URLSearchParams(window.HUD_QUERY != null ? window.HUD_QUERY : location.search);
  const POS = ["tl", "tr", "bl", "br", "bar"].includes(Q.get("pos")) ? Q.get("pos") : "tl";
  const HUD_SCALE = parseFloat(Q.get("scale") || "1") || 1;
  const VOL = Math.max(0, Math.min(1, parseFloat(Q.get("vol") ?? "0.55")));
  const MUTE = Q.get("mute") === "1";
  const SHOW_HUD = Q.get("hud") !== "0";
  const SHOW_BIG = Q.get("big") !== "0";
  // winfx / deathfx = "pop" (default: small pop in the feed) or "full" (the old full-screen banner / K.I.A.)
  const WINFX = Q.get("winfx") === "full" ? "full" : "pop";
  const DEATHFX = Q.get("deathfx") === "full" ? "full" : "pop";
  const SHOW_CASH = Q.get("cash") !== "0";
  // stage=WxH changes the design canvas (e.g. 1440x1072 for the TikTok game panel)
  const _st = (Q.get("stage") || "1920x1080").split("x").map(Number);
  const SW = _st[0] > 0 ? _st[0] : 1920, SH = _st[1] > 0 ? _st[1] : 1080;

  const $ = (id) => document.getElementById(id);
  const stage = $("stage"), hud = $("hud"), feed = $("feed");
  hud.classList.add("pos-" + POS);
  document.body.classList.add("layout-" + POS);
  hud.style.transform = `scale(${HUD_SCALE})`;
  $("cashbox").style.transform = `scale(${HUD_SCALE})`;
  if (!SHOW_HUD) hud.querySelectorAll(".brand,.bar,.sub").forEach((e) => (e.style.display = "none"));

  let S = 1;
  stage.style.width = SW + "px"; stage.style.height = SH + "px";
  { const f = $("fx"); f.width = SW; f.height = SH; f.style.width = SW + "px"; f.style.height = SH + "px"; }
  function fit() {
    const host = window.HUD_FIT_EL;
    S = host ? Math.min(host.clientWidth / SW, host.clientHeight / SH) : Math.min(innerWidth / SW, innerHeight / SH);
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
  function spawn(x, y, n, colors, speed, grav, spread, lifeMul = 1) {
    for (let i = 0; i < n; i++) {
      const a = spread ? -Math.PI / 2 + (Math.random() - 0.5) * spread : Math.random() * Math.PI * 2;
      const sp = speed * (0.4 + Math.random() * 0.8);
      parts.push({ x, y, vx: Math.cos(a) * sp, vy: Math.sin(a) * sp, g: grav,
        w: 4 + Math.random() * 8, h: 2 + Math.random() * 5, r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4,
        c: colors[i % colors.length], life: (70 + Math.random() * 90) * lifeMul });
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
    const r = el.getBoundingClientRect(), o = stage.getBoundingClientRect();
    return [(r.left - o.left + r.width / 2) / S, (r.top - o.top + r.height / 2) / S];
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

  // ---------------- themes ----------------
  const THEMES = ["protocol", "fieldkit", "nvg", "stencil", "killfeed"];
  const sv = (body, fill) => `<svg class="ico" viewBox="0 0 24 24" ${fill ? 'fill="currentColor" stroke="none"' : 'fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"'}>${body}</svg>`;
  const ICONS = {
    protocol: {
      kill: sv('<circle cx="12" cy="12" r="8"/><path d="M12 2v5M12 17v5M2 12h5M17 12h5"/>'),
      death: sv('<path d="M12 3a7 7 0 0 0-7 7v4l2 2v3h10v-3l2-2v-4a7 7 0 0 0-7-7z"/><circle cx="9" cy="11" r="1.5"/><circle cx="15" cy="11" r="1.5"/>'),
      win: sv('<path d="M7 4h10v5a5 5 0 0 1-10 0z"/><path d="M7 6H4v2a3 3 0 0 0 3 3M17 6h3v2a3 3 0 0 1-3 3M12 14v4M8 21h8"/>'),
      cash: sv('<path d="M12 2v20M17 6.5C17 4.6 14.8 3 12 3S7 4.6 7 6.5 9.2 9.5 12 10s5 1.6 5 3.5-2.2 3.5-5 3.5-5-1.6-5-3.5"/>'),
    },
    fieldkit: {  // reticle, dog tags, flag, banknote
      kill: sv('<circle cx="12" cy="12" r="6.5"/><path d="M12 2.5v5M12 16.5v5M2.5 12h5M16.5 12h5"/><circle cx="12" cy="12" r=".6" fill="currentColor"/>'),
      death: sv('<rect x="4.5" y="7" width="8.5" height="13" rx="2.8" transform="rotate(-12 8.75 13.5)"/><rect x="10.5" y="5" width="8.5" height="13" rx="2.8" transform="rotate(10 14.75 11.5)"/><path d="M8 4.5c2.2-2 5.2-2 7 .3"/>'),
      win: sv('<path d="M5.5 21V3.5"/><path d="M5.5 4.5h11.5l-2.5 4 2.5 4H5.5"/>'),
      cash: sv('<rect x="2.5" y="6.5" width="19" height="11" rx="1.5"/><circle cx="12" cy="12" r="2.6"/><path d="M6 9.5v5M18 9.5v5"/>'),
    },
    nvg: {  // bracket reticle, crossed-out skull, star, bracketed $
      kill: sv('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5M12 8.5v7M8.5 12h7"/>'),
      death: sv('<path d="M12 3.5a6.8 6.8 0 0 0-6.8 6.8v3.4l1.9 1.9v3.4h9.8v-3.4l1.9-1.9v-3.4A6.8 6.8 0 0 0 12 3.5z"/><path d="M8 9.8l2.6 2.6M10.6 9.8L8 12.4M13.4 9.8l2.6 2.6M16 9.8l-2.6 2.6"/>'),
      win: sv('<path d="M12 3l2.6 5.9 6.4.5-4.9 4.2 1.5 6.3L12 16.6 6.4 19.9l1.5-6.3L3 9.4l6.4-.5z"/>'),
      cash: sv('<path d="M7 4H4v16h3M17 4h3v16h-3"/><path d="M12 6.5v11M14.6 9c0-1-1.2-1.8-2.6-1.8S9.4 8 9.4 9s1.2 1.6 2.6 2 2.6 1 2.6 2-1.2 1.8-2.6 1.8-2.6-.8-2.6-1.8"/>'),
    },
    stencil: {  // rifle round, skull, medal, cash bundle (solid)
      kill: sv('<path d="M10 20.5V9.2c0-2.9.9-5.2 2-6.7 1.1 1.5 2 3.8 2 6.7v11.3z"/><path d="M9.2 21h5.6v1.6H9.2z"/>', true),
      death: sv('<path fill-rule="evenodd" d="M12 2.5a7.5 7.5 0 0 0-7.5 7.5v3.6l2 2V20h11v-4.4l2-2V10A7.5 7.5 0 0 0 12 2.5zM9 9.3a1.9 1.9 0 1 1 0 3.8 1.9 1.9 0 0 1 0-3.8zm6 0a1.9 1.9 0 1 1 0 3.8 1.9 1.9 0 0 1 0-3.8zM11 15h2l-1 2z"/>', true),
      win: sv('<path d="M12 2l2.9 6.3 6.9.7-5.2 4.6 1.5 6.8L12 16.9l-6.1 3.5 1.5-6.8L2.2 9l6.9-.7z"/>', true),
      cash: sv('<path fill-rule="evenodd" d="M2 6h20v12H2zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM4.5 8.5v7h1.6v-7zM17.9 8.5v7h1.6v-7z"/>', true),
    },
    killfeed: {  // hairline crosshair, X, crown, $
      kill: sv('<path d="M12 3v6M12 15v6M3 12h6M15 12h6"/><circle cx="12" cy="12" r="1" fill="currentColor"/>'),
      death: sv('<path d="M6 6l12 12M18 6L6 18"/>'),
      win: sv('<path d="M3.5 18.5h17M4.5 8l4 4 3.5-6.5 3.5 6.5 4-4-1.8 9.5H6.3z"/>'),
      cash: sv('<path d="M12 3v18M16.5 7C16.5 5.3 14.5 4 12 4S7.5 5.3 7.5 7 9.5 10 12 10.5s4.5 1.5 4.5 3.5-2 3.5-4.5 3.5-4.5-1.3-4.5-3"/>'),
    },
  };
  let THEME = "protocol";
  function cvar(name) { return getComputedStyle(document.body).getPropertyValue(name).trim() || "#ffffff"; }
  function setTheme(name) {
    if (!THEMES.includes(name)) name = "protocol";
    THEMES.forEach((t) => document.body.classList.remove("theme-" + t));
    document.body.classList.add("theme-" + name);
    THEME = name;
    const set = ICONS[name];
    [["tk", "kill"], ["td", "death"], ["tw", "win"], ["tc", "cash"]].forEach(([id, k]) => {
      const old = $(id) && $(id).querySelector(".ico");
      if (old) old.outerHTML = set[k];
    });
  }
  setTheme(Q.get("theme") || "protocol");
  window.HUD = { setTheme, themes: THEMES, handle: (e) => handle(e) };

  // ---------------- debt tracker ----------------
  function fmtCash(v) {
    const sgn = v < 0 ? "-" : "", a = Math.abs(v);
    let s;
    if (a >= 1e6) s = (a / 1e6).toFixed(2) + "M";
    else if (a >= 1e5) s = Math.round(a / 1e3) + "K";
    else if (a >= 1e4) s = (a / 1e3).toFixed(1) + "K";
    else s = a.toLocaleString("en-US");
    return sgn + "$" + s;
  }
  let cashShown = null, cashAnim = null;
  function setCash(v, animate) {
    const tile = $("tc"), el = $("nc");
    if (!tile) return;
    if (!SHOW_CASH) { tile.style.display = "none"; return; }
    tile.classList.toggle("neg", v != null && v < 0);
    if (v == null) { cancelAnimationFrame(cashAnim); el.textContent = "$0"; cashShown = null; return; }
    if (cashShown === v) return;
    const from = cashShown == null ? 0 : cashShown;
    cashShown = v;
    cancelAnimationFrame(cashAnim);
    if (!animate) { el.textContent = fmtCash(v); return; }
    // small in-match income just rolls the number; a real payday (+$5k or more) also pulses the tile
    if (v - from >= 5000) {
      restart(tile, "hit");
      const [x, y] = centerOf(tile);
      spawn(x, y, 12, [cvar("--cash"), "#ffffff"], 5, 0.22, 0, 0.4);
    }
    const t0 = performance.now(), dur = Math.min(1400, 300 + Math.abs(v - from) / 40);
    const step = (t) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      el.textContent = fmtCash(Math.round(from + (v - from) * e));
      if (k < 1) cashAnim = requestAnimationFrame(step);
    };
    cashAnim = requestAnimationFrame(step);
  }

  function render(st, animate) {
    const ck = setNum($("nk"), st.kills, animate);
    const cd = setNum($("nd"), st.deaths, animate);
    const cw = setNum($("nw"), st.wins, animate);
    $("kd").textContent = Number(st.kd || 0).toFixed(2);
    $("sv").textContent = st.streak;
    $("bv").textContent = st.best_streak;
    $("stk").className = st.streak >= 3 ? "hot" : "dim";
    setCash(st.cash_delta, animate);
    return { ck, cd, cw };
  }

  function pop(kind, title, sub, value, ms = 2800) {
    if (!SHOW_BIG && kind !== "kill") return;
    const el = document.createElement("div");
    el.className = "pop k-" + kind;
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
    spawn(x, y, 14, [cvar("--kill"), "#ffffff"], 6, 0.22, 0, 0.4);
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
    const ended = evt.ended_streak || 0;
    const sub = ended >= 3 ? `STREAK ENDED · ${ended}` : `K/D ${Number(st.kd).toFixed(2)}`;
    if (DEATHFX === "full" && SHOW_BIG) {
      restart($("vig"), "on");
      $("kia").querySelector(".s").textContent = sub;
      restart($("kia"), "on");
    } else {
      pop("death", "K.I.A.", sub, "", 3200);
    }
    play("death", () => SYNTH.death());
  }

  function onWin(evt) {
    const st = Object.assign({}, evt.state);
    if (evt.test) st.wins += 1; else render(st, true);
    restart($("tw"), "hit");
    const gold = THEME === "protocol" ? ["#ffd24a", "#ffe89a", "#fff6d8", "#c98a00"] : [cvar("--win"), "#ffffff"];
    if (WINFX !== "full" || !SHOW_BIG) {
      const [x, y] = centerOf($("tw"));
      spawn(x, y, 22, gold, 6, 0.12, Math.PI * 0.9, 0.45);
      pop("win", "VICTORY", `WIN #${st.wins} · ${st.kills} KILLS`, "+1", 4500);
      play("win", () => SYNTH.win());
      return;
    }
    $("win").querySelector(".ws").textContent = `WIN #${st.wins}  ·  ${st.kills} KILLS  ·  K/D ${Number(st.kd).toFixed(2)}`;
    {
      restart($("win"), "on");
      spawn(SW / 2, SH * 0.48, 70, gold, 13, 0.2, 0);
      setTimeout(() => rain(50, gold), 300);
    }
    play("win", () => SYNTH.win());
  }

  function handle(evt) {
    switch (evt.type) {
      case "hello":
        if (window.__build && evt.build && evt.build !== window.__build) { location.reload(); return; }
        window.__build = evt.build;
        if (typeof evt.overlay_sounds === "boolean") SERVER_SOUND = evt.overlay_sounds;
        if (!Q.get("theme") && evt.theme && evt.theme !== THEME) setTheme(evt.theme);
        render(evt.state, false);
        break;
      case "undo": render(evt.state, false); break;
      case "kill": onKill(evt); break;
      case "death": onDeath(evt); break;
      case "win": onWin(evt); break;
      case "cash": if (evt.test) setCash(evt.state.cash_delta, true); else render(evt.state, true); break;
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
      case "theme":
        if (!Q.get("theme")) setTheme(evt.name);
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
