"""Explicit OAuth provisioning with fake HTTPS and Keychain, never real accounts."""
import asyncio
import json
import time
from types import SimpleNamespace as NS

import pytest

from actor_broadcast import chat
from actor_broadcast.chat import ChatError, HttpResult, TwitchDeviceAuth
from actor_broadcast.twitch_login import ACTIVATION_URL, twitch_login

CLIENT = "publicClient123"
ACCESS = "access_SENTINEL_opaque"
REFRESH = "refresh_SENTINEL_opaque"
DEVICE = "device_SENTINEL_opaque"


class FakeHttps:
    def __init__(self):
        self.calls = []
        self.login = "fedorr_codex"
        self.scopes = ["user:read:chat"]
        self.token_status = 200
        self.token_error = "access_denied"
        self.poll_errors = []
        self.uri = ACTIVATION_URL + "?device-code=USER-CODE"

    async def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/device"):
            return HttpResult(200, {"device_code": DEVICE, "user_code": "USER-CODE",
                "verification_uri": self.uri, "interval": 5, "expires_in": 600})
        if url.endswith("/token"):
            if self.poll_errors:
                return HttpResult(400, {"message": self.poll_errors.pop(0)})
            if self.token_status != 200:
                return HttpResult(self.token_status, {"message": self.token_error})
            return HttpResult(200, {"access_token": ACCESS, "refresh_token": REFRESH,
                "expires_in": 3600, "token_type": "bearer", "scope": self.scopes})
        assert url.endswith("/validate")
        return HttpResult(200, {"client_id": CLIENT, "login": self.login,
            "user_id": "private-user-id", "scopes": self.scopes})


class Store:
    def __init__(self): self.saved = []; self.fail = False
    def save_twitch_token_pair(self, **pair):
        if self.fail: raise RuntimeError("private_Keychain_details_SENTINEL")
        self.saved.append(pair)


@pytest.fixture
def flow(monkeypatch):
    api, store, auths, shown, sleeps = FakeHttps(), Store(), [], [], []
    async def no_delay(seconds): sleeps.append(seconds)
    # Replace this module's dependency, not the event loop's actual clock or
    # asyncio.sleep; production polling is covered by the bounded flow tests.
    monkeypatch.setattr(chat, "asyncio", NS(Lock=asyncio.Lock, sleep=no_delay))
    def factory(client_id, **kwargs):
        auth = TwitchDeviceAuth(client_id, http=api, **kwargs)
        auths.append(auth)
        return auth
    return NS(api=api, store=store, auths=auths, shown=shown, sleeps=sleeps,
        options={"store": store, "auth_factory": factory,
                 "show_activation": lambda *args: shown.append(args)})


async def test_login_uses_exact_public_device_scope_validates_before_atomic_store_and_redacts(flow):
    report = await twitch_login(CLIENT, **flow.options)
    assert flow.shown == [(ACTIVATION_URL, "USER-CODE")]
    assert [url.rsplit("/", 1)[-1] for _, url, _ in flow.api.calls] == ["device", "token", "validate"]
    assert flow.api.calls[0][2]["form"] == {"client_id": CLIENT, "scopes": "user:read:chat"}
    assert flow.api.calls[1][2]["form"] == {"client_id": CLIENT, "device_code": DEVICE,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code"}
    assert len(flow.store.saved) == 1
    saved = flow.store.saved[0]
    assert saved["access_token"] == ACCESS and saved["refresh_token"] == REFRESH
    assert time.time() + 3500 < saved["expires_at_unix"] <= time.time() + 3600
    assert report == {"authenticated": True, "token_saved": True, "channel": "fedorr_codex",
        "scopes": ["user:read:chat"], "eventsub_connected": False, "model_calls": 0}
    assert flow.auths[0]._closed
    assert not any(value in json.dumps(report) for value in (ACCESS, REFRESH, DEVICE, "private-user-id", "USER-CODE"))
    assert not any("client_secret" in call[2].get("form", {}) for call in flow.api.calls)


@pytest.mark.parametrize("client_id", ["", "bad id", "x" * 129, None])
async def test_missing_or_invalid_public_client_id_fails_before_network_or_store(client_id):
    def forbidden(*_, **__): pytest.fail("invalid client ID touched network/Keychain")
    with pytest.raises(ChatError, match="twitch_client_id_required"):
        await twitch_login(client_id, auth_factory=forbidden)


@pytest.mark.parametrize("fault", ["wrong_channel", "extra_scope", "denied"])
async def test_rejected_or_overprivileged_authorization_never_saves(flow, fault):
    if fault == "wrong_channel": flow.api.login = "foreign_channel"
    elif fault == "extra_scope": flow.api.scopes.append("user:write:chat")
    else: flow.api.token_status = 400
    with pytest.raises(ChatError): await twitch_login(CLIENT, **flow.options)
    assert flow.store.saved == [] and flow.auths[0]._closed


@pytest.mark.parametrize("uri", [ACTIVATION_URL + ":not-a-port", "https://www.twitch.tv:8443/activate",
    "https://www.twitch.tv:invalid/activate", ACTIVATION_URL + "#private-fragment"])
async def test_activation_output_rejects_untrusted_uri_geometry(flow, uri):
    flow.api.uri = uri
    with pytest.raises(ChatError): await twitch_login(CLIENT, **flow.options)
    assert flow.shown == [] and flow.store.saved == [] and flow.auths[0]._closed


async def test_pending_and_slow_down_poll_at_server_intervals_before_single_save(flow):
    flow.api.poll_errors = ["authorization_pending", "slow_down"]
    assert (await twitch_login(CLIENT, **flow.options))["authenticated"]
    assert flow.sleeps == [5, 5, 10]
    assert len(flow.store.saved) == 1


async def test_keychain_failure_is_safe_and_never_claims_authenticated(flow):
    flow.store.fail = True
    with pytest.raises(ChatError, match="^twitch_token_persistence_failed$") as error:
        await twitch_login(CLIENT, **flow.options)
    assert "SENTINEL" not in str(error.value) and flow.auths[0]._closed


async def test_cancellation_during_poll_closes_auth_without_keychain(flow):
    polling = asyncio.Event()
    class WaitingAuth(TwitchDeviceAuth):
        async def complete(self, authorization):
            polling.set(); await asyncio.Event().wait()
    def factory(client_id, **kwargs):
        auth = WaitingAuth(client_id, http=flow.api, **kwargs)
        flow.auths.append(auth); return auth
    options = dict(flow.options, auth_factory=factory)
    task = asyncio.create_task(twitch_login(CLIENT, **options))
    await asyncio.wait_for(polling.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert flow.store.saved == [] and flow.auths[0]._closed


async def test_poll_deadline_is_bounded_and_expired_code_cannot_reach_store(flow, monkeypatch):
    from actor_broadcast import twitch_login as module
    original = asyncio.wait_for
    waits = []
    async def deadline(awaitable, timeout):
        waits.append(timeout)
        if len(waits) == 2:
            awaitable.close(); raise asyncio.TimeoutError
        return await original(awaitable, timeout)
    monkeypatch.setattr(module, "asyncio", NS(wait_for=deadline, to_thread=asyncio.to_thread,
        TimeoutError=asyncio.TimeoutError, CancelledError=asyncio.CancelledError))
    with pytest.raises(ChatError, match="^twitch_login_timeout$"):
        await twitch_login(CLIENT, **flow.options)
    assert waits[0] == 10 and 600 < waits[1] <= 610
    assert flow.store.saved == [] and flow.auths[0]._closed


async def test_default_prompt_only_prints_fixed_activation_and_user_code_to_stderr(flow, capsys):
    options = dict(flow.options); options.pop("show_activation")
    await twitch_login(CLIENT, **options)
    output = capsys.readouterr()
    assert output.out == "" and ACTIVATION_URL in output.err and "USER-CODE" in output.err
    assert not any(value in output.err for value in (ACCESS, REFRESH, DEVICE, "private-user-id"))
