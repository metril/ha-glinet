"""Test fixtures: stub Home Assistant (when not installed) so the package imports.

The package ``__init__`` (and the api/coordinator/services chain it pulls in)
imports Home Assistant, which isn't installed in CI. The pure modules under test
(crypt_util, api, parsers) don't actually use HA, so we inject minimal stubs —
mirroring the approach used in the ha-awtrix integration's test suite.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from unittest.mock import MagicMock


def _mod(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _stub_homeassistant() -> None:
    if getattr(sys.modules.get("homeassistant"), "_glinet_stub", False):
        return

    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    ha = _mod("homeassistant")
    ha._glinet_stub = True

    class _SupportsResponse:
        NONE = "none"
        ONLY = "only"
        OPTIONAL = "optional"

    ha_core = _mod(
        "homeassistant.core",
        HomeAssistant=MagicMock,
        ServiceCall=MagicMock,
        ServiceResponse=dict,
        SupportsResponse=_SupportsResponse,
        callback=lambda f: f,
    )

    class _ConfigEntryState:
        LOADED = "loaded"
        NOT_LOADED = "not_loaded"
        SETUP_RETRY = "setup_retry"
        SETUP_ERROR = "setup_error"

    ha_ce = _mod(
        "homeassistant.config_entries",
        ConfigEntry=MagicMock,
        ConfigFlow=object,
        ConfigFlowResult=dict,
        ConfigEntryState=_ConfigEntryState,
        OptionsFlow=object,
        OptionsFlowWithReload=object,
    )

    class _Platform:
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        DEVICE_TRACKER = "device_tracker"
        SELECT = "select"
        SENSOR = "sensor"
        SWITCH = "switch"
        TEXT = "text"
        UPDATE = "update"

    ha_const = _mod("homeassistant.const", Platform=_Platform)
    _HAError = type(
        "HomeAssistantError",
        (Exception,),
        {"__init__": lambda self, *a, **k: Exception.__init__(self, *a)},
    )
    ha_exc = _mod(
        "homeassistant.exceptions",
        HomeAssistantError=_HAError,
        ServiceValidationError=type("ServiceValidationError", (_HAError,), {}),
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
        ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
        UpdateFailed=type("UpdateFailed", (Exception,), {}),
    )

    ha_helpers = _mod("homeassistant.helpers")

    class _DUC(Generic[_T]):
        def __init__(
            self,
            hass=None,
            logger=None,
            *,
            config_entry=None,
            name="",
            update_interval=None,
            **k,
        ):
            self.hass = hass
            self.config_entry = config_entry
            self.name = name
            self.update_interval = update_interval
            self.data = None

        async def async_request_refresh(self):
            return None

        def __init_subclass__(cls, **k):
            super().__init_subclass__()

    class _CE(Generic[_T]):
        def __init__(self, coordinator=None, **k):
            self.coordinator = coordinator

        def __init_subclass__(cls, **k):
            super().__init_subclass__()

    ha_uc = _mod(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=_DUC,
        CoordinatorEntity=_CE,
        UpdateFailed=ha_exc.UpdateFailed,
    )
    ha_ac = _mod(
        "homeassistant.helpers.aiohttp_client",
        async_get_clientsession=MagicMock(return_value=MagicMock()),
    )
    ha_cv = _mod(
        "homeassistant.helpers.config_validation",
        string=str,
        boolean=bool,
        config_entry_only_config_schema=lambda domain: (lambda config: config),
    )
    ha_typing = _mod("homeassistant.helpers.typing", ConfigType=dict)
    ha_evt = _mod(
        "homeassistant.helpers.event",
        async_call_later=lambda hass, delay, action: (lambda: None),
    )
    ha_dr = _mod(
        "homeassistant.helpers.device_registry",
        DeviceInfo=dict,
        CONNECTION_NETWORK_MAC="mac",
        async_get=MagicMock(),
    )
    ha_sel = _mod(
        "homeassistant.helpers.selector",
        BooleanSelector=MagicMock,
        NumberSelector=MagicMock,
        NumberSelectorConfig=MagicMock,
        NumberSelectorMode=types.SimpleNamespace(BOX="box"),
    )

    ha_helpers.update_coordinator = ha_uc
    ha_helpers.aiohttp_client = ha_ac
    ha_helpers.config_validation = ha_cv
    ha_helpers.device_registry = ha_dr
    ha_helpers.event = ha_evt
    ha_helpers.typing = ha_typing
    ha_helpers.selector = ha_sel

    modules = {
        "homeassistant": ha,
        "homeassistant.core": ha_core,
        "homeassistant.config_entries": ha_ce,
        "homeassistant.const": ha_const,
        "homeassistant.exceptions": ha_exc,
        "homeassistant.helpers": ha_helpers,
        "homeassistant.helpers.update_coordinator": ha_uc,
        "homeassistant.helpers.aiohttp_client": ha_ac,
        "homeassistant.helpers.config_validation": ha_cv,
        "homeassistant.helpers.device_registry": ha_dr,
        "homeassistant.helpers.event": ha_evt,
        "homeassistant.helpers.typing": ha_typing,
        "homeassistant.helpers.selector": ha_sel,
    }
    for name, module in modules.items():
        sys.modules.setdefault(name, module)


if importlib.util.find_spec("homeassistant") is None:
    _stub_homeassistant()
