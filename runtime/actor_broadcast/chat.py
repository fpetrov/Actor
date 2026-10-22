"""Optional installed-client Twitch OAuth + public EventSub chat input only.

No chat sending, stream key, OBS credential, account password or file store.
Token persistence is an injected trusted Keychain callback owned by controller.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import ssl
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler

SCOPES = frozenset({"user:read:chat"})


class ChatError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


@dataclass(frozen=True)
class HttpResult:
    status: int
    body: dict = field(repr=False)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ChatError("twitch_redirect_rejected")


async def official_http(method, url, headers=None, form=None, document=None):
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname not in ("id.twitch.tv", "api.twitch.tv")
            or p.username or p.password or p.port not in (None, 443) or p.fragment):
        raise ChatError("twitch_endpoint_rejected")
    if method not in ("GET", "POST"): raise ChatError("twitch_method_rejected")
    h = dict(headers or {})
    if form is not None:
        body = urlencode(form).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    elif document is not None:
        body = json.dumps(document, allow_nan=False).encode(); h["Content-Type"] = "application/json"
    else: body = None
    if body is not None and len(body) > 16384: raise ChatError("twitch_request_bound")

    def run():
        opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
        try:
            response = opener.open(Request(url, data=body, headers=h, method=method), timeout=8)
        except HTTPError as error: response = error
        except (OSError, URLError): raise ChatError("twitch_https_failed") from None
        with response:
            raw = response.read(262145)
            if len(raw) > 262144: raise ChatError("twitch_response_bound")
            try: value = json.loads(raw)
            except (ValueError, UnicodeError): raise ChatError("twitch_response_invalid") from None
            if not isinstance(value, dict): raise ChatError("twitch_response_invalid")
            return HttpResult(response.status, value)
    return await asyncio.to_thread(run)


@dataclass(frozen=True)
class DeviceAuthorization:
    device_code: str = field(repr=False)
    user_code: str = field(repr=False)
    verification_uri: str = field(repr=False)
    interval: int
    expires_at_monotonic: float


@dataclass(frozen=True)
class TokenPair:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at_monotonic: float


@dataclass(frozen=True)
class TwitchIdentity:
    login: str
    user_id: str = field(repr=False)
    client_id: str = field(repr=False)


def _string(value, maximum=4096):
    if not isinstance(value, str) or not 0 < len(value) <= maximum or any(ord(c) < 32 for c in value):
        raise ChatError("twitch_field_invalid")
    return value


class TwitchDeviceAuth:
    def __init__(self, client_id, *, expected_login="fedorr_codex", http=official_http, save_token=None):
        if not re.fullmatch(r"[A-Za-z0-9]{8,128}", client_id): raise ChatError("twitch_client_id_invalid")
        self.client_id = client_id; self.expected_login = expected_login
        self.http = http; self.save_token = save_token; self.token = None; self.identity = None
        self._refresh = asyncio.Lock(); self._closed = False

    async def begin(self):
        if self._closed: raise ChatError("twitch_auth_closed")
        result = await self.http("POST", "https://id.twitch.tv/oauth2/device",
                                 form={"client_id": self.client_id, "scopes": "user:read:chat"})
        if result.status != 200: raise ChatError("twitch_device_request_failed")
        x = result.body; uri = _string(x.get("verification_uri")); p = urlsplit(uri)
        if p.scheme != "https" or p.hostname != "www.twitch.tv" or p.path != "/activate" or p.username or p.password:
            raise ChatError("twitch_verification_uri_invalid")
        interval = x.get("interval"); expires = x.get("expires_in")
        if type(interval) is not int or not 1 <= interval <= 60 or type(expires) is not int or not 1 <= expires <= 1800:
            raise ChatError("twitch_device_lifetime_invalid")
        return DeviceAuthorization(_string(x.get("device_code")), _string(x.get("user_code"), 128),
                                   uri, max(5, interval), time.monotonic() + expires)

    async def complete(self, authorization: DeviceAuthorization):
        interval = authorization.interval
        while not self._closed and time.monotonic() < authorization.expires_at_monotonic:
            await asyncio.sleep(min(interval, max(0, authorization.expires_at_monotonic - time.monotonic())))
            if self._closed or time.monotonic() >= authorization.expires_at_monotonic: break
            result = await self.http("POST", "https://id.twitch.tv/oauth2/token", form={
                "client_id": self.client_id, "device_code": authorization.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
            if self._closed: raise ChatError("twitch_auth_closed")
            if result.status == 200: return await self._accept_token(result.body)
            error = result.body.get("message", result.body.get("error"))
            if error == "authorization_pending": continue
            if error == "slow_down": interval = min(60, interval + 5); continue
            raise ChatError("twitch_device_authorization_rejected")
        raise ChatError("twitch_device_authorization_expired")

    async def _accept_token(self, value):
        expires = value.get("expires_in")
        if type(expires) is not int or not 1 <= expires <= 86400 or value.get("token_type", "").lower() != "bearer" or set(value.get("scope", [])) != SCOPES:
            raise ChatError("twitch_token_shape")
        token = TokenPair(_string(value.get("access_token")), _string(value.get("refresh_token")),
                          time.monotonic() + expires)
        identity = await self.validate(token)
        if self._closed: raise ChatError("twitch_auth_closed")
        if self.save_token is not None:
            try:
                saved = self.save_token(token)
                if hasattr(saved, "__await__"): await saved
            except BaseException:
                # The old rotating refresh token may already be invalid.
                self.token = None; self.identity = None
                raise ChatError("twitch_token_persistence_failed") from None
        self.token = token; self.identity = identity
        return identity

    async def adopt_token(self, token: TokenPair):
        """Trusted Keychain import only; no token-bearing file/argv API."""
        if self._closed or not isinstance(token, TokenPair): raise ChatError("twitch_authorization_missing")
        _string(token.access_token); _string(token.refresh_token)
        self.token = token
        try:
            await self.refresh()
            self.identity = await self.validate(self.token)
            return self.identity
        except BaseException:
            self.token = None; self.identity = None; raise

    async def validate(self, token: TokenPair):
        result = await self.http("GET", "https://id.twitch.tv/oauth2/validate",
                                 headers={"Authorization": "OAuth " + token.access_token})
        value = result.body
        if (result.status != 200 or value.get("client_id") != self.client_id
                or value.get("login") != self.expected_login or set(value.get("scopes", [])) != SCOPES):
            raise ChatError("twitch_authorized_channel_mismatch")
        return TwitchIdentity(self.expected_login, _string(value.get("user_id"), 128), self.client_id)

    async def refresh(self):
        # DCF public refresh tokens rotate and are single-use. Never refresh
        # them concurrently or persist half of a new pair.
        async with self._refresh:
            if self._closed or self.token is None: raise ChatError("twitch_authorization_missing")
            if self.token.expires_at_monotonic > time.monotonic() + 120: return self.identity
            result = await self.http("POST", "https://id.twitch.tv/oauth2/token", form={
                "client_id": self.client_id, "grant_type": "refresh_token", "refresh_token": self.token.refresh_token})
            if result.status != 200: raise ChatError("twitch_refresh_rejected")
            return await self._accept_token(result.body)

    def close(self): self._closed = True


@dataclass(frozen=True)
class PublicChatMessage:
    id: str
    sender: str
    text: str
    received_at_monotonic: float


def clean_public_chat(text):
    if not isinstance(text, str) or len(text.encode("utf-8")) > 2048: return None
    text = "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf", "Cs"))
    text = re.sub(r"https?://\S+|www\.\S+", "[ссылка]", text, flags=re.I)
    text = re.sub(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b|\b\+?[\d ()-]{8,}\d\b", "[скрыто]", text)
    text = " ".join(text.split())
    if not text or len(text.encode("utf-8")) > 512: return None
    return text


class TwitchChatSource:
    def __init__(self, auth: TwitchDeviceAuth, *, connect=None, on_status=None):
        self.auth = auth; self._connect = connect; self.on_status = on_status
        self._ws = None; self._closed = False; self._seen = OrderedDict(); self._session = None
        self.metrics = {"received": 0, "duplicates": 0, "filtered": 0, "dedup_capacity_dropped": 0,
                        "reconnects": 0}

    async def _open(self, url):
        p = urlsplit(url)
        if p.scheme != "wss" or p.hostname != "eventsub.wss.twitch.tv" or p.path != "/ws" or p.username or p.password or p.port not in (None, 443) or p.fragment:
            raise ChatError("twitch_eventsub_endpoint_rejected")
        connector = self._connect
        if connector is None:
            try: from websockets.asyncio.client import connect as connector
            except ImportError: raise ChatError("broadcast_websockets_dependency_missing") from None
        return await connector(url, proxy=None, compression=None, max_size=65536, max_queue=16,
                               open_timeout=8, close_timeout=2, ping_interval=10, ping_timeout=5)

    async def _packet(self, timeout, *, ws=None):
        raw = await asyncio.wait_for((ws or self._ws).recv(), timeout)
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536: raise ChatError("twitch_eventsub_bound")
        x = json.loads(raw)
        if not isinstance(x, dict) or not isinstance(x.get("metadata"), dict) or not isinstance(x.get("payload"), dict):
            raise ChatError("twitch_eventsub_shape")
        return x

    async def _welcome(self, url, *, subscribe):
        previous = self._ws; candidate = await self._open(url)
        try:
            packet = await self._packet(10, ws=candidate)
            if packet["metadata"].get("message_type") != "session_welcome": raise ChatError("twitch_welcome_required")
            session = packet["payload"].get("session", {}); session_id = _string(session.get("id"), 256)
            timeout = session.get("keepalive_timeout_seconds")
            if type(timeout) is not int or not 1 <= timeout <= 600: raise ChatError("twitch_keepalive_invalid")
        except BaseException:
            await candidate.close(); raise
        self._ws = candidate; self._session = session_id
        # Twitch migrates subscriptions on a reconnect URL. Keep the original
        # connection until the replacement's welcome is actually received.
        if previous is not None: await asyncio.wait_for(previous.close(), 3)
        if subscribe:
            await self.auth.refresh()
            if self.auth.token is None or self.auth.identity is None: raise ChatError("twitch_authorization_missing")
            identity = self.auth.identity
            result = await self.auth.http("POST", "https://api.twitch.tv/helix/eventsub/subscriptions",
                headers={"Client-ID": self.auth.client_id, "Authorization": "Bearer " + self.auth.token.access_token},
                document={"type": "channel.chat.message", "version": "1",
                    "condition": {"broadcaster_user_id": identity.user_id, "user_id": identity.user_id},
                    "transport": {"method": "websocket", "session_id": self._session}})
            if result.status != 202: raise ChatError("twitch_subscription_rejected")
        return timeout + 2

    def _notification(self, packet):
        meta = packet["metadata"]
        if meta.get("subscription_type") != "channel.chat.message" or meta.get("subscription_version") != "1":
            raise ChatError("twitch_subscription_type_rejected")
        event = packet["payload"].get("event", {}); identity = self.auth.identity
        if identity is None or event.get("broadcaster_user_id") != identity.user_id:
            raise ChatError("twitch_channel_mismatch")
        message_id = _string(event.get("message_id"), 256)
        now = time.monotonic()
        while self._seen and next(iter(self._seen.values())) < now - 600: self._seen.popitem(last=False)
        if message_id in self._seen: self.metrics["duplicates"] += 1; return None
        if len(self._seen) >= 2048: self.metrics["dedup_capacity_dropped"] += 1; return None
        self._seen[message_id] = now
        stamp = meta.get("message_timestamp")
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None: raise ValueError
            timestamp = parsed.timestamp()
        except (ValueError, TypeError, AttributeError): raise ChatError("twitch_timestamp_invalid") from None
        if not -10 <= time.time() - timestamp <= 120: self.metrics["filtered"] += 1; return None
        text = clean_public_chat(event.get("message", {}).get("text"))
        sender = clean_public_chat(event.get("chatter_user_name"))
        if text is None or sender is None or len(sender.encode("utf-8")) > 128:
            self.metrics["filtered"] += 1; return None
        self.metrics["received"] += 1
        return PublicChatMessage(message_id, sender, text, now)

    async def messages(self):
        attempts = 0; reconnect = None
        while not self._closed:
            try:
                if self.auth.token is None: raise ChatError("twitch_authorization_missing")
                await self.auth.refresh()
                self.auth.identity = await self.auth.validate(self.auth.token)
                timeout = await self._welcome(reconnect or "wss://eventsub.wss.twitch.tv/ws", subscribe=reconnect is None)
                reconnect = None
                validated_at = time.monotonic()
                if self.on_status is not None: self.on_status("connected")
                while not self._closed:
                    packet = await self._packet(timeout)
                    if time.monotonic() - validated_at >= 3600:
                        await self.auth.refresh()
                        self.auth.identity = await self.auth.validate(self.auth.token)
                        validated_at = time.monotonic()
                    kind = packet["metadata"].get("message_type")
                    if kind == "session_keepalive": continue
                    if kind == "session_reconnect":
                        url = _string(packet["payload"].get("session", {}).get("reconnect_url"), 4096)
                        timeout = await self._welcome(url, subscribe=False)
                        self.metrics["reconnects"] += 1; continue
                    if kind == "revocation": raise ChatError("twitch_authorization_revoked")
                    if kind != "notification": raise ChatError("twitch_unexpected_message")
                    message = self._notification(packet)
                    if message is not None: yield message
            except asyncio.CancelledError: raise
            except ChatError as error:
                if error.code in {"twitch_authorization_revoked", "twitch_channel_mismatch", "twitch_authorized_channel_mismatch",
                                  "twitch_eventsub_endpoint_rejected", "twitch_subscription_type_rejected"}:
                    if self.on_status is not None: self.on_status(error.code)
                    raise
                attempts += 1
                if attempts > 3: raise ChatError("twitch_retries_exhausted") from None
                if self.on_status is not None: self.on_status("reconnecting")
                await asyncio.sleep(min(4, 2 ** (attempts - 1)))
            except Exception:
                attempts += 1
                if attempts > 3: raise ChatError("twitch_retries_exhausted") from None
                await asyncio.sleep(min(4, 2 ** (attempts - 1)))
            finally:
                if self._ws is not None:
                    try: await asyncio.wait_for(self._ws.close(), 3)
                    except BaseException: pass
                    self._ws = None
            if reconnect is not None: self.metrics["reconnects"] += 1

    async def close(self):
        self._closed = True
        if self._ws is not None:
            try: await asyncio.wait_for(self._ws.close(), 3)
            except BaseException: pass
