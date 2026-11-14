"""Coordinator probe/transient-error/invalidate behaviour (HA stubbed, xdist-safe)."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet.api import GlinetApiError, GlinetConnectionError
from custom_components.glinet.coordinator import GlinetDataUpdateCoordinator


class FakeClient:
    """Scripted client; per-instance state only."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail: dict[tuple[str, str], Exception] = {}
        self.results: dict[tuple[str, str], dict] = {}

    async def get_info(self):
        return {"firmware_version": "4.8.1"}

    async def get_status(self):
        return {}

    async def get_clients(self):
        return [{"mac": "AA:BB:CC:00:00:01", "online": True}]

    async def call(self, service, method, params=None, **kw):
        self.calls.append((service, method))
        key = (service, method)
        if key in self.fail:
            raise self.fail[key]
        return self.results.get(key, {"ok": True})


def make(client):
    clock = [1000.0]
    hass = SimpleNamespace(loop=SimpleNamespace(time=lambda: clock[0]))
    entry = SimpleNamespace(options={}, title="r", entry_id="e", data={})
    coord = GlinetDataUpdateCoordinator(hass, entry, client)
    return coord, clock


async def test_first_run_probe_marks_unsupported():
    client = FakeClient()
    client.fail[("led", "get_config")] = GlinetApiError("nope")
    coord, _ = make(client)
    data = await coord._async_update_data()
    assert "led" not in data["configs"]
    assert coord._supported["led"] is False
    assert "tor" in data["configs"]
    n = len(client.calls)
    await coord._async_update_data()
    assert ("led", "get_config") not in client.calls[n:]
    assert "aa:bb:cc:00:00:01" in coord.clients_by_mac


async def test_transient_error_keeps_cache_and_support():
    client = FakeClient()
    client.results[("led", "get_config")] = {"led_enable": True}
    coord, clock = make(client)
    await coord._async_update_data()
    client.fail[("led", "get_config")] = GlinetApiError("blip")
    client.fail[("vpn-client", "get_status")] = GlinetApiError("blip")
    clock[0] += 10_000  # make config tier due
    data = await coord._async_update_data()
    assert coord._supported["led"] is True
    assert data["configs"]["led"] == {"led_enable": True}
    assert coord._supported["vpn_client"] is True


async def test_invalidate_forces_refetch():
    client = FakeClient()
    coord, _ = make(client)
    await coord._async_update_data()
    n = len(client.calls)
    await coord._async_update_data()
    assert ("led", "get_config") not in client.calls[n:]  # throttled
    coord.invalidate("led")
    n = len(client.calls)
    await coord._async_update_data()
    assert ("led", "get_config") in client.calls[n:]


async def test_connection_error_propagates():
    client = FakeClient()
    coord, _ = make(client)
    await coord._async_update_data()
    client.fail[("vpn-client", "get_status")] = GlinetConnectionError("down")
    with pytest.raises(Exception) as ei:
        await coord._async_update_data()
    assert type(ei.value).__name__ == "UpdateFailed"
