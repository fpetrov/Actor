"""Auth-free tests of the fault runner; no process, server or game is launched."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from google.protobuf.any_pb2 import Any
from actor.core.v1 import actor_pb2 as core, bridge_pb2 as bridge
from actor.standard.v1.transform2d_pb2 import Transform2DObservation
from actor.standard.v1.motion2d_pb2 import Motion2DObservation
from extensions.agents_arena.v1 import social_pb2 as pb


spec = importlib.util.spec_from_file_location("social_faults", Path(__file__).with_name("run_faults.py"))
faults = importlib.util.module_from_spec(spec)
spec.loader.exec_module(faults)


def raw_summary(*, lost_impostor=False):
    players = [{"actor_id": f"actor-{i}", "alignment": "SOCIAL_ALIGNMENT_" + ("IMPOSTOR" if i == (1 if lost_impostor else 2) else "CREW"),
        "life": "SOCIAL_LIFE_" + ("DISCONNECTED" if i == 1 else "DEAD" if i == 3 and not lost_impostor else "ALIVE")} for i in range(1, 5)]
    replay = [{"sequence": "1", "elapsed_ms": "30000", "kind": "disconnect", "actor_id": "actor-1", "outcome": "participant_disconnected"}]
    if not lost_impostor:
        replay.append({"sequence": "2", "elapsed_ms": "45000", "kind": "kill", "actor_id": "actor-2", "target_id": "actor-3", "outcome": "dead"})
    replay.append({"sequence": str(len(replay) + 1), "elapsed_ms": "45000", "kind": "match_ended", "outcome": "impostors_eliminated" if lost_impostor else "parity"})
    return {"match_id": "match-1", "mode": "SocialDeduction", "winning_side": "SOCIAL_ALIGNMENT_CREW" if lost_impostor else "SOCIAL_ALIGNMENT_IMPOSTOR",
        "win_reason": "impostors_eliminated" if lost_impostor else "parity", "duration_ms": "45000", "players": players, "kills": 0 if lost_impostor else 1,
        "replay": replay, "replay_truncated": False}


def evidence(*, case="client-loss", component="client", lost_impostor=False):
    summary = faults.summary_projection(raw_summary(lost_impostor=lost_impostor))
    result = {"case": case, "phase": "exploration", "fault": {"issued": True, "actor_id": "actor-1", "participant": 1,
        "component": component, "pid": 101 if component == "runtime" else 102, "phase": "Exploration", "match_id": "match-1"},
        "guard": dict(manifest_pid_matches=True, child_identity_matches=True, component_only=True, selected_was_alive=True),
        "pre_fault_client": {"actorId": "actor-1", "socialLife": "Alive", "socialPhase": "Exploration", "socialMatchId": "match-1"},
        "launches": [], "audits": [], "clients": [], "runtimes": [], "cleanup": {"started": 8, "remaining": 0},
        "fault_to_gameover_seconds": 45.0, "harness_error": None}
    for i in range(1, 5):
        result["launches"].append({"runtime_pid": 99 + i * 2, "client_pid": 100 + i * 2, "bridge_port": 18600 + i,
            "bridge_credential_sha256": str(i) * 64, "agent_profile": f"profile-{i}", "memory_database": f"/tmp/agent{i}/memory.sqlite3",
            "operation": "match", "brain": "social-fixture-parity"})
        result["audits"].append({"terminal": True, "summary": summary, "probe_failures": 0, "observation_frames": 100,
            "self_actor_id": f"actor-{i}", "observed_body_victim_ids": [] if lost_impostor else ["actor-3"]})
        result["clients"].append({"socialPhase": "GameOver", "socialSummaryPresent": True, "socialMatchId": "match-1", "actorId": f"actor-{i}",
            "lastAcceptedSessionId": f"session-{i}", "meanFps": 60.0, "elapsedSeconds": 50.0, "error_count": 0, "warning_count": 0})
        result["runtimes"].append({"match": {"passed": True, "result": {"status": "completed", "authoritative_terminal": True, "scope_id": "match-1",
            "outcome": {"match_id": "match-1", "winning_side": "SOCIAL_ALIGNMENT_" + summary["winning_side"], "win_reason": summary["win_reason"]}}},
            "runtime": {"sessions": [{"actor_id": f"actor-{i}", "session_id": f"session-{i}"}]}})
    return result


class SummaryTests(unittest.TestCase):
    def test_real_protobuf_json_64_bit_strings_and_default_fields(self):
        from google.protobuf.json_format import MessageToDict, ParseDict
        proto = ParseDict(raw_summary(), pb.SocialMatchSummary())
        projected = faults.summary_projection(MessageToDict(proto, preserving_proto_field_name=True))
        self.assertEqual(projected["duration_ms"], 45000)
        self.assertEqual(projected["replay"][0]["sequence"], 1)
        self.assertEqual(faults.canonical_summary(projected), projected)

    def test_private_and_arbitrary_prose_is_not_in_projection(self):
        value = raw_summary(); value.update(authorization_code="SECRET", text="SECRET", objective="SECRET")
        value["players"][0].update(display_name="SECRET", chat="SECRET")
        self.assertNotIn("SECRET", json.dumps(faults.summary_projection(value)))

    def test_duplicate_actor_invalid_enum_and_order_are_rejected(self):
        for mutation in (lambda x: x["players"][1].update(actor_id="actor-1"),
                lambda x: x["players"][0].update(alignment="SOCIAL_ALIGNMENT_UNSPECIFIED"),
                lambda x: x["replay"][1].update(sequence="1"),
                lambda x: x.update(duration_ms="not-an-integer")):
            value = raw_summary(); mutation(value)
            self.assertIsNone(faults.summary_projection(value))


class AcceptanceTests(unittest.TestCase):
    def test_crew_loss_uses_actual_parity_and_impostor_loss_crew_victory(self):
        for lost_impostor in (False, True):
            with self.subTest(lost_impostor=lost_impostor):
                value = faults.evaluate(evidence(lost_impostor=lost_impostor))
                self.assertTrue(value["passed"])
                self.assertEqual(value["selected_postgame_alignment"], "IMPOSTOR" if lost_impostor else "CREW")
                self.assertEqual(value["postgame_win_reason"], "impostors_eliminated" if lost_impostor else "parity")

    def test_missing_actual_fault_and_missing_summary_do_not_pass(self):
        self.assertFalse(faults.evaluate({})["passed"])
        value = evidence(); value["fault"]["issued"] = False
        self.assertFalse(faults.evaluate(value)["executed"])
        self.assertEqual(faults.evaluate(value)["checks"]["selected_actor_irreversibly_forfeited_disconnected"]["status"], "not_executed")
        value = evidence(); value["audits"][2]["summary"] = None
        self.assertFalse(faults.evaluate(value)["passed"])

    def test_wrong_game_summary_or_runtime_scope_and_session_are_rejected(self):
        for mutation in (lambda x: x["audits"].__setitem__(2, {**x["audits"][2], "summary": {**x["audits"][2]["summary"], "match_id": "other-match"}}),
                lambda x: x["runtimes"][2]["match"]["result"].update(scope_id="other-match"),
                lambda x: x["runtimes"][2]["match"]["result"].update(status="failed"),
                lambda x: x["runtimes"][2]["runtime"]["sessions"][0].update(actor_id="other-actor"),
                lambda x: x["clients"][2].update(lastAcceptedSessionId="other-session")):
            value = evidence(); mutation(value)
            self.assertFalse(faults.evaluate(value)["passed"])

    def test_unknown_case_cannot_claim_an_actual_pass(self):
        value = evidence(); value["case"] = "unknown-case"
        projected = faults.evaluate(value)
        self.assertFalse(projected["executed"])
        self.assertFalse(projected["passed"])

    def test_fault_and_pre_fault_match_ids_must_bind_to_terminal_match(self):
        for area, key in (("fault", "match_id"), ("pre_fault_client", "socialMatchId")):
            value = evidence(); value[area][key] = "foreign-match"
            self.assertFalse(faults.evaluate(value)["passed"])

    def test_duplicate_accepted_session_id_is_rejected_even_with_foreign_actor(self):
        value = evidence()
        value["runtimes"][1]["runtime"]["sessions"].append({"actor_id": "foreign-actor", "session_id": "session-2"})
        self.assertFalse(faults.evaluate(value)["passed"])

    def test_no_body_claim_requires_disconnect_complete_replay_and_no_observed_body(self):
        for mutation in (lambda x: x["audits"][2]["observed_body_victim_ids"].append("actor-1"),
                lambda x: x["audits"][0]["summary"].update(replay_truncated=True),
                lambda x: x["audits"][0]["summary"]["players"][0].update(life="DEAD"),
                lambda x: x["audits"][0]["summary"]["replay"][1].update(target_id="actor-1"),
                lambda x: x["audits"][0]["summary"]["replay"].pop(0)):
            value = evidence(); mutation(value)
            self.assertFalse(faults.evaluate(value)["passed"])

    def test_survivors_complete_but_faulted_runtime_need_not_report_success(self):
        value = evidence(); value["runtimes"][0] = {}
        value["clients"][0]["socialPhase"] = "Exploration"
        value["audits"][0]["terminal"] = False
        self.assertTrue(faults.evaluate(value)["passed"])

    def test_runtime_loss_requires_own_unavailable_zero_keyboard_and_live_fps(self):
        value = evidence(case="runtime-loss", component="runtime")
        self.assertFalse(faults.evaluate(value)["passed"])
        value["recovery"] = dict(completed=True, keyboard_disabled=True, client_alive=True, stopped=True,
            fps_above_30=True, error_state_observed=True, own_disconnected_observed=True, samples=5, seconds=2.5, max_speed=0, minimum_fps=59)
        self.assertTrue(faults.evaluate(value)["passed"])
        for key in ("keyboard_disabled", "client_alive", "stopped", "fps_above_30", "error_state_observed", "own_disconnected_observed"):
            bad = copy.deepcopy(value); bad["recovery"][key] = False
            self.assertFalse(faults.evaluate(bad)["passed"], key)

    def test_single_phase_cannot_claim_both_or_other_faults(self):
        value = evidence(case="exploration-vs-meeting")
        result = faults.evaluate(value)
        self.assertTrue(result["passed"])
        self.assertEqual(result["coverage"], {"exploration": True, "discussion": False})
        self.assertIn("runtime-loss", result["not_executed_cases"])
        value["phase"] = "discussion"
        self.assertFalse(faults.evaluate(value)["passed"])
        value["fault"]["phase"] = "Discussion"
        value["pre_fault_client"]["socialPhase"] = "Discussion"
        self.assertTrue(faults.evaluate(value)["passed"])

    def test_metadata_isolation_cleanup_and_exact_pid_guard_are_required(self):
        for mutation in (lambda x: x["launches"][1].update(runtime_pid=x["launches"][0]["runtime_pid"]),
                lambda x: x["launches"][1].update(bridge_credential_sha256=x["launches"][0]["bridge_credential_sha256"]),
                lambda x: x["cleanup"].update(remaining=1), lambda x: x["cleanup"].update(remaining=False),
                lambda x: x["guard"].update(manifest_pid_matches=False), lambda x: x.update(harness_error="interrupted")):
            value = evidence(); mutation(value)
            self.assertFalse(faults.evaluate(value)["passed"])

    def test_public_acceptance_never_copies_nested_reports_or_prose(self):
        value = evidence(); value.update(private_code="SECRET", text="SECRET", objective="SECRET")
        value["runtimes"][1]["text"] = "SECRET"
        value["clients"][1].update(error="SECRET", chat="SECRET", meanFps="SECRET")
        value["recovery"] = {"samples": "SECRET", "completed": "SECRET"}
        public = json.dumps(faults.evaluate(value))
        for prohibited in ("SECRET", "actor-1", "match-1", "session-1"):
            self.assertNotIn(prohibited, public)


class SpectatorTests(unittest.TestCase):
    def values(self):
        wire = {"schema": faults.PRIVATE_SCHEMA, "probe_failures": 0, "self_actor_id": "fresh-actor", "match_id": "match-1",
            "own": {"actor_id": "fresh-actor", "match_id": "match-1", "life": "SPECTATOR", "alignment": "UNSPECIFIED", "assigned": False, "can_move": False},
            "spectator_samples": 5, "spectator_seconds": 2.5, "spectator_max_displacement": 0, "spectator_max_speed": 0, "commands_sent": 0}
        client = dict(actorId="fresh-actor", socialMatchId="match-1", socialLife="Spectator", socialAssigned=False, keyboardEnabled=False, commands=0)
        return wire, client

    def test_fresh_stationary_no_alignment_no_commands_is_required(self):
        wire, client = self.values()
        self.assertTrue(faults.spectator_projection(wire, client, "old-actor", "match-1")["passed"])
        for key, value in (("alignment", "CREW"), ("assigned", True), ("can_move", True), ("life", "ALIVE")):
            bad = copy.deepcopy(wire); bad["own"][key] = value
            self.assertFalse(faults.spectator_projection(bad, client, "old-actor", "match-1")["passed"])
        for key, value in (("spectator_seconds", 1), ("spectator_samples", 3), ("spectator_max_displacement", .03), ("spectator_max_speed", .06), ("commands_sent", 1)):
            bad = copy.deepcopy(wire); bad[key] = value
            self.assertFalse(faults.spectator_projection(bad, client, "old-actor", "match-1")["passed"])
        self.assertFalse(faults.spectator_projection(wire, client, "fresh-actor", "match-1")["passed"])
        self.assertFalse(faults.spectator_projection(wire, client, "old-actor", "other-match")["passed"])

    def test_late_reconnect_cannot_pass_without_actual_spectator_proof(self):
        value = evidence(case="late-reconnect")
        self.assertFalse(faults.evaluate(value)["passed"])
        wire, client = self.values()
        value["reconnect_audit"], value["reconnect_client"] = wire, client
        value["cleanup"]["started"] = 10
        value["launches"].append({"runtime_pid": 109, "client_pid": 110, "bridge_port": 18605,
            "bridge_credential_sha256": "5" * 64, "agent_profile": "profile-5", "memory_database": "/tmp/agent5/memory.sqlite3", "operation": "serve"})
        self.assertTrue(faults.evaluate(value)["passed"])

    def meeting_values(self):
        wire, client = self.values()
        wire.update(phase="DISCUSSION", phase_revision=7, phase_ends_at_unix_ms=160000,
            spectator_phase_samples=6, spectator_phase_seconds=2.5,
            spectator_phase_first_unix_ms=102000, spectator_phase_last_unix_ms=104500)
        client.update(socialPhase="Discussion", socialPhaseRevision=7, lastAcceptedSessionId="fresh-session")
        runtime = {"runtime": {"sessions": [{"actor_id": "fresh-actor", "session_id": "fresh-session"}]}}
        fault = {"unix_ms": 100000}; launch = {"client_started_unix_ms": 101000}
        return wire, client, runtime, fault, launch

    def meeting_projection(self, values):
        wire, client, runtime, fault, launch = values
        return faults.meeting_reconnect_projection(wire, client, runtime, "old-actor", "match-1", fault, launch, 104400)

    def test_reconnect_requires_real_discussion_epoch_time_window_and_session(self):
        values = self.meeting_values()
        self.assertTrue(self.meeting_projection(values)["passed"])
        for area, key, bad in ((0,"phase","GAME_OVER"), (1,"socialPhase","GameOver"), (1,"socialPhaseRevision",8),
                (0,"phase_ends_at_unix_ms",104000), (0,"spectator_phase_first_unix_ms",99999),
                (0,"spectator_phase_last_unix_ms",103000), (0,"spectator_phase_samples",3),
                (0,"spectator_phase_seconds",1.9), (4,"client_started_unix_ms",130000),
                (1,"lastAcceptedSessionId","wrong-session"), (1,"commands",False)):
            with self.subTest(area=area,key=key):
                bad_values=copy.deepcopy(values); bad_values[area][key]=bad
                self.assertFalse(self.meeting_projection(bad_values)["passed"])
        duplicate=copy.deepcopy(values); duplicate[2]["runtime"]["sessions"] *= 2
        self.assertFalse(self.meeting_projection(duplicate)["passed"])
        bad=copy.deepcopy(values); bad[0]["spectator_phase_first_unix_ms"] = 131000; bad[0]["spectator_phase_last_unix_ms"] = 134000
        self.assertFalse(self.meeting_projection(bad)["passed"])

    def test_meeting_proof_rejects_old_unity_report_or_postgame_only_spectator(self):
        wire, client, runtime, fault, launch = self.meeting_values()
        self.assertFalse(faults.meeting_reconnect_projection(wire, client, runtime,"old-actor","match-1",fault,launch,100000)["passed"])
        wire.pop("spectator_phase_first_unix_ms")
        self.assertFalse(faults.meeting_reconnect_projection(wire, client, runtime,"old-actor","match-1",fault,launch,104400)["passed"])

    def test_discussion_evaluation_uses_frozen_phase_proof_not_final_gameover_values(self):
        value=evidence(case="late-reconnect"); value.update(phase="discussion",discussion_remaining_at_fault_ms=60000,public_kill_cooldown_at_fault_ms=45000)
        value["fault"].update(phase="Discussion",unix_ms=100000); value["pre_fault_client"]["socialPhase"]="Discussion"
        wire,client,runtime,_,launch=self.meeting_values()
        value.update(reconnect_phase_audit=wire,reconnect_phase_client=client,reconnect_phase_runtime=runtime,reconnect_client_file_unix_ms=104400)
        value["launches"].append(dict(runtime_pid=109,client_pid=110,bridge_port=18605,bridge_credential_sha256="5"*64,
            agent_profile="profile-5",memory_database="/tmp/agent5/memory.sqlite3",operation="serve",**launch))
        value["cleanup"]["started"]=10
        for audit in value["audits"]: audit["summary"]=copy.deepcopy(audit["summary"])
        for audit in value["audits"]:
            rows=audit["summary"]["replay"]
            rows.insert(0,dict(sequence=1,elapsed_ms=1000,kind="phase",actor_id="",target_id="",outcome="Discussion"))
            rows.insert(0,dict(sequence=0,elapsed_ms=1000,kind="meeting",actor_id="actor-2",target_id="",outcome="body_report"))
            for index,row in enumerate(rows):row["sequence"]=index+1
        value["reconnect_audit"]=dict(wire,phase="GAME_OVER")
        value["reconnect_client"]=dict(client,socialPhase="GameOver")
        self.assertTrue(faults.evaluate(value)["passed"])
        for field in ("reconnect_phase_audit","reconnect_phase_client","reconnect_phase_runtime"):
            bad=copy.deepcopy(value); bad.pop(field)
            self.assertFalse(faults.evaluate(bad)["passed"])
        bad=copy.deepcopy(value); bad["discussion_remaining_at_fault_ms"]=44000
        self.assertFalse(faults.evaluate(bad)["passed"])
        bad=copy.deepcopy(value); bad["public_kill_cooldown_at_fault_ms"]=5000
        self.assertFalse(faults.evaluate(bad)["passed"])
        bad=copy.deepcopy(value); bad["reconnect_phase_client"]["lastAcceptedSessionId"]="session-2"
        bad["reconnect_phase_runtime"]["runtime"]["sessions"][0]["session_id"]="session-2"
        self.assertFalse(faults.evaluate(bad)["passed"])
        encoded=json.dumps(faults.evaluate(value))
        for private in ("fresh-actor","fresh-session","match-1","104400","131000"):
            self.assertNotIn(private,encoded)


class WireTests(unittest.TestCase):
    def setUp(self):
        self.time = 0.0
        self.audit = faults.WireAudit(clock=lambda: self.time, unix=lambda: int(self.time * 1000))
        self.audit.inspect(bridge.GameToRuntime(manifest=core.CapabilityManifest(self_actor_id="self")))

    def envelope(self, cap, value, actor="self"):
        packed = Any(); packed.Pack(value)
        return core.ObservationEnvelope(capability_id=cap, entity_id=actor, payload=packed)

    def frame(self, values):
        self.time += .5
        self.audit.inspect(bridge.GameToRuntime(observation=core.ObservationFrame(observations=[self.envelope(cap, value) for cap, value in values])))

    def test_only_real_new_pose_motion_frames_count_towards_stationary_spectator(self):
        own = pb.SocialOwnStateObservation(actor_id="self", match_id="match-1", life=pb.SOCIAL_LIFE_SPECTATOR)
        values = [("agents-arena.social-match", pb.SocialMatchStateObservation(match_id="match-1", phase=pb.SOCIAL_PHASE_EXPLORATION)),
            ("agents-arena.social-own-state", own), ("actor.standard.transform2d", Transform2DObservation(x=1, y=2)),
            ("actor.standard.motion2d", Motion2DObservation())]
        for _ in range(6): self.frame(values)
        self.assertEqual(self.audit.spectator_samples, 6)
        self.assertGreaterEqual(self.audit.spectator_seconds, 2)
        for _ in range(6): self.frame([])
        self.assertEqual(self.audit.spectator_samples, 6)

    def test_discussion_samples_reset_on_phase_change_gap_and_require_current_own_and_match(self):
        match=pb.SocialMatchStateObservation(match_id="match-1",phase=pb.SOCIAL_PHASE_DISCUSSION,phase_revision=7,phase_ends_at_unix_ms=100000)
        own=pb.SocialOwnStateObservation(actor_id="self",match_id="match-1",life=pb.SOCIAL_LIFE_SPECTATOR)
        values=[("agents-arena.social-match",match),("agents-arena.social-own-state",own),
            ("actor.standard.transform2d",Transform2DObservation()),("actor.standard.motion2d",Motion2DObservation())]
        for _ in range(6):self.frame(values)
        self.assertEqual(self.audit.spectator_phase_samples,6);self.assertGreaterEqual(self.audit.spectator_phase_seconds,2)
        for _ in range(4):self.frame(values[2:])
        self.assertEqual(self.audit.spectator_phase_samples,6)
        self.frame(values)
        self.assertEqual(self.audit.spectator_phase_samples,1)
        self.assertEqual(self.audit.spectator_phase_seconds,0)
        match.phase=pb.SOCIAL_PHASE_VOTING;match.phase_revision=8
        self.frame(values)
        self.assertEqual(self.audit.spectator_phase_samples,1)
        self.assertEqual(self.audit.phase,"VOTING")

    def test_foreign_entity_wrong_canonical_type_and_foreign_own_are_errors(self):
        value = pb.SocialOwnStateObservation(actor_id="self", match_id="match-1")
        envelope = self.envelope("agents-arena.social-own-state", value, actor="foreign")
        self.audit.inspect(bridge.GameToRuntime(observation=core.ObservationFrame(observations=[envelope])))
        envelope = self.envelope("agents-arena.social-own-state", value); envelope.payload.type_url = "untrusted/" + value.DESCRIPTOR.full_name
        self.audit.inspect(bridge.GameToRuntime(observation=core.ObservationFrame(observations=[envelope])))
        self.audit.payload(pb.SocialOwnStateObservation(actor_id="foreign", match_id="match-1"))
        self.assertEqual(self.audit.faults, 3)

    def test_body_victims_are_bounded_and_only_permitted_wire_ids_retained(self):
        self.frame([("agents-arena.social-visible-world", pb.SocialVisibleWorldObservation(recipient_actor_id="self",
            bodies=[pb.SocialVisibleBody(body_id="body", victim_actor_id="seen-victim")]))])
        self.assertEqual(self.audit.report()["observed_body_victim_ids"], ["seen-victim"])
        self.audit.payload(pb.SocialVisibleWorldObservation(recipient_actor_id="foreign", bodies=[pb.SocialVisibleBody(victim_actor_id="leak")]))
        self.assertNotIn("leak", self.audit.body_victims)
        self.assertEqual(self.audit.faults, 1)

    def test_postgame_requires_terminal_but_same_frame_order_is_irrelevant(self):
        from google.protobuf.json_format import ParseDict
        summary = ParseDict(raw_summary(), pb.SocialMatchSummary())
        self.audit.payload(pb.SocialPostgameObservation(summary=summary))
        self.assertEqual(self.audit.faults, 1)
        self.audit.faults = 0
        self.frame([("agents-arena.social-postgame", pb.SocialPostgameObservation(summary=summary)),
            ("agents-arena.social-match", pb.SocialMatchStateObservation(match_id="match-1", phase=pb.SOCIAL_PHASE_GAME_OVER))])
        self.assertTrue(self.audit.terminal)
        self.assertIsNotNone(self.audit.summary)
        self.assertEqual(self.audit.faults, 0)

    def test_nonfinite_spectator_motion_is_not_stationary_evidence(self):
        self.audit.own = dict(life="SPECTATOR", alignment="UNSPECIFIED", assigned=False, can_move=False)
        self.audit.sample_spectator(Transform2DObservation(x=float("nan")), Motion2DObservation())
        self.assertEqual(self.audit.spectator_samples, 0)
        self.assertEqual(self.audit.faults, 1)


class ProcessSafetyTests(unittest.TestCase):
    def test_only_exact_owned_live_child_can_be_faulted(self):
        process = Mock(pid=100); process.poll.return_value = None
        guard = faults.fault_owned(process, {"runtime_pid": 100}, "runtime")
        process.kill.assert_called_once_with(); process.wait.assert_called_once_with(timeout=5)
        self.assertTrue(guard["child_identity_matches"])

    def test_wrong_manifest_pid_exited_child_and_unknown_component_are_never_signalled(self):
        for pid, status, component in ((99, None, "runtime"), (100, 0, "runtime"), (100, None, "server")):
            process = Mock(pid=100); process.poll.return_value = status
            with self.assertRaises(RuntimeError): faults.fault_owned(process, {component + "_pid": pid}, component)
            process.kill.assert_not_called()

    def test_cleanup_only_holds_own_children_and_requests_runtime_stop_before_client(self):
        order = []
        runtime, client = Mock(), Mock()
        for label, process in (("runtime", runtime), ("client", client)):
            process.poll.side_effect = [None, 0]
            process.terminate.side_effect = lambda label=label: order.append(label)
        value = faults.cleanup_owned([("client", client), ("runtime", runtime)])
        self.assertEqual(order, ["runtime", "client"])
        self.assertEqual(value["remaining"], 0)


class ArgumentTests(unittest.TestCase):
    def test_meeting_reconnect_is_explicit_and_exploration_stays_default(self):
        base=["--room","ROOM01","--output","/tmp/social-fault-test","--case","late-reconnect"]
        self.assertEqual(faults.arguments(base).phase,"exploration")
        self.assertEqual(faults.arguments([*base,"--phase","discussion"]).phase,"discussion")
        with patch("sys.stderr"):
            with self.assertRaises(SystemExit):faults.arguments([*base[:-1],"runtime-loss","--phase","discussion"])
    def test_discussion_must_be_explicit_and_url_cannot_contain_credentials(self):
        base = ["--room", "ROOM01", "--output", "/tmp/social-fault-test", "--case", "exploration-vs-meeting"]
        with self.assertRaises(SystemExit): faults.arguments(base)
        args = faults.arguments([*base, "--phase", "discussion"])
        self.assertEqual(args.phase, "discussion")
        with self.assertRaises(SystemExit): faults.arguments([*base, "--phase", "discussion", "--api-url", "http://secret@127.0.0.1:5081"])

    def test_summarize_only_does_not_need_lobby_or_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            faults.write(Path(folder) / "evidence-private.json", evidence())
            with patch.object(faults.subprocess, "Popen", side_effect=AssertionError("must not launch")):
                self.assertEqual(faults.main(["--summarize-only", "--output", folder]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
