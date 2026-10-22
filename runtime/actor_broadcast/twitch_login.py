"""Explicit installed-client Twitch chat provisioning; no OBS/model startup.

Only the operator-facing activation code is displayed. OAuth credentials go
directly to the exact owned Keychain item, never a report, argument or file.
"""
from __future__ import annotations

import asyncio
import math
import re
import sys
import time
from urllib.parse import urlsplit

from .chat import ChatError, SCOPES, TokenPair, TwitchDeviceAuth

ACTIVATION_URL = "https://www.twitch.tv/activate"


def _show_activation(url, code):
    print(f"Open {url} and enter code {code}. Sign in as fedorr_codex; Ctrl-C cancels.",
          file=sys.stderr, flush=True)


async def twitch_login(client_id, *, expected_login="fedorr_codex", store=None,
                       auth_factory=TwitchDeviceAuth, show_activation=None):
    """Authorize read-only chat and persist only after exact identity validation.

    Factories/callbacks are explicit test seams. The production CLI takes its
    public client ID and fixed channel from the existing installation config.
    No token import, browser launch, account password or client secret API.
    """
    if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9]{8,128}", client_id):
        raise ChatError("twitch_client_id_required")
    if expected_login != "fedorr_codex": raise ChatError("twitch_channel_not_configured")
    auth = auth_factory(client_id, expected_login=expected_login)
    try:
        authorization = await asyncio.wait_for(auth.begin(), 10)
        try:
            uri = urlsplit(authorization.verification_uri)
            valid_uri = (uri.scheme == "https" and uri.hostname == "www.twitch.tv" and uri.path == "/activate"
                and not uri.username and not uri.password and uri.port in (None, 443) and not uri.fragment)
        except ValueError:
            valid_uri = False
        if (not valid_uri or not isinstance(authorization.user_code, str)
                or not re.fullmatch(r"[A-Za-z0-9-]{4,128}", authorization.user_code)):
            raise ChatError("twitch_verification_uri_invalid")
        remaining = authorization.expires_at_monotonic - time.monotonic()
        if not math.isfinite(remaining) or not 0 < remaining <= 1800:
            raise ChatError("twitch_device_lifetime_invalid")
        # Discard all server query parameters; only this fixed official URL and
        # the bounded user code reach the private operator terminal.
        (show_activation or _show_activation)(ACTIVATION_URL, authorization.user_code)
        identity = await asyncio.wait_for(auth.complete(authorization), remaining + 10)
        token = auth.token
        if (identity.login != expected_login or identity.client_id != client_id
                or not isinstance(token, TokenPair)):
            raise ChatError("twitch_authorized_channel_mismatch")
        remaining = token.expires_at_monotonic - time.monotonic()
        if not math.isfinite(remaining) or not 0 < remaining <= 86400:
            raise ChatError("twitch_device_authorization_expired")
        if store is None:
            from .secrets import MacOSKeychainSecretStore
            store = MacOSKeychainSecretStore()
        try:
            await asyncio.to_thread(store.save_twitch_token_pair,
                access_token=token.access_token, refresh_token=token.refresh_token,
                expires_at_unix=time.time() + remaining)
        except asyncio.CancelledError: raise
        except Exception:
            raise ChatError("twitch_token_persistence_failed") from None
        return {"authenticated": True, "token_saved": True, "channel": expected_login,
                "scopes": sorted(SCOPES), "eventsub_connected": False, "model_calls": 0}
    except asyncio.TimeoutError:
        raise ChatError("twitch_login_timeout") from None
    finally:
        auth.close()
