"""Tests for the GL.iNet payload parsers."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.glinet import parsers


def test_system_sensors_real_shape():
    # Mirrors firmware 4.8.1 system.get_status.system from a real GL-MT3000.
    status = {
        "system": {
            "uptime": 10053.55,
            "cpu": {"temperature": 63},
            "load_average": [0.03, 0.03, 0],
            "memory_total": 503181312,
            "memory_free": 117669888,
            "memory_buff_cache": 133885952,
        }
    }
    assert parsers.uptime(status) == 10053
    assert parsers.cpu_temperature(status) == 63.0
    assert parsers.load_average(status) == 0.03
    # (total - free - buff_cache) / total
    assert parsers.memory_used_percent(status) == 50.0


def test_cpu_temperature_alt_paths():
    assert parsers.cpu_temperature({"system": {"cpu_temperature": 47.5}}) == 47.5
    assert parsers.cpu_temperature({"temperature": 40}) == 40.0


def test_missing_fields_return_none():
    assert parsers.uptime({}) is None
    assert parsers.cpu_temperature({}) is None
    assert parsers.memory_used_percent({"memory_total": 0, "memory_free": 0}) is None


# network is a LIST of interface dicts on real firmware.
NETWORK = {
    "network": [
        {"interface": "wan", "online": False, "up": False},
        {"interface": "wwan", "online": True, "up": True},
        {"interface": "tethering", "online": False, "up": False},
    ]
}


def test_internet_and_wan_from_interface_list():
    assert parsers.internet_online(NETWORK) is True
    assert parsers.wan_connected(NETWORK) is True
    assert parsers.active_wan_interface(NETWORK) == "wwan"
    # all offline
    offline = {"network": [{"interface": "wan", "online": False, "up": False}]}
    assert parsers.internet_online(offline) is False
    assert parsers.active_wan_interface(offline) is None
    assert parsers.internet_online({}) is None


def test_wan_public_ip_from_ddns():
    data = {
        "configs": {
            "ddns": {
                "ips": [
                    {"interface": "wan6", "ip": []},
                    {"interface": "wwan", "ip": ["203.0.113.7"]},
                ]
            }
        }
    }
    assert parsers.wan_public_ip(data) == "203.0.113.7"
    assert parsers.wan_public_ip({"configs": {}}) is None


def test_client_count_and_fields():
    data = {
        "clients": [
            {"mac": "AA:BB", "online": True, "alias": "poseidon", "ip": "192.168.8.2"},
            {"mac": "CC:DD", "online": False, "name": "laptop"},
        ]
    }
    assert parsers.client_count(data) == 1  # only online counted when some online
    c = data["clients"][0]
    assert parsers.client_mac(c) == "aa:bb"
    assert parsers.client_name(c) == "poseidon"  # alias preferred
    assert parsers.client_name(data["clients"][1]) == "laptop"
    assert parsers.client_is_online(c) is True
    assert parsers.client_is_online(data["clients"][1]) is False


def test_vpn_connected_variants():
    assert parsers.vpn_connected({"status": 2}) is True
    assert parsers.vpn_connected({"status": 0}) is False
    assert parsers.vpn_connected({"status": 1, "rx_bytes": 10}) is True
    assert parsers.vpn_connected({"connected": True}) is True
    assert parsers.vpn_connected({"status": "running"}) is True
    assert parsers.vpn_connected(None) is None
    # wg-server nests status under "server"
    assert parsers.vpn_connected({"server": {"status": 0}, "peers": []}) is False
    assert parsers.vpn_connected({"server": {"status": 2}, "peers": []}) is True
    # tailscale running
    assert parsers.vpn_connected({"status": 3, "login_name": "x"}) is True


def test_vpn_client_unified():
    # Real vpn-client.get_status shape from firmware 4.8.1.
    off = {"mode": 0, "status_list": [{"enabled": False, "name": "Home", "tunnel_id": 10}]}
    assert parsers.vpn_client_connected(off) is False
    assert parsers.vpn_client_active_name(off) is None

    on = {"mode": 3, "status_list": [{"enabled": True, "name": "Home", "tunnel_id": 10}]}
    assert parsers.vpn_client_connected(on) is True
    assert parsers.vpn_client_active_name(on) == "Home"

    # mode missing -> fall back to enabled flags
    assert parsers.vpn_client_connected({"status_list": [{"enabled": True}]}) is True
    assert parsers.vpn_client_connected(None) is None


def test_vpn_client_profiles_and_tunnel():
    cfg = {
        "mode": 0,
        "status_list": [
            {"enabled": False, "tunnel_id": 10, "name": "Home", "type": "wireguard"},
            {"enabled": True, "tunnel_id": 11, "name": "Work", "type": "openvpn"},
        ],
    }
    profiles = parsers.vpn_client_profiles(cfg)
    assert [p["tunnel_id"] for p in profiles] == [10, 11]
    assert parsers.vpn_client_tunnel_enabled(cfg, 10) is False
    assert parsers.vpn_client_tunnel_enabled(cfg, 11) is True
    assert parsers.vpn_client_tunnel_enabled(cfg, 99) is None
    assert parsers.vpn_client_active_name(cfg) == "Work"
    assert parsers.vpn_client_profiles(None) == []


def test_led_enabled():
    assert parsers.led_enabled({"led_enable": True}) is True
    assert parsers.led_enabled({"enable": 0}) is False
    assert parsers.led_enabled(None) is None


def test_operating_mode():
    # netmode.get_mode is authoritative when present
    assert parsers.operating_mode({}, {"mode": "ap"}) == "ap"
    assert parsers.operating_mode({"system": {"mode": 0}}, {"mode": "relay"}) == "relay"
    # fall back to the integer system.mode
    assert parsers.operating_mode({"system": {"mode": 0}}) == "router"
    assert parsers.operating_mode({"system": {"mode": 3}}) == "mode_3"
    assert parsers.operating_mode({"mode": 0}) == "router"
    assert parsers.operating_mode({}) is None


# Mirrors the real wifi.get_config res[] shape (band -> ifaces).
WIFI_CONFIG = {
    "res": [
        {
            "band": "2G",
            "device": "mt798111",
            "hwmode": "11g/n/ax",
            "channel": 0,
            "htmode": "auto",
            "txpower": "Max",
            "random_bssid": True,
            "ifaces": [
                {"name": "wifi2g", "ssid": "Home", "key": "secret12", "encryption": "sae-mixed", "guest": False, "hidden": False},
                {"name": "guest2g", "ssid": "Home-Guest", "key": "guestpass", "encryption": "psk2", "guest": True, "hidden": False},
            ],
        },
        {
            "band": "5G",
            "device": "mt798112",
            "hwmode": "11a/n/ac/ax",
            "channel": 0,
            "htmode": "80",
            "txpower": "Max",
            "random_bssid": True,
            "ifaces": [
                {"name": "wifi5g", "ssid": "Home", "key": "secret12", "encryption": "sae-mixed", "guest": False, "hidden": False},
            ],
        },
    ]
}


def test_wifi_status_ifaces_and_up():
    status = {
        "wifi": [
            {"band": "2G", "guest": False, "name": "wifi2g", "ssid": "Home", "up": True},
            {"band": "5G", "guest": False, "name": "wifi5g", "ssid": "Home", "up": False},
        ]
    }
    ifaces = parsers.wifi_status_ifaces(status)
    assert [i["iface_name"] for i in ifaces] == ["wifi2g", "wifi5g"]
    assert parsers.wifi_iface_up(status, "wifi2g") is True
    assert parsers.wifi_iface_up(status, "wifi5g") is False
    assert parsers.wifi_iface_up(status, "nope") is None
    assert parsers.wifi_status_ifaces({}) == []


def test_wifi_config_ifaces_and_payload():
    ifaces = parsers.wifi_config_ifaces(WIFI_CONFIG)
    assert [i["iface_name"] for i in ifaces] == ["wifi2g", "guest2g", "wifi5g"]
    # band-level context merged into each iface
    g = next(i for i in ifaces if i["iface_name"] == "guest2g")
    assert g["device"] == "mt798111" and g["guest"] is True

    # enable-toggle-style override carries the full echoed config
    payload = parsers.wifi_set_payload(WIFI_CONFIG, "wifi5g", ssid="NewName")
    assert payload["iface_name"] == "wifi5g"
    assert payload["ssid"] == "NewName"
    assert payload["key"] == "secret12"
    assert payload["device"] == "mt798112"
    assert payload["htmode"] == "80"
    assert parsers.wifi_set_payload(WIFI_CONFIG, "missing", ssid="x") is None
    assert parsers.wifi_iface_value(WIFI_CONFIG, "wifi2g", "ssid") == "Home"


def test_firmware_parsers():
    fw = {"current_version": "4.8.1", "prompt": True}
    assert parsers.firmware_update_available(fw) is True
    assert parsers.firmware_current_version(fw) == "4.8.1"
    assert parsers.firmware_update_available({"prompt": False}) is False
    assert parsers.firmware_update_available(None) is None


def test_repeater_status_real_shape():
    # Mirrors repeater.get_status from a real GL-MT3000 in repeater (WISP) uplink.
    status = {
        "running": True,
        "state": 2,
        "state_s": "connected",
        "ssid": "UpstreamNet",
        "signal": -55,
        "channel": 44,
        "config": {"ssid": "UpstreamNet"},
    }
    assert parsers.repeater_connected(status) is True
    assert parsers.repeater_upstream_ssid(status) == "UpstreamNet"
    assert parsers.repeater_signal(status) == -55
    assert parsers.repeater_state(status) == "connected"
    # disconnected
    down = {"running": False, "state": 0, "state_s": "disconnected"}
    assert parsers.repeater_connected(down) is False
    assert parsers.repeater_connected(None) is None
    assert parsers.repeater_upstream_ssid(None) is None


def test_repeater_scan_networks():
    result = {
        "res": [
            {
                "band": "2g",
                "ssid": "Net1",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "channel": 1,
                "signal": -58,
                "encryption": {"enabled": True, "description": "WPA2"},
                "saved": False,
            },
            {
                "band": "5g",
                "ssid": "OpenNet",
                "channel": 44,
                "signal": -70,
                "encryption": {"enabled": False},
                "saved": True,
            },
        ]
    }
    nets = parsers.repeater_scan_networks(result)
    assert [n["ssid"] for n in nets] == ["Net1", "OpenNet"]
    assert nets[0]["encrypted"] is True
    assert nets[1]["encrypted"] is False
    assert nets[1]["saved"] is True
    assert parsers.repeater_scan_networks(None) == []
    assert parsers.repeater_scan_networks({"res": "nope"}) == []


def test_repeater_saved_networks():
    cfg = {
        "res": [
            {"ssid": "HomeWifi", "macaddr": {"mode": "clone"}, "protocol": "dhcp"},
            {"ssid": "OfficeWifi", "protocol": "dhcp"},
            {"protocol": "dhcp"},  # no ssid -> skipped
        ]
    }
    saved = parsers.repeater_saved_networks(cfg)
    # full entries are preserved (so the exact saved network can be rejoined)
    assert [n["ssid"] for n in saved] == ["HomeWifi", "OfficeWifi"]
    assert saved[0]["protocol"] == "dhcp"
    assert parsers.repeater_saved_networks(None) == []
    assert parsers.repeater_saved_networks({"res": "nope"}) == []


def test_repeater_saved_option_map():
    # Unique SSIDs -> labels are just the SSID.
    uniq = {"res": [{"ssid": "Home"}, {"ssid": "Office"}]}
    assert list(parsers.repeater_saved_option_map(uniq)) == ["Home", "Office"]

    # Same name -> disambiguated by stored config (protocol, else clone MAC, else index).
    dup = {
        "res": [
            {"ssid": "MyWifi", "protocol": "dhcp"},
            {"ssid": "MyWifi", "protocol": "static"},
            {"ssid": "MyWifi", "macaddr": {"macaddr": "aa:bb:cc:dd:ee:ff"}},
        ]
    }
    labels = parsers.repeater_saved_option_map(dup)
    # every option is unique, and each maps back to its full entry
    assert len(labels) == 3
    assert "MyWifi (dhcp)" in labels
    assert "MyWifi (static)" in labels
    assert labels["MyWifi (dhcp)"]["protocol"] == "dhcp"
    assert parsers.repeater_saved_option_map(None) == {}


def test_cable_tethering_tor_ddns():
    assert parsers.cable_connected({"status": 3}) is True
    assert parsers.cable_connected({"status": 0}) is False
    assert parsers.cable_connected(None) is None

    assert parsers.tethering_active({"status": 0, "devices": []}) is False
    assert parsers.tethering_active({"status": 1}) is True
    # status absent -> fall back to the devices list
    assert parsers.tethering_active({"devices": [{"x": 1}]}) is True
    assert parsers.tethering_active({"devices": []}) is False

    assert parsers.tor_enabled({"enable": False}) is False
    assert parsers.tor_enabled({"enable": True}) is True
    assert parsers.tor_enabled(None) is None

    assert parsers.ddns_enabled({"enable_ddns": False}) is False
    assert parsers.ddns_enabled({"enable_ddns": True}) is True


def test_modem_parsers():
    empty = {"modems": [], "new_sms_count": 0}
    assert parsers.modem_present(empty) is False
    assert parsers.modem_state(empty) is None
    assert parsers.modem_signal(empty) is None
    present = {"modems": [{"state": "connected", "signal": -71}]}
    assert parsers.modem_present(present) is True
    assert parsers.modem_state(present) == "connected"
    assert parsers.modem_signal(present) == -71
    assert parsers.modem_present(None) is None


def test_vpn_client_option_map_and_active():
    cfg = {
        "mode": 3,
        "status_list": [
            {"enabled": False, "tunnel_id": 10, "name": "Home"},
            {"enabled": True, "tunnel_id": 11, "name": "Work"},
            {"enabled": False, "tunnel_id": 12, "name": "Home"},  # duplicate name
        ],
    }
    labels = parsers.vpn_client_option_map(cfg)
    # duplicate "Home" disambiguated by tunnel id
    assert labels == {"Home": 10, "Work": 11, "Home (12)": 12}
    assert parsers.vpn_client_active_tunnel(cfg) == 11
    assert parsers.vpn_client_active_tunnel({"status_list": []}) is None
    assert parsers.vpn_client_option_map(None) == {}


def test_wifi_band_and_guest():
    # Mirrors system.get_status.wifi from a real GL-MT3000.
    status = {
        "wifi": [
            {"band": "2G", "guest": False, "up": True},
            {"band": "5G", "guest": False, "up": False},
            {"band": "2G", "guest": True, "up": False},
            {"band": "5G", "guest": True, "up": True},
        ]
    }
    assert parsers.wifi_band_up(status, "2G", False) is True
    assert parsers.wifi_band_up(status, "5G", False) is False
    assert parsers.guest_wifi_up(status) is True
    assert parsers.wifi_band_up({}, "2G", False) is None
    assert parsers.guest_wifi_up({"wifi": [{"band": "2G", "guest": False, "up": True}]}) is None
