#!/usr/bin/env python3
"""Verify GL.iNet write paths safely + capture VPN-client config params.

What it does (all reversible / non-destructive):
  1. Reads vpn-client.get_status (now that a WireGuard client is configured) and
     re-probes wg-client/ovpn-client status.
  2. Probes get_config_list with several parameter shapes to learn the params
     (tunnel_id / group_id) needed for VPN start/stop — READ ONLY.
  3. LED WRITE TEST: reads led_enable, flips it via led.set_config, confirms the
     change, then RESTORES the original value. Proves the write path works.

It does NOT start/stop the VPN (that would reroute live traffic). Writes a
redacted glinet_write_verify.json.

Usage (password prompted, never echoed):
    python3 tools/verify_writes.py [host]      # default host 10.200.200.1
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
_spec = importlib.util.spec_from_file_location(
    "glinet_crypt", os.path.join(HERE, "..", "custom_components", "glinet", "crypt_util.py")
)
crypt_util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crypt_util)

SENSITIVE_KEY = re.compile(
    r"(mac|ip|ssid|key|psk|pass|secret|token|sid|nonce|salt|pubkey|private|"
    r"endpoint|name|host|address|public|email|serial|imei|iccid|phone|peer|dns)",
    re.IGNORECASE,
)
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def redact(obj, key_hint=None):
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str) and (
        MAC_RE.match(obj) or IPV4_RE.match(obj)
        or (key_hint and SENSITIVE_KEY.search(str(key_hint)))
    ):
        return f"<str:{len(obj)}>"
    return obj


def rpc(url, method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RuntimeError(json.dumps(data["error"]))
    return data.get("result")


def main():
    host = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GLINET_HOST", "10.200.200.1")).strip()
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    url = f"{host}/rpc"
    user = os.environ.get("GLINET_USER", "root")
    pw = os.environ.get("GLINET_PASSWORD") or getpass.getpass("Router admin password: ")

    ch = rpc(url, "challenge", {"username": user})
    cipher = crypt_util.crypt_password(pw, int(ch["alg"]), ch["salt"])
    hm = (ch.get("hash-method") or "md5").lower()
    h = getattr(hashlib, hm)(f"{user}:{cipher}:{ch['nonce']}".encode()).hexdigest()
    sid = rpc(url, "login", {"username": user, "hash": h})["sid"]
    print("login: OK\n")

    def call(service, method, params=None):
        return rpc(url, "call", [sid, service, method, params or {}])

    out = {}

    # 1) VPN client status (WG now configured) + re-probe per-protocol status.
    print("=== VPN client status (WG configured) ===")
    for label, svc, meth in [
        ("vpn-client.get_status", "vpn-client", "get_status"),
        ("wg-client.get_status", "wg-client", "get_status"),
        ("ovpn-client.get_status", "ovpn-client", "get_status"),
    ]:
        try:
            res = call(svc, meth)
            out[label] = redact(res)
            print(f"  {label}: {json.dumps(redact(res))[:200]}")
        except Exception as err:  # noqa: BLE001
            out[label] = {"_error": str(err)}
            print(f"  {label}: {err}")

    # 2) Discover get_config_list params (READ ONLY).
    print("\n=== get_config_list param discovery (read only) ===")
    param_shapes = [
        {}, {"type": "wireguard"}, {"type": "wg"}, {"type": "ovpn"},
        {"protocol": "wireguard"}, {"mode": 3}, {"mode": 1}, {"group_id": 0},
    ]
    for svc in ("vpn-client", "wg-client", "ovpn-client"):
        for params in param_shapes:
            try:
                res = call(svc, "get_config_list", params)
            except Exception as err:  # noqa: BLE001
                if "32601" not in str(err):  # skip "method not found" noise
                    out[f"{svc}.get_config_list {json.dumps(params)}"] = {"_error": str(err)}
                continue
            key = f"{svc}.get_config_list {json.dumps(params)}"
            out[key] = redact(res)
            print(f"  OK {key} -> {json.dumps(redact(res))[:200]}")

    # 3) LED write test (reversible).
    print("\n=== LED write test (auto-revert) ===")
    led_result = {}
    try:
        orig = call("led", "get_config").get("led_enable")
        led_result["original"] = orig
        call("led", "set_config", {"led_enable": (not orig)})
        after = call("led", "get_config").get("led_enable")
        led_result["after_flip"] = after
        call("led", "set_config", {"led_enable": orig})  # restore
        restored = call("led", "get_config").get("led_enable")
        led_result["restored"] = restored
        led_result["write_works"] = (after == (not orig)) and (restored == orig)
        print(f"  original={orig} after_flip={after} restored={restored} "
              f"=> write_works={led_result['write_works']}")
    except Exception as err:  # noqa: BLE001
        led_result["_error"] = str(err)
        print(f"  LED test error: {err}")
    out["_led_write_test"] = led_result

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:  # noqa: BLE001
        pass

    dest = os.path.join(os.getcwd(), "glinet_write_verify.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"\nWrote redacted results to: {dest}")


if __name__ == "__main__":
    main()
