"""Add TACTICAL (combos + core stingers) and TACTICAL FX pages to Bitfocus Companion.

Uses Companion's own import API (tRPC over ws://<companion>/trpc). Existing pages are never
modified: the script refuses if the target pages exist and verifies every other page is
byte-identical afterwards. Buttons POST to the Tactical Deck Director (127.0.0.1:8781).

Requirements: Companion 3.x/4.x running, a "Generic HTTP" connection, and one existing page whose
button at row 0 / col 1 is an HTTP POST button (it is cloned as the button template; column 0 of
that page, e.g. page-up/down navigation, is copied onto the new pages).

    pip install requests websocket-client
    python add_tactical_pages.py [--companion http://127.0.0.1:8000] [--connection-label http]
                                 [--template-page 1] [--director http://127.0.0.1:8781]
"""
import argparse
import base64
import copy
import hashlib
import json
import uuid
from pathlib import Path

import requests
import websocket  # websocket-client

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--companion", default="http://127.0.0.1:8000")
ap.add_argument("--connection-label", default="http", help="label of the Generic HTTP connection in Companion")
ap.add_argument("--template-page", type=int, default=1)
ap.add_argument("--director", default="http://127.0.0.1:8781")
ARGS = ap.parse_args()
BASE = ARGS.companion.rstrip("/")
DIRECTOR = ARGS.director.rstrip("/")
TEMPLATE = ARGS.template_page


class Companion:
    """Tiny tRPC-over-websocket client for Companion's import mutations."""

    def __init__(self):
        self.ws = websocket.create_connection(BASE.replace("http", "ws", 1) + "/trpc", timeout=15)
        self.id = 0

    def call(self, path, data):
        self.id += 1
        self.ws.send(json.dumps({"id": self.id, "method": "mutation", "params": {"path": path, "input": data}}))
        while True:
            res = json.loads(self.ws.recv())
            if res.get("id") == self.id:
                if "error" in res:
                    raise RuntimeError(res["error"])
                return res["result"].get("data")

    def close(self):
        self.ws.close()

RED, BLUE, PURPLE, GREEN, GREY, GOLD, STEEL, AMBER, DARK = (0x8E2B26, 0x1F5FA8, 0x5E2C7A, 0x1E7A47,
                                                           0x4A5560, 0x8A6D12, 0x2B3F55, 0x8C5A12, 0x2A2F36)
PAGES = {
    "TACTICAL": [
        (0, 1, "AIRSTRIKE", "/combo/airstrike", RED), (0, 2, "COMMS\n(toggle)", "/combo/comms", BLUE),
        (0, 3, "CLUTCH\n(toggle)", "/combo/clutch", PURPLE), (0, 4, "MATCH\nSTART", "/combo/match_start", GREEN),
        (1, 1, "IMPACT", "/play/t_impact", STEEL), (1, 2, "HORN\nHIT", "/play/t_horn", STEEL),
        (1, 3, "SUB\nDROP", "/play/t_subdrop", STEEL), (1, 4, "REVERSE\nSWELL", "/play/t_swell", STEEL),
        (2, 1, "EXPLOSION", "/play/t_explosion", AMBER), (2, 2, "INCOMING", "/play/t_incoming", AMBER),
        (2, 3, "JET\nFLYBY", "/play/t_flyby", AMBER), (2, 4, "TARGET\nLOCK", "/play/t_lock", AMBER),
        (3, 1, "SQUAD\nWIPE", "/combo/squad_wipe", GREY), (3, 2, "VICTORY", "/combo/victory", GOLD),
        (3, 3, "STOP\nALL", "/stop", 0x743738),
    ],
    "TACTICAL FX": [
        (0, 1, "SONAR\nPING", "/play/t_sonar", BLUE), (0, 2, "COMMS\nOPEN", "/play/t_comms_open", BLUE),
        (0, 3, "COMMS\nOVER", "/play/t_comms_close", BLUE), (0, 4, "INTER-\nFERENCE", "/play/t_static", BLUE),
        (1, 1, "SLOW-MO", "/play/t_slowmo", PURPLE), (1, 2, "HEART-\nBEAT", "/play/t_heartbeat", PURPLE),
        (1, 3, "GLITCH\nHIT", "/play/t_glitch", PURPLE), (1, 4, "COUNT-\nDOWN", "/play/t_countdown", PURPLE),
        (2, 1, "OBJECTIVE\nSECURED", "/play/t_secured", GOLD), (2, 2, "OBJECTIVE\nLOST", "/play/t_lost", GREY),
        (2, 3, "LOCK &\nLOAD", "/play/t_lockload", GREEN), (2, 4, "REVEAL", "/play/t_reveal", GOLD),
        (3, 1, "KILL\nCONFIRM", "/play/t_confirm", DARK), (3, 2, "K.I.A.", "/play/t_kia", DARK),
        (3, 3, "STOP\nALL", "/stop", 0x743738),
    ],
}


def export_page(n):
    return requests.get(f"{BASE}/int/export/page/{n}?includeSecrets=false&format=json", timeout=10).json()


def main():
    full = requests.get(f"{BASE}/int/export/full?includeSecrets=false&format=json", timeout=10).json()
    existing = {k: v["name"] for k, v in full["pages"].items()}
    if any(name in PAGES for name in existing.values()):
        raise SystemExit(f"Tactical pages already exist: {existing}")
    before = {n: export_page(int(n))["page"] for n in existing}
    template = export_page(TEMPLATE)
    connection = next((k for k, v in full["instances"].items() if v["label"] == ARGS.connection_label), None)
    if connection is None:
        labels = [v["label"] for v in full["instances"].values()]
        raise SystemExit(f"No connection labelled {ARGS.connection_label!r}; have {labels}")
    sample = copy.deepcopy(template["page"]["controls"]["0"]["1"])  # a working HTTP POST button
    backup_mic = copy.deepcopy(template["page"]["controls"].get("3", {}).get("4"))
    next_page = max(int(k) for k in existing) + 1
    client = Companion()
    report = []
    try:
        for offset, (name, buttons) in enumerate(PAGES.items()):
            number = next_page + offset
            page = copy.deepcopy(template)
            page["oldPageNumber"] = number
            page["page"]["id"] = str(uuid.uuid4())
            page["page"]["name"] = name
            page["instances"] = {connection: full["instances"][connection]}
            controls = {r: {"0": copy.deepcopy(cols["0"])} for r, cols in template["page"]["controls"].items() if "0" in cols}
            for row, col, label, path, color in buttons:
                button = copy.deepcopy(sample)
                button["feedbacks"] = []
                for step in button["steps"].values():
                    for actions in step["action_sets"].values():
                        for action in actions:
                            action["id"] = str(uuid.uuid4())
                            action["connectionId"] = connection
                            action["options"]["url"]["value"] = DIRECTOR + path
                for layer in button["style"]["layers"]:
                    if layer["id"] == "text0":
                        layer["text"]["value"] = label
                    elif layer["id"] == "box0":
                        layer["color"]["value"] = color
                controls.setdefault(str(row), {})[str(col)] = button
                report.append({"page": number, "location": f"{number}/{row}/{col}", "label": label.replace("\n", " "), "url": DIRECTOR + path})
            if backup_mic:  # keep the template page's bottom-right safety button in the same spot
                controls.setdefault("3", {})["4"] = backup_mic
            page["page"]["controls"] = controls
            payload = json.dumps(page).encode()
            sid = client.call("importExport.prepareImport.start", {"name": f"{name}.companionconfig", "size": len(payload)})
            client.call("importExport.prepareImport.uploadChunk", {"sessionId": sid, "offset": 0, "data": base64.b64encode(payload).decode()})
            result = client.call("importExport.prepareImport.complete",
                                 {"sessionId": sid, "expectedChecksum": hashlib.sha1(payload).hexdigest(), "userData": None})
            if result[0]:
                raise RuntimeError(str(result[0]))
            client.call("importExport.importSinglePage", {"targetPage": -1, "sourcePage": number,
                                                          "connectionIdRemapping": {connection: connection}})
            (Path(__file__).parent / f"{name.replace(' ', '-')}.companionconfig").write_bytes(payload)
    finally:
        client.close()
    after = requests.get(f"{BASE}/int/export/full?includeSecrets=false&format=json", timeout=10).json()["pages"]
    for n, page in before.items():
        assert export_page(int(n))["page"] == page, f"page {n} changed!"
    print(json.dumps({"pages_now": {k: v["name"] for k, v in after.items()}, "unchanged": sorted(before),
                      "buttons": len(report)}))
    (Path(__file__).parent / "tactical-button-map.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
