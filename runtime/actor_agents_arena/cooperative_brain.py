"""Two independent deterministic reference providers for the live cooperation test.

Only each provider's own decision context supplies IDs, assignment and secrets.
The Codex provider remains separate and receives no predefined sequence.
"""
from __future__ import annotations

import re

from actor_runtime.brains import FinishObjective, SkillCall
from actor_runtime.errors import ActorError


class CooperativeBrain:
    def __init__(self, role):
        self.role = role
        self.step = self.turn_count = 0
        self.task_id = self.partner_id = self.code = ""
        self.interrupted = self.closed = False

    async def start(self, objective, catalog):
        self.step = self.turn_count = 0
        self.task_id = self.partner_id = self.code = ""
        self.interrupted = self.closed = False

    async def decide(self, context):
        if self.interrupted:
            raise ActorError("brain_interrupted", "Cooperative provider was interrupted")
        self.turn_count += 1
        previous = context.previous_skill_result or {}
        if previous and previous.get("status") != "succeeded":
            return FinishObjective(status="failure", reason="A required cooperative Skill did not succeed")
        output = previous.get("output", {})
        step = self.step
        self.step += 1
        if self.role == "coordinator":
            if step == 0:
                # Prefer the named example technician when a human observer is
                # also present. Actor identity still comes only from our roster.
                peers = [peer for observation in context.observations
                    if observation.capability_id == "actor.standard.communication.peers"
                    and isinstance(observation.value, dict)
                    for peer in observation.value.get("peers", [])
                    if peer.get("actorId") != context.actor_id]
                named = [peer for peer in peers if peer.get("displayName", "").casefold() == "technician"]
                partner = named[0].get("actorId", "") if len(named) == 1 else ""
                return SkillCall(skill_id="agents-arena.start-calibration",
                                 arguments={"technician_actor_id": partner})
            if step == 1:
                self.task_id = output["task_id"]
                self.partner_id = output["technician_actor_id"]
                return SkillCall(skill_id="agents-arena.navigate-to-room", arguments={"target_room_id": "main-hall"})
            if step == 2:
                return SkillCall(skill_id="agents-arena.inspect-diagnostic", arguments={"task_id": self.task_id})
            if step == 3:
                self.code = output["authorization_code"]
                return SkillCall(skill_id="actor.send-message", arguments={
                    "recipient_actor_id": self.partner_id, "channel_id": "direct", "text": self.code,
                    "task_id": self.task_id, "sensitive": True, "ttl_ms": 300000})
            if step == 4:
                self.code = ""
                return SkillCall(skill_id="agents-arena.wait-for-calibration", arguments={"task_id": self.task_id})
        else:
            if step == 0:
                return SkillCall(skill_id="agents-arena.wait-for-calibration-assignment", arguments={})
            if step == 1:
                self.task_id = output["task_id"]
                self.partner_id = output["coordinator_actor_id"]
                return SkillCall(skill_id="agents-arena.navigate-to-room", arguments={"target_room_id": "engineering"})
            if step == 2:
                return SkillCall(skill_id="actor.wait-for-message", arguments={
                    "sender_actor_id": self.partner_id, "task_id": self.task_id, "timeout_seconds": 240.0})
            if step == 3:
                # Scripted+Codex mode allows a human-readable prefix around
                # the documented opaque 32-hex authorization format.
                match = re.search(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])", output["text"])
                if match is None:
                    return FinishObjective(status="failure", reason="Coordinator's message did not contain the required authorization")
                self.code = match.group(0)
                return SkillCall(skill_id="agents-arena.submit-calibration", arguments={
                    "task_id": self.task_id, "authorization_code": self.code})
            if step == 4:
                self.code = ""
                return SkillCall(skill_id="agents-arena.wait-for-calibration", arguments={"task_id": self.task_id})
        if step == 5 and output.get("status") == "completed":
            return FinishObjective(status="success", reason="Authoritative server confirmed cooperative calibration completion")
        return FinishObjective(status="failure", reason="No authoritative completion evidence")

    async def interrupt(self):
        self.interrupted = True
        self.code = ""

    async def interrupt_decision(self):
        pass

    async def close(self):
        self.closed = True
        self.code = ""

    def report(self):
        return {"provider": "cooperative-scripted", "role": self.role, "decisions": self.turn_count,
                "interrupted": self.interrupted, "closed": self.closed}


def coordinator_brain(**_options):
    return CooperativeBrain("coordinator")


def technician_brain(**_options):
    return CooperativeBrain("technician")
