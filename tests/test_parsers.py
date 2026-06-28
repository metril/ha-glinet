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


def test_led_enabled():
    assert parsers.led_enabled({"led_enable": True}) is True
    assert parsers.led_enabled({"enable": 0}) is False
    assert parsers.led_enabled(None) is None


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
