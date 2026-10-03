"""Tests for coordinator probe / retry / backoff semantics (HA stubbed)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import homeassistant

if not getattr(homeassistant, "_glinet_stub", False):
    pytest.skip(
        "coordinator tests use a fake hass; they need the conftest HA stubs",
        allow_module_level=True,
    )

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.glinet.api import (
    GlinetApiError,
    GlinetAuthError,
    GlinetConnectionError,
)
from custom_components.glinet.coordinator import GlinetDataUpdateCoordinator


class _Client:
    """Scripted client: (service, method) -> list of results/exceptions (last repeats)."""

    def __init__(self, script=None):
        self.script = script or {}
        self.calls: list[tuple[str, str]] = []
        self.info = {"firmware_version": "4.8.1", "mac": "aa:bb:cc:dd:ee:ff"}

    async def get_info(self):
        return self.info

    async def get_status(self):
        return {}

    async def get_clients(self):
        return []

    async def call(self, service, method, params=None, timeout=None):
        key = (service, method)
        self.calls.append(key)
        steps = self.script.get(key)
        if not steps:
            raise GlinetApiError("unsupported")
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        return step

    def count(self, service, method):
        return self.calls.count((service, method))


def _make(script):
    now = [1000.0]
    hass = SimpleNamespace(loop=SimpleNamespace(time=lambda: now[0]))
    entry = SimpleNamespace(options={}, entry_id="eid", title="Router", data={})
    client = _Client(script)
    return GlinetDataUpdateCoordinator(hass, entry, client), client, now


def _ok(v=1):
    return {"v": v}


@pytest.mark.asyncio
async def test_first_probe_error_marks_unsupported():
    coord, client, _ = _make({("ddns", "get_status"): [GlinetApiError("x")]})
    data = await coord._async_update_data()
    assert coord.supported_reads["ddns"] is False
    assert "ddns" not in data["configs"]
    await coord._async_update_data()
    assert client.count("ddns", "get_status") == 1


@pytest.mark.asyncio
async def test_later_error_keeps_last_value_and_retries():
    key = ("ddns", "get_status")
    coord, client, _ = _make({key: [_ok(1), GlinetApiError("x"), _ok(2)]})
    d1 = await coord._async_update_data()
    assert d1["configs"]["ddns"] == _ok(1)
    d2 = await coord._async_update_data()
    assert coord.supported_reads["ddns"] is True
    assert d2["configs"]["ddns"] == _ok(1)
    d3 = await coord._async_update_data()
    assert d3["configs"]["ddns"] == _ok(2)
    assert client.count(*key) == 3


@pytest.mark.asyncio
async def test_three_consecutive_failures_drop_value():
    key = ("ddns", "get_status")
    err = GlinetApiError("x")
    coord, _, _ = _make({key: [_ok(1), err]})
    await coord._async_update_data()
    assert (await coord._async_update_data())["configs"]["ddns"] == _ok(1)
    assert (await coord._async_update_data())["configs"]["ddns"] == _ok(1)
    assert "ddns" not in (await coord._async_update_data())["configs"]


@pytest.mark.asyncio
async def test_failure_counter_resets_on_success():
    key = ("ddns", "get_status")
    err = GlinetApiError("x")
    coord, _, _ = _make({key: [_ok(1), err, err, _ok(2), err, err]})
    for _ in range(6):
        data = await coord._async_update_data()
    assert data["configs"]["ddns"] == _ok(2)


@pytest.mark.asyncio
async def test_throttled_cache_and_invalidate():
    key = ("led", "get_config")
    coord, client, now = _make({key: [_ok(1), _ok(2)]})
    assert (await coord._async_update_data())["configs"]["led"] == _ok(1)
    now[0] += 30
    assert (await coord._async_update_data())["configs"]["led"] == _ok(1)
    assert client.count(*key) == 1
    coord.invalidate("led")
    assert (await coord._async_update_data())["configs"]["led"] == _ok(2)
    assert client.count(*key) == 2


@pytest.mark.asyncio
async def test_failed_throttled_read_keeps_cache_and_retries_within_300s():
    key = ("led", "get_config")
    coord, client, now = _make({key: [_ok(1), GlinetApiError("x"), _ok(3)]})
    await coord._async_update_data()
    now[0] += 301  # default config interval (300s) elapsed
    assert (await coord._async_update_data())["configs"]["led"] == _ok(1)
    assert client.count(*key) == 2
    now[0] += 100  # inside backoff: cached, no call
    assert (await coord._async_update_data())["configs"]["led"] == _ok(1)
    assert client.count(*key) == 2
    now[0] += 250  # >300s since failure
    assert (await coord._async_update_data())["configs"]["led"] == _ok(3)
    assert client.count(*key) == 3


@pytest.mark.asyncio
async def test_failed_slow_read_retries_within_five_minutes():
    key = ("upgrade", "check_firmware_online")
    coord, client, now = _make({key: [_ok(1), GlinetApiError("x"), _ok(3)]})
    await coord._async_update_data()
    now[0] += 6 * 3600 + 1
    await coord._async_update_data()
    assert client.count(*key) == 2
    now[0] += 301
    assert (await coord._async_update_data())["configs"]["firmware"] == _ok(3)


@pytest.mark.asyncio
async def test_auth_error_raises_reauth():
    coord, client, _ = _make({})

    async def boom():
        raise GlinetAuthError("denied")

    client.get_status = boom
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_connection_error_raises_update_failed():
    coord, client, _ = _make({("ddns", "get_status"): [GlinetConnectionError("down")]})
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_info_refresh_failure_backs_off_and_updates_registry():
    coord, client, now = _make({})
    await coord._async_update_data()
    calls = []

    async def failing():
        calls.append(1)
        raise GlinetApiError("x")

    client.get_info = failing
    now[0] += 6 * 3600 + 1
    await coord._async_update_data()
    await coord._async_update_data()
    assert len(calls) == 1  # backed off after the failed refresh

    registry = MagicMock()
    device = SimpleNamespace(id="dev")
    registry.async_get_device_by_identifier.return_value = device
    dr.async_get.return_value = registry

    async def newer():
        return {"firmware_version": "4.9.0"}

    client.get_info = newer
    now[0] += 6 * 3600 + 1
    await coord._async_update_data()
    registry.async_update_device.assert_called_once_with("dev", sw_version="4.9.0")
