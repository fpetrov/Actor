"""Offline lifecycle safety checks. No client, socket, authentication or live SDK."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("social_lifecycle", Path(__file__).with_name("lifecycle.py"))
MODULE = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MODULE)


def evidence(case="objective-cancel"):
    return {"case": case, "executed": True, "harness_error": None, "owned_client_cleanup": True,
        "live": {"client_owned": True, "client_connected": True, "actor_id": "own-actor", "session_id": "own-session", "match_id": "own-match"},
        "after": {"active_skill": False, "runner_running": False, "cleanup_errors": 0, "pending_cleanup_tasks": 0,
            "provider_closed": True, "commands_at_cleanup": 3, "commands_later": 3, "observation_seconds": 2.2,
            "session_closed": False, "client_alive": True, "client_connected": True, "keyboard_disabled": True,
            "lease_reacquired": True, "speed": 0},
        "cancel_api_called": True, "result_status": "cancelled", "result_reason": "objective_cancelled",
        "controlled_skill_started": True, "controlled_skill_cancelled": True, "cleanup_seconds": .1}


class ProjectionTests(unittest.TestCase):
    def test_explicit_cancel_requires_live_bridge_released_lease_and_stable_commands(self):
        self.assertTrue(MODULE.evaluate(evidence())["passed"])
        for container, key, bad in (("", "cancel_api_called", False), ("", "controlled_skill_cancelled", False),
                ("after", "session_closed", True), ("after", "lease_reacquired", False), ("after", "active_skill", True),
                ("after", "commands_later", 4), ("after", "provider_closed", False), ("after", "pending_cleanup_tasks", 1),
                ("after", "speed", .5), ("after", "observation_seconds", .1)):
            with self.subTest(key=key):
                value = evidence(); (value[container] if container else value)[key] = bad
                self.assertFalse(MODULE.evaluate(value)["passed"])

    def test_shutdown_or_session_loss_does_not_impersonate_explicit_objective_cancel(self):
        for reason in ("runtime_shutdown", "session_disconnected", "participation_cancelled"):
            value = evidence(); value["result_reason"] = reason
            self.assertFalse(MODULE.evaluate(value)["passed"])

    def test_codex_denial_requires_real_turn_metadata_and_original_error_code(self):
        value = evidence("codex-turn-budget"); value.update(result_status="failed", result_reason="brain_failure", real_wait_succeeded=True)
        value["model"] = {"unique_model_turns": 1, "sdk_version": "0.162.1", "sandbox": "read-only", "ephemeral": True,
            "denials": ["codex_turn_limit"], "configured_max_turns": 1, "provider_decisions": 1, "attempts": 2,
            "max_active": 1, "active": 0, "tool_calls": 0}
        self.assertTrue(MODULE.evaluate(value)["passed"])
        for key, bad in (("unique_model_turns", 0), ("denials", []), ("denials", ["decision_limit"]), ("attempts", 1),
                ("sdk_version", "fake"), ("configured_max_turns", 2), ("max_active", 2), ("tool_calls", 1)):
            with self.subTest(key=key):
                changed = copy.deepcopy(value); changed["model"][key] = bad
                self.assertFalse(MODULE.evaluate(changed)["passed"])
        value["real_wait_succeeded"] = False; self.assertFalse(MODULE.evaluate(value)["passed"])

    def test_server_observer_requires_expiry_and_session_failure_with_separate_fault_proof(self):
        value = evidence("server-loss-observer"); value.update(result_status="failed", result_reason="session_disconnected",
            motion_became_stale=True, stale_to_terminal_seconds=30)
        value["after"]["session_closed"] = True
        result = MODULE.evaluate(value); self.assertTrue(result["passed"])
        self.assertFalse(result["server_mutation_performed"])
        self.assertTrue(any("separate" in item for item in result["limits"]))
        for key, bad in (("motion_became_stale", False), ("stale_to_terminal_seconds", 46), ("result_reason", "objective_timeout")):
            changed = copy.deepcopy(value); changed[key] = bad; self.assertFalse(MODULE.evaluate(changed)["passed"])
        value.update(result_reason="brain_reported_failure", controlled_skill_failure_reason="stale_world_state")
        self.assertTrue(MODULE.evaluate(value)["passed"])
        value["after"]["session_closed"] = False
        self.assertFalse(MODULE.evaluate(value)["passed"])

    def test_absent_execution_unknown_case_and_cleanup_failure_never_pass(self):
        for change in ({"executed": False}, {"case": "invented"}, {"owned_client_cleanup": False}, {"harness_error": "bad"}):
            self.assertFalse(MODULE.evaluate(dict(evidence(), **change))["passed"])
        self.assertFalse(MODULE.evaluate({})["passed"])

    def test_private_nested_payloads_and_arbitrary_errors_are_never_published(self):
        secret = "PRIVATE_CONTEXT_CHAT_REASONING_SENTINEL"
        value = evidence(); value.update(prompt=secret, raw_result={"reasoning": secret}, role=secret)
        value["after"]["raw_context"] = secret
        result = MODULE.evaluate(value)
        self.assertTrue(result["passed"]); self.assertNotIn(secret, json.dumps(result, allow_nan=False))
        projected = MODULE.client_projection({"actorId": "own", "lastAcceptedSessionId": "session", "state": "Connected",
            "keyboardEnabled": False, "position": {"x": secret}, "velocity": {"x": 0, "y": 0}, "chat": secret})
        self.assertNotIn(secret, json.dumps(projected))

    def test_invalid_cli_is_rejected_without_starting_a_client(self):
        with tempfile.TemporaryDirectory() as folder, patch("subprocess.Popen") as popen:
            base = ["--case", "objective-cancel", "--room", "ABC123", "--client", "/bin/echo", "--output", folder + "/fresh"]
            for extra in (["--port", "1"], ["--timeout", "nan"], ["--room", "bad"], ["--api-url", "http://user:secret@host"]):
                with self.subTest(extra=extra), patch("sys.stderr"):
                    with self.assertRaises(SystemExit): MODULE.arguments(base + extra)
            popen.assert_not_called()


class AsyncSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_held_owned_popen_can_be_terminated(self):
        class FakeProcess:
            pid = 12
            def __init__(self): self.code = None; self.signals = []
            def poll(self): return self.code
            def terminate(self): self.signals.append("terminate"); self.code = 0
            def kill(self): self.signals.append("kill"); self.code = -9
            def wait(self): return self.code
        process = FakeProcess()
        self.assertFalse(await MODULE.stop_owned_client(process, 999)); self.assertEqual(process.signals, [])
        self.assertTrue(await MODULE.stop_owned_client(process, 12)); self.assertEqual(process.signals, ["terminate"])

    async def test_task_drain_is_bounded_even_when_cancel_is_suppressed(self):
        started, released = asyncio.Event(), asyncio.Event()
        async def slow():
            started.set()
            try: await asyncio.Event().wait()
            except asyncio.CancelledError: await released.wait()
        task = asyncio.create_task(slow()); await started.wait()
        self.assertFalse(await MODULE.drain(task, timeout=.01))
        released.set(); await task

    async def test_stop_interrupts_provider_wait_without_waiting_for_model_timeout(self):
        stopping = asyncio.Event(); task = asyncio.create_task(asyncio.Event().wait())
        stopping.set()
        with self.assertRaisesRegex(RuntimeError, "harness_interrupted"):
            await MODULE.wait_completion(task, timeout=240, stopping=stopping)
        self.assertFalse(task.done())  # The caller owns actual provider cleanup.
        task.cancel(); self.assertTrue(await MODULE.drain(task, .1))

    async def test_actual_objectiverunner_cancel_keeps_session_and_releases_foreground(self):
        from actor_runtime.brains import SequenceBrain, SkillCall, FinishObjective
        from actor_runtime.builtin_skills import skill_package
        from actor_runtime.extensions import ExtensionRegistry
        from actor_runtime.objectives import Objective, ObjectiveRunner
        from actor_runtime.session import RuntimeSession
        from actor_runtime.skills import SkillRegistry, SkillScheduler
        from actor_runtime.world import WorldState
        from actor.core.v1 import actor_pb2 as core
        session = RuntimeSession(ExtensionRegistry()); session.session_id = "test-session"
        session.manifest = core.CapabilityManifest(game_id="offline-fixture", game_version="1", protocol_version=core.ProtocolVersion(major=1))
        session.world = WorldState("self"); session.ready.set()
        registry = SkillRegistry()
        for definition in skill_package(): registry.register(definition)
        scheduler = SkillScheduler(session, registry)
        brain = SequenceBrain([SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 30.0}), FinishObjective(status="success")])
        runner = ObjectiveRunner(session, registry, brain, scheduler=scheduler, run_finalizers=False)
        task = asyncio.create_task(runner.run(Objective(text="Wait until the explicit API cancellation.")))
        async with asyncio.timeout(1):
            while scheduler.working_skill_metadata() is None: await asyncio.sleep(.001)
        await runner.cancel(); result = await task
        self.assertEqual(str(result.status), "cancelled"); self.assertEqual(result.reason, "objective_cancelled")
        self.assertFalse(session.closed.is_set()); self.assertIsNone(scheduler.working_skill_metadata()); self.assertTrue(brain.closed)
        followup = await SkillScheduler(session, registry).run("actor.wait", {"duration_seconds": 0.0})
        self.assertEqual(followup.status, "succeeded"); self.assertEqual(session.commands_sent, 0)

    async def test_real_codexbrain_cap_raises_before_second_adapter_call(self):
        from actor_codex.brain import CodexBrain
        from actor_codex.adapter import CodexReply
        from actor_runtime.brains import SequenceBrain
        from actor_runtime.builtin_skills import skill_package
        from actor_runtime.extensions import ExtensionRegistry
        from actor_runtime.objectives import Objective, ObjectiveRunner
        from actor_runtime.session import RuntimeSession
        from actor_runtime.skills import SkillRegistry
        from actor_runtime.world import WorldState
        from actor.core.v1 import actor_pb2 as core
        class FakeAdapter:
            def __init__(self): self.calls = 0; self.closed = False
            async def start_episode(self, objective, catalog): pass
            async def decide(self, context):
                self.calls += 1
                return CodexReply(json.dumps({"decision": {"kind": "skill", "skill_id": "actor.wait", "arguments": {"duration_seconds": .001}}}),
                    "fake-offline-turn", "completed", .01, None, 0)
            async def interrupt(self): pass
            async def interrupt_decision(self): pass
            async def close(self): self.closed = True
            def report(self): return {"unique_model_turns": self.calls, "usage_totals": {}, "closed": self.closed}
        session = RuntimeSession(ExtensionRegistry()); session.session_id = "test-session"
        session.manifest = core.CapabilityManifest(game_id="offline-fixture", game_version="1", protocol_version=core.ProtocolVersion(major=1))
        session.world = WorldState("self"); session.ready.set()
        registry = SkillRegistry()
        for definition in skill_package(): registry.register(definition)
        adapter = FakeAdapter(); brain = MODULE.BrainProbe(CodexBrain(adapter=adapter, max_turns=1))
        runner = ObjectiveRunner(session, registry, brain, max_decisions=4, run_finalizers=False)
        result = await runner.run(Objective(text="Wait then request the next bounded decision."))
        self.assertEqual(brain.denials, ["codex_turn_limit"]); self.assertEqual(adapter.calls, 1)
        self.assertEqual(brain.attempts, 2); self.assertEqual(brain.max_active, 1)
        self.assertEqual(str(result.status), "failed"); self.assertEqual(result.reason, "brain_failure")
        self.assertTrue(adapter.closed); self.assertFalse(session.closed.is_set())
        self.assertEqual(result.skill_results[0]["status"], "succeeded")

    async def test_probe_does_not_convert_unrelated_errors_into_budget_evidence(self):
        from actor_runtime.errors import ActorError
        class Fake:
            async def decide(self, context): raise ActorError("brain_cancelled", "not a budget denial")
        probe = MODULE.BrainProbe(Fake())
        with self.assertRaises(ActorError): await probe.decide(None)
        self.assertEqual(probe.denials, []); self.assertEqual(probe.active, 0)


if __name__ == "__main__": unittest.main()
