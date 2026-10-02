"""Renders the Protocol HUD sound packs to 48 kHz stereo WAV (numpy only).
License: this file and the sounds it renders are CC BY 4.0 (credit: "Sounds by SwampBewdy"), see sounds/LICENSE.
python tools/gen_sounds.py   ->  sounds/tactical/*.wav, sounds/cinematic/*.wav
"""
import os, wave
import numpy as np

SR = 48000
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rng = np.random.default_rng(7)


# ---------------------------------------------------------------- building blocks
def t_(dur):
    return np.arange(int(SR * dur)) / SR


def env(dur, attack=0.002, decay=None, curve=4.0):
    """Fast attack, exponential-ish decay to silence at `dur`."""
    t = t_(dur)
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    d = np.exp(-curve * t / (decay or dur))
    tail = np.clip((dur - t) / 0.01, 0, 1)  # guarantee a clean end
    return a * d * tail


def sweep(f0, f1, dur, shape="sine", exp=True):
    t = t_(dur)
    f = f0 * (f1 / f0) ** (t / dur) if exp else f0 + (f1 - f0) * t / dur
    ph = 2 * np.pi * np.cumsum(f) / SR
    if shape == "sine":
        return np.sin(ph)
    if shape == "tri":
        return 2 / np.pi * np.arcsin(np.sin(ph))
    if shape == "saw":  # band-limited-ish saw from 12 partials
        return sum(np.sin(k * ph) / k for k in range(1, 13)) * 0.6
    raise ValueError(shape)


def spectral(x, lo=None, hi=None, tilt=0.0):
    """Zero-phase band filter in the frequency domain. Zero-padded so nothing wraps
    around (circular filtering was causing clicks), then edge-faded."""
    n = len(x)
    y = _spectral(np.pad(x, (0, n)), lo, hi, tilt)[:n]
    k = min(n // 4, int(SR * 0.002))
    if k > 1:
        y[:k] *= np.linspace(0, 1, k)
        y[-k:] *= np.linspace(1, 0, k)
    return y


def _spectral(x, lo=None, hi=None, tilt=0.0):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    g = np.ones_like(f)
    if lo:
        g *= 1 / np.sqrt(1 + (lo / np.maximum(f, 1)) ** 4)
    if hi:
        g *= 1 / np.sqrt(1 + (f / hi) ** 4)
    if tilt:
        g *= (np.maximum(f, 20) / 1000) ** tilt
    return np.fft.irfft(X * g, len(x))


def noise(dur, lo=None, hi=None):
    return spectral(rng.standard_normal(int(SR * dur)), lo, hi)


def ping(freqs, dur, decay):
    """Inharmonic metallic ping (a few partials with their own decays)."""
    t = t_(dur)
    out = np.zeros_like(t)
    for i, f in enumerate(freqs):
        out += np.sin(2 * np.pi * f * t + rng.uniform(0, 6.28)) * np.exp(-t / (decay / (1 + 0.6 * i))) / (1 + 0.5 * i)
    return out * env(dur, 0.001, dur, 0.5)


def place(parts, total):
    """Mix [(signal, start_s, gain), ...] into a mono buffer of `total` seconds."""
    out = np.zeros(int(SR * total))
    for sig, start, gain in parts:
        i = int(SR * start)
        n = min(len(sig), len(out) - i)
        out[i:i + n] += sig[:n] * gain
    return out


def reverb(x, seconds, damp_hz, mix, predelay=0.012, width=0.5):
    """Stereo convolution reverb with a synthetic decaying-noise impulse."""
    n = int(SR * seconds)
    t = np.arange(n) / SR
    irs = []
    for _ in range(2):
        ir = rng.standard_normal(n) * np.exp(-6.9 * t / seconds)
        ir = spectral(ir, 120, damp_hz)
        ir = np.concatenate([np.zeros(int(SR * predelay)), ir])
        irs.append(ir / np.sqrt(np.sum(ir ** 2)))
    L = len(x) + len(irs[0]) - 1
    nfft = 1 << (L - 1).bit_length()
    X = np.fft.rfft(x, nfft)
    wet = [np.fft.irfft(X * np.fft.rfft(ir, nfft), nfft)[:L] for ir in irs]
    dry = np.pad(x, (0, L - len(x)))
    mid = (wet[0] + wet[1]) / 2
    side = (wet[0] - wet[1]) / 2 * width
    return np.stack([dry + mix * (mid + side), dry + mix * (mid - side)])


def master(st, peak_db=-1.5, drive=1.4):
    st = np.tanh(st * drive) / np.tanh(drive)  # gentle glue / saturation
    st = st / (np.max(np.abs(st)) + 1e-9) * 10 ** (peak_db / 20)
    fade = int(SR * 0.02)
    st[:, -fade:] *= np.linspace(1, 0, fade)
    # trim trailing silence below -70 dB
    alive = np.where(np.max(np.abs(st), axis=0) > 10 ** (-70 / 20))[0]
    return st[:, : (alive[-1] + 1 if len(alive) else st.shape[1])]


def save(pack, name, st):
    d = os.path.join(ROOT, "sounds", pack)
    os.makedirs(d, exist_ok=True)
    pcm = (np.clip(st.T, -1, 1) * 32767).astype("<i2")
    with wave.open(os.path.join(d, name + ".wav"), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.tobytes())
    rms = 20 * np.log10(np.sqrt(np.mean(st ** 2)) + 1e-9)
    print(f"{pack:10s} {name:8s} {st.shape[1] / SR:5.2f}s  rms {rms:6.1f} dBFS")


# ---------------------------------------------------------------- shared layers
def thud(f0=110, f1=48, dur=0.12, curve=5):
    return sweep(f0, f1, dur) * env(dur, 0.001, dur, curve)


def click(dur=0.014, lo=2500, hi=9000):
    return noise(dur, lo, hi) * env(dur, 0.0005, dur, 6)


def chord_pad(notes, dur, cutoff, attack=0.35, detune_cents=5):
    t = t_(dur)
    out = np.zeros_like(t)
    for f in notes:
        for c in (-detune_cents, detune_cents):
            out += sweep(f * 2 ** (c / 1200), f * 2 ** (c / 1200), dur, "saw")
    a = np.clip(t / attack, 0, 1) ** 1.5
    d = np.exp(-4.6 * np.clip(t - attack, 0, None) / (dur - attack))  # decays to ~-40 dB
    tail = np.clip((dur - t) / 0.4, 0, 1)                               # then fades fully
    return spectral(out * a * d * tail, 60, cutoff) / len(notes)


def heartbeat(gain=1.0):
    # pure low sine with a soft 6 ms attack: no broadband click on each beat
    beat = sweep(70, 42, 0.16) * env(0.16, 0.006, 0.16, 4)
    return place([(beat, 0, 1.0), (beat, 0.26, 0.7), (beat, 0.95, 0.6), (beat, 1.21, 0.4)], 1.5) * gain


# ---------------------------------------------------------------- TACTICAL pack
def tactical():
    P = "tactical"
    ping_a = [2350, 3620, 5210]

    def kill_core(pitch=1.0, weight=1.0):
        return place([
            (thud(110 * weight, 48), 0, 0.9),
            (click(), 0, 0.55),
            (noise(0.06, 300, 1500) * env(0.06, 0.001, 0.06, 6), 0, 0.25),
            (ping([f * pitch for f in ping_a], 0.24, 0.07), 0.004, 0.32),
        ], 0.3)

    save(P, "kill", master(reverb(kill_core(), 0.45, 5000, 0.16), -7))
    save(P, "double", master(reverb(place([(kill_core(), 0, 1), (kill_core(1.26), 0.075, 0.85)], 0.45), 0.5, 5000, 0.18), -6))
    trip = place([(kill_core(1.0, 1.1), 0, 1), (kill_core(1.26), 0.06, 0.8), (kill_core(1.5), 0.12, 0.75)], 0.5)
    save(P, "triple", master(reverb(trip, 0.6, 5500, 0.2), -5))
    save(P, "multi", master(reverb(trip, 0.6, 5500, 0.2), -5))

    spree = place([
        (sweep(78, 32, 1.3) * env(1.3, 0.003, 1.3, 3.2), 0, 1.0),
        (noise(0.35, None, 900) * env(0.35, 0.001, 0.35, 5), 0, 0.6),
        (ping([420, 1130, 1870, 2950], 1.1, 0.35), 0.005, 0.28),
        (click(0.02, 1500, 7000), 0, 0.5),
        (noise(0.7, 2500, 9000) * env(0.7, 0.002, 0.7, 5), 0.01, 0.12),
    ], 1.4)
    save(P, "spree", master(reverb(spree, 1.8, 3500, 0.32), -2.5))

    death = place([
        (spectral(thud(90, 34, 0.75, 3), None, 400), 0, 1.0),
        (noise(0.5, None, 450) * env(0.5, 0.002, 0.5, 4), 0, 0.5),
        (np.sin(2 * np.pi * 3900 * t_(1.3)) * env(1.3, 0.06, 1.3, 3.0), 0.05, 0.022),  # faint ear-ring
    ], 1.4)
    save(P, "death", master(reverb(death, 1.2, 1800, 0.3), -5))

    win = place([
        (sweep(62, 30, 1.5) * env(1.5, 0.003, 1.5, 3), 0, 1.0),
        (noise(0.4, None, 1200) * env(0.4, 0.001, 0.4, 5), 0, 0.45),
        (chord_pad([110, 164.81, 220, 277.18, 329.63], 3.2, 1500), 0.02, 1.6),
        (ping([1760, 2637, 3520], 2.0, 0.6), 0.25, 0.08),
    ], 3.4)
    save(P, "win", master(reverb(win, 2.8, 6000, 0.34, width=0.8), -1.5))

    save(P, "downed", master(reverb(heartbeat(), 0.8, 900, 0.2), -10))
    rev = place([
        (np.sin(2 * np.pi * 660 * t_(0.3)) * env(0.3, 0.01, 0.3, 5), 0, 0.5),
        (np.sin(2 * np.pi * 990 * t_(0.45)) * env(0.45, 0.01, 0.45, 5), 0.11, 0.45),
    ], 0.6)
    save(P, "revived", master(reverb(rev, 0.9, 6000, 0.3), -10))


# ---------------------------------------------------------------- CINEMATIC pack
def cinematic():
    P = "cinematic"

    def punch(f0=170, weight=1.0):
        return place([
            (thud(f0, 40, 0.2, 4.5), 0, 1.0 * weight),
            (click(0.03, 800, 6000), 0, 0.45),
            (noise(0.12, 200, 2500) * env(0.12, 0.001, 0.12, 5), 0, 0.3),
        ], 0.3)

    save(P, "kill", master(reverb(punch(), 0.9, 3500, 0.24, width=0.9), -6))
    save(P, "double", master(reverb(place([(punch(), 0, 1), (punch(210), 0.09, 0.9)], 0.45), 1.0, 3500, 0.26, width=0.9), -5))
    riser = noise(0.35, 400, 6000) * np.linspace(0, 1, int(SR * 0.35)) ** 2
    trip = place([(punch(170, 1.1), 0, 1), (punch(210), 0.07, 0.85), (punch(250), 0.14, 0.8), (riser[::-1], 0.14, 0.18)], 0.6)
    save(P, "triple", master(reverb(trip, 1.2, 4000, 0.28, width=0.9), -4))
    save(P, "multi", master(reverb(trip, 1.2, 4000, 0.28, width=0.9), -4))

    braam = chord_pad([55, 55 * 1.5, 110], 2.0, 520, attack=0.03, detune_cents=12)
    spree = place([
        (braam, 0, 2.6),
        (sweep(70, 28, 1.6) * env(1.6, 0.004, 1.6, 3), 0, 1.0),
        (noise(0.5, None, 700) * env(0.5, 0.001, 0.5, 4), 0, 0.5),
        (click(0.03, 1000, 6000), 0, 0.35),
    ], 2.1)
    save(P, "spree", master(reverb(spree, 2.5, 2500, 0.38, width=1.0), -7.5, drive=1.4))

    t = t_(1.1)
    down_sweep = np.sin(2 * np.pi * np.cumsum(300 * (80 / 300) ** (t / 1.1)) / SR) * env(1.1, 0.01, 1.1, 3)
    death = place([
        (spectral(thud(80, 28, 1.1, 2.5), None, 300), 0, 1.0),
        (spectral(noise(1.0, 150, 1400) * env(1.0, 0.02, 1.0, 3), None, 900), 0, 0.35),
        (down_sweep, 0, 0.12),
    ], 1.3)
    save(P, "death", master(reverb(death, 2.0, 1500, 0.35, width=1.0), -3.5))

    win = place([
        (sweep(58, 26, 1.8) * env(1.8, 0.003, 1.8, 2.6), 0, 1.0),
        (noise(0.6, None, 1500) * env(0.6, 0.001, 0.6, 4), 0, 0.5),
        (chord_pad([110, 164.81, 220, 277.18, 329.63, 440], 3.8, 2200, attack=0.5), 0.02, 1.8),
        (chord_pad([659.26, 880], 3.4, 5000, attack=0.8), 0.3, 0.5),
    ], 4.0)
    save(P, "win", master(reverb(win, 3.5, 7000, 0.4, width=1.0), -1.2))

    save(P, "downed", master(reverb(place([(heartbeat(1.1), 0, 1), (noise(1.4, 200, 900) * env(1.4, 0.3, 1.4, 2) , 0, 0.05)], 1.6), 1.2, 800, 0.25), -9))
    t = t_(0.8)
    glide = np.sin(2 * np.pi * np.cumsum(440 * 1.5 ** (t / 0.8)) / SR) * env(0.8, 0.15, 0.8, 3)
    rev = place([(glide, 0, 0.4), (noise(0.8, 2000, 8000) * env(0.8, 0.3, 0.8, 3), 0, 0.06)], 1.0)
    save(P, "revived", master(reverb(rev, 1.5, 7000, 0.35, width=1.0), -9))


if __name__ == "__main__":
    tactical()
    cinematic()
