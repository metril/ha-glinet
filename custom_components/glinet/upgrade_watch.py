"""Poll the router's online-upgrade progress (HA-independent, unit-testable)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from . import parsers
from .api import GlinetApiClient, GlinetApiError, GlinetAuthError, GlinetConnectionError
from .const import SVC_UPGRADE

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = 2.0  # seconds between status polls
MAX_POLLS = 450  # ~15 min overall budget
TERMINAL_GRACE_POLLS = 30  # ~60 s of a terminal status with the RPC still answering
_TERMINAL = {2, 5, 7, 9}

REBOOTING = "rebooting"  # link dropped: the router is flashing/rebooting
FAILED = "failed"  # terminal status but the router never rebooted
TIMEOUT = "timeout"
BACK = "back"  # router answers RPC again after the reboot
RETURN_MAX_POLLS = 450  # ~15 min for flash + reboot


async def watch_upgrade(
    client: GlinetApiClient, on_progress: Callable[[int | None], None]
) -> str:
    """Poll ``upgrade.get_online_upgrade_status`` until the outcome is known."""
    terminal_polls = 0
    for _ in range(MAX_POLLS):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            resp = await client.call(SVC_UPGRADE, "get_online_upgrade_status", {})
        except (GlinetConnectionError, GlinetAuthError):
            return REBOOTING
        except GlinetApiError as err:
            _LOGGER.debug("Upgrade status poll failed: %s", err)
            continue
        status, percent = parsers.firmware_upgrade_progress(resp)
        if status in _TERMINAL:
            terminal_polls += 1
            if terminal_polls >= TERMINAL_GRACE_POLLS:
                return FAILED
            continue
        terminal_polls = 0
        on_progress(percent)
    return TIMEOUT


async def wait_for_return(client: GlinetApiClient) -> str:
    """After the link dropped, poll until the router answers again (re-login included)."""
    for _ in range(RETURN_MAX_POLLS):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            await client.get_info()
        except (GlinetConnectionError, GlinetAuthError, GlinetApiError):
            continue
        return BACK
    return TIMEOUT
