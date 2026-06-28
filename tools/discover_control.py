#!/usr/bin/env python3
"""Discover GL.iNet control/read methods for mode, wifi, repeater, firmware, tor.

Authenticated RPC discovery against the live router. Two probe classes:

* **READ probes** — call ``get_*``/``status``/``scan`` style methods with ``{}``.
  Fully safe; records the (redacted) result so we learn both that the method
  exists and its response shape.
* **EXISTENCE probes** — for setters we must not actually trigger, we call with an
  intentionally invalid param (e.g. ``{"mode": 999}`` / a bogus iface) so the
  router validates and rejects *without applying a real change*. The error code
  distinguishes "method not found" (``-32601``) from "exists but bad params".

Deliberately NEVER calls: ``repeater.disconnect``, ``*.reboot``, ``*.set_mode`` with
a *valid* value, or anything that could drop the live link.

Usage:
    python3 tools/discover_control.py [host]
    # password from .glinet_pass, $GLINET_PASSWORD, or prompt

Writes redacted findings to glinet_control_discovery.json (gitignored).
"""

from __future__ import annotations

import getpass
import hashlib
import importlib.util
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CRYPT_PATH = os.path.join(HERE, "..", "custom_components", "glinet", "crypt_util.py")
PASS_FILE = os.path.join(HERE, "..", ".glinet_pass")


def _load_crypt():
    spec = importlib.util.spec_from_file_location("glinet_crypt", CRYPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crypt_util = _load_crypt()


class RpcError(Exception):
    def __init__(self, payload):
        super().__init__(json.dumps(payload))
        self.payload = payload


def rpc(url, method, params):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RpcError(data["error"])
    return data.get("result")


def login(url, username, password):
    ch = rpc(url, "challenge", {"username": username})
    cipher = crypt_util.crypt_password(password, int(ch["alg"]), ch["salt"])
    method = (ch.get("hash-method") or "md5").lower()
    h = getattr(hashlib, method)(f"{username}:{cipher}:{ch['nonce']}".encode()).hexdigest()
    return rpc(url, "login", {"username": username, "hash": h})["sid"]


SENSITIVE = re.compile(
    r"(mac|ip|ssid|key|psk|pass|secret|token|sid|nonce|salt|pubkey|private|"
    r"endpoint|name|host|address|public|email|serial|imei|iccid|phone|bssid)",
    re.IGNORECASE,
)
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def redact(obj, hint=None):
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj[:8]]  # cap long lists
    if isinstance(obj, str):
        if MAC_RE.match(obj) or IPV4_RE.match(obj) or (hint and SENSITIVE.search(str(hint))):
            return f"<str:{len(obj)}>"
    return obj


# Safe READ probes: (service, method, params).
READS = [
    ("ui", "get_menu_list", {}),
    ("ui", "get_init_info", {}),
    ("ui", "check_initialized", {}),
    ("system", "get_status", {}),
    ("network", "get_status", {}),
    ("network", "get_config", {}),
    ("network", "get_mode", {}),
    ("network", "get_interfaces", {}),
    ("mwan", "get_status", {}),
    ("mwan3", "get_status", {}),
    ("multiwan", "get_status", {}),
    ("repeater", "get_config", {}),
    ("repeater", "get_status", {}),
    ("repeater", "get_config_list", {}),
    ("repeater", "get_saved_config", {}),
    ("repeater", "get_sta_config", {}),
    ("repeater", "get_scan_result", {}),
    ("repeater", "scan", {}),
    ("repeater", "get_channel_prompt", {}),
    ("wifi", "get_config", {}),
    ("wifi", "get_status", {}),
    ("wifi", "get_channels", {}),
    ("firmware", "get_info", {}),
    ("firmware", "check", {}),
    ("firmware", "online_check", {}),
    ("firmware", "check_online", {}),
    ("upgrade", "get_info", {}),
    ("upgrade", "check_online", {}),
    ("upgrade", "get_firmware_info", {}),
    ("system", "check_firmware", {}),
    ("system", "get_firmware_info", {}),
    ("system", "online_check", {}),
    ("system", "get_mode", {}),
    ("tor", "get_config", {}),
    ("tor", "get_status", {}),
    ("cable", "get_status", {}),
    ("cable", "get_config", {}),
    ("tethering", "get_status", {}),
    ("ddns", "get_config", {}),
]

# EXISTENCE probes for setters: invalid params so a real change can't apply.
# (service, method, deliberately-invalid params)
EXISTENCE = [
    ("system", "set_mode", {"mode": 999}),
    ("network", "set_mode", {"mode": 999}),
    ("system", "set_network_mode", {"mode": 999}),
    ("network", "set_network_mode", {"mode": 999}),
    ("mwan", "set_mode", {"mode": 999}),
    ("system", "switch_mode", {"mode": 999}),
    ("network", "set_config", {"__probe__": 1}),
    ("repeater", "set_config", {"__probe__": 1}),
    ("repeater", "connect", {"ssid": "__glinet_discovery_probe__nonexistent__"}),
    ("wifi", "set_config", {"iface": "__glinet_discovery_probe__"}),
    ("wifi", "apply", {"__probe__": 1}),
    ("tor", "set_config", {"__probe__": 1}),
    ("firmware", "upgrade", {"__probe__": 1, "dry_run": True}),
]


def classify(err_payload):
    code = err_payload.get("code")
    msg = str(err_payload.get("message", ""))
    if code == -32601 or "not found" in msg.lower():
        return "NOT_FOUND"
    return f"EXISTS(err code={code} msg={msg[:60]})"


def main():
    host = (
        sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GLINET_HOST", "10.200.200.1")
    ).strip()
    base = host if host.startswith(("http://", "https://")) else f"http://{host}"
    url = f"{base}/rpc"
    username = os.environ.get("GLINET_USER", "root")
    password = os.environ.get("GLINET_PASSWORD")
    if not password and os.path.exists(PASS_FILE):
        password = open(PASS_FILE).read().strip()
    if not password:
        password = getpass.getpass("Router admin password: ")

    sid = login(url, username, password)
    print(f"login OK\n")

    findings = {"reads": {}, "existence": {}}

    print("== READ probes ==")
    for service, method, params in READS:
        label = f"{service}.{method}"
        try:
            res = rpc(url, "call", [sid, service, method, params])
            findings["reads"][label] = {"ok": True, "result": redact(res)}
            print(f"  {label}: OK")
        except RpcError as err:
            findings["reads"][label] = {"ok": False, "verdict": classify(err.payload)}
            print(f"  {label}: {classify(err.payload)}")
        except Exception as err:  # noqa: BLE001
            findings["reads"][label] = {"ok": False, "error": str(err)}
            print(f"  {label}: ERR {err}")

    print("\n== EXISTENCE probes (invalid params; no real change) ==")
    for service, method, params in EXISTENCE:
        label = f"{service}.{method}"
        try:
            res = rpc(url, "call", [sid, service, method, params])
            # Unexpectedly accepted — record (redacted) but flag.
            findings["existence"][label] = {
                "verdict": "ACCEPTED(!) — review", "result": redact(res)
            }
            print(f"  {label}: ACCEPTED(!) {redact(res)}")
        except RpcError as err:
            findings["existence"][label] = {"verdict": classify(err.payload)}
            print(f"  {label}: {classify(err.payload)}")
        except Exception as err:  # noqa: BLE001
            findings["existence"][label] = {"error": str(err)}
            print(f"  {label}: ERR {err}")

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:  # noqa: BLE001
        pass

    out = os.path.join(os.getcwd(), "glinet_control_discovery.json")
    with open(out, "w") as fh:
        json.dump(findings, fh, indent=2)
    print(f"\nWrote findings to: {out}")


if __name__ == "__main__":
    main()
