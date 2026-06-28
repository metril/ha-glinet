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
| **Switches** | **Wi-Fi radios** (2.4/5 GHz + guest, on/off), **Tor**, router LEDs, per-tunnel VPN clients, WireGuard/OpenVPN server, Tailscale |
| **Select** | **VPN client** (which profile is active), **Operating mode** (Router / Access Point) |
| **Text** | **Wi-Fi SSID** and **password**, per radio (editable) |
| **Button** | Reboot, Disconnect repeater |
| **Sensors** | Uptime, CPU temperature, load average, memory used %, connected clients, WAN public IP, WAN interface, operating mode, VPN client profile, repeater upstream SSID / signal / state, cellular modem state / signal |
| **Binary sensors** | Internet, WAN, 2.4/5 GHz & guest Wi-Fi, VPN client, Tailscale, repeater, WAN cable, USB tethering, Dynamic DNS, cellular modem |
| **Device trackers** | One per connected client (home/away presence) |
| **Update** | Firmware-available notification (via the router's online check) |
| **Services** | `glinet.block_client`, `glinet.scan_repeater` (returns nearby networks), `glinet.connect_repeater`, `glinet.set_wifi` |

Entities for features a given model lacks (e.g. cellular modem, a VPN type that
isn't configured, no repeater uplink) are automatically omitted.

### Wi-Fi control

Each radio (2.4 GHz, 5 GHz, and their guest networks) has an **on/off switch** and
editable **SSID** / **password** text entities. These call `wifi.set_config` exactly
as the router UI does (keyed by the interface name, e.g. `wifi2g`), verified on a live
GL-MT3000.

### Operating mode

The **Operating Mode** select switches the router between **Router** and **Access
Point** via `netmode.set_mode`. ⚠️ Changing mode is disruptive — it can change the
router's IP and briefly drop connectivity (so you may need to reconfigure the
integration's host afterwards). Repeater/Extender modes join an upstream network and
are driven by the repeater scan/connect flow instead.

### Choosing / switching VPNs

With several VPN profiles configured, the **VPN client** select switches between them
(or **Off**) in one tap — calling the same `vpn-client.set_tunnel` the router UI uses.
Each tunnel is also exposed as its own switch for per-tunnel automations.

### Repeater (Wi-Fi as WAN)

When the router uses a Wi-Fi uplink, the **Repeater** binary sensor plus the upstream
SSID / signal / state sensors report the connection. `glinet.scan_repeater` returns
nearby networks as service response data, `glinet.connect_repeater` joins one, and the
**Disconnect repeater** button drops the uplink.

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
