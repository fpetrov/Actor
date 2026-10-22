"""Operator/admitted observer boundaries; no HTTP, sockets or credentials."""
import asyncio
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from actor.core.v1 import actor_pb2 as core
from actor_broadcast.admission import AdmissionClient, AdmissionError, ObserverAdmission
from actor_broadcast.bridge import ALLOWED_CAPABILITIES, BroadcastBridge
from actor_runtime.errors import ActorError

SESSION = '1' * 32
OBSERVER = '2' * 32
MATCH = '3' * 32
KEY = 'test-only-operator-key-not-a-real-credential'
TICKET = 'observer-v1.' + 'synthetic-not-a-signed-ticket' * 3
NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def document():
    return {'lobbyId': MATCH, 'roomCode': 'ABC123', 'host': '127.0.0.1', 'port': 7781,
        'joinTicket': TICKET, 'expiresAt': (NOW + timedelta(seconds=45)).isoformat(),
        'gameMode': 'SocialDeduction', 'broadcastSessionId': SESSION, 'observerId': OBSERVER,
        'connectionGeneration': 1}


def test_exact_admission_env_contract_has_no_ticket_in_repr():
    value = ObserverAdmission.parse(document(), room_code='ABC123', session_id=SESSION, now=NOW)
    assert json.loads(value.child_environment_value()) == document()
    assert TICKET not in repr(value)
    assert 'playerName' not in value.child_environment_value()
    assert value.game_mode == 'SocialDeduction'


@pytest.mark.parametrize('change', [dict(lobbyId='foreign'), dict(broadcastSessionId='4'*32),
    dict(roomCode='OTHER1'), dict(gameMode='Sandbox'), dict(connectionGeneration=True), dict(connectionGeneration=1000001),
    dict(port=True), dict(port=65536), dict(host='http://remote/'), dict(joinTicket='player-v1.synthetic'),
    dict(expiresAt=(NOW-timedelta(seconds=1)).isoformat()), dict(expiresAt=(NOW+timedelta(seconds=66)).isoformat()),
    dict(expiresAt='2026-10-22T00:00:45')])
def test_foreign_stale_malformed_or_player_admission_is_rejected(change):
    x = document(); x.update(change)
    with pytest.raises(AdmissionError): ObserverAdmission.parse(x, room_code='ABC123', session_id=SESSION, now=NOW)


def test_extra_reply_field_is_not_silently_forwarded_to_native_environment():
    x = document(); x['privateRole'] = 'not-an-allowed-field'
    with pytest.raises(AdmissionError, match='shape'): ObserverAdmission.parse(x, room_code='ABC123', session_id=SESSION, now=NOW)


@pytest.mark.parametrize('endpoint', ['http://remote.example:5081', 'http://localhost:5081',
    'https://127.0.0.1:5081', 'http://user:pass@127.0.0.1:5081', 'http://127.0.0.1:5081/api',
    'http://127.0.0.1:5081/?key=secret', 'http://127.0.0.1:5081/#fragment'])
def test_operator_secret_is_only_sent_to_direct_loopback_endpoint(endpoint):
    with pytest.raises(AdmissionError, match='direct_loopback'): AdmissionClient(endpoint, KEY)


@pytest.mark.asyncio
async def test_observe_and_release_use_only_protected_paths_with_same_scope():
    calls = []
    async def request(method, path, body=None):
        calls.append((method, path, body))
        x = document(); x['expiresAt'] = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat()
        return x if method == 'POST' else None
    client = AdmissionClient('http://127.0.0.1:5081', KEY, request=request)
    value = await client.observe('ABC123', SESSION)
    await client.release('ABC123', value.broadcast_session_id)
    assert calls == [('POST', '/internal/broadcast-observers/ABC123',
        {'broadcastSessionId': SESSION, 'gameVersion': '0.10.0', 'protocolVersion': '4'}),
        ('DELETE', '/internal/broadcast-observers/ABC123/' + SESSION, None)]
    for code, identity in [('../escape', SESSION), ('ABC123', '../escape')]:
        with pytest.raises(AdmissionError): await client.release(code, identity)
    assert len(calls) == 2


class Session:
    def __init__(self, *, observer=OBSERVER, version='0.10.0', capabilities=ALLOWED_CAPABILITIES):
        self.manifest = core.CapabilityManifest(game_id='agents-arena', game_version=version, self_actor_id=observer)
        self.capabilities = dict.fromkeys(capabilities)
        self.closed = asyncio.Event(); self.mailbox = SimpleNamespace(closed=False)


def bridge():
    # Exercise the exact production publication boundary without starting gRPC.
    result = object.__new__(BroadcastBridge)
    result.observer_id = OBSERVER; result.game_version = '0.10.0'; result.sessions = set()
    result.available = OrderedDict(); result.availability_changed = asyncio.Event()
    return result


def test_bridge_publishes_only_exact_admitted_observer_capabilities():
    value = bridge(); allowed = Session(); value.sessions.add(allowed); value._publish_session(allowed)
    assert list(value.available) == [allowed]
    for rejected in [Session(observer='4'*32), Session(version='0.9.0'),
            Session(capabilities=ALLOWED_CAPABILITIES | {'actor.standard.locomotion'}), Session(capabilities=[])]:
        with pytest.raises(ActorError, match='admitted observer'): value._publish_session(rejected)
    assert list(value.available) == [allowed]


def test_bridge_reconnect_waits_for_previous_session_closure():
    value = bridge(); old = Session(); replacement = Session(); value.sessions.update((old, replacement))
    with pytest.raises(ActorError): value._publish_session(replacement)
    old.closed.set(); value._publish_session(replacement)
    assert list(value.available) == [replacement]
