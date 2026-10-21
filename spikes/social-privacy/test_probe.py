"""Auth-free tests of the passive wire rules and strict public projection."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from google.protobuf.any_pb2 import Any
from actor.core.v1 import actor_pb2 as core, bridge_pb2 as bridge
from actor.standard.v1.transform2d_pb2 import Transform2DObservation
from actor.standard.v1.motion2d_pb2 import Motion2DObservation
from extensions.agents_arena.v1 import rooms_pb2, social_pb2 as social


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


audit = load("social_privacy_runtime_audit", "runtime_audit.py")
probe = load("social_privacy_run_probe", "run_probe.py")


class ProjectionTests(unittest.TestCase):
    def unity(self):
        result = dict.fromkeys(probe.CLIENT_COUNTS, 0)
        result.update(schema="actor.social-privacy-probe.v1", completed=True, passed=True, socialModeObserved=True,
            farSecurityCoverage=True, durationSeconds=90.0, farSecuritySeconds=40.0, maxFarSecurityContinuousSeconds=30.0,
            checks=[{"name": name, "passed": True} for name in sorted(probe.CHECKS)])
        for key in probe.CLIENT_COUNTS[:11]: result[key] = 20
        result["initialLegitimateHallActors"] = 3
        result["matchStartedAtUnixMs"] = 1800000000000
        result["stableFarIntervals"] = [{"incoming_first_unix_ms": 1800000010000, "incoming_last_unix_ms": 1800000050000,
            "adapter_first_unix_ms": 1800000010100, "adapter_last_unix_ms": 1800000050100, "incoming_frames": 400, "adapter_frames": 600}]
        return result

    def wire(self):
        result = dict.fromkeys(probe.WIRE_COUNTS, 0)
        result.update(schema="actor.social-wire-privacy-probe.v1", completed=True, passed=True, far_security_coverage=True,
                      streams=1, manifests=1, initial_legitimate_hall_actors=3, max_far_security_seconds=30.0, duration_seconds=90.0)
        for key in ("observation_frames", "world_frames", "own_payloads", "self_envelopes", "far_security_frames"): result[key] = 20
        result["match_started_at_unix_ms"] = 1800000000000
        result["stable_far_intervals"] = [{"first_unix_ms": 1800000010200, "last_unix_ms": 1800000050000, "frames": 600}]
        return result

    def test_actual_surface_coverage_and_zero_violations_are_required(self):
        self.assertTrue(probe.project_client(self.unity())["passed"])
        self.assertTrue(probe.project_wire(self.wire())["passed"])
        for key in ("incomingWorldFrames", "adapterWorldFrames", "spawnAuditSamples", "endpointCheckedCalls", "foreignComponentSamples"):
            report = self.unity(); report[key] = 0
            self.assertFalse(probe.project_client(report)["passed"], key)
        for key in probe.CLIENT_ZEROS:
            report = self.unity(); report[key] = 1
            self.assertFalse(probe.project_client(report)["passed"], key)
        for key in probe.WIRE_ZEROS:
            report = self.wire(); report[key] = 1
            self.assertFalse(probe.project_wire(report)["passed"], key)

    def test_bool_missing_nonfinite_and_duplicate_checks_are_not_evidence(self):
        for bad in (False, None, "0", -1):
            report = self.unity(); report["wrongRecipientIncoming"] = bad
            self.assertFalse(probe.project_client(report)["passed"])
        report = self.unity(); report["maxFarSecurityContinuousSeconds"] = float("nan")
        self.assertFalse(probe.project_client(report)["passed"])
        report = self.unity(); report["checks"] = [report["checks"][0]] * len(probe.CHECKS)
        self.assertFalse(probe.project_client(report)["passed"])
        report = self.unity(); report["checks"][0]["name"] = []
        self.assertFalse(probe.project_client(report)["passed"])

    def test_isolation_requires_well_typed_distinct_launch_evidence(self):
        def rows():
            return [{"bridge_port": 18000+i, "bridge_credential_sha256": f"{i:064x}",
                     "agent_profile": str(i), "memory_database": str(i)} for i in range(4)]
        self.assertTrue(probe.independent_launches(rows()))
        for key, bad in (("bridge_port", True), ("bridge_port", 65536), ("bridge_credential_sha256", []),
                         ("bridge_credential_sha256", "not-a-fingerprint"), ("agent_profile", {}), ("memory_database", None)):
            evidence = rows(); evidence[0][key] = bad
            self.assertFalse(probe.independent_launches(evidence), key)
        for key in rows()[0]:
            evidence = rows(); evidence[1][key] = evidence[0][key]
            self.assertFalse(probe.independent_launches(evidence), key)

    def test_actual_kill_must_be_inside_the_same_continuous_far_interval(self):
        def summary(elapsed="25000"):
            return {"durationMs": "90000", "replay": [{"kind": "kill", "elapsedMs": elapsed}]}
        self.assertTrue(probe.temporal_kill_coverage(self.unity(), self.wire(), summary()))
        for elapsed in ("5000", "60000", "10200", "50000", True, 25000.0, "-1"):
            self.assertFalse(probe.temporal_kill_coverage(self.unity(), self.wire(), summary(elapsed)), elapsed)
        for key in ("matchStartedAtUnixMs", "stableFarIntervals"):
            unity = self.unity(); unity.pop(key)
            self.assertFalse(probe.temporal_kill_coverage(unity, self.wire(), summary()), key)
        wire = self.wire(); wire["match_started_at_unix_ms"] += 100
        self.assertFalse(probe.temporal_kill_coverage(self.unity(), wire, summary()))
        self.assertFalse(probe.temporal_kill_coverage(self.unity(), self.wire(), {"durationMs": "90000", "replay": []}))

    def test_disjoint_windows_are_not_joined_and_timestamps_never_leak(self):
        unity, wire = self.unity(), self.wire()
        unity["stableFarIntervals"] = [
            {"incoming_first_unix_ms": 1800000010000, "incoming_last_unix_ms": 1800000020000,
             "adapter_first_unix_ms": 1800000010000, "adapter_last_unix_ms": 1800000020000, "incoming_frames": 100, "adapter_frames": 100},
            {"incoming_first_unix_ms": 1800000030000, "incoming_last_unix_ms": 1800000040000,
             "adapter_first_unix_ms": 1800000030000, "adapter_last_unix_ms": 1800000040000, "incoming_frames": 100, "adapter_frames": 100}]
        self.assertFalse(probe.temporal_kill_coverage(unity, wire, {"durationMs": "90000", "replay": [{"kind": "kill", "elapsedMs": "25000"}]}))
        public = json.dumps({"unity": probe.project_client(unity), "wire": probe.project_wire(wire)})
        self.assertNotIn("18000000", public)

    def active_evidence(self):
        started = 1800000000000
        reports = [{"schema": "actor.social-out-of-sight-kill-probe.v1", "completed": True, "passed": True,
            "eligible": False, "attempts": 0, "authoritative_rejections": 0, "accepted": 0, "probe_failures": 0,
            "outcome": "own_participant_ineligible"} for _ in range(4)]
        reports[0].update(eligible=True, attempts=1, authoritative_rejections=1, candidate_was_not_visible=True,
            initial_legitimate_candidates=3, match_started_at_unix_ms=started, requested_at_unix_ms=started+90000,
            request_world_captured_at_unix_ms=started+89900, received_at_unix_ms=started+90100, outcome="authoritative_rejection_verified")
        audits = []
        for _ in range(4):
            unity, wire = self.unity(), self.wire()
            for interval in unity["stableFarIntervals"]:
                for key in ("incoming_first_unix_ms", "adapter_first_unix_ms"): interval[key] = started+80000
                for key in ("incoming_last_unix_ms", "adapter_last_unix_ms"): interval[key] = started+140000
            wire["stable_far_intervals"] = [{"first_unix_ms": started+80000, "last_unix_ms": started+140000, "frames": 900}]
            audits.append((unity, wire))
        agents = [{"unity": probe.project_client(u), "wire": probe.project_wire(w)} for u, w in audits]
        summary = {"durationMs": "180000", "kills": 1, "replay": [{"kind": "kill", "elapsedMs": "100000"}]}
        return reports, audits, agents, summary

    def test_active_rejection_requires_actual_receipt_and_temporal_far_witness(self):
        evidence = self.active_evidence()
        self.assertTrue(probe.project_active_probe(*evidence)["passed"])
        for key, bad in (("attempts", True), ("accepted", 1), ("authoritative_rejections", 0),
                         ("candidate_was_not_visible", False), ("outcome", "local_submit_only")):
            reports, audits, agents, summary = self.active_evidence(); reports[0][key] = bad
            self.assertFalse(probe.project_active_probe(reports, audits, agents, summary)["passed"], key)
        reports, audits, agents, summary = self.active_evidence()
        for unity, wire in audits:
            unity["stableFarIntervals"] = []; wire["stable_far_intervals"] = []
        self.assertFalse(probe.project_active_probe(reports, audits, agents, summary)["passed"])

    def test_active_probe_cannot_hide_an_actual_kill_or_export_private_metadata(self):
        reports, audits, agents, summary = self.active_evidence()
        for kind in ("kill", "disconnect", "ejection"):
            altered = {**summary, "replay": [{"kind": kind, "elapsedMs": "90000"}, *summary["replay"]]}
            self.assertFalse(probe.project_active_probe(reports, audits, agents, altered)["passed"], kind)
        reports[0].update(token="PRIVATE-CANARY", target_actor_id="PRIVATE-CANARY", role="PRIVATE-CANARY", path="PRIVATE-CANARY")
        public = probe.project_active_probe(reports, audits, agents, summary)
        self.assertTrue(public["passed"])
        self.assertNotIn("PRIVATE-CANARY", json.dumps(public)); self.assertNotIn("18000000", json.dumps(public))

    def test_far_coverage_requires_actual_duration_and_both_surfaces(self):
        report = self.unity(); report["maxFarSecurityContinuousSeconds"] = 3.9
        self.assertTrue(probe.project_client(report)["passed"])
        self.assertFalse(probe.project_client(report)["far_security_coverage"])
        report = self.wire(); report["far_security_frames"] = 9
        self.assertFalse(probe.project_wire(report)["far_security_coverage"])

    def test_public_projection_never_copies_private_unknown_fields(self):
        private = "PRIVATE-CANARY-not-for-public-artifacts"
        unity = self.unity(); unity.update(role=private, text=private, token=private, actor_id=private, path=private)
        wire = self.wire(); wire.update(own_alignment=private, raw_payload=private, evidence_id=private)
        text = json.dumps({"unity": probe.project_client(unity), "wire": probe.project_wire(wire)})
        self.assertNotIn(private, text)
        self.assertFalse(probe.runtime_result({"match": []})["participation_complete"])

    def test_postgame_same_scope_and_isolation_are_not_optional(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(1, 5):
                child = root / f"agent{index:02}"
                probe.write(child / "privacy-unity.json", self.unity())
                probe.write(child / "privacy-wire.json", self.wire())
                probe.write(child / "runtime.json", {"match": {"passed": True, "result": {"status": "completed", "authoritative_terminal": True,
                    "scope_id": "actual-match", "episodes": 1, "decisions": 4}}})
            probe.write(root / "launch.json", {"agents": [{"bridge_port": 18000+i, "bridge_credential_sha256": f"{i:064x}",
                "agent_profile": str(i), "memory_database": str(i)} for i in range(4)]})
            probe.write(root / "cleanup.json", {"started": 8, "remaining": 0})
            server = root / "server.json"; probe.write(server, {"mode": "SocialDeduction", "matchId": "actual-match", "kills": 1,
                "durationMs": "90000", "replay": [{"kind": "kill", "elapsedMs": "25000"}]})
            self.assertTrue(probe.summarize(root, server)["passed"])
            probe.write(root / "cleanup.json", {"started": 8, "remaining": False})
            self.assertFalse(probe.summarize(root, server)["passed"])
            probe.write(root / "cleanup.json", {"started": 8, "remaining": 0})
            self.assertFalse(probe.summarize(root, None)["passed"])
            probe.write(server, {"mode": "SocialDeduction", "matchId": "stale-match", "kills": 1})
            self.assertFalse(probe.summarize(root, server)["passed"])
            probe.write(server, {"mode": "SocialDeduction", "matchId": "actual-match", "kills": 0})
            self.assertFalse(probe.summarize(root, server)["passed"])


class WireRuleTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.auditor = audit.WireAuditor(None, clock=lambda: self.now[0]); self.auditor.counts["streams"] = 1
        self.auditor.inspect(bridge.GameToRuntime(manifest=core.CapabilityManifest(self_actor_id="self")))

    def envelope(self, cap, value, entity="self"):
        packed = Any(); packed.Pack(value)
        return core.ObservationEnvelope(capability_id=cap, entity_id=entity, payload=packed)

    def frame(self, *, room="main-hall", actors=(), bodies=(), phase=social.SOCIAL_PHASE_EXPLORATION, entity="self", recipient="self"):
        root_x, root_y = (6.44, 3.31) if room == "security" else (0, -1.4)
        values = [("actor.standard.transform2d", Transform2DObservation(x=root_x, y=root_y)),
            ("actor.standard.motion2d", Motion2DObservation()),
            ("agents-arena.room-membership", rooms_pb2.RoomMembershipObservation(room_id=room)),
            (audit.PREFIX+"match", social.SocialMatchStateObservation(match_id="match", phase=phase, started_at_unix_ms=1000)),
            (audit.PREFIX+"own-state", social.SocialOwnStateObservation(match_id="match", actor_id=recipient, life=social.SOCIAL_LIFE_ALIVE,
                alignment=social.SOCIAL_ALIGNMENT_IMPOSTOR)),
            (audit.PREFIX+"visible-world", social.SocialVisibleWorldObservation(match_id="match", recipient_actor_id=recipient,
                visibility_radius=3.5, captured_at_unix_ms=2000+int(self.now[0]*1000), actors=actors, bodies=bodies))]
        self.auditor.inspect(bridge.GameToRuntime(observation=core.ObservationFrame(observations=[self.envelope(cap, value, entity) for cap, value in reversed(values)])))

    def far(self):
        self.frame(actors=[social.SocialVisibleActor(actor_id="legitimately-seen", x=1, y=-.94)])
        self.now[0] = 1; self.frame(room="security")
        for index in range(12):
            self.now[0] = 6 + index * .1; self.frame(room="security")

    def test_same_frame_pose_is_order_independent_and_no_secret_is_reported(self):
        self.far(); self.frame(room="security", phase=social.SOCIAL_PHASE_GAME_OVER)
        report = self.auditor.report()
        self.assertTrue(report["passed"])
        self.assertTrue(report["far_security_coverage"])
        text = json.dumps(report)
        for private in ("legitimately-seen", "SOCIAL_ALIGNMENT_IMPOSTOR", '"self"', '"match"'):
            self.assertNotIn(private, text)

    def test_raw_wrong_entity_and_own_recipient_are_counted_before_runtime_filter(self):
        self.frame(entity="foreign", recipient="foreign")
        self.assertGreater(self.auditor.counts["foreign_entities"], 0)
        self.assertGreater(self.auditor.counts["foreign_recipients"], 0)

    def test_legacy_manifest_surface_is_rejected_even_without_live_payload(self):
        self.auditor.inspect(bridge.GameToRuntime(manifest=core.CapabilityManifest(self_actor_id="self", capabilities=[
            core.CapabilityDefinition(capability_id="agents-arena.duty-roster")])))
        self.assertEqual(self.auditor.counts["legacy_capabilities"], 1)

    def test_far_hidden_actor_body_and_attack_are_never_silently_accepted(self):
        self.far()
        self.frame(room="security", actors=[social.SocialVisibleActor(actor_id="legitimately-seen", x=0, y=-.94)],
            bodies=[social.SocialVisibleBody(body_id="body", victim_actor_id="legitimately-seen", x=0, y=-.94)])
        self.assertEqual(self.auditor.counts["far_forbidden_world_entries"], 2)
        packed = Any(); packed.Pack(social.SocialVisibleAttackEvent(match_id="match", observer_actor_id="self", x=0, y=-.94, observed_at_unix_ms=3000))
        self.auditor.inspect(bridge.GameToRuntime(event=core.GameEventEnvelope(capability_id=audit.PREFIX+"visible-attack", entity_id="self", payload=packed)))
        self.assertEqual(self.auditor.counts["far_forbidden_attack_events"], 1)

    def test_legitimate_old_witness_event_is_not_misclassified_as_new_far_delivery(self):
        self.far()
        packed = Any(); packed.Pack(social.SocialVisibleAttackEvent(match_id="match", observer_actor_id="self", x=0, y=-.94, observed_at_unix_ms=1000))
        self.auditor.inspect(bridge.GameToRuntime(event=core.GameEventEnvelope(capability_id=audit.PREFIX+"visible-attack", entity_id="self", payload=packed)))
        self.assertEqual(self.auditor.counts["far_forbidden_attack_events"], 0)

    def test_wire_interval_gap_never_stitches_two_separate_windows(self):
        self.far()
        self.assertEqual(self.auditor.report()["match_started_at_unix_ms"], 1000)
        first = dict(self.auditor.report()["stable_far_intervals"][0])
        self.now[0] = 20; self.frame(room="security")
        self.now[0] = 21.1; self.frame(room="security")
        windows = self.auditor.report()["stable_far_intervals"]
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[0], first)
        self.assertGreater(windows[1]["first_unix_ms"] - first["last_unix_ms"], 1500)

    def test_conflicting_public_match_start_fails_audit(self):
        self.frame()
        self.auditor.payload(social.SocialMatchChangedEvent(state=social.SocialMatchStateObservation(started_at_unix_ms=1001)))
        self.assertEqual(self.auditor.counts["probe_failures"], 1)

    def test_canonical_type_and_early_postgame_are_strict(self):
        self.frame()
        envelope = self.envelope(audit.PREFIX+"own-state", social.SocialOwnStateObservation(actor_id="self"))
        envelope.payload.type_url = "untrusted.example/" + social.SocialOwnStateObservation.DESCRIPTOR.full_name
        self.auditor.inspect(bridge.GameToRuntime(observation=core.ObservationFrame(observations=[envelope])))
        self.assertEqual(self.auditor.counts["type_mismatches"], 1)
        self.auditor.payload(social.SocialPostgameObservation(summary=social.SocialMatchSummary(match_id="match")))
        self.assertEqual(self.auditor.counts["early_postgame_payloads"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
