"""Tests for the firmware-upgrade progress watcher."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from custom_components.glinet import upgrade_watch as uw
from custom_components.glinet.api import GlinetApiError, GlinetConnectionError


class _Client:
    def __init__(self, steps):
        self.steps = list(steps)

    async def call(self, service, method, params=None, timeout=None):
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return step

    async def get_info(self):
        return await self.call("system", "get_info")


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(uw, "POLL_INTERVAL", 0)
    monkeypatch.setattr(uw, "MAX_POLLS", 20)
    monkeypatch.setattr(uw, "TERMINAL_GRACE_POLLS", 3)


@pytest.mark.asyncio
async def test_progress_then_reboot():
    seen = []
    client = _Client(
        [
            {"status": 1, "percent": 10},
            GlinetApiError("x"),
            {"status": 1, "percent": 60},
            GlinetConnectionError("down"),
        ]
    )
    assert await uw.watch_upgrade(client, seen.append) == uw.REBOOTING
    assert seen == [10, 60]


@pytest.mark.asyncio
async def test_terminal_without_reboot_fails():
    client = _Client([{"status": 1, "percent": 5}, {"status": 5, "percent": 0}])
    assert await uw.watch_upgrade(client, lambda p: None) == uw.FAILED


@pytest.mark.asyncio
async def test_timeout():
    client = _Client([{"status": 1, "percent": 5}])
    assert await uw.watch_upgrade(client, lambda p: None) == uw.TIMEOUT


@pytest.mark.asyncio
async def test_wait_for_return_back(monkeypatch):
    monkeypatch.setattr(uw, "RETURN_MAX_POLLS", 20)
    client = _Client([GlinetConnectionError("down"), GlinetConnectionError("down"), {"ok": 1}])
    assert await uw.wait_for_return(client) == uw.BACK


@pytest.mark.asyncio
async def test_wait_for_return_timeout(monkeypatch):
    monkeypatch.setattr(uw, "RETURN_MAX_POLLS", 5)
    client = _Client([GlinetConnectionError("down")])
    assert await uw.wait_for_return(client) == uw.TIMEOUT
