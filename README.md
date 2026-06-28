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
| **Sensors** | Uptime, CPU temperature, load average, memory used %, connected clients, WAN public IP, WAN protocol |
| **Binary sensors** | Internet, WAN, WireGuard client, OpenVPN client, Tailscale connectivity |
| **Switches** | Router LEDs, WireGuard/OpenVPN client & server, Tailscale |
| **Button** | Reboot |
| **Device trackers** | One per connected client (home/away presence) |
| **Update** | Firmware-available notification |
| **Services** | `glinet.block_client`, `glinet.connect_repeater`, `glinet.set_wifi` |

Entities for features a given model lacks (e.g. cellular modem, a VPN type that
isn't configured) are automatically omitted.

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
