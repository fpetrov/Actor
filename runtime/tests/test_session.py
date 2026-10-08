import asyncio
import time

import pytest

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor_runtime.errors import ActorError
from actor_runtime.extensions import OpaquePayload
from actor_runtime.mailbox import OutboundMailbox
from actor_runtime.session import RuntimeSession
from actor_standard import TRANSFORM, LOCOMOTION, Transform2DObservation, Locomotion2DCommand
from actor_agents_arena import RoomMembershipObservation
from conftest import message, negotiate, envelope


async def test_registered_manifest_and_accepted_handshake(session, manifest):
    assert set(session.capabilities) == {item.capability_id for item in manifest.capabilities}
    outgoing = await anext(session.outgoing())
    assert outgoing.hello.accepted
    assert outgoing.session_id == "session-1"
    assert outgoing.sequence == 1


async def test_observation_cache_is_typed_and_entity_scoped(session):
    frame = core.ObservationFrame(revision=1, observations=[
        envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=1)),
        envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=99), entity="other-actor")])
    await session.accept(message(3, observation=frame))
    assert session.world.latest(Transform2DObservation).x == 1
    assert session.world.latest(Transform2DObservation, "other-actor").x == 99
    value = session.world.latest(Transform2DObservation)
    value.x = 999
    assert session.world.latest(Transform2DObservation).x == 1


async def test_duplicate_old_frames_and_stale_revisions_ignored(session):
    for seq, revision, x in ((3, 10, 1), (3, 11, 2), (2, 12, 3), (4, 9, 4)):
        await session.accept(message(seq, observation=core.ObservationFrame(revision=revision,
            observations=[envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=x))])))
    assert session.world.latest(Transform2DObservation).x == 1


async def test_wrong_session_identity_rejected(session):
    incoming = message(3, heartbeat=core.Heartbeat())
    incoming.session_id = "different-session"
    with pytest.raises(ActorError, match="SessionMismatch"):
        await session.accept(incoming)


@pytest.mark.parametrize("mutation,code", [
    ("capability", "UnknownCapability"), ("type", "PayloadTypeMismatch"),
    ("extension", "EnvelopeInvalid"), ("wire", "PayloadInvalid")])
async def test_invalid_observation_is_structured(session, mutation, code):
    item = envelope(TRANSFORM, "actor.standard", Transform2DObservation())
    if mutation == "capability":
        item.capability_id = "not-registered"
    elif mutation == "type":
        item.payload.Pack(RoomMembershipObservation())
    elif mutation == "extension":
        item.extension_version = "2.0.0"
    else:
        item.payload.value = b"\xff"
    with pytest.raises(ActorError, match=code):
        await session.accept(message(3, observation=core.ObservationFrame(revision=1, observations=[item])))


async def test_latest_value_overwrites_backlog_and_writer_assigns_order(session):
    for i in range(1000):
        session.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=i / 1000))
    assert len(session.mailbox.commands) == 1
    assert session.mailbox.replaced == 999
    outgoing = session.outgoing()
    assert (await anext(outgoing)).hello.accepted
    command = await anext(outgoing)
    assert command.sequence == 2
    assert command.command.command_id == 1000
    payload = Locomotion2DCommand()
    assert command.command.payload.Unpack(payload)
    assert payload.desired_move_x == pytest.approx(0.999)
    await outgoing.aclose()


async def test_stale_unsent_command_is_not_dispatched(session):
    session.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1), ttl_ms=10)
    await asyncio.sleep(0.015)
    session.mailbox.closed = True
    items = [item async for item in session.outgoing()]
    assert len(items) == 1 and items[0].hello.accepted
    assert session.commands_sent == 0


async def test_command_target_type_and_ttl_validation(session):
    for call, code in (
        (lambda: session.command("missing", Locomotion2DCommand()), "UnknownCapability"),
        (lambda: session.command(LOCOMOTION, RoomMembershipObservation()), "PayloadTypeMismatch"),
        (lambda: session.command(LOCOMOTION, Locomotion2DCommand(), ttl_ms=0), "CommandTimeoutInvalid"),
        (lambda: session.command(LOCOMOTION, Locomotion2DCommand(), target_actor_id="other"), "CommandTargetInvalid"),
    ):
        with pytest.raises(ActorError, match=code):
            call()


async def test_command_result_deadline_and_bound(session):
    session.pending_commands[1] = (time.monotonic() - 1, time.monotonic() - 2)
    session.expire_results()
    assert not session.pending_commands
    assert session.command_results[-1].status == core.COMMAND_STATUS_TIMED_OUT
    for i in range(200):
        await session.accept(message(3 + i, command_result=core.CommandResult(command_id=100 + i,
                                                       status=core.COMMAND_STATUS_ACCEPTED)))
    assert len(session.command_results) == 64


async def test_reconnect_creates_fresh_cache(registry, manifest, session):
    new = RuntimeSession(registry)
    await negotiate(new, manifest)
    assert new.world is not session.world
    assert not new.world.events and not new.world.observations


async def test_reliable_queue_is_bounded():
    mailbox = OutboundMailbox(control_capacity=2)
    mailbox.put_control(1)
    mailbox.put_control(2)
    with pytest.raises(ActorError, match="BackpressureLimit"):
        mailbox.put_control(3)
    mailbox.close()
    assert await mailbox.get() is None


async def test_optional_incompatible_package_stays_opaque(registry, manifest):
    standard = manifest.extensions[0]
    standard.required = False
    standard.extension_version = "99.0.0"
    session = RuntimeSession(registry)
    await negotiate(session, manifest)
    item = envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=1))
    item.extension_version = "99.0.0"
    await session.accept(message(3, observation=core.ObservationFrame(revision=1, observations=[item])))
    assert isinstance(session.world.observations[("actor-1", TRANSFORM)].payload, OpaquePayload)
    assert session.world.latest(Transform2DObservation) is None
    with pytest.raises(ActorError, match="ExtensionUnavailable"):
        session.command(LOCOMOTION, Locomotion2DCommand())
    session.close()


async def test_terminal_error_discards_full_backlog_and_closes(session):
    while len(session.mailbox.controls) < session.mailbox.control_capacity:
        session.mailbox.put_control(wire.RuntimeToGame(heartbeat=core.Heartbeat()))
    session.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
    session.fail(ActorError("ForcedFailure", "A deterministic terminal test error"))
    assert session.mailbox.closed
    assert not session.mailbox.commands
    items = [item async for item in session.outgoing()]
    assert len(items) == 2
    assert items[0].error.code == "ForcedFailure"
    assert items[1].disconnect.reason == "ForcedFailure"


@pytest.mark.parametrize("capability_index,field,foreign_type", [
    (0, "output_type_url", RoomMembershipObservation),
    (2, "input_type_url", RoomMembershipObservation),
    (3, "output_type_url", Transform2DObservation),
])
async def test_manifest_type_must_belong_to_its_declared_extension(
    registry, manifest, capability_index, field, foreign_type,
):
    from actor_runtime.extensions import type_url
    setattr(manifest.capabilities[capability_index], field, type_url(foreign_type))
    session = RuntimeSession(registry)
    with pytest.raises(ActorError, match="ManifestInvalid"):
        await negotiate(session, manifest)
    assert not session.ready.is_set()
