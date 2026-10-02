"""Tactical Cinematic SFX pack -- every sound is synthesized from scratch (no samples), so the
whole pack can be released under CC0.  Requires numpy + scipy.

    python tactical_pack.py OUT_DIR [--preview]

Each clip is mastered the same way Haven Sound Studio levels clips (about -22 dBFS RMS over the
active part, peaks <= 0.23) so new buttons sit at the same loudness as the existing board.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, fftconvolve, sosfilt

SR = 48_000
RNG = np.random.default_rng(1867240)  # deterministic: same pack every build


# ----------------------------------------------------------------------------- primitives
def t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR))


def noise(seconds: float) -> np.ndarray:
    return RNG.standard_normal(int(seconds * SR))


def filt(x, kind, freq, order=4):
    nyq = SR / 2
    if kind == "band":
        lo, hi = freq
        sos = butter(order, [max(lo, 10) / nyq, min(hi, nyq * 0.98) / nyq], "bandpass", output="sos")
    else:
        sos = butter(order, min(freq, nyq * 0.98) / nyq, {"low": "lowpass", "high": "highpass"}[kind], output="sos")
    return sosfilt(sos, x)


def sweep_filter(x, f_start, f_end, kind="low", steps=48):
    """Time-varying filter by crossfading short filtered blocks (cheap, click-free)."""
    out = np.zeros_like(x)
    n = len(x)
    edges = np.linspace(0, n, steps + 1).astype(int)
    freqs = np.geomspace(f_start, f_end, steps)
    win_pad = 512
    for i, f in enumerate(freqs):
        a, b = edges[i], edges[i + 1]
        lo, hi = max(0, a - win_pad), min(n, b + win_pad)
        seg = filt(x[lo:hi], kind, f, order=2)
        out[a:b] = seg[a - lo:a - lo + (b - a)]
    return out


def env_exp(seconds, decay, attack=0.002):
    tt = t(seconds)
    e = np.exp(-tt / max(decay, 1e-4))
    na = max(1, int(attack * SR))
    e[:na] *= np.linspace(0, 1, na)
    return e


def env_adsr(seconds, a, d, s, r):
    n = int(seconds * SR)
    na, nd, nr = int(a * SR), int(d * SR), int(r * SR)
    ns = max(0, n - na - nd - nr)
    e = np.concatenate([np.linspace(0, 1, na, endpoint=False), np.linspace(1, s, nd, endpoint=False),
                        np.full(ns, s), np.linspace(s, 0, nr)])
    return np.pad(e, (0, max(0, n - len(e))))[:n]


def osc(freq, seconds, shape="sine", phase0=0.0):
    """freq may be scalar or per-sample array (for sweeps / doppler)."""
    n = int(seconds * SR)
    f = np.broadcast_to(np.asarray(freq, dtype=float), (n,)) if np.ndim(freq) else np.full(n, float(freq))
    ph = phase0 + 2 * np.pi * np.cumsum(f) / SR
    if shape == "sine":
        return np.sin(ph)
    if shape == "saw":  # band-limited-ish saw via additive partials under Nyquist
        out = np.zeros(n)
        fmax = float(np.max(f))
        for k in range(1, int((SR / 2) / max(fmax, 1)) + 1):
            if k > 60:
                break
            out += np.sin(k * ph) / k
        return out * (2 / np.pi)
    if shape == "square":
        out = np.zeros(n)
        fmax = float(np.max(f))
        for k in range(1, int((SR / 2) / max(fmax, 1)) + 1, 2):
            if k > 61:
                break
            out += np.sin(k * ph) / k
        return out * (4 / np.pi)
    raise ValueError(shape)


def glide(f0, f1, seconds, curve=3.0):
    x = np.linspace(0, 1, int(seconds * SR))
    return f1 + (f0 - f1) * np.exp(-curve * x)


def sat(x, drive=2.0):
    return np.tanh(x * drive) / np.tanh(drive)


def reverb_ir(seconds=2.4, decay=0.55, damp=5000, predelay=0.012, early=6):
    n = int(seconds * SR)
    tt = np.arange(n) / SR
    ir = RNG.standard_normal(n) * np.exp(-tt / decay)
    ir = filt(ir, "low", damp, 2)
    ir[: int(predelay * SR)] = 0
    for i in range(early):  # sparse early reflections
        pos = int((predelay + 0.006 + RNG.random() * 0.05) * SR)
        ir[pos] += (0.6 - i * 0.07) * (1 if RNG.random() > 0.5 else -1)
    return ir / np.sqrt(np.sum(ir ** 2))


IRS = {
    "hall": reverb_ir(3.2, 0.85, 4200),
    "room": reverb_ir(1.2, 0.25, 7000),
    "plate": reverb_ir(2.2, 0.5, 9000, 0.004),
    "canyon": reverb_ir(4.5, 1.4, 2500, 0.06),
}


def verb(x, space="hall", wet=0.3, tail=1.0):
    ir = IRS[space]
    y = fftconvolve(np.concatenate([x, silence(tail)]), ir)[: len(x) + int(tail * SR)]
    dry = np.concatenate([x, silence(tail)])
    return dry * (1 - wet * 0.5) + y * wet


def at(base: np.ndarray, sound: np.ndarray, start: float, gain=1.0) -> np.ndarray:
    a = int(start * SR)
    if a + len(sound) > len(base):
        base = np.pad(base, (0, a + len(sound) - len(base)))
    base[a:a + len(sound)] += sound * gain
    return base


def fade_tail(x, seconds=0.05):
    n = min(len(x), int(seconds * SR))
    x = x.copy()
    x[-n:] *= np.linspace(1, 0, n) ** 2
    return x


# ----------------------------------------------------------------------------- building blocks
def sub_boom(seconds=1.6, f0=72, f1=31, decay=0.45, drive=1.6):
    s = osc(glide(f0, f1, seconds, 5), seconds) * env_exp(seconds, decay, 0.001)
    return sat(s, drive)


def crack(seconds=0.35, decay=0.05, lo=900, hi=9000):
    return filt(noise(seconds), "band", (lo, hi)) * env_exp(seconds, decay, 0.0005)


def metal(seconds=1.4, f=420, decay=0.35, ratios=(1, 2.76, 5.40, 8.93, 13.34), detune=0.004):
    out = np.zeros(int(seconds * SR))
    for i, r in enumerate(ratios):
        fr = f * r * (1 + detune * RNG.standard_normal())
        out += osc(fr, seconds) * env_exp(seconds, decay / (1 + i * 0.6), 0.0005) / (1 + i * 0.7)
    return out


def whoosh(seconds=1.0, f_start=400, f_end=4000, peak_at=0.7):
    n = noise(seconds)
    shaped = sweep_filter(n, f_start, f_end, "low")
    x = np.linspace(0, 1, len(n))
    bell = np.exp(-((x - peak_at) ** 2) / 0.04)
    return shaped * bell


def thump(f=55, seconds=0.35, decay=0.09):
    return sat(osc(glide(f * 1.8, f, seconds, 18), seconds) * env_exp(seconds, decay, 0.002), 1.3)


def beep(f, seconds, shape="sine", attack=0.003, release=0.01):
    return osc(f, seconds, shape) * env_adsr(seconds, attack, 0.0, 1.0, release)


def static_burst(seconds, density=1.0):
    n = filt(noise(seconds), "band", (350, 3200))
    am = filt(RNG.random(len(n)) ** (3 / density), "low", 60, 2)
    am = (am - am.min()) / (np.ptp(am) + 1e-9)
    return sat(n * (0.35 + am), 2.5)


def radio_eq(x):
    return sat(filt(filt(x, "high", 380), "low", 3100), 2.2)


def brass_chord(freqs, seconds, open_time=0.08, close=0.9):
    out = np.zeros(int(seconds * SR))
    for f in freqs:
        for d in (-0.006, 0, 0.007):
            out += osc(f * (1 + d), seconds, "saw")
    out /= len(freqs) * 3
    bright = sweep_filter(out, 5200, 500, "low")
    e = env_adsr(seconds, open_time, 0.35, 0.55, close)
    return sat(bright * e, 1.8)


def timpani(f=98, seconds=1.6):
    body = osc(glide(f * 1.12, f, seconds, 12), seconds) * env_exp(seconds, 0.55, 0.001)
    skin = filt(noise(seconds), "band", (120, 1400)) * env_exp(seconds, 0.04)
    return sat(body * 0.9 + skin * 0.4, 1.4)


def explosion(seconds=3.0, size=1.0):
    s = sub_boom(seconds, 64 * (1.2 - size * 0.2), 26, 0.55 * size, 2.2) * 1.0
    body = filt(noise(seconds), "low", 1400) * env_exp(seconds, 0.32 * size, 0.003)
    roar = sweep_filter(noise(seconds), 3000, 180, "low") * env_exp(seconds, 0.9 * size, 0.02) * 0.6
    crackle = np.zeros(int(seconds * SR))
    for _ in range(int(140 * size)):
        p = int(RNG.uniform(0.05, seconds * 0.8) * SR)
        crackle[p] += RNG.uniform(-1, 1) * math.exp(-p / SR / (0.8 * size))
    crackle = filt(crackle, "band", (900, 7000)) * 0.9
    mix = s * 1.0 + sat(body, 2.5) * 0.8 + roar + crackle + crack(seconds, 0.03, 600, 6000) * 0.7
    return verb(mix, "canyon", 0.35, 1.2)


# ----------------------------------------------------------------------------- the pack
def impact():
    d = 2.6
    x = sub_boom(d, 80, 30, 0.6, 2.0) + crack(d, 0.035) * 0.9 + metal(d, 310, 0.5) * 0.35
    x = at(x, filt(noise(0.25), "high", 3000) * env_exp(0.25, 0.02) * 0.4, 0.0)
    return verb(x, "hall", 0.38, 1.0)


def sub_drop():
    d = 2.4
    x = sat(osc(glide(140, 24, d, 2.6), d) * env_adsr(d, 0.004, 0.3, 0.7, 1.6), 2.6)
    x += filt(x, "high", 120) * 0.25  # harmonics so it reads on small speakers
    return verb(x, "room", 0.15, 0.4)


def horn_hit():
    d = 3.2
    x = brass_chord([41.2, 82.4, 123.5, 164.8], d, 0.03, 1.8) * 1.2
    x += sub_boom(d, 70, 38, 0.8, 1.4) * 0.6 + crack(d, 0.04, 500, 5000) * 0.35
    return verb(x, "hall", 0.4, 1.2)


def reverse_swell():
    d = 1.6
    src = metal(d, 520, 0.6) * 0.5 + filt(noise(d), "high", 1800) * env_exp(d, 0.4) * 0.6
    sw = verb(src, "plate", 0.8, 0.0)[: int(d * SR)]
    sw = sw[::-1] * np.linspace(0.2, 1, len(sw)) ** 1.6   # reversed reverb rushing into the hit
    return at(np.concatenate([sw, silence(2.8)]), impact(), d - 0.02)


def slow_mo():
    d = 3.4
    # tape-stop: a mid drone whose playback speed falls to zero
    speed = np.clip(1 - np.linspace(0, 1, int(d * SR)) ** 0.7, 0, 1)
    src_t = np.cumsum(speed) / SR
    drone = np.sin(2 * np.pi * 220 * src_t) + 0.5 * np.sin(2 * np.pi * 330 * src_t) + 0.3 * np.sin(2 * np.pi * 440 * src_t)
    drone = filt(sat(drone, 1.5), "low", 2200) * env_adsr(d, 0.02, 0.2, 0.8, 1.2) * 0.45
    x = drone + whoosh(d, 3000, 200, 0.25) * 0.5
    for i, s in enumerate((0.9, 1.75, 2.6)):
        x = at(x, thump(48, 0.5, 0.12) * (0.9 - i * 0.15), s)
    x = filt(x, "low", 3800)
    return verb(x, "canyon", 0.3, 1.0)


def comms_open():
    x = silence(0.9)
    x = at(x, filt(noise(0.012), "high", 2000) * 0.8, 0.0)          # PTT click
    x = at(x, static_burst(0.32, 1.4) * env_adsr(0.32, 0.005, 0.1, 0.6, 0.12) * 0.5, 0.01)
    x = at(x, beep(1400, 0.07, "square") * 0.25, 0.36)
    x = at(x, beep(1850, 0.09, "square") * 0.25, 0.45)
    return radio_eq(x) * 0.9


def comms_close():
    x = silence(0.8)
    x = at(x, beep(1850, 0.06, "square") * 0.25, 0.0)
    x = at(x, beep(1250, 0.09, "square") * 0.25, 0.07)
    x = at(x, static_burst(0.4, 0.9) * env_exp(0.4, 0.12) * 0.55, 0.18)
    x = at(x, filt(noise(0.01), "high", 2000) * 0.7, 0.58)
    return radio_eq(x) * 0.9


def interference():
    d = 1.8
    n = static_burst(d, 2.0)
    warble = 1 + 0.8 * np.sin(2 * np.pi * np.cumsum(glide(18, 4, d, 2)) / SR)
    hold = np.repeat(n[::6], 6)[: len(n)]                                # sample-and-hold grit
    tone = osc(glide(1900, 700, d, 1.5), d, "square") * 0.08
    x = (n * 0.6 + hold * 0.4) * warble * 0.5 + tone
    return radio_eq(x * env_adsr(d, 0.01, 0.2, 0.8, 0.35))


def sonar_ping():
    d = 0.9
    p = osc(1180, d) * env_exp(d, 0.22, 0.002) + osc(2360, d) * env_exp(d, 0.08) * 0.15
    x = np.concatenate([p, silence(2.6)])
    for k, g in ((0.55, 0.38), (1.1, 0.16), (1.65, 0.07)):
        x = at(x, p * g, k)
    return verb(x, "canyon", 0.45, 1.2)


def target_lock():
    x = silence(1.9)
    pos, gap = 0.0, 0.17
    while pos < 1.05:
        x = at(x, beep(2050, 0.035, "square") * 0.22, pos)
        pos += gap
        gap = max(0.045, gap * 0.8)
    tone = osc(2050, 0.7, "square") * env_adsr(0.7, 0.004, 0.0, 1.0, 0.08) * 0.2
    x = at(x, tone, 1.12)
    return verb(filt(x, "low", 6000), "room", 0.2, 0.3)


def incoming():
    d = 2.2
    f = np.where((t(d) % 0.55) < 0.275, 640.0, 860.0)
    f = filt(f, "low", 40, 1)  # slight slew between tones
    x = sat(filt(osc(f, d, "saw"), "low", 3200) * 0.6, 2.2) * env_adsr(d, 0.02, 0.0, 1.0, 0.25)
    return verb(x, "canyon", 0.3, 1.2) * 0.85


def jet_flyby():
    d = 3.4
    x = np.linspace(-1, 1, int(d * SR))
    closeness = 1 / (1 + (x * 3.2) ** 2)
    doppler = 1 + 0.22 * np.tanh(-x * 4)                       # pitch high on approach, low after
    roar = sweep_filter(noise(d), 900, 7000, "low") * closeness
    roar = filt(roar, "high", 120)
    whine = osc(1650 * doppler, d) * closeness ** 1.6 * 0.18 + osc(3300 * doppler, d) * closeness ** 2 * 0.06
    rumble = filt(noise(d), "low", 160) * closeness * 0.9
    return verb((roar * 0.8 + whine + rumble) * 0.9, "canyon", 0.25, 1.0)


def explosion_big():
    return explosion(3.2, 1.0)


def airstrike():
    x = silence(7.5)
    x = at(x, incoming()[: int(1.7 * SR)] * env_adsr(1.7, 0.01, 0, 1, 0.3), 0.0, 0.8)
    x = at(x, jet_flyby(), 1.3, 0.9)
    x = at(x, explosion(3.0, 0.8), 3.0, 0.8)
    x = at(x, explosion(3.0, 1.0), 3.45, 0.9)
    x = at(x, explosion(3.4, 1.15), 3.95, 1.0)
    return x


def heartbeat():
    x = silence(3.6)
    for i in range(4):
        b = 0.12 + i * 0.86
        x = at(x, thump(52, 0.4, 0.085), b, 1.0)
        x = at(x, thump(44, 0.4, 0.10), b + 0.21, 0.75)
    hiss = filt(noise(3.6), "band", (200, 900)) * env_adsr(3.6, 0.6, 0.2, 0.3, 1.5) * 0.05
    return filt(x + hiss, "low", 1800)


def objective_secured():
    d = 3.0
    x = brass_chord([146.8, 220.0, 293.7, 370.0, 440.0], d, 0.05, 1.6)
    x = at(x, timpani(73.4), 0.0, 0.9)
    x = at(x, metal(2.2, 880, 0.9, (1, 2.0, 3.0, 4.2)) * 0.18, 0.02)
    x = at(x, crack(0.4, 0.03, 2000, 12000) * 0.3, 0.0)
    return verb(x, "hall", 0.35, 1.0)


def objective_lost():
    x = brass_chord([146.8, 174.6, 220.0], 1.1, 0.04, 0.6) * 0.9
    x = at(x, brass_chord([116.5, 138.6, 174.6], 2.1, 0.04, 1.6), 0.55)
    x = at(x, timpani(65.4, 2.0), 0.55, 0.9)
    x = at(x, sub_boom(2.0, 60, 30, 0.6, 1.4) * 0.6, 0.55)
    return verb(filt(x, "low", 3500), "hall", 0.4, 1.2)


def lock_and_load():
    x = silence(1.3)
    clack = lambda f, g: (metal(0.18, f, 0.03, (1, 1.7, 2.9)) * 0.6 + crack(0.18, 0.012, 1500, 9000)) * g
    scrape = filt(noise(0.16), "band", (1800, 6500)) * env_adsr(0.16, 0.01, 0.05, 0.7, 0.05) * 0.35
    x = at(x, clack(1450, 0.8), 0.05)
    x = at(x, scrape, 0.12)
    x = at(x, clack(1150, 1.0), 0.30)
    x = at(x, scrape[::-1] * 0.8, 0.52)
    x = at(x, clack(1300, 1.1) + thump(90, 0.18, 0.04) * 0.6, 0.66)
    return verb(x, "room", 0.22, 0.4)


def glitch_hit():
    base = impact()[: int(0.7 * SR)]
    out = silence(1.4)
    pos, seg = 0.0, 0.11
    for i in range(7):
        chunk = base[: int(seg * SR)] * (1 - i * 0.1)
        crushed = np.round(chunk * 12) / 12                  # bit-crush
        out = at(out, np.repeat(crushed[::3], 3)[: len(chunk)], pos)
        pos += seg
        seg *= 0.72
    out = at(out, base * 0.9, pos)
    return fade_tail(out, 0.2)


def countdown():
    x = silence(4.6)
    for i, f in enumerate((880, 988, 1175)):
        tick = (beep(f, 0.09) * 0.5 + filt(noise(0.09), "band", (1500, 5000)) * env_exp(0.09, 0.012) * 0.6)
        x = at(x, tick * env_exp(0.09, 0.03), i * 1.0, 0.9)
    x = at(x, whoosh(0.9, 300, 6000, 0.95) * 0.5, 2.15)
    x = at(x, impact(), 3.0)
    return verb(x, "room", 0.15, 0.2)


def reveal():
    d = 2.6
    sw = filt(noise(1.2), "high", 2500) * np.linspace(0, 1, int(1.2 * SR)) ** 3 * 0.6
    shimmer = np.zeros(int(d * SR))
    for f in (587.3, 880.0, 1174.7, 1480.0):
        shimmer += osc(f * (1 + 0.003 * np.sin(2 * np.pi * 5 * t(d))), d) * env_adsr(d, 0.01, 0.4, 0.35, 1.6) / 4
    x = at(np.concatenate([sw, silence(d)]), shimmer * 0.7 + timpani(73.4, d) * 0.4, 1.18)
    return verb(x, "plate", 0.5, 1.0)


def squad_wipe():
    d = 4.0
    drone = brass_chord([55.0, 82.4, 110.0], d, 0.6, 2.0) * 0.7
    x = drone + filt(explosion(3.0, 0.9), "low", 700)[: int(d * SR)] * 0.8
    x = at(x, radio_eq(static_burst(1.2, 0.8)) * env_exp(1.2, 0.5) * 0.35, 2.4)
    return verb(x, "canyon", 0.35, 1.2)


def kill_confirm():
    x = metal(0.6, 1900, 0.09, (1, 2.4, 3.9)) * 0.35 + crack(0.6, 0.008, 3000, 12000) * 0.5
    x += thump(70, 0.6, 0.07) * 0.9 + sub_boom(0.6, 90, 45, 0.12, 1.2) * 0.5
    return verb(x, "room", 0.25, 0.4)


def kia():
    d = 3.2
    x = filt(impact(), "low", 900)[: int(d * SR)] * 1.0
    ring = osc(3520, d) * env_adsr(d, 0.25, 0.4, 0.5, 2.0) * 0.12
    return x + ring


PACK = [
    # id, label, category, builder, usage
    ("t_impact", "Impact", "Cinematic", impact, "Big moment, reveal, landing a clutch."),
    ("t_subdrop", "Sub Drop", "Cinematic", sub_drop, "Under a dramatic line."),
    ("t_horn", "Horn Hit", "Cinematic", horn_hit, "Trailer-style 'BRAAAM'."),
    ("t_swell", "Reverse Swell", "Cinematic", reverse_swell, "Build-up into a hit."),
    ("t_slowmo", "Slow-Mo", "Cinematic", slow_mo, "Replay / 'wait for it' moment."),
    ("t_comms_open", "Comms Open", "Comms", comms_open, "Before a callout."),
    ("t_comms_close", "Comms Over", "Comms", comms_close, "After a callout."),
    ("t_static", "Interference", "Comms", interference, "Jammed / lost signal joke."),
    ("t_sonar", "Sonar Ping", "Recon", sonar_ping, "Spotted / searching."),
    ("t_lock", "Target Lock", "Recon", target_lock, "Locking on, about to push."),
    ("t_incoming", "Incoming", "Recon", incoming, "Danger close / enemy push."),
    ("t_flyby", "Jet Flyby", "Ordnance", jet_flyby, "Air support inbound."),
    ("t_explosion", "Explosion", "Ordnance", explosion_big, "Grenade / vehicle down."),
    ("t_airstrike", "Airstrike", "Ordnance", airstrike, "Full call-in: alarm, flyby, triple blast."),
    ("t_heartbeat", "Heartbeat", "Tension", heartbeat, "Clutch / last alive."),
    ("t_secured", "Objective Secured", "Stingers", objective_secured, "Win / point captured."),
    ("t_lost", "Objective Lost", "Stingers", objective_lost, "Lost the point / round."),
    ("t_lockload", "Lock & Load", "Stingers", lock_and_load, "Match start / going in."),
    ("t_glitch", "Glitch Hit", "Stingers", glitch_hit, "Something broke / desync."),
    ("t_countdown", "Countdown", "Stingers", countdown, "3-2-1 then hit."),
    ("t_reveal", "Reveal", "Stingers", reveal, "Showing something off."),
    ("t_wipe", "Squad Wipe", "Stingers", squad_wipe, "Whole squad down."),
    ("t_confirm", "Kill Confirm", "Reactive", kill_confirm, "Auto on kill (short)."),
    ("t_kia", "K.I.A.", "Reactive", kia, "Auto on death."),
]


def master(x: np.ndarray, level=-22.0, peak=0.23) -> np.ndarray:
    """Same rule as Haven Sound Studio prepare(): active-window RMS to level, never exceed peak."""
    x = np.asarray(x, dtype=np.float64)
    x = x - np.mean(x)
    x = filt(x, "high", 22, 2)                     # remove DC/sub-sonic rumble
    # Program limiter: transient-heavy clips (clicks, ticks) would otherwise be peak-limited
    # far below the meme clips. Pull peaks down until crest factor <= ~12 dB.
    win = 960

    def crest(y):
        e = np.array([np.mean(y[i:i + win] ** 2) for i in range(0, len(y), win)])
        a = e[e > max(e.max() * 0.01, 1e-12)]
        return 20 * math.log10(np.max(np.abs(y)) / math.sqrt(float(a.mean())))

    for _ in range(6):
        if crest(x) <= 12.0:
            break
        pk = np.max(np.abs(x))
        env = np.abs(x)
        # fast-attack / 60 ms release envelope
        alpha = math.exp(-1 / (0.06 * SR))
        smooth = np.empty_like(env)
        acc = 0.0
        for i in range(0, len(env), 64):                  # block-wise for speed
            blk = env[i:i + 64].max()
            acc = blk if blk > acc else acc * alpha ** 64 + blk * (1 - alpha ** 64)
            smooth[i:i + 64] = acc
        thr = pk * 0.5
        x = x * np.minimum(1.0, thr / np.maximum(smooth, 1e-9))
        x = sat(x / (thr * 1.15), 1.2) * thr * 1.15       # catch overs smoothly
    energies = None
    energies = np.array([np.mean(x[i:i + win] ** 2) for i in range(0, len(x), win)])
    active = energies[energies > max(energies.max() * 0.01, 1e-12)]
    rms = math.sqrt(float(active.mean()))
    gain = min(10 ** (level / 20) / rms, peak / float(np.max(np.abs(x))))
    x = x * gain
    n = min(480, len(x) // 8)
    x[:n] *= np.linspace(0, 1, n)
    x = fade_tail(x, 0.08)
    return x.astype(np.float32)


# Snapshot after every import-time draw (reverb IRs), so each build() starts from the same state
# and repeated builds in one process are byte-identical.
_RNG_START = RNG.bit_generator.state


def build(out_dir: Path, preview=False):
    RNG.bit_generator.state = _RNG_START
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest, previews = [], []
    for ident, label, category, fn, usage in PACK:
        x = master(fn())
        # trim trailing near-silence
        alive = np.flatnonzero(np.abs(x) > 1e-4)
        x = fade_tail(x[: int(alive[-1]) + 2400], 0.05) if len(alive) else x
        path = out_dir / f"{ident}.wav"
        wavfile.write(path, SR, x)
        rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
        manifest.append({"id": ident, "label": label, "category": category, "file": path.name,
                         "seconds": round(len(x) / SR, 3), "peak": round(float(np.max(np.abs(x))), 4),
                         "rms_db": round(20 * math.log10(max(rms, 1e-9)), 1), "usage": usage,
                         "license": "CC0-1.0 (synthesized by tactical_pack.py)"})
        previews += [x, np.zeros(SR // 2, dtype=np.float32)]
        print(f"{ident:15} {label:18} {len(x)/SR:5.2f}s peak={np.max(np.abs(x)):.3f}")
    (out_dir / "pack.json").write_text(json.dumps({"name": "Tactical Cinematic", "rate": SR, "clips": manifest}, indent=2))
    if preview:
        wavfile.write(out_dir / "preview_all.wav", SR, np.concatenate(previews))
    return manifest


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "tactical"), "--preview" in sys.argv)
