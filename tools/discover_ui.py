#!/usr/bin/env python3
"""Discover undocumented GL.iNet RPC methods + param shapes.

Two complementary techniques, both run against the live router:

1. **RPC ``list``** — firmware 4.x exposes a top-level ``list`` method that returns
   the ACL of callable ``service`` / ``method`` names for the session. This is the
   authoritative method inventory (far better than guessing names).
2. **Web-UI bundle scrape** — the admin UI is a JS app that calls ``/rpc``; its
   bundle literally contains the method names and the param object keys. We fetch
   the (auth-gated) JS with the session token and grep it for the tokens we care
   about (operating mode, repeater, wifi.set_config, firmware, tor, modem).

Standalone: stdlib only. Reuses the integration's verified crypt implementation.

Usage:
    python3 tools/discover_ui.py [host]
    # host defaults to 10.200.200.1 (or $GLINET_HOST)
    # password from .glinet_pass file, $GLINET_PASSWORD, or prompt

Writes redacted findings to glinet_ui_methods.json (gitignored).
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
    pass


def rpc(url, method, params):
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    if data.get("error"):
        raise RpcError(json.dumps(data["error"]))
    return data.get("result")


def login(url, username, password):
    challenge = rpc(url, "challenge", {"username": username})
    alg = int(challenge["alg"])
    salt = challenge["salt"]
    nonce = challenge["nonce"]
    hash_method = (challenge.get("hash-method") or "md5").lower()
    cipher = crypt_util.crypt_password(password, alg, salt)
    login_hash = getattr(hashlib, hash_method)(
        f"{username}:{cipher}:{nonce}".encode()
    ).hexdigest()
    result = rpc(url, "login", {"username": username, "hash": login_hash})
    return result["sid"]


# Tokens of interest grepped out of the JS bundle, grouped by capability.
TOKEN_GROUPS = {
    "mode": [
        "set_mode", "get_mode", "working_mode", "workmode", "network_mode",
        "set_network_mode", "ap_mode", "access_point", "extender", "wds", "drop_wan",
    ],
    "repeater": [
        "repeater", "set_config", "scan", "connect", "disconnect", "join", "leave",
        "saved_network", "get_config_list",
    ],
    "wifi": [
        "wifi", "set_config", "iface", "ifaces", "guest", "txpower", "set_status",
    ],
    "firmware": [
        "firmware", "check_online", "get_firmware", "check_upgrade", "upgrade",
        "online_check", "fw_version",
    ],
    "tor": ["tor", "set_config", "set_state"],
    "modem_cable": ["modem", "cable", "tethering"],
}

# Candidate asset paths (the index references app.<hash>.js; chunk-vendors too).
ASSET_HINTS = ["/js/app", "/js/chunk-vendors", "/js/chunk-common"]


def fetch_index(base, sid):
    """Return the index HTML (try with and without the session cookie)."""
    for headers in (
        {"Cookie": f"Admin-Token={sid}", "Authorization": sid},
        {},
    ):
        try:
            req = urllib.request.Request(base + "/", headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8", "replace")
            if "app" in body and ".js" in body:
                return body, headers
        except Exception as err:  # noqa: BLE001
            print(f"  index fetch ({headers or 'no-auth'}) failed: {err}")
    return "", {}


def fetch_asset(base, path, sid):
    """Fetch a JS asset, trying several auth styles; follow one redirect."""
    attempts = [
        {"Cookie": f"Admin-Token={sid}", "Authorization": sid},
        {"Cookie": f"Admin-Token={sid}"},
        {"Authorization": sid},
        {},
    ]
    for headers in attempts:
        for url in (f"{base}{path}", f"{base}{path}?Admin-Token={sid}"):
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=20) as resp:
                    body = resp.read()
                if len(body) > 2000 and b"<html" not in body[:200].lower():
                    return body.decode("utf-8", "replace"), headers
            except Exception:  # noqa: BLE001
                continue
    return "", {}


def context_snippets(js, token, radius=80, limit=6):
    """Return short redacted snippets around each token occurrence."""
    out = []
    for m in re.finditer(re.escape(token), js):
        s = max(0, m.start() - radius)
        e = min(len(js), m.end() + radius)
        snippet = js[s:e].replace("\n", " ")
        out.append(snippet)
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
        with open(PASS_FILE) as fh:
            password = fh.read().strip()
    if not password:
        password = getpass.getpass("Router admin password: ")

    sid = login(url, username, password)
    print(f"login OK (sid len={len(sid)})")

    findings: dict = {}

    # --- 1. Authoritative method inventory via `list` -----------------------
    list_inventory = None
    for params in ([sid], [sid, ""], [], [sid, "all"]):
        try:
            res = rpc(url, "list", params)
            if res:
                list_inventory = res
                print(f"list({params!r}) -> {type(res).__name__} OK")
                break
        except Exception as err:  # noqa: BLE001
            print(f"list({params!r}) -> {err}")
    findings["list_inventory"] = list_inventory

    # --- 2. Fetch + scrape the web UI bundle --------------------------------
    index, _ = fetch_index(base, sid)
    asset_paths = sorted(set(re.findall(r"/js/[A-Za-z0-9._-]+\.js", index)))
    if not asset_paths:
        # fall back to hinted names if the index didn't enumerate them
        asset_paths = [p + ".js" for p in ASSET_HINTS]
    print(f"asset paths from index: {asset_paths}")

    js_all = ""
    fetched = []
    for path in asset_paths:
        body, used = fetch_asset(base, path, sid)
        if body:
            js_all += "\n" + body
            fetched.append({"path": path, "bytes": len(body), "auth": list(used)})
            print(f"  fetched {path}: {len(body)} bytes")
        else:
            print(f"  FAILED to fetch {path}")
    findings["assets_fetched"] = fetched
    findings["js_total_bytes"] = len(js_all)

    grep: dict = {}
    if js_all:
        for group, tokens in TOKEN_GROUPS.items():
            grep[group] = {}
            for token in tokens:
                snips = context_snippets(js_all, token)
                if snips:
                    grep[group][token] = snips
    findings["js_grep"] = grep

    # Also pull every dotted rpc-ish call("svc","method") style occurrence.
    call_pairs = sorted(set(re.findall(r'["\']([a-z][a-z0-9_-]+)["\']\s*,\s*["\']([a-z][a-z0-9_]+)["\']', js_all)))
    findings["call_pairs_sample"] = ["{}.{}".format(a, b) for a, b in call_pairs][:400]

    try:
        rpc(url, "logout", {"sid": sid})
    except Exception:  # noqa: BLE001
        pass

    out = os.path.join(os.getcwd(), "glinet_ui_methods.json")
    with open(out, "w") as fh:
        json.dump(findings, fh, indent=2)
    print(f"\nWrote findings to: {out}")


if __name__ == "__main__":
    main()
