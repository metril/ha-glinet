#!/usr/bin/env python3
"""Dump GL.iNet RPC payloads (redacted) to help map field paths.

Standalone: stdlib only, no Home Assistant, no aiohttp. Loads the verified
crypt implementation from the integration directly.

Usage (password is prompted, never echoed or stored):

    python3 tools/dump_rpc.py [host]
    # host defaults to 10.200.200.1; or set GLINET_HOST
    # password from prompt, or GLINET_PASSWORD env var

Writes redacted JSON to glinet_rpc_dump.json in the current directory.
Sensitive values (MACs, IPs, SSIDs, keys, names, tokens) are masked; keys,
numbers and booleans are preserved so field paths can be verified.
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


def _load_crypt():
    spec = importlib.util.spec_from_file_location("glinet_crypt", CRYPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


crypt_util = _load_crypt()

# Reads to dump: (label, service, method).
READS = [
    ("system.get_info", "system", "get_info"),
    ("system.get_status", "system", "get_status"),
    ("clients.get_list", "clients", "get_list"),
    ("wifi.get_status", "wifi", "get_status"),
    ("wifi.get_config", "wifi", "get_config"),
    ("led.get_config", "led", "get_config"),
    ("wg-client.get_status", "wg-client", "get_status"),
    ("ovpn-client.get_status", "ovpn-client", "get_status"),
    ("wg-server.get_status", "wg-server", "get_status"),
    ("ovpn-server.get_status", "ovpn-server", "get_status"),
    ("tailscale.get_status", "tailscale", "get_status"),
    ("cable.get_status", "cable", "get_status"),
    ("ddns.get_status", "ddns", "get_status"),
    ("modem.get_status", "modem", "get_status"),
]

# Keys whose string values should be masked entirely.
SENSITIVE_KEY = re.compile(
    r"(mac|ip|ssid|key|psk|pass|secret|token|sid|nonce|salt|pubkey|private|"
    r"endpoint|name|host|address|public|email|serial|imei|iccid|phone)",
    re.IGNORECASE,
)
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _mask(value):
    if isinstance(value, str):
        return f"<str:{len(value)}>"
    return value


def redact(obj, key_hint=None):
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        if MAC_RE.match(obj) or IPV4_RE.match(obj):
            return _mask(obj)
        if key_hint and SENSITIVE_KEY.search(str(key_hint)):
            return _mask(obj)
    return obj


class RpcError(Exception):
    pass


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
        raise RpcError(json.dumps(data["error"]))
    return data.get("result")


def main():
    host = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GLINET_HOST", "10.200.200.1")).strip()
    if not host.startswith(("http://", "https://")):
        host = f"http://{host}"
    url = f"{host}/rpc"
    password = os.environ.get("GLINET_PASSWORD") or getpass.getpass("Router admin password: ")
    username = os.environ.get("GLINET_USER", "root")

    challenge = rpc(url, "challenge", {"username": username})
    alg = int(challenge["alg"])
    salt = challenge["salt"]
    nonce = challenge["nonce"]
    hash_method = (challenge.get("hash-method") or "md5").lower()
    print(f"challenge: alg={alg} hash-method={hash_method}")

    cipher = crypt_util.crypt_password(password, alg, salt)
    login_hash = getattr(hashlib, hash_method)(
        f"{username}:{cipher}:{nonce}".encode()
    ).hexdigest()
    login = rpc(url, "login", {"username": username, "hash": login_hash})
    sid = login["sid"]
    print("login: OK")

    dump = {"_challenge": {"alg": alg, "hash_method": hash_method}}
    for label, service, method in READS:
        try:
            result = rpc(url, "call", [sid, service, method, {}])
            dump[label] = redact(result)
            print(f"  {label}: OK")
        except Exception as err:  # noqa: BLE001 - record and continue
            dump[label] = {"_error": str(err)}
            print(f"  {label}: {err}")

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:  # noqa: BLE001
        pass

    out = os.path.join(os.getcwd(), "glinet_rpc_dump.json")
    with open(out, "w") as fh:
        json.dump(dump, fh, indent=2, sort_keys=True)
    print(f"\nWrote redacted dump to: {out}")
    print("Review it, then share it (paste or point me at the file).")


if __name__ == "__main__":
    main()
