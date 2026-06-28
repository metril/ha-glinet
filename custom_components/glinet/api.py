"""Async JSON-RPC client for GL.iNet firmware-4.x routers.

Firmware 4.x exposes a LuCI-style JSON-RPC 2.0 API at ``POST http://<host>/rpc``.
Authentication is a three-step challenge/response:

1. ``challenge`` returns ``{alg, salt, nonce, hash-method?}`` (nonce TTL ~1s).
2. The client computes ``crypt(password, $alg$salt)`` then
   ``HASH(f"{user}:{crypt}:{nonce}")`` where HASH defaults to MD5 but honours the
   advertised ``hash-method`` (firmware 4.8+ may use sha256/sha512).
3. ``login`` returns an ``sid`` session token (~5 min TTL) which is passed as the
   first element of ``params`` on every subsequent ``call``.

The client transparently re-authenticates once when the router reports an expired
or invalid ``sid``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import aiohttp

from .const import (
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_USERNAME,
    SVC_SYSTEM,
)
from .crypt_util import crypt_password

_LOGGER = logging.getLogger(__name__)

# JSON-RPC error codes the router returns for an expired/invalid session.
_ACCESS_DENIED_CODES = {-32000, -32002}
_ACCESS_DENIED_TEXT = ("access denied", "no permission", "invalid session", "login")


class GlinetError(Exception):
    """Base error for the GL.iNet client."""


class GlinetAuthError(GlinetError):
    """Raised when authentication fails (bad password / denied)."""


class GlinetConnectionError(GlinetError):
    """Raised when the router cannot be reached."""


class GlinetApiError(GlinetError):
    """Raised when the router returns an unexpected RPC error."""


class GlinetApiClient:
    """Client for the GL.iNet firmware-4.x JSON-RPC API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        password: str,
        username: str = DEFAULT_USERNAME,
        http_timeout: int = DEFAULT_HTTP_TIMEOUT,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._host = host.strip().rstrip("/")
        self._username = username
        self._password = password
        self._http_timeout = http_timeout
        self._sid: str | None = None
        self._rpc_id = 0
        self._auth_lock = asyncio.Lock()

    @property
    def _url(self) -> str:
        """Return the JSON-RPC endpoint URL."""
        host = self._host
        if not host.startswith(("http://", "https://")):
            host = f"http://{host}"
        return f"{host}/rpc"

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    async def _rpc(self, method: str, params: Any) -> Any:
        """Send a raw JSON-RPC request and return the ``result`` payload."""
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        try:
            async with self._session.post(
                self._url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=self._http_timeout),
            ) as resp:
                if resp.status != 200:
                    raise GlinetApiError(f"HTTP {resp.status} from router")
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as err:
            raise GlinetConnectionError(f"Failed to reach router: {err}") from err
        except asyncio.TimeoutError as err:
            raise GlinetConnectionError("Timed out talking to router") from err

        if "error" in data and data["error"] is not None:
            err = data["error"]
            code = err.get("code")
            message = str(err.get("message", "")).lower()
            if code in _ACCESS_DENIED_CODES or any(
                t in message for t in _ACCESS_DENIED_TEXT
            ):
                raise GlinetAuthError(err.get("message", "Access denied"))
            raise GlinetApiError(f"RPC error {code}: {err.get('message')}")

        return data.get("result")

    async def _login(self) -> None:
        """Perform the challenge/login handshake and store the sid."""
        # Step 1: challenge (nonce is short-lived, so do this immediately before login).
        challenge = await self._rpc("challenge", {"username": self._username})
        if not isinstance(challenge, dict):
            raise GlinetAuthError("Unexpected challenge response")

        alg = int(challenge.get("alg", 1))
        salt = challenge.get("salt", "")
        nonce = challenge.get("nonce", "")
        hash_method = (challenge.get("hash-method") or "md5").lower()
        if hash_method not in ("md5", "sha256", "sha512"):
            hash_method = "md5"

        # Step 2: derive the login hash.
        cipher = crypt_password(self._password, alg, salt)
        hasher = getattr(hashlib, hash_method)
        login_hash = hasher(
            f"{self._username}:{cipher}:{nonce}".encode()
        ).hexdigest()

        # Step 3: login.
        result = await self._rpc(
            "login", {"username": self._username, "hash": login_hash}
        )
        if not isinstance(result, dict) or "sid" not in result:
            raise GlinetAuthError("Login did not return a session id")
        self._sid = result["sid"]
        _LOGGER.debug("GL.iNet login succeeded for %s", self._host)

    async def async_login(self) -> None:
        """Ensure there is a valid session, logging in if needed."""
        async with self._auth_lock:
            if self._sid is None:
                await self._login()

    async def call(self, service: str, method: str, params: dict | None = None) -> Any:
        """Call ``service.method`` with auto-login and one re-auth retry."""
        if self._sid is None:
            await self.async_login()

        try:
            return await self._rpc("call", [self._sid, service, method, params or {}])
        except GlinetAuthError:
            # Session likely expired — re-authenticate once and retry.
            _LOGGER.debug("Session expired, re-authenticating")
            async with self._auth_lock:
                self._sid = None
                await self._login()
            return await self._rpc("call", [self._sid, service, method, params or {}])

    async def async_logout(self) -> None:
        """Best-effort logout to release the session."""
        if self._sid is None:
            return
        try:
            await self._rpc("logout", {"sid": self._sid})
        except GlinetError:
            pass
        finally:
            self._sid = None

    # --- Convenience reads ---------------------------------------------------

    async def get_info(self) -> dict[str, Any]:
        """Return ``system.get_info`` (model, firmware, mac, feature flags)."""
        result = await self.call(SVC_SYSTEM, "get_info")
        return result if isinstance(result, dict) else {}

    async def get_status(self) -> dict[str, Any]:
        """Return the aggregate ``system.get_status`` payload."""
        result = await self.call(SVC_SYSTEM, "get_status")
        return result if isinstance(result, dict) else {}

    async def get_clients(self) -> list[dict[str, Any]]:
        """Return the connected-client list from ``clients.get_list``."""
        result = await self.call("clients", "get_list")
        if isinstance(result, dict):
            # Some firmware wraps the list under a key.
            for key in ("clients", "list", "data"):
                if isinstance(result.get(key), list):
                    return result[key]
            return []
        return result if isinstance(result, list) else []

    async def test_connection(self) -> dict[str, Any]:
        """Validate credentials and return device info. Raises on failure."""
        await self.async_login()
        return await self.get_info()
