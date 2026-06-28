#!/usr/bin/env python3
"""Enumerate GL.iNet RPC method names per service WITHOUT executing them.

Calling ``service.method {}`` returns:
  -32601 "Method not found"   -> method does NOT exist
  20001011 "parameter missing" -> method EXISTS but needs args (safe; nothing ran)
  anything else                -> method exists and ran with no args

So an empty-param sweep maps which methods are real. Then for the known-but-
param-hungry ``vpn-client.stop``, sweep candidate parameter KEYS (values use the
discovered ids) to find the one that's accepted (a no-op while nothing is active).

SAFE: only empty params and stop no-ops while the VPN is inactive. Nothing starts.

Usage:
    python3 tools/enum_methods.py [host]
"""

from __future__ import annotations

import getpass
import hashlib
import importlib.util
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "glinet_crypt", os.path.join(HERE, "..", "custom_components", "glinet", "crypt_util.py")
)
crypt_util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crypt_util)

SERVICES = ["vpn-client", "wg-client", "ovpn-client", "vpn"]

METHODS = [
    "get_status", "get_config", "get_config_list", "get_group_list", "get_list",
    "start", "stop", "connect", "disconnect", "enable", "disable",
    "set_status", "set_enable", "set_config", "set_state", "set_group",
    "set_default", "set_switch", "toggle", "switch", "up", "down",
    "restart", "reload", "open", "close", "kill", "clear", "apply",
]

# Candidate parameter keys for vpn-client.stop / start. Values use known ids.
KNOWN = {"tunnel_id": 10, "group_id": 6494, "peer_id": 2001, "type": "wireguard"}
STOP_KEYS = [
    "tunnel_id", "group_id", "peer_id", "id", "vpn_id", "client_id", "name",
    "type", "vpn_type", "proto", "protocol", "mode", "instance", "index", "idx",
]


def rpc(url, method, params):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    return data  # return full envelope so we can classify error codes


def classify(env):
    if env.get("error"):
        code = env["error"].get("code")
        return ("NOT_FOUND" if code == -32601 else f"ERR{code}")
    res = env.get("result")
    if isinstance(res, dict) and res.get("err_code") == 20001011:
        return "EXISTS(needs params)"
    return "EXISTS(ran ok)"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    host = (args[0] if args else os.environ.get("GLINET_HOST", "10.200.200.1")).strip()
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    url = f"{host}/rpc"
    user = os.environ.get("GLINET_USER", "root")
    pw = os.environ.get("GLINET_PASSWORD") or getpass.getpass("Router admin password: ")

    ch = rpc(url, "challenge", {"username": user})["result"]
    cipher = crypt_util.crypt_password(pw, int(ch["alg"]), ch["salt"])
    hm = (ch.get("hash-method") or "md5").lower()
    h = getattr(hashlib, hm)(f"{user}:{cipher}:{ch['nonce']}".encode()).hexdigest()
    sid = rpc(url, "login", {"username": user, "hash": h})["result"]["sid"]
    print("login: OK\n")

    out = {}

    print("=== method existence sweep (empty params) ===")
    for svc in SERVICES:
        existing = []
        for meth in METHODS:
            env = rpc(url, "call", [sid, svc, meth, {}])
            verdict = classify(env)
            out[f"{svc}.{meth}"] = verdict
            if verdict.startswith("EXISTS"):
                existing.append(f"{meth} [{verdict.split('(')[1][:-1]}]")
        print(f"  {svc}: {existing or '(none)'}")

    print("\n=== vpn-client.stop param-key sweep (no-op while inactive) ===")
    for key in STOP_KEYS:
        value = KNOWN.get(key, 10)
        env = rpc(url, "call", [sid, "vpn-client", "stop", {key: value}])
        verdict = classify(env)
        out[f"vpn-client.stop {{{key}}}"] = verdict
        flag = "  <-- ACCEPTED" if verdict.startswith("EXISTS(ran") else ""
        print(f"  stop {{{key}: {value}}} -> {verdict}{flag}")

    try:
        rpc(url, "call", [sid, "", "", {}])  # ignore
    except Exception:  # noqa: BLE001
        pass
    rpc(url, "logout", {"sid": sid})

    dest = os.path.join(os.getcwd(), "glinet_methods.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"\nWrote results to: {dest}")


if __name__ == "__main__":
    main()
