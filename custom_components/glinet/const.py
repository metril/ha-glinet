"""Constants for the GL.iNet integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "glinet"

MANUFACTURER: Final = "GL.iNet"

# Config entry keys
CONF_HOST: Final = "host"
CONF_PASSWORD: Final = "password"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_CONFIG_SCAN_INTERVAL: Final = "config_scan_interval"
CONF_ENABLE_DEVICE_TRACKER: Final = "enable_device_tracker"

# Defaults
DEFAULT_HOST: Final = "192.168.8.1"
DEFAULT_USERNAME: Final = "root"
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 600
# Rarely-changing config reads (Wi-Fi config, mode, LED, Tor, …) poll on a slower tier.
DEFAULT_CONFIG_SCAN_INTERVAL: Final = 300
MIN_CONFIG_SCAN_INTERVAL: Final = 60
MAX_CONFIG_SCAN_INTERVAL: Final = 3600
DEFAULT_HTTP_TIMEOUT: Final = 10

# JSON-RPC services (the wire name; note VPN services are hyphenated)
SVC_SYSTEM: Final = "system"
SVC_CLIENTS: Final = "clients"
SVC_WIFI: Final = "wifi"
SVC_LED: Final = "led"
SVC_WG_CLIENT: Final = "wg-client"
SVC_OVPN_CLIENT: Final = "ovpn-client"
SVC_VPN_CLIENT: Final = "vpn-client"
SVC_WG_SERVER: Final = "wg-server"
SVC_OVPN_SERVER: Final = "ovpn-server"
SVC_TAILSCALE: Final = "tailscale"
SVC_REPEATER: Final = "repeater"
SVC_CABLE: Final = "cable"
SVC_DDNS: Final = "ddns"
SVC_MODEM: Final = "modem"
SVC_TOR: Final = "tor"
SVC_TETHERING: Final = "tethering"
SVC_NETMODE: Final = "netmode"
SVC_UPGRADE: Final = "upgrade"

# Keys used inside the coordinator's normalized data dict
DATA_INFO: Final = "info"
DATA_STATUS: Final = "status"
DATA_CLIENTS: Final = "clients"
DATA_FEATURES: Final = "features"
DATA_CONFIGS: Final = "configs"
