"""Official endpoint/API shapes with fakes. No OAuth/network/account access."""
import asyncio
from datetime import datetime, timezone
import json
import time

import pytest

from actor_broadcast.chat import (ChatError, HttpResult, TokenPair, TwitchChatSource,
    TwitchDeviceAuth, TwitchIdentity, clean_public_chat, official_http)


CLIENT = "publicClient123"


class Api:
    def __init__(self): self.calls = []; self.refreshes = 0; self.login = "fedorr_codex"; self.scopes = ["user:read:chat"]
    async def __call__(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/validate"):
            return HttpResult(200, {"login": self.login, "client_id": CLIENT, "user_id": "channel-id", "scopes": self.scopes})
        if url.endswith("/token"):
            self.refreshes += 1
            return HttpResult(200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 14400,
                                    "token_type": "bearer", "scope": self.scopes})
        if url.endswith("/device"):
            return HttpResult(200, {"device_code": "device-secret", "user_code": "USERCODE", "verification_uri": "https://www.twitch.tv/activate",
                                    "interval": 5, "expires_in": 1800})
        if url.endswith("/subscriptions"): return HttpResult(202, {})
        raise AssertionError(url)


def auth(api=None):
    api = api or Api(); result = TwitchDeviceAuth(CLIENT, http=api)
    result.token = TokenPair("old-access", "old-refresh", time.monotonic() + 1000)
    result.identity = TwitchIdentity("fedorr_codex", "channel-id", CLIENT)
    return result, api


async def test_device_code_scope_and_secret_records_never_repr_tokens():
    a, api = auth(); device = await a.begin()
    assert api.calls[-1][2]["form"] == {"client_id": CLIENT, "scopes": "user:read:chat"}
    assert "device-secret" not in repr(device) and "USERCODE" not in repr(device)
    assert "old-access" not in repr(a.token) and "old-refresh" not in repr(a.token)


async def test_single_use_refresh_serialized_and_new_pair_persisted_atomically():
    a, api = auth(); saved = []; a.save_token = saved.append
    a.token = TokenPair("old-access", "old-refresh", 0)
    await asyncio.gather(a.refresh(), a.refresh(), a.refresh())
    assert api.refreshes == 1 and len(saved) == 1
    assert saved[0].access_token == "new-access" and saved[0].refresh_token == "new-refresh"
    assert not any("client_secret" in call[2].get("form", {}) for call in api.calls)


async def test_refresh_persistence_failure_revokes_in_memory_old_single_use_pair():
    a, _ = auth(); a.token = TokenPair("old-access", "old-refresh", 0)
    def fail(_): raise RuntimeError("private store detail")
    a.save_token = fail
    with pytest.raises(ChatError, match="persistence_failed"): await a.refresh()
    assert a.token is None and a.identity is None


@pytest.mark.parametrize("login,scopes", [("foreign_channel", ["user:read:chat"]), ("fedorr_codex", ["user:read:chat", "user:write:chat"])])
async def test_foreign_identity_or_extra_privilege_rejected(login, scopes):
    a, api = auth(); api.login = login; api.scopes = scopes
    with pytest.raises(ChatError): await a.adopt_token(a.token)
    assert a.token is None and a.identity is None


def notification(identity="message", channel="channel-id", stamp=None):
    return {"metadata": {"message_type": "notification", "subscription_type": "channel.chat.message", "subscription_version": "1",
            "message_timestamp": stamp or datetime.now(timezone.utc).isoformat()}, "payload": {"event": {
                "broadcaster_user_id": channel, "message_id": identity, "chatter_user_name": "Viewer",
                "message": {"text": "Привет https://example.org user@example.org"}}}}


def test_public_chat_is_untrusted_sanitized_bounded_dedup_and_scoped():
    a, _ = auth(); source = TwitchChatSource(a)
    message = source._notification(notification())
    assert message.text == "Привет [ссылка] [скрыто]"
    assert source._notification(notification()) is None and source.metrics["duplicates"] == 1
    with pytest.raises(ChatError, match="channel_mismatch"): source._notification(notification("new", channel="foreign"))
    with pytest.raises(ChatError, match="timestamp"): source._notification(notification("new", stamp="2026-01-01T12:00:00"))
    assert clean_public_chat("\u202eHi\x00") == "Hi"
    assert clean_public_chat("Я" * 300) is None


def test_chat_flood_never_evicts_recent_dedup_and_redelivers_old_message():
    a, _ = auth(); source = TwitchChatSource(a)
    for i in range(2048): assert source._notification(notification(str(i))) is not None
    assert source._notification(notification("overflow")) is None
    assert len(source._seen) == 2048 and source.metrics["dedup_capacity_dropped"] == 1
    assert source._notification(notification("0")) is None


async def test_eventsub_reconnect_welcome_precedes_closing_old_and_no_resubscribe():
    a, api = auth(); history = []
    class Ws:
        def __init__(self, name): self.name = name; self.messages = asyncio.Queue(); self.closed = False
        async def recv(self):
            value = await self.messages.get(); history.append(("received", self.name)); return json.dumps(value)
        async def close(self): self.closed = True; history.append(("closed", self.name))
    old, new = Ws("old"), Ws("new")
    def welcome(identity): return {"metadata": {"message_type": "session_welcome"}, "payload": {"session": {"id": identity, "keepalive_timeout_seconds": 10}}}
    old.messages.put_nowait(welcome("session-old"))
    old.messages.put_nowait({"metadata": {"message_type": "session_reconnect"}, "payload": {"session": {"reconnect_url": "wss://eventsub.wss.twitch.tv/ws?session=reconnect"}}})
    new.messages.put_nowait(welcome("session-new")); new.messages.put_nowait(notification())
    async def connect(url, **kwargs):
        assert kwargs["proxy"] is None
        return new if "session=" in url else old
    source = TwitchChatSource(a, connect=connect); iterator = source.messages()
    message = await anext(iterator)
    assert message.id == "message" and old.closed and not new.closed
    assert history.index(("received", "new")) < history.index(("closed", "old"))
    assert len([x for x in api.calls if x[1].endswith("/subscriptions")]) == 1
    await iterator.aclose(); assert new.closed


async def test_nonofficial_https_and_websocket_redirect_endpoints_rejected_before_network():
    for url in ("http://id.twitch.tv/oauth2/token", "https://evil.invalid/", "https://x:y@id.twitch.tv/oauth2/token"):
        with pytest.raises(ChatError, match="endpoint"): await official_http("GET", url)
    a, _ = auth(); source = TwitchChatSource(a)
    for url in ("ws://eventsub.wss.twitch.tv/ws", "wss://evil.invalid/ws", "wss://eventsub.wss.twitch.tv/ws#token"):
        with pytest.raises(ChatError, match="endpoint"): await source._open(url)
