"""Test fixtures: stub Home Assistant + voluptuous so the package imports.

The package ``__init__`` (and the api/coordinator/services chain it pulls in)
imports Home Assistant, which isn't installed in CI. The pure modules under test
(crypt_util, api, parsers) don't actually use HA, so we inject minimal stubs —
mirroring the approach used in the ha-awtrix integration's test suite.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock


def _mod(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def _stub_voluptuous() -> None:
    if "voluptuous" in sys.modules:
        return
    vol = types.ModuleType("voluptuous")

    class _Schema:
        def __init__(self, schema, *a, **k):
            self._schema = schema

        def __call__(self, data):
            return data

    class _Marker:
        def __init__(self, key, *a, **k):
            self.key = key

        def __hash__(self):
            return hash(self.key)

        def __eq__(self, other):
            return self.key == getattr(other, "key", other)

    vol.Schema = _Schema
    vol.Required = _Marker
    vol.Optional = _Marker
    vol.All = lambda *a: (a[-1] if a else (lambda x: x))
    vol.Range = lambda *a, **k: (lambda x: x)
    vol.Coerce = lambda tp: tp
    vol.In = lambda *a, **k: (lambda x: x)
    sys.modules["voluptuous"] = vol


def _stub_homeassistant() -> None:
    if getattr(sys.modules.get("homeassistant"), "_glinet_stub", False):
        return

    from typing import Generic, TypeVar

    _T = TypeVar("_T")

    ha = _mod("homeassistant")
    ha.__path__ = []
    ha._glinet_stub = True

    class _SupportsResponse:
        NONE = "none"
        ONLY = "only"
        OPTIONAL = "optional"

    class _ConfigEntry(Generic[_T]):
        """Subscriptable stand-in (``ConfigEntry[Coordinator]``)."""

        def __init__(self, *a, **k):
            self.options = k.get("options", {})
            self.title = k.get("title", "router")
            self.entry_id = k.get("entry_id", "eid")

    ha_core = _mod(
        "homeassistant.core",
        HomeAssistant=MagicMock,
        ServiceCall=MagicMock,
        ServiceResponse=dict,
        SupportsResponse=_SupportsResponse,
        callback=lambda f: f,
    )
    ha_ce = _mod(
        "homeassistant.config_entries",
        ConfigEntry=_ConfigEntry,
        ConfigFlow=object,
        ConfigFlowResult=dict,
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

    def _const_getattr(name):
        if name.startswith("__"):
            raise AttributeError(name)
        return name.lower() if name.startswith("CONF_") else MagicMock()

    ha_const.__getattr__ = _const_getattr
    ha_exc = _mod(
        "homeassistant.exceptions",
        HomeAssistantError=type("HomeAssistantError", (Exception,), {}),
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
        ServiceValidationError=type("ServiceValidationError", (Exception,), {}),
        ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}),
        UpdateFailed=type("UpdateFailed", (Exception,), {}),
    )

    ha_helpers = _mod("homeassistant.helpers")
    ha_helpers.__path__ = []

    class _DUC(Generic[_T]):
        def __init__(self, hass=None, logger=None, *, name="", update_interval=None, **k):
            self.hass = hass
            self.config_entry = k.get("config_entry")
            self.data = None

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
    ha_cv = _mod("homeassistant.helpers.config_validation", string=str, boolean=bool)
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

    ha_helpers.update_coordinator = ha_uc
    ha_helpers.aiohttp_client = ha_ac
    ha_helpers.config_validation = ha_cv
    ha_helpers.device_registry = ha_dr
    ha_helpers.event = ha_evt

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
    }
    def _lenient(name):
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock()

    for name, module in modules.items():
        if not hasattr(module, "__getattr__"):
            module.__getattr__ = _lenient
        sys.modules.setdefault(name, module)


def _install_fallback_finder() -> None:
    """Auto-stub any other ``homeassistant.*`` submodule the package imports."""
    import importlib.abc
    import importlib.machinery

    class _Loader(importlib.abc.Loader):
        def create_module(self, spec):
            module = types.ModuleType(spec.name)
            module.__path__ = []
            module.__getattr__ = lambda name: (
                (_ for _ in ()).throw(AttributeError(name))
                if name.startswith("__")
                else MagicMock()
            )
            return module

        def exec_module(self, module):
            return None

    class _GlinetStubFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.startswith("homeassistant."):
                return importlib.machinery.ModuleSpec(fullname, _Loader())
            return None

    if not any(type(f).__name__ == "_GlinetStubFinder" for f in sys.meta_path):
        sys.meta_path.append(_GlinetStubFinder())


_stub_voluptuous()
_stub_homeassistant()
_install_fallback_finder()
