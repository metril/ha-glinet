#!/usr/bin/env python3
"""Confirm GL.iNet WireGuard-client start/stop and capture the active status.

Enumeration (tools/enum_methods.py) showed the control methods live on wg-client:
  - START: wg-client.connect (also open)
  - STOP:  wg-client.close (also kill)
vpn-client.get_status already exposes group_id + peer_id + tunnel_id per profile.

Default run is SAFE: reads status and identifiers only.

With --start (OPT-IN, reversible): runs wg-client.connect {group_id, peer_id},
captures the ACTIVE vpn-client.get_status, then wg-client.close to revert, trying
fallbacks until vpn-client.get_status reports mode==0. Warns loudly if it can't.

Usage:
    python3 tools/vpn_control.py [host]            # safe: show identifiers
    python3 tools/vpn_control.py [host] --start    # confirm connect/close (reverts)
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
    r"(mac|ssid|key|psk|pass|secret|token|sid|nonce|salt|pubkey|private|"
    r"endpoint|address|email|serial|imei|iccid|phone|^dns$|name)", re.IGNORECASE)
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
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RuntimeError(json.dumps(data["error"]))
    return data.get("result")


def main():
    do_start = "--start" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
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

    def call(s, m, p=None):
        return rpc(url, "call", [sid, s, m, p or {}])

    def safe(s, m, p=None):
        try:
            return call(s, m, p)
        except Exception as err:  # noqa: BLE001
            return {"_error": str(err)}

    def status():
        return safe("vpn-client", "get_status")

    out = {}
    st0 = status()
    out["status@start"] = redact(st0)
    entry = (st0.get("status_list") or [{}])[0] if isinstance(st0, dict) else {}
    gid, pid, tid = entry.get("group_id"), entry.get("peer_id"), entry.get("tunnel_id")
    print(f"identifiers: group_id={gid} peer_id={pid} tunnel_id={tid} mode={st0.get('mode')}")

    if not do_start:
        print("\n(safe mode) re-run with --start to confirm connect/close.")
    else:
        print("\n=== --start: connect -> capture -> close (auto-revert) ===")
        start_candidates = [
            ("wg-client", "connect", {"group_id": gid, "peer_id": pid}),
            ("wg-client", "open", {"group_id": gid, "peer_id": pid}),
            ("wg-client", "connect", {"group_id": gid}),
        ]
        winner = None
        for s, m, p in start_candidates:
            res = safe(s, m, p)
            time.sleep(4)
            st = status()
            active = isinstance(st, dict) and st.get("mode") not in (0, None)
            out[f"START {s}.{m} {json.dumps(p)}"] = {"result": redact(res),
                                                     "status_after": redact(st), "active": active}
            print(f"  {s}.{m} {p} -> active={active}  result={json.dumps(redact(res))[:80]}")
            if active:
                winner = (s, m, p)
                out["ACTIVE_status"] = redact(st)
                break

        # revert: try close/kill variants until inactive
        stop_candidates = [
            ("wg-client", "close", {"group_id": gid, "peer_id": pid}),
            ("wg-client", "close", {}),
            ("wg-client", "kill", {"group_id": gid, "peer_id": pid}),
            ("wg-client", "kill", {}),
            ("vpn-client", "stop", {"group_id": gid, "peer_id": pid, "type": "wireguard"}),
        ]
        stop_winner = None
        for s, m, p in stop_candidates:
            safe(s, m, p)
            time.sleep(3)
            st = status()
            if isinstance(st, dict) and st.get("mode") in (0, None):
                stop_winner = (s, m, p)
                break
        final = status()
        reverted = isinstance(final, dict) and final.get("mode") in (0, None)
        out["status@final"] = redact(final)
        out["_result"] = {"start_call": winner, "stop_call": stop_winner, "reverted": reverted}
        print(f"\n  START worked via: {winner}")
        print(f"  STOP  worked via: {stop_winner}")
        print(f"  reverted to inactive: {reverted}")
        if not reverted:
            print("  !!! WARNING: could not revert — disable the VPN client in the GL.iNet UI !!!")

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
