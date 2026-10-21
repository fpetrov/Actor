"""Auth-free fixture policy, local Skill boundary and safe projection tests."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from google.protobuf.json_format import MessageToDict
from extensions.agents_arena.v1 import social_pb2 as pb
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import DecisionContext, GameMetadata, PayloadSnapshot
from actor_runtime.extensions import type_url
from actor_runtime.brains import FinishObjective
from pydantic import ValidationError

import actor_social_protocol as fixture
import controller
from verify_report import ProjectionTests


def context(*, alignment=pb.SOCIAL_ALIGNMENT_CREW, phase=pb.SOCIAL_PHASE_EXPLORATION,
            life=pb.SOCIAL_LIFE_ALIVE, previous=None, meeting_id="", recipient="self"):
    values = [(fixture.SOCIAL_MATCH, pb.SocialMatchStateObservation(match_id="scope", phase=phase, phase_revision=3)),
        (fixture.SOCIAL_OWN_STATE, pb.SocialOwnStateObservation(match_id="scope", actor_id=recipient, alignment=alignment,
            life=life, assigned=phase != pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS, can_move=True)),
        (fixture.SOCIAL_MEETING, pb.SocialMeetingObservation(match_id="scope", meeting_id=meeting_id))]
    return DecisionContext(objective=Objective(text="Use only ordinary permitted game observations"), decision_index=1,
        game=GameMetadata(game_id="agents-arena", game_version="0.8.0", protocol_major=1, protocol_minor=0), actor_id="self",
        observations=[PayloadSnapshot(capability_id=cap, entity_id="self", type_url=type_url(type(value)), sequence=5, revision=3,
            age_seconds=0.0, value=MessageToDict(value), data_policy="session_only") for cap, value in values],
        recent_events=[], previous_skill_result=previous, available_skills=[])


class BrainTests(unittest.IsolatedAsyncioTestCase):
    async def create(self):
        brain = fixture.brain_factory(); await brain.start(Objective(text="Same trusted objective"), []); return brain

    async def test_readiness_delay_and_task_sequence_are_role_agnostic(self):
        for alignment in (pb.SOCIAL_ALIGNMENT_CREW, pb.SOCIAL_ALIGNMENT_IMPOSTOR):
            with patch.object(fixture, "NOW", return_value=100):
                brain = await self.create()
                decision = await brain.decide(context(alignment=alignment, phase=pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS))
                self.assertEqual(decision.skill_id, "actor.wait"); self.assertEqual(decision.arguments, {"duration_seconds": 8.0})
            with patch.object(fixture, "NOW", return_value=109):
                self.assertEqual((await brain.decide(context(alignment=alignment, phase=pb.SOCIAL_PHASE_WAITING_FOR_PLAYERS))).skill_id, "agents-arena.ready-for-match")
                decision = await brain.decide(context(alignment=alignment))
                self.assertEqual(decision.skill_id, fixture.APPROACH); self.assertEqual(decision.arguments, {"target": "main-task"})

    async def test_task_stop_wait_then_console_without_starting_work_or_voting(self):
        with patch.object(fixture, "NOW", return_value=100):
            brain = await self.create(); await brain.decide(context())
            waiting = await brain.decide(context(previous={"skill_id": fixture.APPROACH, "status": "succeeded"}))
            self.assertEqual(waiting.skill_id, "actor.wait"); self.assertEqual(waiting.arguments["duration_seconds"], 12)
        with patch.object(fixture, "NOW", return_value=113):
            approach = await brain.decide(context(previous={"skill_id": "actor.wait", "status": "succeeded"}))
            self.assertEqual(approach.arguments, {"target": "meeting"})
            call = await brain.decide(context(previous={"skill_id": fixture.APPROACH, "status": "succeeded"}))
            self.assertEqual(call.skill_id, "agents-arena.call-meeting")
            for phase in (pb.SOCIAL_PHASE_DISCUSSION, pb.SOCIAL_PHASE_VOTING):
                self.assertEqual((await brain.decide(context(phase=phase, meeting_id="public-meeting"))).skill_id, "actor.wait")

    async def test_new_phase_provider_uses_retained_public_meeting_to_avoid_restart(self):
        brain = await self.create()
        self.assertEqual((await brain.decide(context(meeting_id="public-meeting"))).skill_id, "actor.wait")
        self.assertEqual((await brain.decide(context(recipient="foreign"))).skill_id, "actor.wait")
        self.assertFalse(isinstance(await brain.decide(context(life=pb.SOCIAL_LIFE_DEAD)), FinishObjective))
        self.assertIsInstance(await brain.decide(context(phase=pb.SOCIAL_PHASE_GAME_OVER)), FinishObjective)
        self.assertEqual(set(brain.report()), {"provider", "decisions", "closed"})


class BoundaryTests(unittest.TestCase):
    def test_launcher_resolves_installed_console_entrypoint_not_missing_module_main(self):
        import sys
        from importlib import metadata
        actual = controller.runtime_entrypoint(Path(sys.executable))
        self.assertTrue(actual.is_file())
        entrypoints = metadata.entry_points(group="console_scripts", name=actual.name)
        self.assertTrue(any(entry.value == "actor_runtime.cli:main" for entry in entrypoints))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError): controller.runtime_entrypoint(Path(folder)/"python")

    def test_fixture_input_and_policy_cannot_expand_authority(self):
        for value in ({"target": "foreign-player"}, {"target": "main-task", "x": 1}, {"target": True}):
            with self.assertRaises(ValidationError): fixture.ApproachInput.model_validate(value)
        match = pb.SocialMatchStateObservation(match_id="scope", phase=pb.SOCIAL_PHASE_EXPLORATION)
        own = pb.SocialOwnStateObservation(match_id="scope", actor_id="self", life=pb.SOCIAL_LIFE_ALIVE, can_move=True)
        world = SimpleNamespace(self_actor_id="self", latest=lambda cls, **_: match if cls is pb.SocialMatchStateObservation else own)
        session = SimpleNamespace(world=world)
        policy = fixture.ProtocolMatchPolicy(); definition = fixture.skill_package()[0]
        self.assertTrue(policy.skill_available(session, definition))
        own.life = pb.SOCIAL_LIFE_DEAD
        self.assertFalse(policy.skill_available(session, definition)); own.life = pb.SOCIAL_LIFE_ALIVE
        own.actor_id = "foreign"
        self.assertFalse(policy.skill_available(session, definition)); own.actor_id = "self"
        match.phase = pb.SOCIAL_PHASE_VOTING
        self.assertFalse(policy.skill_available(session, definition))

    def test_supervision_requires_four_same_scope_completions_and_no_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(1, 5):
                controller.write(root / f"agent{index:02}/runtime.json", {"match": {"passed": True, "result": {
                    "scope_id": "actual-scope", "status": "completed", "authoritative_terminal": True}}})
            proof = ProjectionTests.report(); proof.update(secret="PRIVATE-CANARY", pid=12345)
            controller.write(root / "agent04/protocol.json", proof)
            controller.write(root / "cleanup.json", {"started": 8, "remaining": 0})
            controller.write(root / "launch.json", {"agents": [{"bridge_port": 18000+i, "credential_sha256": str(i),
                "profile": str(i), "database": str(i), "runtime_pid": 12345} for i in range(4)]})
            public = controller.summarize(root)
            self.assertTrue(public["passed"]); self.assertNotIn("PRIVATE-CANARY", json.dumps(public)); self.assertNotIn("12345", json.dumps(public))
            controller.write(root / "cleanup.json", {"started": 8, "remaining": False})
            self.assertFalse(controller.summarize(root)["passed"])
            controller.write(root / "cleanup.json", {"started": 8, "remaining": 0})
            controller.write(root / "agent03/runtime.json", {"match": {"passed": True, "result": {
                "scope_id": "stale-scope", "status": "completed", "authoritative_terminal": True}}})
            self.assertFalse(controller.summarize(root)["passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
