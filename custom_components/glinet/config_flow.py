"""Config flow for the GL.iNet integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GlinetApiClient, GlinetAuthError, GlinetConnectionError, GlinetError
from .const import (
    CONF_CONFIG_SCAN_INTERVAL,
    CONF_ENABLE_DEVICE_TRACKER,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    DEFAULT_CONFIG_SCAN_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MANUFACTURER,
    MAX_CONFIG_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_CONFIG_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


async def _validate(
    hass, host: str, password: str, username: str = DEFAULT_USERNAME
) -> dict[str, Any]:
    """Validate credentials, returning device info. Raises on failure."""
    session = async_get_clientsession(hass)
    client = GlinetApiClient(
        session=session, host=host, password=password, username=username
    )
    try:
        return await client.test_connection()
    finally:
        await client.async_logout()


def _title_from_info(info: dict[str, Any], host: str) -> str:
    """Build an entry title from device info."""
    board_model = (info.get("board_info") or {}).get("model")
    if board_model:  # e.g. "GL.iNet GL-MT3000"
        return board_model
    model = info.get("model") or info.get("product")
    if model:
        return f"{MANUFACTURER} {model}"
    return f"{MANUFACTURER} {host}"


def _has_mac(info: dict[str, Any]) -> bool:
    return bool(info.get("mac") or info.get("factory_mac") or info.get("lan_mac"))


def _unique_id_from_info(info: dict[str, Any], host: str) -> str:
    """Pick a stable unique id (router MAC, falling back to host)."""
    mac = info.get("mac") or info.get("factory_mac") or info.get("lan_mac")
    return str(mac or host).lower()


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
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating GL.iNet router")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(_unique_id_from_info(info, host))
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=_title_from_info(info, host),
                    data={CONF_HOST: host, CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                    vol.Required(CONF_PASSWORD): str,
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
                info = await _validate(
                    self.hass,
                    host,
                    password,
                    reauth_entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                )
            except GlinetAuthError:
                errors["base"] = "invalid_auth"
            except GlinetConnectionError:
                errors["base"] = "cannot_connect"
            except GlinetError:
                _LOGGER.exception("Unexpected error re-authenticating GL.iNet router")
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error re-authenticating GL.iNet router")
                errors["base"] = "unknown"
            else:
                if _has_mac(info):
                    await self.async_set_unique_id(_unique_id_from_info(info, host))
                    self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host / username / password of an existing entry."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            host = user_input[CONF_HOST]
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            try:
                info = await _validate(self.hass, host, password, username)
            except GlinetAuthError:
                errors["base"] = "invalid_auth"
            except GlinetConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating GL.iNet router")
                errors["base"] = "unknown"
            else:
                if _has_mac(info):
                    await self.async_set_unique_id(_unique_id_from_info(info, host))
                    self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_HOST: host,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                    vol.Required(
                        CONF_USERNAME,
                        default=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> GlinetOptionsFlow:
        """Return the options flow handler."""
        return GlinetOptionsFlow()


class GlinetOptionsFlow(OptionsFlowWithReload):
    """Handle GL.iNet options (poll interval, device tracker)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
                    vol.Required(
                        CONF_CONFIG_SCAN_INTERVAL,
                        default=opts.get(
                            CONF_CONFIG_SCAN_INTERVAL, DEFAULT_CONFIG_SCAN_INTERVAL
                        ),
                    ): vol.All(
                        int,
                        vol.Range(min=MIN_CONFIG_SCAN_INTERVAL, max=MAX_CONFIG_SCAN_INTERVAL),
                    ),
                    vol.Required(
                        CONF_ENABLE_DEVICE_TRACKER,
                        default=opts.get(CONF_ENABLE_DEVICE_TRACKER, True),
                    ): bool,
                }
            ),
        )
