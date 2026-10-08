#!/usr/bin/env python3
"""Bounded real-Unity bridge checks using the normal localhost RuntimeServer.

Start this fixture with ACTOR_BRIDGE_TOKEN in its environment, then connect the
development game client in ACTOR control mode. JSON output is diagnostic evidence;
it never supplies state or actions to the game. No token is logged or persisted.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
from pathlib import Path
import time

from google.protobuf.any_pb2 import Any

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2 as wire
from actor_runtime.errors import ActorError
from actor_runtime.extensions import ExtensionRegistry, type_url
from actor_runtime.server import RuntimeServer
from actor_runtime.session import RuntimeSession
from actor_standard import (
    LOCOMOTION, MOTION, TRANSFORM, Locomotion2DCommand,
    Motion2DObservation, Transform2DObservation,
)
from actor_agents_arena import ROOM_MEMBERSHIP, RoomMembershipObservation

LOG = logging.getLogger("actor_control_safety")


def fresh(entry, *, after=0.0):
    return entry is not None and entry.received_at >= after and time.monotonic() - entry.received_at < 0.5


def snapshot(session: RuntimeSession):
    transform = session.world.latest_entry(Transform2DObservation, capability_id=TRANSFORM)
    motion = session.world.latest_entry(Motion2DObservation, capability_id=MOTION)
    room = session.world.latest_entry(RoomMembershipObservation, capability_id=ROOM_MEMBERSHIP)
    if not all(fresh(entry) for entry in (transform, motion, room)):
        raise ActorError("ObservationTimeout", "Fresh Transform, Motion and RoomMembership are required")
    coordinates = (transform.payload.x, transform.payload.y,
                   motion.payload.velocity_x, motion.payload.velocity_y)
    if not all(math.isfinite(value) for value in coordinates):
        raise ActorError("ObservationInvalid", "Authoritative observations must be finite")
    return {
        "actor_id": session.world.self_actor_id,
        "position": {"x": transform.payload.x, "y": transform.payload.y},
        "velocity": {"x": motion.payload.velocity_x, "y": motion.payload.velocity_y},
        "is_moving": motion.payload.is_moving,
        "room_id": room.payload.room_id,
        "transform_sequence": transform.sequence,
        "motion_sequence": motion.sequence,
        "room_sequence": room.sequence,
    }


def send_raw_command(session: RuntimeSession, capability_id: str, payload: Any):
    """Tester-only bypass of the safe typed command API for negative wire tests."""
    standard = session.capabilities[LOCOMOTION]
    session.next_command_id += 1
    command = core.CommandEnvelope(
        command_id=session.next_command_id,
        capability_id=capability_id,
        extension_id=standard.extension_id,
        extension_version=session.extensions[standard.extension_id].extension_version,
        target_actor_id=session.world.self_actor_id,
        ttl_ms=250,
        issued_at_unix_ms=int(time.time() * 1000),
        payload=payload,
    )
    # The existing stream writer assigns sequence/session identity and tracks the
    # result deadline. No Core production interface needs an unsafe send method.
    session.mailbox.put_control(wire.RuntimeToGame(command=command))
    return command.command_id


async def result_for(session: RuntimeSession, command_id: int, timeout=3.0):
    async with asyncio.timeout(timeout):
        while True:
            for result in reversed(session.command_results):
                if result.command_id == command_id:
                    return {
                        "command_id": result.command_id,
                        "status": core.CommandStatus.Name(result.status),
                        "code": result.error.code,
                        "message": result.error.message,
                    }
            if session.closed.is_set():
                raise ActorError("SessionUnavailable", "Game disconnected while awaiting a command result")
            await asyncio.sleep(0.02)


async def exercise(session: RuntimeSession, report: dict):
    checks, command_results = [], []
    report["checks"] = checks
    report["command_results"] = command_results

    def check(name, passed, **details):
        item = {"name": name, "passed": bool(passed), **details}
        checks.append(item)
        LOG.info("%s: %s", "PASS" if passed else "FAIL", name)
        if not passed:
            raise ActorError("CheckFailed", name)

    check("Hello and CapabilityManifest negotiate the controlled actor",
          session.hello is not None and session.manifest is not None and session.ready.is_set()
          and session.hello.self_actor_id == session.manifest.self_actor_id
          and session.world.self_actor_id == session.manifest.self_actor_id)
    for capability, payload_type in (
        (TRANSFORM, Transform2DObservation), (MOTION, Motion2DObservation),
        (LOCOMOTION, Locomotion2DCommand), (ROOM_MEMBERSHIP, RoomMembershipObservation),
    ):
        session.require(capability, payload_type)
    await session.world.wait_until(lambda world: all(fresh(world.latest_entry(kind, capability_id=capability))
        for kind, capability in ((Transform2DObservation, TRANSFORM), (Motion2DObservation, MOTION),
                                 (RoomMembershipObservation, ROOM_MEMBERSHIP))), timeout=10)
    before = snapshot(session)
    report["state_before"] = before
    check("Typed Standard observations and game RoomMembership are present",
          bool(before["actor_id"]) and bool(before["room_id"]), state=before)

    packed = Any()
    packed.Pack(Locomotion2DCommand())
    command_id = send_raw_command(session, "actor.diagnostics.unknown", packed)
    reply = await result_for(session, command_id)
    command_results.append(reply)
    check("Unknown capability is structurally rejected",
          reply["status"] == "COMMAND_STATUS_REJECTED" and reply["code"] == "UnknownCapability", result=reply)

    command_id = send_raw_command(session, LOCOMOTION,
        Any(type_url=type_url(Locomotion2DCommand), value=b"\xff"))
    reply = await result_for(session, command_id)
    command_results.append(reply)
    check("Malformed typed Protobuf command is rejected",
          reply["status"] == "COMMAND_STATUS_REJECTED" and reply["code"] == "MalformedCommand", result=reply)

    command_id = session.command(LOCOMOTION,
        Locomotion2DCommand(desired_move_x=float("nan")), ttl_ms=250)
    reply = await result_for(session, command_id)
    command_results.append(reply)
    check("Non-finite movement is rejected by the game adapter",
          reply["status"] == "COMMAND_STATUS_REJECTED" and reply["code"] == "InvalidMovement", result=reply)

    movement_started = time.monotonic()
    valid_commands = []
    while time.monotonic() - movement_started < 0.2:
        valid_commands.append(session.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1), ttl_ms=250))
        await asyncio.sleep(0.04)
    no_commands_since = time.monotonic()
    # Deliberately send no locomotion messages, including zero intent, for 600 ms.
    # The game must stop through its input watchdog and command TTL.
    await asyncio.sleep(0.6)
    for command_id in valid_commands:
        reply = await result_for(session, command_id)
        command_results.append(reply)
    check("Valid movement commands are accepted",
          all(item["status"] == "COMMAND_STATUS_ACCEPTED" for item in command_results[-len(valid_commands):]),
          count=len(valid_commands))

    def authoritative_stopped(world):
        motion = world.latest_entry(Motion2DObservation, capability_id=MOTION)
        transform = world.latest_entry(Transform2DObservation, capability_id=TRANSFORM)
        return (fresh(motion, after=no_commands_since + 0.5) and fresh(transform, after=no_commands_since + 0.5)
                and not motion.payload.is_moving
                and math.hypot(motion.payload.velocity_x, motion.payload.velocity_y) < 0.05)

    await session.world.wait_until(authoritative_stopped, timeout=3)
    after = snapshot(session)
    report["state_after"] = after
    distance = math.hypot(after["position"]["x"] - before["position"]["x"],
                         after["position"]["y"] - before["position"]["y"])
    check("Valid intent moves the authoritative multiplayer actor", distance > 0.05,
          displacement=distance, state=after)
    check("Fresh authoritative motion stops without commands",
          not after["is_moving"] and math.hypot(after["velocity"]["x"], after["velocity"]["y"]) < 0.05,
          no_command_seconds=time.monotonic() - no_commands_since)
    return {"passed": True, "checks": checks, "command_results": command_results,
            "state_before": before, "state_after": after}


async def run(args):
    server = RuntimeServer(ExtensionRegistry.discover(), os.environ.get("ACTOR_BRIDGE_TOKEN", ""),
                           port=args.port, command_timeout_ms=250)
    session = None
    report = {"passed": False, "checks": []}
    try:
        await server.start()
        LOG.info("Waiting for the Unity client in ACTOR control mode")
        session = await server.next_session(timeout=args.connect_timeout)
        report.update(await exercise(session, report))
    except Exception as error:
        report["passed"] = False
        report["error"] = error.as_dict() if isinstance(error, ActorError) else {
            "code": type(error).__name__, "message": str(error)}
        LOG.error("Control safety fixture failed: %s", report["error"])
    finally:
        try:
            if session is not None and not session.closed.is_set() and not session.mailbox.closed:
                try:
                    session.command(LOCOMOTION, Locomotion2DCommand(), ttl_ms=250)
                    await asyncio.sleep(0.05)
                except ActorError:
                    pass  # Shutdown still cancels the stream and clears game input.
        finally:
            try:
                await server.stop()
            finally:
                report["runtime"] = server.report()
                if args.report:
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    temporary = args.report.with_suffix(".tmp")
                    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
                    temporary.replace(args.report)
    LOG.info("Control safety fixture completed passed=%s", report["passed"])
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=50051)
    parser.add_argument("--connect-timeout", type=float, default=120)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
