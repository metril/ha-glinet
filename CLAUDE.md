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

## Verified against a real GL-MT3000 (firmware 4.8.1)

Read paths confirmed against a live router (`tools/dump_rpc.py` produces a redacted
dump). Key real shapes:

- Auth: `alg=1` (md5_crypt cipher), **`hash-method=sha256`** (outer hash). The
  client honors `hash-method`, so this works.
- `system.get_status.system`: `uptime`, `cpu.temperature` (nested!), `load_average[]`,
  `memory_total`/`memory_free`/`memory_buff_cache`. Memory-used subtracts buff/cache.
- `system.get_status.network` is a **list** of interface dicts (`interface`,
  `online`, `up`) — connectivity is derived by scanning it (active iface = first
  online; e.g. `wwan` in repeater mode).
- `system.get_status.wifi` is a list of `{band, guest, up}` — drives the WiFi
  binary sensors.
- WAN IP comes from `ddns.get_status.ips[].ip[]`, not `system.get_status`.
- `clients.get_list` → `{clients:[{mac, alias, name, ip, online, iface, total_rx/tx}]}`;
  `alias` preferred for the display name.
- `system.get_info`: `board_info.model` = "GL.iNet GL-MT3000", `firmware_version`,
  top-level `mac`, `hardware_feature`/`software_feature` dicts.
- LED: `led.get_config` → `{led_enable}`.

## ⚠️ Still unverified (writes + a few reads)

- **VPN client status — RESOLVED** (via `tools/discover_vpn.py`): the per-protocol
  `wg-client`/`ovpn-client` `.get_status` don't exist; the unified
  **`vpn-client.get_status`** is the one. Shape:
  `{mode, status_list:[{enabled, name, tunnel_id}]}` — `mode != 0` = a client is
  active, and the `enabled` entry is the active profile. Read-only binary sensor
  "VPN Client" + diagnostic sensor "VPN Client Profile" use this. (Only `get_status`
  is confirmed; `wg-client`/`ovpn-client` expose `get_config_list` but it needs a
  parameter, and vpn-client **start/stop** params are still unknown → no write
  switch for the client yet. `tor.get_status`/`tor.get_config` also exist.)
- **wg-server** nests status under `server.status`; ovpn-server/tailscale are
  top-level. Tailscale `status:3` is treated as connected (heuristic ≥2).
- **Write payloads** (`led.set_config`, VPN `start`/`stop`, `wifi.set_config`,
  `clients.block_client`, `repeater.connect`) use documented method names but the
  exact params are unconfirmed — verify before trusting the switches/services.
- **Firmware-update** field (`new_version`) not present in get_status; the update
  entity reports "up to date" until a real check method is found.

## License

MIT. Clean-room — do NOT copy GPL code from python-glinet / gli-py or vendor their
`api_description.json`.

## Tests

`pytest -q` — no Home Assistant install required for `test_crypt.py` /
`test_api.py` / `test_parsers.py` (HA is stubbed where needed, following the
ha-awtrix test pattern).
