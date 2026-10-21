#!/usr/bin/env python3
"""Passive, game-specific wire auditor around the ordinary authenticated Runtime."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import time

from actor_runtime.server import RuntimeServer
from actor.standard.v1.transform2d_pb2 import Transform2DObservation
from actor.standard.v1.motion2d_pb2 import Motion2DObservation
from extensions.agents_arena.v1 import rooms_pb2, social_pb2 as social


PREFIX = "agents-arena.social-"
LEGACY = {"agents-arena.duty-roster", "agents-arena.own-role", "agents-arena.own-investigation-view",
          "agents-arena.calibration-state", "agents-arena.calibration-progress", "agents-arena.station-signal",
          "agents-arena.diagnostic-evidence", "agents-arena.security-evidence"}
def legacy(capability):
    return capability.startswith("agents-arena.") and not capability.startswith(PREFIX) and capability not in (
        "agents-arena.room-membership", "agents-arena.room-changed")
TYPES = {"actor.standard.transform2d": Transform2DObservation, "actor.standard.motion2d": Motion2DObservation,
         "agents-arena.room-membership": rooms_pb2.RoomMembershipObservation}
for cap, cls in (
    ("match", social.SocialMatchStateObservation), ("own-state", social.SocialOwnStateObservation),
    ("visible-world", social.SocialVisibleWorldObservation), ("meeting", social.SocialMeetingObservation),
    ("tasks", social.SocialOwnTasksObservation), ("emergency", social.SocialEmergencyStateObservation),
    ("postgame", social.SocialPostgameObservation), ("action-result", social.SocialActionResultEvent),
    ("match-changed", social.SocialMatchChangedEvent), ("own-state-changed", social.SocialOwnStateChangedEvent),
    ("meeting-changed", social.SocialMeetingChangedEvent), ("emergency-changed", social.SocialEmergencyChangedEvent),
    ("visible-attack", social.SocialVisibleAttackEvent), ("visible-activity", social.SocialVisibleActivityEvent),
    ("match-ended", social.SocialMatchEndedEvent),
):
    TYPES[PREFIX + cap] = cls


def canonical(cls) -> str:
    return "type.googleapis.com/" + cls.DESCRIPTOR.full_name


class WireAuditor:
    """Bounded audit metadata, never an agent WorldState or a source of commands."""
    def __init__(self, report: Path | None, *, clock=time.monotonic):
        self.path, self.clock = report, clock
        self.started = clock()
        self.self_actor = None
        self.match_id = ""
        self.match_started_at_unix_ms = 0
        self.terminal = False
        self.room = ""
        self.alive = False
        self.phase = 0
        self.center = None
        self.speed = math.inf
        self.radius = 0.0
        self.far_since = None
        self.far_started_unix_ms = 0
        self.hall_ids: set[str] = set()
        self.counts = {key: 0 for key in (
            "streams", "wire_messages", "manifests", "observation_frames", "world_frames", "events", "own_payloads",
            "self_envelopes", "far_security_frames", "foreign_entities", "foreign_recipients", "legacy_capabilities",
            "legacy_payloads", "type_mismatches", "far_forbidden_world_entries", "far_forbidden_attack_events",
            "early_postgame_payloads", "probe_failures")}
        self.max_far_seconds = 0.0
        self.stable_far_intervals = []
        self.current_far_interval = None
        self._next_save = 0.0

    def recipient(self, value: str):
        if self.self_actor is None or value != self.self_actor:
            self.counts["foreign_recipients"] += 1

    def decode(self, envelope):
        if legacy(envelope.capability_id):
            self.counts["legacy_payloads"] += 1
        if self.self_actor is None or envelope.entity_id != self.self_actor:
            self.counts["foreign_entities"] += 1
        else:
            self.counts["self_envelopes"] += 1
        cls = TYPES.get(envelope.capability_id)
        if cls is None:
            # Standard communication remains an installed optional extension.
            # Its content is deliberately neither copied nor inspected here.
            if envelope.capability_id.startswith(PREFIX):
                self.counts["type_mismatches"] += 1
            return None
        if envelope.payload.type_url != canonical(cls):
            self.counts["type_mismatches"] += 1
            return None
        value = cls()
        try:
            value.ParseFromString(envelope.payload.value)
        except Exception:
            self.counts["type_mismatches"] += 1
            return None
        return value

    def inspect(self, message):
        self.counts["wire_messages"] += 1
        try:
            kind = message.WhichOneof("body")
            if kind == "manifest":
                self.counts["manifests"] += 1
                manifest = message.manifest
                if not manifest.self_actor_id or len(manifest.self_actor_id) > 128:
                    self.counts["probe_failures"] += 1
                elif self.self_actor is not None and manifest.self_actor_id != self.self_actor:
                    self.counts["probe_failures"] += 1
                else:
                    self.self_actor = manifest.self_actor_id
                self.counts["legacy_capabilities"] += sum(legacy(cap.capability_id) for cap in manifest.capabilities)
            elif kind == "observation":
                self.counts["observation_frames"] += 1
                values = [value for envelope in message.observation.observations if (value := self.decode(envelope)) is not None]
                # Establish same-frame self pose before auditing a visible-world
                # payload, regardless of capability registration order.
                self.center, self.speed = None, math.inf
                for value in values:
                    if isinstance(value, Transform2DObservation):
                        if all(math.isfinite(number) for number in (value.x, value.y)):
                            self.center = (value.x, value.y + .46)
                    elif isinstance(value, Motion2DObservation):
                        self.speed = math.hypot(value.velocity_x, value.velocity_y)
                    elif isinstance(value, rooms_pb2.RoomMembershipObservation):
                        self.room = value.room_id
                    elif isinstance(value, social.SocialOwnStateObservation):
                        self.recipient(value.actor_id)
                        self.alive = value.life == social.SOCIAL_LIFE_ALIVE
                    elif isinstance(value, social.SocialMatchStateObservation):
                        self.phase = value.phase
                        self.match_id = value.match_id
                        self.record_match_start(value.started_at_unix_ms)
                        self.terminal |= value.phase == social.SOCIAL_PHASE_GAME_OVER
                for value in values:
                    self.payload(value)
            elif kind == "event":
                self.counts["events"] += 1
                value = self.decode(message.event)
                if value is not None:
                    self.payload(value)
        except Exception:
            self.counts["probe_failures"] += 1
        if self.clock() >= self._next_save:
            self._next_save = self.clock() + .5
            self.save()

    def in_range(self, x, y):
        if self.center is None or not all(math.isfinite(number) for number in (*self.center, x, y, self.radius)):
            return False
        return self.radius > 0 and math.hypot(x - self.center[0], y - self.center[1]) <= self.radius + .35

    def record_match_start(self, started):
        if started <= 0:
            return
        if self.match_started_at_unix_ms and self.match_started_at_unix_ms != started:
            self.counts["probe_failures"] += 1
        else:
            self.match_started_at_unix_ms = started

    def record_far_world(self, captured):
        if captured <= 0:
            self.counts["probe_failures"] += 1
            return
        if self.current_far_interval is None:
            if len(self.stable_far_intervals) >= 16:
                self.counts["probe_failures"] += 1
                return
            self.current_far_interval = {"first_unix_ms": captured, "last_unix_ms": captured, "frames": 0}
            self.stable_far_intervals.append(self.current_far_interval)
        self.current_far_interval["frames"] += 1
        self.current_far_interval["last_unix_ms"] = max(captured, self.current_far_interval["last_unix_ms"])

    def payload(self, value):
        if isinstance(value, social.SocialOwnStateObservation):
            self.counts["own_payloads"] += 1
            self.recipient(value.actor_id)
        elif isinstance(value, social.SocialOwnStateChangedEvent):
            self.recipient(value.state.actor_id)
            self.alive = value.state.life == social.SOCIAL_LIFE_ALIVE
        elif isinstance(value, social.SocialOwnTasksObservation):
            self.recipient(value.recipient_actor_id)
        elif isinstance(value, social.SocialVisibleWorldObservation):
            self.counts["world_frames"] += 1
            self.recipient(value.recipient_actor_id)
            self.radius = value.visibility_radius
            if len(value.actors) > 6 or len(value.bodies) > 6 or len(value.interactables) > 16:
                self.counts["probe_failures"] += 1
                return
            if self.room == "main-hall":
                for actor in value.actors:
                    if actor.actor_id != self.self_actor and 0 < len(actor.actor_id) <= 128 and len(self.hall_ids) < 6:
                        self.hall_ids.add(actor.actor_id)
            eligible = (self.room == "security" and self.alive and self.phase == social.SOCIAL_PHASE_EXPLORATION
                        and self.speed <= .2 and self.center is not None)
            if eligible:
                if (self.current_far_interval is not None
                        and value.captured_at_unix_ms - self.current_far_interval["last_unix_ms"] > 1500):
                    self.far_since = None
                    self.current_far_interval = None
                if self.far_since is None:
                    self.far_since = self.clock()
                    self.far_started_unix_ms = value.captured_at_unix_ms
                self.max_far_seconds = max(self.max_far_seconds, self.clock() - self.far_since)
                if self.clock() - self.far_since >= 1:
                    self.record_far_world(value.captured_at_unix_ms)
                    self.counts["far_security_frames"] += 1
                    self.counts["far_forbidden_world_entries"] += sum(
                        not self.in_range(item.x, item.y) for item in (*value.actors, *value.bodies))
            else:
                self.far_since = None
                self.current_far_interval = None
        elif isinstance(value, social.SocialVisibleAttackEvent):
            self.recipient(value.observer_actor_id)
            if (self.far_since is not None and self.clock() - self.far_since >= 1
                    and value.observed_at_unix_ms >= self.far_started_unix_ms and not self.in_range(value.x, value.y)):
                self.counts["far_forbidden_attack_events"] += 1
        elif isinstance(value, social.SocialVisibleActivityEvent):
            self.recipient(value.observer_actor_id)
        elif isinstance(value, social.SocialMatchChangedEvent):
            self.phase = value.state.phase
            self.record_match_start(value.state.started_at_unix_ms)
            self.terminal |= self.phase == social.SOCIAL_PHASE_GAME_OVER
        elif isinstance(value, social.SocialMatchStateObservation):
            self.record_match_start(value.started_at_unix_ms)
            self.terminal |= value.phase == social.SOCIAL_PHASE_GAME_OVER
        elif isinstance(value, social.SocialMatchEndedEvent):
            self.terminal = True
        elif isinstance(value, social.SocialPostgameObservation) and value.HasField("summary") and not self.terminal:
            self.counts["early_postgame_payloads"] += 1

    def report(self):
        zeros = ("foreign_entities", "foreign_recipients", "legacy_capabilities", "legacy_payloads", "type_mismatches",
                 "far_forbidden_world_entries", "far_forbidden_attack_events", "early_postgame_payloads", "probe_failures")
        exercised = (self.counts["streams"] == 1 and self.counts["manifests"] == 1 and self.counts["observation_frames"] > 0
                     and self.counts["world_frames"] > 0 and self.counts["own_payloads"] > 0)
        return {"schema": "actor.social-wire-privacy-probe.v1", "completed": self.terminal,
                "passed": self.terminal and exercised and all(self.counts[key] == 0 for key in zeros),
                **self.counts, "duration_seconds": max(0, self.clock() - self.started),
                "match_started_at_unix_ms": self.match_started_at_unix_ms,
                "stable_far_intervals": [dict(interval) for interval in self.stable_far_intervals],
                "initial_legitimate_hall_actors": len(self.hall_ids), "max_far_security_seconds": self.max_far_seconds,
                "far_security_coverage": self.max_far_seconds >= 4 and self.counts["far_security_frames"] >= 10 and bool(self.hall_ids),
                "scope": "Actual authenticated gRPC GameToRuntime before RuntimeSession.accept; typed recipient/entity/manifest and same-frame range audit. Unity probe checks Physics2D LOS."}

    def save(self):
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.report(), indent=2) + "\n")
            temporary.replace(self.path)


class AuditRuntimeServer(RuntimeServer):
    def __init__(self, *args, audit_report: Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.audit = WireAuditor(audit_report)

    async def Connect(self, request_iterator, context):
        self.audit.counts["streams"] += 1
        async def audited():
            async for message in request_iterator:
                self.audit.inspect(message)
                yield message
        try:
            async for response in super().Connect(audited(), context):
                yield response
        finally:
            self.audit.save()


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--privacy-wire-report", type=Path, required=True)
    own, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    from actor_runtime.cli import main as runtime_main
    return runtime_main(server_factory=lambda *args, **kwargs: AuditRuntimeServer(*args, audit_report=own.privacy_wire_report.resolve(), **kwargs))


if __name__ == "__main__":
    sys.exit(main())
