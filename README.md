<p align="center">
  <img src="https://raw.githubusercontent.com/metril/ha-glinet/main/custom_components/glinet/brand/logo.png"
       alt="GL.iNet for Home Assistant" width="320">
</p>

# GL.iNet Router — Home Assistant Integration

Monitor and control **GL.iNet firmware-4.x routers** (GL-MT3000 "Beryl AX",
GL-MT6000, GL-AXT1800, GL-AX1800, GL-A1300, and similar) from Home Assistant.

Local polling, UI config flow, no cloud, no third-party Python dependencies.

## Features

| Type | Entities |
| --- | --- |
| **Switches** | **Wi-Fi radios** (2.4/5 GHz + guest, on/off), **VPN client** (on/off), **Tor**, router LEDs, WireGuard/OpenVPN server, Tailscale |
| **Select** | **VPN client** (which profile), **Repeater network** (pick a saved upstream) |
| **Button** | Reboot, Disconnect repeater |
| **Sensors** | Uptime, CPU temperature, load average, memory used %, connected clients, WAN public IP, WAN interface, operating mode, VPN client profile, repeater upstream SSID / signal / state, cellular modem state / signal |
| **Binary sensors** | Internet, WAN, 2.4/5 GHz & guest Wi-Fi, VPN client, Tailscale, repeater, WAN cable, USB tethering, Dynamic DNS, cellular modem |
| **Device trackers** | One per connected client (home/away presence) |
| **Update** | Firmware — shows when a newer firmware is offered and installs it (one-click online upgrade) |
| **Services** | `glinet.block_client`, `glinet.scan_repeater`, `glinet.connect_repeater`, `glinet.set_wifi`, `glinet.set_mode` |

Entities for features a given model lacks (e.g. cellular modem, a VPN type that
isn't configured, no repeater uplink) are automatically omitted.

The design keeps **simple, frequent actions as entities** and pushes **complex or rare
configuration to services** (where a richer form and confirmation fit better).

### Wi-Fi

Each radio (2.4 GHz, 5 GHz, and their guest networks) has an **on/off switch**. Changing
the **SSID, password, or security** is done with the `glinet.set_wifi` service — it
accepts `iface_name` (e.g. `wifi2g`), `ssid`, `key`, `encryption` (`none`/`psk2`/
`psk-mixed`/`sae`/`sae-mixed` for open/WPA2/mixed/WPA3), `hidden`, and `enabled`. These
call `wifi.set_config` exactly as the router UI does, verified on a live GL-MT3000.

### Choosing / switching VPNs

Two complementary controls: the **VPN client select** chooses *which* profile is the
target, and the **VPN client switch** turns it on/off. Picking a different profile while
a VPN is active switches over immediately; otherwise it just sets the target the switch
will use. Only one client is ever active (the switch enforces it, matching the router).

### Operating mode (service-only, confirmed)

Switching mode is disruptive — Access Point can change the router's IP and drop
connectivity — so it's a **service**, not a one-tap entity. Call `glinet.set_mode` with
`mode` (`router`/`ap`) **and `confirm: true`**; without the confirmation it refuses. The
current mode is a read-only **Operating Mode** sensor. (Repeater/Extender are an uplink,
handled by the repeater flow below.)

### Repeater (Wi-Fi as WAN)

- **Repeater network** select — pick one of your **saved** upstream networks to
  reconnect (no password needed; the router keeps the key), or "Disconnected".
  Same-named saved networks are disambiguated by their stored config so you can tell
  them apart.
- For a brand-new network: `glinet.scan_repeater` returns nearby networks (SSID, BSSID,
  band, signal, encryption) as service response data; `glinet.connect_repeater` joins one
  (SSID + password, plus optional `identity` for WPA-Enterprise and `bssid` to target a
  specific same-named AP). The **Disconnect repeater** button drops the uplink.
- The **Repeater** binary sensor + upstream SSID / signal / state sensors report status.

### Firmware

The **Firmware** update entity checks GL.iNet's online firmware service (on the config
refresh interval) and, when a newer firmware is offered, shows the new version and an
**Install** button. Installing runs the router's online upgrade — it downloads the image
and **reboots to flash it, keeping your settings**. The router is offline for a few
minutes and this integration shows unavailable until it returns; the entity catches up on
the next poll. (Install is only available when an update is actually offered.)

### Polling

Dynamic status (connectivity, clients, VPN/repeater state) polls on the **Polling
interval** (default 30 s). Rarely-changing config (Wi-Fi, mode, LED, Tor, DDNS) polls on
a separate **Config refresh interval** (default 5 min) — both tunable in **Configure**.
Any change you make from Home Assistant refreshes its own data immediately regardless.

## Installation (HACS)

1. In HACS → **Integrations** → ⋮ → **Custom repositories**, add
   `https://github.com/jpranathar/ha-glinet` with category **Integration**.
2. Install **GL.iNet Router** and restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → GL.iNet Router**.
4. Enter the router address (default `192.168.8.1`) and your **admin password**.

## Configuration

After setup, open the integration's **Configure** dialog to adjust:

- **Polling interval** (default 30s; the router UI itself polls ~5s if you want
  snappier updates, at a little extra router CPU).
- **Device trackers** — enable/disable per-client presence entities.

## How it works

Firmware 4.x exposes a JSON-RPC API at `POST /rpc`. The integration performs the
challenge/response login (your password is never sent in clear; it is hashed with
the salt and nonce the router provides) and refreshes the short-lived session token
automatically. There is no push channel in the firmware, so data is polled.

## Notes

- Requires GL.iNet **firmware 4.x** (the 3.x cgi-bin API is not supported).
- Some `system.get_status` field paths and a few write payloads are best-effort
  because GL.iNet's API docs are intermittently offline; see `CLAUDE.md`. Please
  open an issue with a sample `/rpc` response if an entity shows `unknown` on your
  model so the field path can be corrected.

## License

MIT
