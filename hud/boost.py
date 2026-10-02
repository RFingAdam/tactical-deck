"""Game Boost: while Wardogs is running, gaming + streaming get the P-cores and priority;
background apps are pushed to the E-cores at below-normal priority with Windows EcoQoS.
Everything is restored when the game closes. Started by server.py (config: "game_boost": true)."""
import ctypes, os, threading, time
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PQLI, PSI = 0x1000, 0x0200
ABOVE_NORMAL, NORMAL, BELOW_NORMAL = 0x8000, 0x20, 0x4000

GAME = {"wardogsclient-win64-shipping.exe"}
STREAM = {"obs64.exe"}  # Streamlabs' encoder/compositor (OBS recommends above-normal to avoid encoding lag)
BACKGROUND = {
    "app.exe", "replay.exe", "vision.exe", "syncthing.exe", "steamwebhelper.exe",
    "steelseriesggclient.exe", "steelseriesprism.exe", "lcore.exe", "lghub.exe", "lghub_agent.exe",
    "lghub_system_tray.exe", "bitwarden.exe", "1password.exe", "nordvpn.exe", "onedrive.exe",
    "ms-teams.exe", "winword.exe", "phoneexperiencehost.exe", "crossdeviceresume.exe", "searchhost.exe",
    "fences.exe", "docker desktop.exe", "com.docker.backend.exe", "brave.exe", "claude.exe",
    "powerpanel personal.exe", "ppuser.exe", "navigraph simlink.exe", "microsoft.lists.exe",
}
# never touched: audio (voicemeeter, audiodg, SteelSeriesEngine/Sonar), Discord (voice), TikTok LIVE Studio,
# Companion (stream buttons), the HUD itself, and anything we can't open (services / elevated).


class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


def e_core_mask():
    """Logical processors with the lowest EfficiencyClass (the E-cores on a hybrid CPU)."""
    size = wintypes.ULONG(0)
    k32.GetSystemCpuSetInformation(None, 0, ctypes.byref(size), None, 0)
    buf = (ctypes.c_byte * size.value)()
    if not k32.GetSystemCpuSetInformation(buf, size, ctypes.byref(size), None, 0):
        return 0
    off, cls = 0, {}
    raw = bytes(buf)
    while off < size.value:
        rec_size = int.from_bytes(raw[off:off + 4], "little")
        # SYSTEM_CPU_SET_INFORMATION: Size(4) Type(4) Id(4) Group(2) LogicalProcessorIndex(1) CoreIndex(1)
        # LastLevelCacheIndex(1) NumaNodeIndex(1) EfficiencyClass(1)
        lp = raw[off + 14]
        eff = raw[off + 18]
        cls[lp] = eff
        off += rec_size
    if len(set(cls.values())) < 2:
        return 0  # not a hybrid CPU
    low = min(cls.values())
    return sum(1 << lp for lp, e in cls.items() if e == low)


def list_processes():
    import subprocess, csv
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                         creationflags=0x08000000).stdout
    for row in csv.reader(out.splitlines()):
        if len(row) >= 2:
            try:
                yield row[0].lower(), int(row[1])
            except ValueError:
                pass


class GameBoost(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.ecores = e_core_mask()
        self.active = False
        self.saved = {}  # pid -> (name, priority, affinity)
        self.status = {"active": False, "ecore_mask": hex(self.ecores), "boosted": 0, "background": 0}

    def _open(self, pid):
        return k32.OpenProcess(PQLI | PSI, False, pid)

    def _apply(self, name, pid, prio, bg):
        h = self._open(pid)
        if not h:
            return False
        try:
            if pid not in self.saved:
                cur_aff, sys_aff = ctypes.c_size_t(), ctypes.c_size_t()
                k32.GetProcessAffinityMask(h, ctypes.byref(cur_aff), ctypes.byref(sys_aff))
                self.saved[pid] = (name, k32.GetPriorityClass(h), cur_aff.value)
            ok = bool(k32.SetPriorityClass(h, prio))
            if bg:
                if self.ecores:
                    k32.SetProcessAffinityMask(h, ctypes.c_size_t(self.ecores))
                st = PROCESS_POWER_THROTTLING_STATE(1, 0x1, 0x1)  # EcoQoS on
                k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))
            return ok
        finally:
            k32.CloseHandle(h)

    def _restore(self):
        for pid, (name, prio, aff) in list(self.saved.items()):
            h = self._open(pid)
            if h:
                try:
                    k32.SetPriorityClass(h, prio or NORMAL)
                    if aff:
                        k32.SetProcessAffinityMask(h, ctypes.c_size_t(aff))
                    st = PROCESS_POWER_THROTTLING_STATE(1, 0x1, 0x0)  # EcoQoS off
                    k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st))
                finally:
                    k32.CloseHandle(h)
        self.saved.clear()

    def run(self):
        while True:
            try:
                procs = list(list_processes())
                game_up = any(n in GAME for n, _ in procs)
                if game_up:
                    boosted = bg = 0
                    for name, pid in procs:
                        if name in GAME or name in STREAM:
                            boosted += self._apply(name, pid, ABOVE_NORMAL, False)
                        elif name in BACKGROUND:
                            bg += self._apply(name, pid, BELOW_NORMAL, True)
                    if not self.active:
                        print(time.strftime("%Y-%m-%d %H:%M:%S"), f"game boost ON: {boosted} boosted, {bg} background -> E-cores", flush=True)
                    self.active = True
                    self.status.update(active=True, boosted=boosted, background=bg)
                elif self.active:
                    self._restore()
                    self.active = False
                    self.status.update(active=False, boosted=0, background=0)
                    print(time.strftime("%Y-%m-%d %H:%M:%S"), "game boost OFF: restored", flush=True)
            except Exception as e:
                self.status["error"] = str(e)
            time.sleep(10)
