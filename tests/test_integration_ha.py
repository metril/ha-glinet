"""End-to-end tests against a real Home Assistant (skipped without the harness)."""

from __future__ import annotations

import sys
from datetime import timedelta
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")
if getattr(sys.modules.get("homeassistant"), "_glinet_stub", False):  # pragma: no cover
    pytest.skip("Home Assistant is stubbed", allow_module_level=True)

from homeassistant.config_entries import SOURCE_RECONFIGURE, ConfigEntryState
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.glinet.api import GlinetApiClient, GlinetApiError
from custom_components.glinet.const import DOMAIN
from custom_components.glinet.diagnostics import async_get_config_entry_diagnostics

INFO = {
    "board_info": {"model": "GL.iNet GL-MT3000"},
    "firmware_version": "4.8.1",
    "mac": "94:83:c4:00:00:01",
    "hardware_feature": {"wifi": True},
    "software_feature": {},
}
STATUS = {
    "system": {
        "uptime": 10053.55,
        "cpu": {"temperature": 63},
        "load_average": [0.03, 0.03, 0],
        "memory_total": 503181312,
        "memory_free": 117669888,
        "memory_buff_cache": 133885952,
    },
    "network": [
        {"interface": "wan", "online": False, "up": False},
        {"interface": "wwan", "online": True, "up": True},
    ],
    "wifi": [
        {"band": "2.4G", "guest": False, "up": True},
        {"band": "5G", "guest": False, "up": True},
    ],
}
CLIENTS = [
    {"mac": "AA:BB:CC:00:00:01", "alias": "poseidon", "ip": "192.168.8.2", "online": True},
    {"mac": "AA:BB:CC:00:00:02", "name": "laptop", "ip": "192.168.8.3", "online": False},
]
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
                {"name": "wifi2g", "ssid": "Home", "key": "secret12",
                 "encryption": "sae-mixed", "guest": False, "hidden": False},
            ],
        }
    ]
}
REPEATER = {
    "running": True, "state": 2, "state_s": "connected",
    "ssid": "UpstreamNet", "signal": -55, "channel": 44,
}
RESPONSES = {
    ("ddns", "get_status"): {"ips": [{"interface": "wwan", "ip": ["203.0.113.7"]}]},
    ("ddns", "get_config"): {"enable_ddns": False},
    ("vpn-client", "get_status"): {
        "mode": 0,
        "status_list": [
            {"enabled": False, "name": "Home", "tunnel_id": 10, "type": "wireguard"},
            {"enabled": False, "name": "Work", "tunnel_id": 11, "type": "openvpn"},
        ],
    },
    ("wg-server", "get_status"): {"server": {"status": 0}, "peers": []},
    ("ovpn-server", "get_status"): {"status": 0},
    ("tailscale", "get_status"): {"status": 0},
    ("repeater", "get_status"): REPEATER,
    ("repeater", "get_saved_ap_list"): {
        "res": [
            {"ssid": "HomeWifi", "protocol": "dhcp"},
            {"ssid": "OfficeWifi", "protocol": "dhcp"},
        ]
    },
    ("repeater", "scan"): {
        "res": [{"ssid": "Net1", "bssid": "aa:bb:cc:dd:ee:ff", "band": "2g",
                 "channel": 1, "signal": -58,
                 "encryption": {"enabled": True, "description": "WPA2"}, "saved": False}]
    },
    ("cable", "get_status"): {"status": 0, "mode": 0},
    ("tethering", "get_status"): {"status": 0, "devices": []},
    ("modem", "get_status"): {"modems": []},
    ("led", "get_config"): {"led_enable": False},
    ("tor", "get_config"): {"enable": False, "countries": [], "manual": False},
    ("netmode", "get_mode"): {"mode": "router"},
    ("upgrade", "check_firmware_online"): {
        "current_version": "4.8.1", "version_new": "4.9.0", "prompt": True,
    },
    ("wifi", "get_config"): WIFI_CONFIG,
}


class Router:
    """Fake router recording writes and allowing per-test overrides."""

    def __init__(self) -> None:
        self.responses = dict(RESPONSES)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def call(self, service, method, params=None, timeout=None):
        self.calls.append((service, method, params))
        key = (service, method)
        if key in self.responses:
            result = self.responses[key]
            if isinstance(result, Exception):
                raise result
            return result
        if method.startswith(("set_", "start", "stop", "connect", "disconnect",
                              "upgrade_", "block_")):
            return {}
        raise GlinetApiError(f"unsupported {service}.{method}")

    def writes(self, service, method):
        return [p for s, m, p in self.calls if (s, m) == (service, method)]


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    return


@pytest.fixture
def router():
    r = Router()

    async def get_info(self):
        return INFO

    async def get_status(self):
        return STATUS

    async def get_clients(self):
        return CLIENTS

    async def noop(self):
        return None

    async def call(self, service, method, params=None, timeout=None):
        return await r.call(service, method, params, timeout)

    with (
        patch.object(GlinetApiClient, "async_login", noop),
        patch.object(GlinetApiClient, "async_logout", noop),
        patch.object(GlinetApiClient, "get_info", get_info),
        patch.object(GlinetApiClient, "get_status", get_status),
        patch.object(GlinetApiClient, "get_clients", get_clients),
        patch.object(GlinetApiClient, "call", call),
        patch.object(GlinetApiClient, "test_connection", lambda self: get_info(self)),
    ):
        yield r


@pytest.fixture
async def entry(hass, router):
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "192.168.8.1", "password": "x"},
        unique_id="94:83:c4:00:00:01",
        title="GL.iNet GL-MT3000",
    )
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


def _by_unique(hass, entry, domain, suffix):
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.domain == domain and ent.unique_id == f"{entry.entry_id}_{suffix}":
            return ent.entity_id
    raise AssertionError(f"no {domain} entity with unique id suffix {suffix}")


async def test_entry_loads_and_entities(hass, entry):
    assert entry.state is ConfigEntryState.LOADED
    reg = er.async_get(hass)
    names = {
        e.entity_id: state.attributes.get("friendly_name")
        for e in er.async_entries_for_config_entry(reg, entry.entry_id)
        if (state := hass.states.get(e.entity_id))
    }
    base = "GL.iNet GL-MT3000"
    assert names["sensor.gl_inet_gl_mt3000_uptime"] == f"{base} Uptime"
    assert f"{base} LEDs" in names.values()
    assert f"{base} 2.4 GHz Wi-Fi" in names.values()
    assert f"{base} VPN Client" in names.values()
    assert f"{base} Last Boot" in names.values()
    assert any(k.startswith("update.") for k in names)
    assert any(k.startswith("binary_sensor.") and v == f"{base} Repeater"
               for k, v in names.items())
    trackers = {k: hass.states.get(k).state for k in names if k.startswith("device_tracker.")}
    assert sorted(trackers.values()) == ["home", "not_home"]
    assert all(not reg.async_get(k).disabled for k in trackers)


async def test_update_entity(hass, entry):
    state = hass.states.get(_by_unique(hass, entry, "update", "firmware"))
    assert state.state == "on"
    assert state.attributes["installed_version"] == "4.8.1"
    assert state.attributes["latest_version"] == "4.9.0"


async def test_switch_writes(hass, entry, router):
    led = _by_unique(hass, entry, "switch", "led")
    await hass.services.async_call("switch", "turn_on", {"entity_id": led}, blocking=True)
    assert router.writes("led", "set_config")[-1] == {"led_enable": True}

    vpn = _by_unique(hass, entry, "switch", "vpn_client")
    await hass.services.async_call("switch", "turn_on", {"entity_id": vpn}, blocking=True)
    assert router.writes("vpn-client", "set_tunnel")
    assert router.writes("vpn-client", "set_tunnel")[-1]["enabled"] is True


async def test_services(hass, entry, router):
    for name in ("block_client", "scan_repeater", "connect_repeater", "set_wifi", "set_mode"):
        assert hass.services.has_service(DOMAIN, name)
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, entry.entry_id), entry.entry_id
    )

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_mode",
            {"device_id": device.id, "mode": "ap", "confirm": False}, blocking=True,
        )
    assert not router.writes("netmode", "set_mode")

    await hass.services.async_call(
        DOMAIN, "set_mode",
        {"device_id": device.id, "mode": "ap", "confirm": True}, blocking=True,
    )
    assert router.writes("netmode", "set_mode") == [{"mode": "ap"}]

    resp = await hass.services.async_call(
        DOMAIN, "scan_repeater", {"device_id": device.id},
        blocking=True, return_response=True,
    )
    assert resp["networks"][0]["ssid"] == "Net1"


async def test_options_flow(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval": 60, "config_scan_interval": 300, "enable_device_tracker": True},
    )
    await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options["scan_interval"] == 60
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_flow(hass, entry):
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "192.168.9.1", "password": "newpw"}
    )
    await hass.async_block_till_done()
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "192.168.9.1"
    assert entry.data["password"] == "newpw"


async def test_diagnostics(hass, entry):
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["info"]["mac"] == "**REDACTED**"
    saved = diag["data"]["configs"]["repeater"]["ssid"]
    assert saved == "**REDACTED**"
    assert diag["supported_reads"]["repeater"] is True
    assert diag["supported_reads"]["modem"] is True


async def test_coordinator_resilience(hass, entry, router):
    sensor = _by_unique(hass, entry, "binary_sensor", "repeater")
    first = hass.states.get(sensor).state
    assert first == "on"
    router.responses[("repeater", "get_status")] = GlinetApiError("boom")

    now = dt_util.utcnow()
    states = []
    for i in range(1, 4):
        async_fire_time_changed(hass, now + timedelta(seconds=31 * i))
        await hass.async_block_till_done()
        states.append(hass.states.get(sensor).state)
    assert states[0] == first
    assert states[1] == first
    assert states[2] in ("unavailable", "unknown")
