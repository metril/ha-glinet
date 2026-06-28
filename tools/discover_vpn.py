#!/usr/bin/env python3
"""Discover GL.iNet VPN-client (and other) RPC methods on the live router.

On firmware 4.8.1, ``wg-client.get_status`` / ``ovpn-client.get_status`` return
-32601 (method not found). This script:

  1. logs in,
  2. calls the JSON-RPC ``list`` method (which enumerates every service+method),
  3. probes a broad set of candidate VPN service/method names as a fallback,

and writes a redacted ``glinet_vpn_discovery.json``. Method/service NAMES are not
sensitive and are printed; payload VALUES are masked.

Usage (password prompted, never echoed):

    python3 tools/discover_vpn.py [host]      # host defaults to 10.200.200.1
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
_spec = importlib.util.spec_from_file_location("glinet_crypt", CRYPT_PATH)
crypt_util = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(crypt_util)

SENSITIVE_KEY = re.compile(
    r"(mac|ip|ssid|key|psk|pass|secret|token|sid|nonce|salt|pubkey|private|"
    r"endpoint|name|host|address|public|email|serial|imei|iccid|phone|peer)",
    re.IGNORECASE,
)
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def redact(obj, key_hint=None):
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        if MAC_RE.match(obj) or IPV4_RE.match(obj) or (
            key_hint and SENSITIVE_KEY.search(str(key_hint))
        ):
            return f"<str:{len(obj)}>"
    return obj


def rpc(url, method, params):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RuntimeError(json.dumps(data["error"]))
    return data.get("result")


# Candidate services and methods to probe if `list` is unavailable.
CANDIDATE_SERVICES = [
    "wg-client", "wgclient", "wg_client", "wireguard", "wireguard_client",
    "ovpn-client", "ovpnclient", "ovpn_client", "openvpn", "openvpn_client",
    "vpn", "vpnc", "vpn_client", "vpn-client", "tor", "shadowsocks", "ss-client",
]
CANDIDATE_METHODS = [
    "get_status", "get_config", "get_config_list", "get_state", "status",
    "get_client_list", "get_list", "get_info", "list",
]


def main():
    host = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GLINET_HOST", "10.200.200.1")).strip()
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    url = f"{host}/rpc"
    username = os.environ.get("GLINET_USER", "root")
    password = os.environ.get("GLINET_PASSWORD") or getpass.getpass("Router admin password: ")

    ch = rpc(url, "challenge", {"username": username})
    cipher = crypt_util.crypt_password(password, int(ch["alg"]), ch["salt"])
    hm = (ch.get("hash-method") or "md5").lower()
    login_hash = getattr(hashlib, hm)(f"{username}:{cipher}:{ch['nonce']}".encode()).hexdigest()
    sid = rpc(url, "login", {"username": username, "hash": login_hash})["sid"]
    print("login: OK\n")

    out = {}

    # 1) Try the global `list` enumeration in several param forms.
    print("=== trying `list` enumeration ===")
    list_result = None
    for label, params in [
        ("list[sid]", [sid]),
        ("list[sid,'']", [sid, ""]),
        ("list[sid,'*']", [sid, "*"]),
        ("list{}", {}),
        ("list[]", []),
    ]:
        try:
            res = rpc(url, "list", params)
            print(f"  {label}: OK")
            list_result = res
            out[f"list::{label}"] = redact(res)
            break
        except Exception as err:  # noqa: BLE001
            print(f"  {label}: {err}")

    # If list returned service names, surface anything VPN-ish.
    if isinstance(list_result, (dict, list)):
        text = json.dumps(list_result).lower()
        hits = sorted({w for w in re.findall(r"[a-z0-9_\-]+", text)
                       if any(k in w for k in ("wg", "vpn", "ovpn", "wireguard", "openvpn", "tor"))})
        print(f"\n  VPN-ish tokens in list output: {hits}\n")
        out["_vpn_tokens"] = hits

    # 2) Probe candidate service/method combinations.
    print("=== probing candidate VPN service/method combos ===")
    found = []
    for service in CANDIDATE_SERVICES:
        for method in CANDIDATE_METHODS:
            try:
                res = rpc(url, "call", [sid, service, method, {}])
            except Exception as err:  # noqa: BLE001
                msg = str(err)
                # -32601 = method/service not found (expected for most); skip quietly.
                if "32601" not in msg:
                    out[f"{service}.{method}"] = {"_error": msg}
                    print(f"  {service}.{method}: ERR {msg}")
                continue
            found.append(f"{service}.{method}")
            out[f"{service}.{method}"] = redact(res)
            print(f"  {service}.{method}: OK  ->  {json.dumps(redact(res))[:160]}")

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:  # noqa: BLE001
        pass

    out["_found"] = found
    dest = os.path.join(os.getcwd(), "glinet_vpn_discovery.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"\nFound working combos: {found or '(none beyond list)'}")
    print(f"Wrote redacted results to: {dest}")


if __name__ == "__main__":
    main()
