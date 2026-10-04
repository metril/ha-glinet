"""Config flow for the GL.iNet integration."""

from __future__ import annotations

import logging
from typing import Any

import probatio as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlowWithReload,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import parsers
from .api import GlinetApiClient, GlinetAuthError, GlinetConnectionError, GlinetError
from .const import (
    CONF_CONFIG_SCAN_INTERVAL,
    CONF_DEVICE_TRACKER_MODE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONFIG_SCAN_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MAX_CONFIG_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_CONFIG_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    SUBENTRY_TRACKED_CLIENT,
    TRACKER_MODE_SELECTED,
    TRACKER_MODES,
)

_LOGGER = logging.getLogger(__name__)

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _validate(hass, host: str, password: str) -> dict[str, Any]:
    """Validate credentials, returning device info. Raises on failure."""
    session = async_get_clientsession(hass)
    client = GlinetApiClient(session=session, host=host, password=password)
    info = await client.test_connection()
    await client.async_logout()
    return info


def _title_from_info(info: dict[str, Any], host: str) -> str:
    """Build an entry title from device info."""
    board_model = (info.get("board_info") or {}).get("model")
    if board_model:  # e.g. "GL.iNet GL-MT3000"
        return board_model
    model = info.get("model") or info.get("product")
    if model:
        return f"{MANUFACTURER} {model}"
    return f"{MANUFACTURER} {host}"


def _unique_id_from_info(info: dict[str, Any], host: str) -> str:
    """Pick a stable unique id (router MAC, falling back to host)."""
    mac = info.get("mac") or info.get("factory_mac") or info.get("lan_mac")
    if mac:
        return dr.format_mac(str(mac))
    return str(host).lower()


class GlinetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for GL.iNet."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: host + admin password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            password = user_input[CONF_PASSWORD]
            try:
                info = await _validate(self.hass, host, password)
            except GlinetAuthError:
                errors["base"] = "invalid_auth"
            except GlinetConnectionError:
                errors["base"] = "cannot_connect"
            except GlinetError:
                _LOGGER.exception("Unexpected error validating GL.iNet router")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_unique_id_from_info(info, host))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_title_from_info(info, host),
                    data={CONF_HOST: host, CONF_PASSWORD: password},
                    options={CONF_DEVICE_TRACKER_MODE: TRACKER_MODE_SELECTED},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                    vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-authentication with a new password."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()

        if user_input is not None:
            host = reauth_entry.data[CONF_HOST]
            password = user_input[CONF_PASSWORD]
            try:
                info = await _validate(self.hass, host, password)
            except GlinetAuthError:
                errors["base"] = "invalid_auth"
            except GlinetConnectionError:
                errors["base"] = "cannot_connect"
            except GlinetError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_unique_id_from_info(info, host))
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the router's host and/or admin password."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = user_input[CONF_HOST]
            password = user_input[CONF_PASSWORD]
            try:
                info = await _validate(self.hass, host, password)
            except GlinetAuthError:
                errors["base"] = "invalid_auth"
            except GlinetConnectionError:
                errors["base"] = "cannot_connect"
            except GlinetError:
                _LOGGER.exception("Unexpected error validating GL.iNet router")
                errors["base"] = "unknown"
            else:
                new_uid = _unique_id_from_info(info, host)
                old_uid = entry.unique_id
                legacy = (
                    old_uid is None
                    or old_uid == str(entry.data.get(CONF_HOST, "")).lower()
                    or dr.format_mac(old_uid) != old_uid
                    or len(old_uid) != 17
                )
                await self.async_set_unique_id(new_uid)
                if legacy:
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=new_uid,
                        data_updates={CONF_HOST: host, CONF_PASSWORD: password},
                    )
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_HOST: host, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_HOST, default=entry.data.get(CONF_HOST, DEFAULT_HOST)
                    ): str,
                    vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GlinetOptionsFlow:
        """Return the options flow handler."""
        return GlinetOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the supported subentry types (tracked clients)."""
        return {SUBENTRY_TRACKED_CLIENT: TrackedClientSubentryFlow}


def _interval_selector(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


class GlinetOptionsFlow(OptionsFlowWithReload):
    """Handle GL.iNet options (poll intervals, device tracker); reloads on save."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_CONFIG_SCAN_INTERVAL: int(user_input[CONF_CONFIG_SCAN_INTERVAL]),
                    CONF_DEVICE_TRACKER_MODE: user_input[CONF_DEVICE_TRACKER_MODE],
                },
            )

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): _interval_selector(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL),
                    vol.Required(
                        CONF_CONFIG_SCAN_INTERVAL,
                        default=opts.get(
                            CONF_CONFIG_SCAN_INTERVAL, DEFAULT_CONFIG_SCAN_INTERVAL
                        ),
                    ): _interval_selector(
                        MIN_CONFIG_SCAN_INTERVAL, MAX_CONFIG_SCAN_INTERVAL
                    ),
                    vol.Required(
                        CONF_DEVICE_TRACKER_MODE,
                        default=parsers.device_tracker_mode(opts),
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=list(TRACKER_MODES),
                            mode=SelectSelectorMode.DROPDOWN,
                            translation_key=CONF_DEVICE_TRACKER_MODE,
                        )
                    ),
                }
            ),
        )


class TrackedClientSubentryFlow(ConfigSubentryFlow):
    """Add a router client as a tracked device (one subentry per client)."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick a seen client, or type a MAC address."""
        entry = self._get_entry()
        errors: dict[str, str] = {}
        taken = {sub.unique_id for sub in entry.subentries.values()}

        coordinator = getattr(entry, "runtime_data", None)
        clients = ((getattr(coordinator, "data", None) or {}).get("clients")) or []
        names: dict[str, str] = {}
        for client in clients:
            mac = parsers.normalize_mac(parsers.client_mac(client))
            if mac and mac not in taken:
                names[mac] = parsers.client_name(client) or ""

        if user_input is not None:
            mac = parsers.normalize_mac(user_input["mac"])
            if mac is None:
                errors["mac"] = "invalid_mac"
            else:
                if mac in taken:
                    return self.async_abort(reason="already_configured")
                name = names.get(mac, "")
                return self.async_create_entry(
                    title=name or mac,
                    data={"mac": mac, "name": name},
                    unique_id=mac,
                )

        options = [
            SelectOptionDict(value=mac, label=f"{name} ({mac})" if name else mac)
            for mac, name in names.items()
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required("mac"): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                            custom_value=True,
                        )
                    )
                }
            ),
            errors=errors,
        )
