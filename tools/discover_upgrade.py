#!/usr/bin/env python3
"""Discover the GL.iNet firmware online-upgrade RPC contract — READ-ONLY.

We need the method/params the router UI uses to actually *start* a firmware
upgrade. We never call any upgrade-execution method; instead:

1. Re-capture the live ``upgrade.check_firmware_online`` response (safe read) to
   see whether it carries a new-version string we can surface as ``latest_version``.
2. Fetch the System->Upgrade *view bundle* (the UI is server-driven; page logic +
   its ``/rpc`` calls live in ``/views/gl-sdk4-ui-<view>.common.js``, gated behind a
   ``Sec-Fetch-*`` header check) and grep it for ``upgrade.*`` method names and the
   param object keys (e.g. ``keep_setting``).
3. Probe the session ACL via ``list`` for any ``upgrade`` methods.

Standalone; stdlib only; reuses the integration's verified crypt. Writes a redacted
``glinet_upgrade_discovery.json`` (gitignored).

Usage:
    python3 tools/discover_upgrade.py [host]
    # password from .glinet_pass, $GLINET_PASSWORD, or prompt
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
    h = getattr(hashlib, method)(
        f"{username}:{cipher}:{ch['nonce']}".encode()
    ).hexdigest()
    return rpc(url, "login", {"username": username, "hash": h})["sid"]


SENSITIVE = re.compile(
    r"(mac|ip|ssid|key|psk|pass|secret|token|sid|nonce|salt|serial|imei|iccid|url)",
    re.IGNORECASE,
)
MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def redact(obj, hint=None):
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj[:8]]
    if isinstance(obj, str):
        if MAC_RE.match(obj) or IPV4_RE.match(obj) or (hint and SENSITIVE.search(str(hint))):
            return f"<str:{len(obj)}>"
    return obj


SEC_HEADERS = {
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "script",
    "User-Agent": "Mozilla/5.0",
    "Referer": None,  # filled per-host
}


def fetch(base, path, sid):
    """Fetch an asset read-only with browser-like Sec-Fetch headers + token."""
    headers = {
        "Cookie": f"Admin-Token={sid}",
        "Authorization": sid,
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "script",
        "User-Agent": "Mozilla/5.0",
        "Referer": f"{base}/",
    }
    for url in (f"{base}{path}", f"{base}{path}?Admin-Token={sid}"):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read()
            if len(body) > 200:
                return body.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            continue
    return ""


def snippets(js, token, radius=90, limit=8):
    out = []
    for m in re.finditer(re.escape(token), js):
        s = max(0, m.start() - radius)
        e = min(len(js), m.end() + radius)
        out.append(js[s:e].replace("\n", " "))
        if len(out) >= limit:
            break
    return out


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
    print(f"login OK (sid len={len(sid)})\n")

    findings = {}

    # 1. live check response (safe read)
    print("== upgrade.check_firmware_online ==")
    try:
        res = rpc(url, "call", [sid, "upgrade", "check_firmware_online", {}])
        findings["check_firmware_online"] = {"ok": True, "result": redact(res)}
        print("  keys:", list(res.keys()) if isinstance(res, dict) else type(res).__name__)
        print("  redacted:", json.dumps(redact(res)))
    except RpcError as err:
        findings["check_firmware_online"] = {"ok": False, "error": err.payload}
        print("  ERR", err.payload)

    # also try a couple more safe reads that the upgrade page may use
    for m in ("get_status", "get_clone_status", "get_config", "get_info"):
        try:
            res = rpc(url, "call", [sid, "upgrade", m, {}])
            findings[f"upgrade.{m}"] = {"ok": True, "result": redact(res)}
            print(f"  upgrade.{m}: OK ->", json.dumps(redact(res))[:160])
        except RpcError as err:
            code = err.payload.get("code")
            findings[f"upgrade.{m}"] = {"ok": False, "code": code}
            print(f"  upgrade.{m}: err code={code}")

    # 2. ACL inventory via list
    print("\n== list inventory (upgrade entries) ==")
    inv = None
    for params in ([sid], [sid, ""], [], [sid, "all"]):
        try:
            res = rpc(url, "list", params)
            if res:
                inv = res
                break
        except Exception:  # noqa: BLE001
            continue
    upgrade_methods = []
    if inv is not None:
        blob = json.dumps(inv)
        upgrade_methods = sorted(set(re.findall(r'"(upgrade[._][a-z_]+)"', blob)))
        # also nested {"upgrade": {"method": ...}}
        if isinstance(inv, dict) and "upgrade" in inv:
            findings["list_upgrade_node"] = inv["upgrade"]
            print("  list.upgrade node:", json.dumps(inv["upgrade"])[:300])
    findings["list_upgrade_methods"] = upgrade_methods
    print("  upgrade-ish names in list:", upgrade_methods or "(none / list unavailable)")

    # 3. view bundles — find /views/ refs in index, fetch + grep
    print("\n== view bundles ==")
    index = fetch(base, "/", sid)
    view_paths = sorted(set(re.findall(r"/views/[A-Za-z0-9._/-]+\.js", index)))
    # common explicit guesses for the upgrade/system view
    guesses = [
        "/views/gl-sdk4-ui-upgrade.common.js",
        "/views/gl-sdk4-ui-system.common.js",
        "/views/gl-sdk4-ui-firmware.common.js",
        "/views/gl-sdk4-ui-router.common.js",
    ]
    all_paths = sorted(set(view_paths + guesses))
    print("  candidate view paths:", all_paths)

    grep = {}
    fetched = []
    for path in all_paths:
        js = fetch(base, path, sid)
        if not js:
            continue
        fetched.append({"path": path, "bytes": len(js)})
        for token in ("upgrade", "keep_setting", "online_firmware", "check_firmware",
                      "set_firmware", "flash", "sysupgrade", "download", "progress",
                      "auto_upgrade", "firmware"):
            sn = snippets(js, token)
            if sn:
                grep.setdefault(path, {})[token] = sn
        # every "upgrade","<method>" rpc pair
        pairs = sorted(set(re.findall(r'["\']upgrade["\']\s*,\s*["\']([a-z0-9_]+)["\']', js)))
        if pairs:
            grep.setdefault(path, {})["_rpc_pairs"] = ["upgrade." + p for p in pairs]
        print(f"  fetched {path}: {len(js)} bytes; pairs={pairs}")
    findings["views_fetched"] = fetched
    findings["views_grep"] = grep

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:  # noqa: BLE001
        pass

    out = os.path.join(os.getcwd(), "glinet_upgrade_discovery.json")
    with open(out, "w") as fh:
        json.dump(findings, fh, indent=2)
    print(f"\nWrote findings to: {out}")


if __name__ == "__main__":
    main()
