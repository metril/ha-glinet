#!/usr/bin/env python3
"""Discover GL.iNet VPN-client start/stop methods + the active status shape.

Default run is SAFE (read-only + a stop no-op while nothing is running):
  1. vpn-client.get_status (current).
  2. Scan wg-client.get_config_list {group_id: 0..15} to locate the configured
     peer (and its group_id / peer ids) — read only.
  3. Probe get_group_list and a stop no-op (harmless while mode==0).

With --start (OPT-IN, reversible) it additionally runs a controlled
start -> capture-active-status -> stop cycle, trying candidate start calls until
vpn-client.get_status reports active, then ALWAYS stops to restore the original
state and verifies mode returned to its starting value.

WARNING for --start: this briefly activates your WireGuard client, routing WAN
traffic through the tunnel for a few seconds. LAN management (this script's
connection to the router) is not tunnelled, so access should persist. It reverts
automatically. Only pass --start if you're comfortable with that.

Usage:
    python3 tools/vpn_control.py [host]            # safe discovery
    python3 tools/vpn_control.py [host] --start    # also confirm start/stop
"""

from __future__ import annotations

import getpass
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_start = "--start" in sys.argv
    host = (args[0] if args else os.environ.get("GLINET_HOST", "10.200.200.1")).strip()
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

    def safe(service, method, params=None):
        try:
            return call(service, method, params)
        except Exception as err:  # noqa: BLE001
            return {"_error": str(err)}

    out = {}
    status0 = safe("vpn-client", "get_status")
    out["vpn-client.get_status@start"] = redact(status0)
    print("vpn-client.get_status:", json.dumps(redact(status0))[:200])

    # group enumeration helpers
    for svc in ("wg-client", "vpn-client"):
        out[f"{svc}.get_group_list"] = redact(safe(svc, "get_group_list"))

    print("\n=== scanning wg-client.get_config_list group_id 0..15 ===")
    groups = {}
    for g in range(16):
        res = safe("wg-client", "get_config_list", {"group_id": g})
        if isinstance(res, dict) and not res.get("_error") and res.get("peers"):
            groups[g] = redact(res)
            print(f"  group_id={g}: peers={len(res['peers'])} keys={list(res.keys())}")
    out["wg_groups_with_peers"] = groups

    print("\n=== stop no-op probe (safe while inactive) ===")
    for svc in ("vpn-client", "wg-client"):
        out[f"{svc}.stop@noop"] = redact(safe(svc, "stop"))
        print(f"  {svc}.stop {{}} -> {json.dumps(redact(out[f'{svc}.stop@noop']))[:120]}")

    if do_start:
        print("\n=== --start: controlled start -> capture -> stop (auto-revert) ===")
        tunnel_id = None
        if isinstance(status0, dict):
            for e in status0.get("status_list", []):
                tunnel_id = e.get("tunnel_id", tunnel_id)
        # derive a group_id/peer_id if we found one
        gid = next(iter(groups), None)
        candidates = []
        if tunnel_id is not None:
            candidates += [
                ("vpn-client", "start", {"tunnel_id": tunnel_id}),
                ("vpn-client", "start", {"id": tunnel_id}),
            ]
        if gid is not None:
            candidates += [
                ("wg-client", "start", {"group_id": gid}),
                ("wg-client", "start", {"group_id": gid, "peer_id": 0}),
            ]
        started = None
        for svc, meth, params in candidates:
            res = safe(svc, meth, params)
            time.sleep(2)
            st = safe("vpn-client", "get_status")
            active = isinstance(st, dict) and st.get("mode") not in (0, None)
            out[f"START {svc}.{meth} {json.dumps(params)}"] = {
                "call_result": redact(res),
                "status_after": redact(st),
                "active": active,
            }
            print(f"  {svc}.{meth} {params} -> active={active}")
            if active:
                started = (svc, meth, params, st)
                break
        # ALWAYS attempt to stop/revert
        for svc in ("vpn-client", "wg-client"):
            safe(svc, "stop")
        time.sleep(2)
        final = safe("vpn-client", "get_status")
        out["vpn-client.get_status@final"] = redact(final)
        reverted = isinstance(final, dict) and final.get("mode") in (0, None)
        out["_start_test"] = {
            "worked": bool(started),
            "winning_call": started[:3] if started else None,
            "reverted": reverted,
        }
        print(f"\n  start worked: {bool(started)}; reverted to inactive: {reverted}")

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:  # noqa: BLE001
        pass

    dest = os.path.join(os.getcwd(), "glinet_vpn_control.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"\nWrote redacted results to: {dest}")


if __name__ == "__main__":
    main()
