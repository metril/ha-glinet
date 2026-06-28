#!/usr/bin/env python3
"""Verify wifi/tor state is intact, hunt the operating-mode setter, and confirm
the ``wifi.set_config`` param shape reversibly.

Safe by construction: the router's radios are currently OFF and no client uses
WiFi (cable only), and we reach the router over the upstream/cable link — so
briefly enabling a radio then restoring it cannot drop our connection. Every
write is immediately reverted to the captured original.

Usage: python3 tools/verify_wifi_mode.py [host]
"""

from __future__ import annotations

import copy
import getpass
import hashlib
import importlib.util
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CRYPT_PATH = os.path.join(HERE, "..", "custom_components", "glinet", "crypt_util.py")
PASS_FILE = os.path.join(HERE, "..", ".glinet_pass")

spec = importlib.util.spec_from_file_location("glinet_crypt", CRYPT_PATH)
crypt_util = importlib.util.module_from_spec(spec)
spec.loader.exec_module(crypt_util)


class RpcError(Exception):
    def __init__(self, payload):
        super().__init__(json.dumps(payload))
        self.payload = payload


def rpc(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RpcError(data["error"])
    return data.get("result")


def login(url, user, pw):
    ch = rpc(url, "challenge", {"username": user})
    cipher = crypt_util.crypt_password(pw, int(ch["alg"]), ch["salt"])
    method = (ch.get("hash-method") or "md5").lower()
    h = getattr(hashlib, method)(f"{user}:{cipher}:{ch['nonce']}".encode()).hexdigest()
    return rpc(url, "login", {"username": user, "hash": h})["sid"]


def call(url, sid, service, method, params=None):
    return rpc(url, "call", [sid, service, method, params or {}])


def main():
    host = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GLINET_HOST", "10.200.200.1")).strip()
    base = host if host.startswith(("http://", "https://")) else f"http://{host}"
    url = f"{base}/rpc"
    user = os.environ.get("GLINET_USER", "root")
    pw = os.environ.get("GLINET_PASSWORD") or (open(PASS_FILE).read().strip() if os.path.exists(PASS_FILE) else getpass.getpass("pw: "))
    sid = login(url, user, pw)
    print("login OK\n")

    # --- 1. Confirm tor + wifi unchanged by the earlier junk-param probe -----
    tor = call(url, sid, "tor", "get_config")
    print(f"tor.get_config -> {tor}  (expect enable:false)")
    wifi = call(url, sid, "wifi", "get_config")
    summary = [
        {"band": b["band"], "device": b["device"],
         "ifaces": [{"guest": i["guest"], "enabled": i["enabled"], "ssid_len": len(i["ssid"])} for i in b["ifaces"]]}
        for b in wifi["res"]
    ]
    print("wifi.get_config summary:")
    print(json.dumps(summary, indent=2))

    # --- 2. Hunt the operating-mode setter on more services (READ-ONLY) ------
    print("\n== mode-service hunt (read-only get_* probes) ==")
    for svc in ["init", "mode", "ap", "bridge", "relay", "mesh", "network_mode",
                "cloud", "internet", "wan", "lan", "switch", "openvpn", "gl_mode"]:
        for m in ["get_mode", "get_config", "get_status"]:
            try:
                res = call(url, sid, svc, m)
                print(f"  {svc}.{m}: OK -> {json.dumps(res)[:120]}")
            except RpcError as err:
                code = err.payload.get("code")
                if code != -32601:  # report anything that's not "method not found"
                    print(f"  {svc}.{m}: EXISTS err={err.payload}")

    # --- 3. Reversible wifi.set_config shape probe (5G main iface) -----------
    print("\n== wifi.set_config shape probe (reversible; 5G main radio) ==")
    band5 = next(b for b in wifi["res"] if b["band"] == "5G")
    orig_enabled = band5["ifaces"][0]["enabled"]
    print(f"5G main iface enabled (original) = {orig_enabled}")

    def read_5g_enabled():
        cfg = call(url, sid, "wifi", "get_config")
        b = next(x for x in cfg["res"] if x["band"] == "5G")
        return b["ifaces"][0]["enabled"]

    target = not orig_enabled
    shapes = {
        "band_entry": lambda b: b,
        "device_ifaces": lambda b: {"device": b["device"], "ifaces": b["ifaces"]},
        "res_wrapped": lambda b: {"res": [b]},
        "band_ifaces": lambda b: {"band": b["band"], "ifaces": b["ifaces"]},
    }
    confirmed_shape = None
    for name, build in shapes.items():
        mutated = copy.deepcopy(band5)
        mutated["ifaces"][0]["enabled"] = target
        payload = build(mutated)
        try:
            call(url, sid, "wifi", "set_config", payload)
        except RpcError as err:
            print(f"  shape '{name}': REJECTED {err.payload}")
            continue
        now = read_5g_enabled()
        took = (now == target)
        print(f"  shape '{name}': accepted; enabled now={now} -> {'TOOK' if took else 'no-op'}")
        # restore immediately
        restore = copy.deepcopy(band5)
        restore["ifaces"][0]["enabled"] = orig_enabled
        try:
            call(url, sid, "wifi", "set_config", build(restore))
        except RpcError:
            pass
        if read_5g_enabled() != orig_enabled:
            print(f"  !! restore via '{name}' failed — retrying with band_entry")
            call(url, sid, "wifi", "set_config", band5)
        if took:
            confirmed_shape = name
            break
    print(f"\nCONFIRMED wifi.set_config shape: {confirmed_shape}")
    print(f"final 5G enabled = {read_5g_enabled()} (expect {orig_enabled})")

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:
        pass


if __name__ == "__main__":
    main()
