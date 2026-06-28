"""Tests for the GL.iNet payload parsers."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from custom_components.glinet import parsers


def test_system_sensors_nested():
    status = {
        "system": {
            "uptime": 12345,
            "cpu_temperature": 47.5,
            "load_average": [0.25, 0.1, 0.05],
            "memory_total": 1000,
            "memory_available": 250,
        }
    }
    assert parsers.uptime(status) == 12345
    assert parsers.cpu_temperature(status) == 47.5
    assert parsers.load_average(status) == 0.25
    assert parsers.memory_used_percent(status) == 75.0


def test_system_sensors_flat():
    status = {"uptime": "60", "temperature": 40, "load": 1.5, "memory_total": 4, "memory_free": 1}
    assert parsers.uptime(status) == 60
    assert parsers.cpu_temperature(status) == 40.0
    assert parsers.load_average(status) == 1.5
    assert parsers.memory_used_percent(status) == 75.0


def test_missing_fields_return_none():
    assert parsers.uptime({}) is None
    assert parsers.cpu_temperature({}) is None
    assert parsers.memory_used_percent({"memory_total": 0, "memory_free": 0}) is None


def test_internet_and_wan():
    assert parsers.internet_online({"network": {"online": True}}) is True
    assert parsers.internet_online({"online": "connected"}) is True
    assert parsers.internet_online({"online": 0}) is False
    assert parsers.internet_online({}) is None
    # wan_connected falls back to internet when no explicit wan flag
    assert parsers.wan_connected({"online": 1}) is True


def test_wan_ip_and_protocol():
    status = {"wan": {"ip": "1.2.3.4", "proto": "dhcp"}}
    assert parsers.wan_public_ip(status) == "1.2.3.4"
    assert parsers.wan_protocol(status) == "dhcp"


def test_client_count_and_fields():
    data = {
        "clients": [
            {"mac": "AA:BB", "online": True, "name": "phone", "ip": "192.168.8.2"},
            {"mac": "CC:DD", "online": False, "name": "laptop"},
        ]
    }
    assert parsers.client_count(data) == 1  # only online counted when some online
    c = data["clients"][0]
    assert parsers.client_mac(c) == "aa:bb"
    assert parsers.client_name(c) == "phone"
    assert parsers.client_is_online(c) is True
    assert parsers.client_is_online(data["clients"][1]) is False


def test_vpn_connected_variants():
    assert parsers.vpn_connected({"status": 2}) is True
    assert parsers.vpn_connected({"status": 0}) is False
    assert parsers.vpn_connected({"status": 1, "rx_bytes": 10}) is True
    assert parsers.vpn_connected({"connected": True}) is True
    assert parsers.vpn_connected({"status": "running"}) is True
    assert parsers.vpn_connected(None) is None


def test_led_enabled():
    assert parsers.led_enabled({"led_enable": True}) is True
    assert parsers.led_enabled({"enable": 0}) is False
    assert parsers.led_enabled(None) is None


def test_wifi_radio_enabled():
    wifi = {"res": [{"band": "2g", "enabled": True}, {"band": "5g", "enabled": False}]}
    assert parsers.wifi_radio_enabled(wifi, "2g") is True
    assert parsers.wifi_radio_enabled(wifi, "5g") is False
    assert parsers.wifi_radio_enabled(None, "2g") is None
