"""Offline instrumentation tests. Fake adapters never count as live acceptance."""
import asyncio
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from actor_social_threat import (AuditStore, ThreatAuditBrain, ThreatAuditPolicy,
                                brain, SCHEMA, SOCIAL_VISIBLE_ATTACK, SOCIAL_VISIBLE_WORLD, SOCIAL_OWN_STATE, SOCIAL_MATCH)
from actor_runtime.brains import SkillCall, FinishObjective
from actor_runtime.episodes import EpisodeDirective
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import DecisionContext, GameMetadata, PayloadSnapshot
from actor_runtime.extensions import type_url
from google.protobuf.json_format import MessageToDict
from extensions.agents_arena.v1 import social_pb2 as pb

spec = importlib.util.spec_from_file_location("threat_evaluator", HERE.parent / "social-validation/evaluate.py")
EVAL = importlib.util.module_from_spec(spec); spec.loader.exec_module(EVAL)
NOW, ACTOR, MATCH = 1_000_000, "private-own-actor", "private-match"


def snapshot(capability, value, sequence=1):
    return PayloadSnapshot(capability_id=capability, entity_id=ACTOR, type_url=type_url(type(value)),
        sequence=sequence, revision=sequence, age_seconds=.01,
        value=MessageToDict(value), data_policy="session_only")


def context(*, body=True, attack=True):
    match = pb.SocialMatchStateObservation(match_id=MATCH, mode="SocialDeduction", phase=pb.SOCIAL_PHASE_EXPLORATION,
        phase_revision=2, started_at_unix_ms=NOW-10000)
    own = pb.SocialOwnStateObservation(match_id=MATCH, actor_id=ACTOR, alignment=pb.SOCIAL_ALIGNMENT_CREW,
        life=pb.SOCIAL_LIFE_ALIVE, assigned=True)
    visible = pb.SocialVisibleWorldObservation(match_id=MATCH, recipient_actor_id=ACTOR,
        captured_at_unix_ms=NOW-10, server_revision=2)
    if body:
        visible.bodies.add(body_id="private-body", victim_actor_id="private-victim", observed_revision=2)
    events = []
    if attack:
        event = pb.SocialVisibleAttackEvent(match_id=MATCH, observer_actor_id=ACTOR,
            attacker_actor_id="private-attacker", victim_actor_id="private-victim",
            observed_at_unix_ms=NOW-10, observed_revision=2)
        events = [snapshot(SOCIAL_VISIBLE_ATTACK, event, 5)]
    return DecisionContext(objective=Objective(text="same standard objective"), decision_index=1,
        game=GameMetadata(game_id="agents-arena", game_version="0.8.0", protocol_major=1, protocol_minor=0),
        actor_id=ACTOR, observations=[snapshot(SOCIAL_MATCH, match), snapshot(SOCIAL_OWN_STATE, own),
            snapshot(SOCIAL_VISIBLE_WORLD, visible)], recent_events=events, available_skills=[], cognitive_revision=3)


def terminal(store, *, crew=True, complete=True, actor=ACTOR, scope=MATCH, replay=None):
    session = SimpleNamespace(world=SimpleNamespace(self_actor_id=actor))
    directive = EpisodeDirective(scope_id=scope, episode_key="done", state="terminal", terminal_complete=complete,
        outcome={"authoritative_summary": {"match_id": scope, "mode": "SocialDeduction", "replay": replay or [], "players": [
            {"actor_id": ACTOR, "alignment": "SOCIAL_ALIGNMENT_CREW" if crew else "SOCIAL_ALIGNMENT_IMPOSTOR"}]}})
    store.finalize(session, directive)
    return session, directive


def runtime_proof(*, status="accepted", skill_status="succeeded"):
    store = AuditStore(clock=lambda: NOW)
    capture = store.before(context(), store.new_episode())
    store.after(capture, SkillCall(skill_id="agents-arena.report-body", arguments={"body_id": "private-body"}))
    terminal(store)
    return {"match": {"result": {"outcome": {"threat_audit": store.projection()}}}, "cognitive": {"episodes": [{
        "decisions": [{"index": 1, "cognitive_revision": 3, "status": status, "skill_id": "agents-arena.report-body"}],
        "result": {"skill_results": [{"skill_id": "agents-arena.report-body", "status": skill_status}]}}]}}


class AuditTests(unittest.TestCase):
    def test_plugin_factory_is_actual_codex_with_finite_limits_and_no_fake_adapter_option(self):
        from actor_codex.brain import CodexBrain
        from actor_codex.adapter import OfficialCodexAdapter
        value = brain(model=None)
        self.assertIsInstance(value.delegate, CodexBrain)
        self.assertIsInstance(value.delegate.adapter, OfficialCodexAdapter)
        self.assertEqual(value.delegate.max_turns, 24)
        self.assertEqual(value.delegate.usage_limit_tokens, 1000000)
        with self.assertRaises(TypeError): brain(adapter=object())

    def test_before_then_after_and_no_private_projection(self):
        store = AuditStore(clock=lambda: NOW)
        captured = store.before(context(), store.new_episode())
        self.assertTrue(captured[0]["fresh_threat_before_decide"])
        self.assertFalse(captured[0]["candidate_returned"])
        self.assertIsNone(store.projection())
        store.after(captured, SkillCall(skill_id="agents-arena.report-body", arguments={"body_id": "private-body"}))
        terminal(store)
        text = json.dumps(store.projection())
        for private in (ACTOR, MATCH, "private-body", "private-attacker", "private-victim", "same standard objective"):
            self.assertNotIn(private, text)
        self.assertTrue(store.projection()["records"][0]["target_matches_permitted_fact"])

    def test_wrong_typed_attack_metadata_or_identity_not_fresh(self):
        base = context(body=False)
        record = base.recent_events[0]
        mutations = [{"capability_id": "actor.standard.communication.message-received"}, {"type_url": "wrong"},
            {"entity_id": "someone-else"}, {"opaque": True}, {"truncated": True}, {"expired": True},
            {"stale": True}, {"age_seconds": 3.01}, {"age_seconds": -1.0}, {"age_seconds": float("nan")}]
        for change in mutations:
            with self.subTest(change=change):
                row = AuditStore(clock=lambda: NOW).before(base.model_copy(update={"recent_events": [record.model_copy(update=change)]}), 1)[0]
                self.assertEqual(row["fresh_attack_count"], 0)
        for field, value in (("matchId", "foreign"), ("observerActorId", "foreign"), ("observedRevision", "0"),
                             ("observedAtUnixMs", str(NOW+2000)), ("observedAtUnixMs", str(NOW-20000))):
            with self.subTest(field=field, value=value):
                bad = record.model_copy(update={"value": {**record.value, field: value}})
                row = AuditStore(clock=lambda: NOW).before(base.model_copy(update={"recent_events": [bad]}), 1)[0]
                self.assertFalse(row["fresh_threat_before_decide"])

    def test_chat_claim_hypothesis_and_visible_activity_not_authoritative(self):
        base = context(body=False, attack=False)
        claim = {"kind": "player_claim", "capability_id": SOCIAL_VISIBLE_ATTACK, "scope_id": MATCH,
            "source_actor_id": ACTOR, "expires_at_unix_ms": NOW+1000,
            "content": {"text": "I saw an attack", "attacker_actor_id": "secret"}}
        for kind in ("player_claim", "hypothesis"):
            row = AuditStore(clock=lambda: NOW).before(base.model_copy(update={"working_knowledge": [{**claim, "kind": kind}]}), 1)[0]
            self.assertFalse(row["fresh_threat_before_decide"])
            self.assertEqual(row["historical_attack_count"], 0)
        activity = snapshot("agents-arena.social-visible-activity", pb.SocialVisibleActivityEvent(
            match_id=MATCH, observer_actor_id=ACTOR, actor_id="private-attacker", activity="attack"))
        self.assertFalse(AuditStore(clock=lambda: NOW).before(base.model_copy(update={"recent_events": [activity]}), 1)[0]["fresh_threat_before_decide"])

    def test_duplicate_event_and_historical_witness_are_distinct(self):
        base = context(body=False)
        event = base.recent_events[0]
        ctx = base.model_copy(update={"trigger_events": [event]})
        self.assertEqual(AuditStore(clock=lambda: NOW).before(ctx, 1)[0]["fresh_attack_count"], 1)
        known = {"kind": "historical_observation", "capability_id": SOCIAL_VISIBLE_ATTACK, "scope_id": MATCH,
            "source_actor_id": ACTOR, "expires_at_unix_ms": NOW+1000, "content": event.value}
        ctx = base.model_copy(update={"recent_events": [], "working_knowledge": [known]})
        row = AuditStore(clock=lambda: NOW).before(ctx, 1)[0]
        self.assertEqual(row["historical_attack_count"], 1)
        self.assertFalse(row["fresh_threat_before_decide"])

    def test_own_crew_alive_and_current_body_required(self):
        base = context(attack=False)
        for flag in ("foreign", "dead", "impostor", "stale"):
            with self.subTest(flag=flag):
                records = list(base.observations)
                value = copy.deepcopy(records[1].value)
                if flag == "foreign": value["actorId"] = "foreign"
                if flag == "dead": value["life"] = "SOCIAL_LIFE_DEAD"
                if flag == "impostor": value["alignment"] = "SOCIAL_ALIGNMENT_IMPOSTOR"
                records[1] = records[1].model_copy(update={"value": value, "stale": flag == "stale"})
                row = AuditStore(clock=lambda: NOW).before(base.model_copy(update={"observations": records}), 1)[0]
                self.assertFalse(row["fresh_threat_before_decide"])
        records = list(base.observations)
        records[2] = records[2].model_copy(update={"value": {**records[2].value, "capturedAtUnixMs": str(NOW-4000)}})
        self.assertFalse(AuditStore(clock=lambda: NOW).before(base.model_copy(update={"observations": records}), 1)[0]["fresh_threat_before_decide"])

    def test_wrong_body_argument_does_not_match(self):
        store = AuditStore(clock=lambda: NOW)
        captured = store.before(context(), 1)
        store.after(captured, SkillCall(skill_id="agents-arena.report-body", arguments={"body_id": "not-observed"}))
        self.assertFalse(captured[0]["target_matches_permitted_fact"])
        store.after(captured, SkillCall(skill_id="agents-arena.report-body", arguments={"body_id": ["invalid", "input"]}))
        self.assertFalse(captured[0]["target_matches_permitted_fact"])

    def test_phase_action_confirmation_is_exact_own_replay_inside_actual_call_window(self):
        for change in ({}, {"actor_id": "foreign"}, {"outcome": "emergency_meeting"},
                       {"kind": "kill"}, {"elapsed_ms": "10099"}, {"elapsed_ms": "10201"}):
            with self.subTest(change=change):
                clock = [NOW]; store = AuditStore(clock=lambda: clock[0])
                captured = store.before(context(), store.new_episode())
                clock[0] = NOW + 100
                store.after(captured, SkillCall(skill_id="agents-arena.report-body", arguments={"body_id": "private-body"}))
                clock[0] = NOW + 200
                store.before(context().model_copy(update={"decision_index": 2}), 1)
                clock[0] = NOW + 300
                action = {"kind": "meeting", "actor_id": ACTOR, "outcome": "body_report", "elapsed_ms": "10150", **change}
                terminal(store, replay=[action])
                self.assertEqual(captured[0]["postgame_action_confirmed"], not bool(change))
                self.assertNotIn(ACTOR, json.dumps(store.projection()))
        clock = [NOW]; store = AuditStore(clock=lambda: clock[0])
        captured = store.before(context(), 1)
        store.after(captured, SkillCall(skill_id="agents-arena.report-body", arguments={"body_id": "private-body"}))
        clock[0] += 100
        duplicate = {"kind": "meeting", "actor_id": ACTOR, "outcome": "body_report", "elapsed_ms": "10050"}
        terminal(store, replay=[duplicate, duplicate])
        self.assertFalse(captured[0]["postgame_action_confirmed"])

    def test_old_attacker_does_not_match_a_different_fresh_threat(self):
        base = context()
        content = {**base.recent_events[0].value, "attackerActorId": "historical-other"}
        known = {"kind": "historical_observation", "capability_id": SOCIAL_VISIBLE_ATTACK, "scope_id": MATCH,
            "source_actor_id": ACTOR, "expires_at_unix_ms": NOW+1000, "content": content}
        store = AuditStore(clock=lambda: NOW)
        captured = store.before(base.model_copy(update={"working_knowledge": [known]}), 1)
        store.after(captured, SkillCall(skill_id="agents-arena.evade-threat",
            arguments={"actor_id": "historical-other", "destination_room_id": "security"}))
        self.assertTrue(captured[0]["fresh_threat_before_decide"])
        self.assertEqual(captured[0]["historical_attack_count"], 1)
        self.assertFalse(captured[0]["target_matches_permitted_fact"])

    def test_terminal_requires_complete_exact_own_crew(self):
        for kwargs in ({"complete": False}, {"crew": False}, {"actor": "foreign"}, {"scope": "foreign"}):
            with self.subTest(kwargs=kwargs):
                store = AuditStore(clock=lambda: NOW); store.before(context(), 1); terminal(store, **kwargs)
                self.assertIsNone(store.projection())
        store = AuditStore(clock=lambda: NOW); store.before(context(), 1)
        store.finalize(SimpleNamespace(), EpisodeDirective(state="active", scope_id=MATCH, episode_key="play"))
        self.assertIsNone(store.projection())

    def test_policy_delegates_all_game_behavior_and_only_adds_terminal_audit(self):
        store = AuditStore(clock=lambda: NOW); store.before(context(), store.new_episode())
        session, done = terminal(store)
        active = EpisodeDirective(scope_id=MATCH, episode_key="normal", state="active")
        class Delegate:
            directive = active
            def evaluate(self, received): self.asserted_session = received; return self.directive
            def observe(self, received, knowledge): self.observed = (received, knowledge); return "unchanged"
            def skill_available(self, received, definition): self.checked = (received, definition); return False
        delegate = Delegate(); wrapper = ThreatAuditPolicy(store=store, delegate=delegate)
        self.assertIs(wrapper.evaluate(session), active)
        knowledge, definition = object(), object()
        self.assertEqual(wrapper.observe(session, knowledge), "unchanged")
        self.assertFalse(wrapper.skill_available(session, definition))
        self.assertEqual(delegate.observed, (session, knowledge)); self.assertEqual(delegate.checked, (session, definition))
        delegate.directive = done
        value = wrapper.evaluate(session)
        self.assertEqual(value.state, done.state); self.assertEqual(value.episode_key, done.episode_key)
        self.assertEqual(value.outcome["authoritative_summary"], done.outcome["authoritative_summary"])
        self.assertEqual(value.outcome["threat_audit"]["schema"], SCHEMA)

    def test_scope_change_and_overflow_fail_closed(self):
        store = AuditStore(clock=lambda: NOW); store.before(context(), 1)
        foreign = context().model_copy(update={"actor_id": "other"})
        # Coherent but genuinely new identity cannot enter an existing audit.
        foreign = context().model_copy(update={"observations": [r.model_copy(update={"value": {
            **r.value, "matchId": "other"}}) for r in context().observations]})
        store.before(foreign, 1); terminal(store)
        self.assertIsNone(store.projection())
        store = AuditStore(clock=lambda: NOW)
        for _ in range(513): store.before(context(), 1)
        terminal(store); self.assertIsNone(store.projection())

    def test_evaluator_requires_actual_matching_dispatch_and_success(self):
        own = {"alignment": "SOCIAL_ALIGNMENT_CREW"}
        self.assertTrue(EVAL.threat_proof(runtime_proof(), own)["passed"])
        for status in ("stale", "rejected", "interrupted", "running"):
            with self.subTest(status=status):
                self.assertFalse(EVAL.threat_proof(runtime_proof(status=status), own)["passed"])
        for status in ("failed", "cancelled", "timed_out"):
            self.assertFalse(EVAL.threat_proof(runtime_proof(skill_status=status), own)["passed"])
        value = runtime_proof(); value["cognitive"]["episodes"][0]["decisions"][0]["cognitive_revision"] = 4
        self.assertFalse(EVAL.threat_proof(value, own)["passed"])
        value = runtime_proof(); value["cognitive"]["episodes"][0]["result"]["skill_results"] = []
        self.assertFalse(EVAL.threat_proof(value, own)["passed"])
        self.assertFalse(EVAL.threat_proof(runtime_proof(), {"alignment": "SOCIAL_ALIGNMENT_IMPOSTOR"})["passed"])

    def test_server_confirmed_phase_action_keeps_local_cancelled_status_honest(self):
        value = runtime_proof(skill_status="cancelled")
        row = value["match"]["result"]["outcome"]["threat_audit"]["records"][0]
        row["postgame_action_confirmed"] = True
        result = EVAL.threat_proof(value, {"alignment": "SOCIAL_ALIGNMENT_CREW"})
        self.assertTrue(result["passed"])
        self.assertEqual(result["succeeded_reactions"], 0)
        self.assertEqual(result["server_confirmed_reactions"], 1)
        self.assertEqual(result["completed_reactions"], 1)
        value["cognitive"]["episodes"][0]["decisions"][0]["status"] = "stale"
        self.assertFalse(EVAL.threat_proof(value, {"alignment": "SOCIAL_ALIGNMENT_CREW"})["passed"])

    def test_strict_participation_requires_separate_real_discussion_send_and_vote(self):
        value = runtime_proof()
        audit = value["match"]["result"]["outcome"]["threat_audit"]
        episode = value["cognitive"]["episodes"][0]
        for index, skill in ((2, "actor.send-message"), (3, "agents-arena.cast-vote")):
            audit["records"].append({**audit["records"][0], "decision_index": index, "fresh_attack_count": 0,
                "fresh_visible_body_count": 0, "fresh_threat_before_decide": False, "returned_skill_id": skill,
                "in_discussion_context": index == 2, "discussion_channel_selected": index == 2})
            episode["decisions"].append({"index": index, "cognitive_revision": 3, "status": "accepted", "skill_id": skill})
            episode["result"]["skill_results"].append({"skill_id": skill, "status": "succeeded"})
        audit["decisions_observed"] = 3
        result = EVAL.threat_proof(value, {"alignment": "SOCIAL_ALIGNMENT_CREW"})
        self.assertTrue(result["full_participation_passed"])
        self.assertEqual(result["discussion_communications_succeeded"], 1)
        self.assertEqual(result["votes_succeeded"], 1)
        original = copy.deepcopy(value)
        for field in ("in_discussion_context", "discussion_channel_selected"):
            bad = copy.deepcopy(original)
            bad["match"]["result"]["outcome"]["threat_audit"]["records"][1][field] = False
            self.assertFalse(EVAL.threat_proof(bad, {"alignment": "SOCIAL_ALIGNMENT_CREW"})["full_participation_passed"])
        bad = copy.deepcopy(original)
        bad["cognitive"]["episodes"][0]["result"]["skill_results"][2]["status"] = "cancelled"
        self.assertFalse(EVAL.threat_proof(bad, {"alignment": "SOCIAL_ALIGNMENT_CREW"})["full_participation_passed"])

    def test_discussion_audit_only_retains_channel_boolean_never_message(self):
        base = context(body=False, attack=False)
        records = list(base.observations)
        records[0] = records[0].model_copy(update={"value": {**records[0].value, "phase": "SOCIAL_PHASE_DISCUSSION"}})
        store = AuditStore(clock=lambda: NOW)
        captured = store.before(base.model_copy(update={"observations": records}), store.new_episode())
        store.after(captured, SkillCall(skill_id="actor.send-message", arguments={"channel_id": "discussion", "text": "private-message-sentinel"}))
        terminal(store)
        self.assertTrue(captured[0]["in_discussion_context"] and captured[0]["discussion_channel_selected"])
        self.assertNotIn("private-message-sentinel", json.dumps(store.projection()))


class TransparentTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_episode_collection_preserves_join_when_ids_are_privately_masked(self):
        # This exercises actual report/collection/privacy code; it is not a
        # synthetic action's claim to have executed in an actual game.
        from actor_runtime.privacy import SessionPrivacy
        from actor_runtime.brains import SequenceBrain
        from actor_runtime.episodes import MatchEpisodeController
        from actor_runtime.objectives import ObjectiveRunner, ObjectiveResult, ObjectiveStatus
        from actor_runtime.skills import SkillRegistry
        value = runtime_proof(); original = value["cognitive"]["episodes"][0]
        privacy = SessionPrivacy(); privacy.private_activity = True
        session = SimpleNamespace(privacy=privacy, event_router=None)
        brain = SequenceBrain([]); await brain.start(Objective(text="offline"), [])
        runner = ObjectiveRunner(session, SkillRegistry(), brain)
        runner.objective = Objective(text="offline")
        runner._decisions = original["decisions"]
        runner._skill_results = original["result"]["skill_results"]
        runner.result = ObjectiveResult(objective_id=runner.objective.id, status=ObjectiveStatus.SUCCEEDED,
            reason="finished", duration_seconds=1.0, decision_count=1, skill_results=runner._skill_results)
        controller = MatchEpisodeController(session, SkillRegistry(), lambda: brain, object())
        controller.runner = runner
        completed = asyncio.create_task(asyncio.sleep(0, result=runner.result)); await completed
        controller._collect_runner(completed)
        self.assertEqual(controller._reports[0]["decisions"][0]["skill_id"], "[private_activity]")
        self.assertEqual(controller._reports[0]["result"]["skill_results"][0]["skill_id"], "[private_activity]")
        privacy.activity_revealed = True
        value["cognitive"]["episodes"] = controller._reports
        self.assertTrue(EVAL.threat_proof(value, {"alignment": "SOCIAL_ALIGNMENT_CREW"})["passed"])
        value["cognitive"]["episodes"][0]["decisions"][0]["status"] = "stale"
        self.assertFalse(EVAL.threat_proof(value, {"alignment": "SOCIAL_ALIGNMENT_CREW"})["passed"])

    async def test_wrapper_calls_real_codex_brain_unchanged_with_offline_adapter(self):
        # The instrumented production CodexBrain is real. The adapter is an
        # explicit offline double; this test is never a live model claim.
        from actor_codex.brain import CodexBrain
        from actor_codex.adapter import CodexReply
        class Adapter:
            async def start_episode(self, objective, catalog): self.started = (objective, catalog)
            async def decide(self, text):
                self.received = text
                return CodexReply(output=json.dumps({"decision": {"kind": "skill", "skill_id": "agents-arena.report-body",
                    "arguments": {"body_id": "private-body"}}}), turn_id="offline", status="completed", duration_seconds=.01, usage={}, tool_calls=0)
            async def interrupt(self): self.interrupted = True
            async def interrupt_decision(self): self.soft = True
            async def close(self): self.closed = True
            def report(self): return {"closed": getattr(self, "closed", False), "usage_totals": {}}
        adapter = Adapter(); delegate = CodexBrain(adapter=adapter); store = AuditStore(clock=lambda: NOW)
        wrapper = ThreatAuditBrain(delegate, store=store); ctx = context(); catalog = []
        await wrapper.start(ctx.objective, catalog)
        decision = await wrapper.decide(ctx)
        self.assertEqual(adapter.received, ctx.external_json())
        self.assertIs(adapter.started[1], catalog)
        self.assertEqual(decision.skill_id, "agents-arena.report-body")
        self.assertNotIn("threat_audit", wrapper.report())
        await wrapper.interrupt(); await wrapper.interrupt_decision()
        self.assertTrue(adapter.interrupted and adapter.soft)
        terminal(store)
        self.assertNotIn("threat_audit", wrapper.report())
        await wrapper.close()
        self.assertEqual(wrapper.report()["provider"], "codex")
        self.assertEqual(wrapper.report()["decision_count"], 1)
        self.assertEqual(wrapper.report()["threat_audit"]["schema"], SCHEMA)

    async def test_exception_is_not_replaced_by_decision(self):
        class Delegate:
            async def decide(self, context): raise asyncio.CancelledError()
        store = AuditStore(clock=lambda: NOW)
        wrapper = ThreatAuditBrain(Delegate(), store=store)
        with self.assertRaises(asyncio.CancelledError): await wrapper.decide(context())
        self.assertFalse(store.rows[0]["candidate_returned"])


if __name__ == "__main__": unittest.main()
