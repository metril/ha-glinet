"""Tests for the GL.iNet JSON-RPC client (auth, call wrapper, re-auth, errors)."""

from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import MagicMock

import pytest

from custom_components.glinet.api import (
    GlinetApiClient,
    GlinetApiError,
    GlinetAuthError,
)
from custom_components.glinet.crypt_util import crypt_password


class _FakeResp:
    def __init__(self, status: int, body: dict):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self, content_type=None):
        return self._body


class _FakeSession:
    """Returns queued JSON-RPC responses and records sent payloads."""

    def __init__(self, responses: list[dict], status: int = 200):
        self._responses = list(responses)
        self._status = status
        self.requests: list[dict] = []

    def post(self, url, json, timeout=None):
        self.requests.append(json)
        body = self._responses.pop(0)
        return _FakeResp(self._status, body)


def _challenge_body():
    return {"result": {"alg": 1, "salt": "abcd1234", "nonce": "noncenonce", "hash-method": "md5"}}


def _login_body(sid="SID123"):
    return {"result": {"username": "root", "sid": sid}}


@pytest.mark.asyncio
async def test_login_computes_correct_hash():
    session = _FakeSession([_challenge_body(), _login_body()])
    client = GlinetApiClient(session, "192.168.8.1", "secret")
    await client.async_login()

    # challenge then login were sent
    assert session.requests[0]["method"] == "challenge"
    assert session.requests[1]["method"] == "login"

    cipher = crypt_password("secret", 1, "abcd1234")
    expected = hashlib.md5(f"root:{cipher}:noncenonce".encode()).hexdigest()
    assert session.requests[1]["params"]["hash"] == expected


@pytest.mark.asyncio
async def test_login_honours_hash_method_sha256():
    body = {"result": {"alg": 6, "salt": "abcd1234", "nonce": "nnn", "hash-method": "sha256"}}
    session = _FakeSession([body, _login_body()])
    client = GlinetApiClient(session, "host", "pw")
    await client.async_login()

    cipher = crypt_password("pw", 6, "abcd1234")
    expected = hashlib.sha256(f"root:{cipher}:nnn".encode()).hexdigest()
    assert session.requests[1]["params"]["hash"] == expected


@pytest.mark.asyncio
async def test_call_passes_sid_and_service():
    session = _FakeSession(
        [_challenge_body(), _login_body("THESID"), {"result": {"model": "MT3000"}}]
    )
    client = GlinetApiClient(session, "host", "pw")
    result = await client.call("system", "get_info")

    assert result == {"model": "MT3000"}
    call_req = session.requests[-1]
    assert call_req["method"] == "call"
    assert call_req["params"][0] == "THESID"
    assert call_req["params"][1:3] == ["system", "get_info"]


@pytest.mark.asyncio
async def test_expired_sid_triggers_reauth_and_retry():
    session = _FakeSession(
        [
            _challenge_body(),
            _login_body("OLD"),
            {"error": {"code": -32000, "message": "Access denied"}},  # expired sid
            _challenge_body(),
            _login_body("NEW"),
            {"result": "ok"},
        ]
    )
    client = GlinetApiClient(session, "host", "pw")
    result = await client.call("system", "get_status")

    assert result == "ok"
    # final successful call used the refreshed sid
    assert session.requests[-1]["params"][0] == "NEW"


@pytest.mark.asyncio
async def test_non_auth_rpc_error_raises_api_error():
    session = _FakeSession(
        [
            _challenge_body(),
            _login_body(),
            {"error": {"code": -32601, "message": "Method not found"}},
        ]
    )
    client = GlinetApiClient(session, "host", "pw")
    with pytest.raises(GlinetApiError):
        await client.call("bogus", "method")


@pytest.mark.asyncio
async def test_bad_password_raises_auth_error():
    session = _FakeSession(
        [_challenge_body(), {"error": {"code": -32002, "message": "login failed"}}]
    )
    client = GlinetApiClient(session, "host", "pw")
    with pytest.raises(GlinetAuthError):
        await client.async_login()


@pytest.mark.asyncio
async def test_get_clients_unwraps_list():
    session = _FakeSession(
        [
            _challenge_body(),
            _login_body(),
            {"result": {"clients": [{"mac": "aa", "online": True}]}},
        ]
    )
    client = GlinetApiClient(session, "host", "pw")
    clients = await client.get_clients()
    assert clients == [{"mac": "aa", "online": True}]
