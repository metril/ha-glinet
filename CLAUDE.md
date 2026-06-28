# ha-glinet — project notes

Home Assistant custom integration (HACS) for **GL.iNet firmware-4.x routers**
(GL-MT3000 "Beryl AX" and similar: GL-MT6000, GL-AXT1800, GL-AX1800, GL-A1300…).

## Architecture

- **Polling, not push.** Firmware 4.x exposes only a request/response JSON-RPC API
  at `POST /rpc` (`call`/`list`). There is no WebSocket/SSE, ubus `subscribe` is
  local-socket only, and MQTT is outbound-to-GoodCloud. So this uses a
  `DataUpdateCoordinator` polling on a user-configurable interval (default 30s),
  matching the IPMI integration's pattern.
- `api.py` — `GlinetApiClient`: async JSON-RPC, 3-step challenge/login auth, `sid`
  with transparent single re-auth on expiry. Typed errors: `GlinetAuthError`,
  `GlinetConnectionError`, `GlinetApiError`.
- `crypt_util.py` — pure-Python `md5_crypt`/`sha256_crypt`/`sha512_crypt` (no
  stdlib `crypt`, which is gone in Python 3.13). Verified byte-for-byte against
  stdlib `crypt` in `tests/test_crypt.py`.
- `coordinator.py` — fetches `system.get_info` (once), `system.get_status`,
  `clients.get_list`, plus optional control reads (wifi/led/vpn/tailscale) that are
  **probed once** and only polled if supported. Feature flags parsed from
  `hardware_feature`/`software_feature`.
- `parsers.py` — **all field extraction lives here**, each via candidate dotted
  paths with fallbacks. This is the one place to adjust if a field path differs on
  a given router/firmware.
- Platforms: `sensor`, `binary_sensor`, `switch`, `button` (reboot),
  `device_tracker` (per-client), `update` (firmware notify). Services in
  `services.py`: `block_client`, `connect_repeater`, `set_wifi` (device-scoped).

## Auth flow (firmware 4.x)

1. `challenge {username:"root"}` → `{alg, salt, nonce, hash-method?}`
   (alg 1=md5_crypt, 5=sha256_crypt, 6=sha512_crypt; nonce TTL ~1s).
2. `cipher = crypt(password, "$alg$salt")`;
   `login_hash = HASH(f"{user}:{cipher}:{nonce}")` — HASH defaults to **md5**, but
   honors `hash-method` if the challenge advertises it (firmware 4.8+).
3. `login {username, hash}` → `{sid}`. Subsequent calls:
   `call [sid, service, method, params]`.
   **VPN services are hyphenated on the wire**: `wg-client`, `ovpn-client`,
   `wg-server`, `ovpn-server`.

## ⚠️ Needs verification on a live router

The GL.iNet 4.x API docs are intermittently offline, so exact JSON field layouts
and some write payloads are best-effort. When testing against a real router, verify
and adjust in `parsers.py` / `switch.py` / `services.py`:

- `system.get_status` field paths: cpu temp, load, memory, uptime, WAN ip/proto,
  internet/wan online flags.
- VPN `get_status` shape: the `status` int semantics (1 vs 2 = connected) and the
  `start` params (group_id/peer_id/client_id).
- `wifi.set_config` / `led.set_config` payload shape.
- Firmware-update field (`new_version`) and `tailscale.get_status` keys.
- Whether `hash-method` actually appears on the user's firmware (4.8+).

Tip: hit `/rpc` with curl after logging in, or use python-glinet's
`api_description.json` (GPL — reference only, do not vendor) to confirm shapes.

## License

MIT. Clean-room — do NOT copy GPL code from python-glinet / gli-py or vendor their
`api_description.json`.

## Tests

`pytest -q` — no Home Assistant install required for `test_crypt.py` /
`test_api.py` / `test_parsers.py` (HA is stubbed where needed, following the
ha-awtrix test pattern).
