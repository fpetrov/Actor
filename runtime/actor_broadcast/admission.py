"""Operator-only observer admission over a direct loopback SSH forward.

No observer bearer, operator key or child environment is a diagnostic object.
The public game API is used only for the non-secret room list.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener


class AdmissionError(RuntimeError):
    def __init__(self, code): self.code = code; super().__init__(code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise AdmissionError("admission_redirect_rejected")


def canonical_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


@dataclass(frozen=True)
class ObserverAdmission:
    lobby_id: str
    room_code: str
    host: str
    port: int
    join_ticket: str = field(repr=False)
    expires_at: str
    broadcast_session_id: str
    observer_id: str
    connection_generation: int
    game_mode: str = "SocialDeduction"

    @classmethod
    def parse(cls, document, *, room_code, session_id, now=None):
        expected = {"lobbyId", "roomCode", "host", "port", "joinTicket", "expiresAt", "gameMode",
                    "broadcastSessionId", "observerId", "connectionGeneration"}
        if not isinstance(document, dict) or set(document) != expected:
            raise AdmissionError("observer_admission_shape")
        x = document
        if (not all(canonical_id(x[k]) for k in ("lobbyId", "broadcastSessionId", "observerId"))
                or x["broadcastSessionId"] != session_id or x["roomCode"] != room_code
                or x["gameMode"] != "SocialDeduction"
                or type(x["port"]) is not int or not 1024 <= x["port"] <= 65535
                or type(x["connectionGeneration"]) is not int or not 1 <= x["connectionGeneration"] <= 1000000
                or not isinstance(x["host"], str) or not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", x["host"])
                or not isinstance(x["joinTicket"], str) or not x["joinTicket"].startswith("observer-v1.")
                or not 32 <= len(x["joinTicket"]) <= 1040 or not x["joinTicket"].isascii()):
            raise AdmissionError("observer_admission_invalid")
        try:
            expiry = datetime.fromisoformat(x["expiresAt"].replace("Z", "+00:00"))
            seconds = (expiry - (now or datetime.now(timezone.utc))).total_seconds()
            if expiry.tzinfo is None or not 0 < seconds <= 65: raise ValueError()
        except (ValueError, TypeError, AttributeError): raise AdmissionError("observer_admission_expired") from None
        return cls(x["lobbyId"], x["roomCode"], x["host"], x["port"], x["joinTicket"], x["expiresAt"],
                   x["broadcastSessionId"], x["observerId"], x["connectionGeneration"])

    def child_environment_value(self):
        return json.dumps({"lobbyId": self.lobby_id, "roomCode": self.room_code, "host": self.host,
            "port": self.port, "joinTicket": self.join_ticket, "expiresAt": self.expires_at,
            "gameMode": self.game_mode, "broadcastSessionId": self.broadcast_session_id,
            "observerId": self.observer_id, "connectionGeneration": self.connection_generation}, separators=(",", ":"))


class AdmissionClient:
    def __init__(self, endpoint, operator_key, *, game_version="0.10.0", protocol_version="4", request=None):
        p = urlsplit(endpoint)
        if (p.scheme != "http" or p.hostname not in ("127.0.0.1", "::1") or not p.port
                or p.username or p.password or p.query or p.fragment or p.path not in ("", "/")):
            raise AdmissionError("admission_direct_loopback_required")
        if not isinstance(operator_key, str) or not 32 <= len(operator_key) <= 512:
            raise AdmissionError("admission_operator_key_required")
        self.endpoint, self._key = endpoint.rstrip("/"), operator_key
        self.game_version, self.protocol_version, self._request = game_version, protocol_version, request or self._http

    async def _http(self, method, path, document=None):
        body = json.dumps(document, separators=(",", ":")).encode() if document is not None else None
        headers = {"X-Actor-Broadcast-Operator": self._key, "Accept": "application/json", "Cache-Control": "no-store"}
        if body is not None: headers["Content-Type"] = "application/json"
        def run():
            opener = build_opener(ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
            try: response = opener.open(Request(self.endpoint + path, data=body, method=method, headers=headers), timeout=8)
            except HTTPError as error: response = error
            except (URLError, OSError): raise AdmissionError("admission_connection_failed") from None
            with response:
                raw = response.read(16385)
                if len(raw) > 16384: raise AdmissionError("admission_response_bound")
                if response.status == 204: return None
                if response.status != 200: raise AdmissionError("observer_admission_rejected")
                try: return json.loads(raw)
                except (UnicodeError, ValueError): raise AdmissionError("admission_response_invalid") from None
        return await asyncio.to_thread(run)

    async def observe(self, room_code, session_id):
        if not isinstance(room_code, str) or not re.fullmatch(r"[A-Z0-9]{6}", room_code) or not canonical_id(session_id):
            raise AdmissionError("observer_request_invalid")
        value = await self._request("POST", "/internal/broadcast-observers/" + room_code,
            {"broadcastSessionId": session_id, "gameVersion": self.game_version, "protocolVersion": self.protocol_version})
        return ObserverAdmission.parse(value, room_code=room_code, session_id=session_id)

    async def release(self, room_code, session_id):
        if not isinstance(room_code, str) or not re.fullmatch(r"[A-Z0-9]{6}", room_code) or not canonical_id(session_id):
            raise AdmissionError("observer_request_invalid")
        await self._request("DELETE", f"/internal/broadcast-observers/{room_code}/{session_id}")
