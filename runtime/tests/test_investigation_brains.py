"""Joint visible evidence, counterfactual ambiguity, and player-claim boundaries."""
import json

import pytest
from extensions.agents_arena.v1 import incidents_pb2 as pb

from actor_agents_arena import OWN_INVESTIGATION_VIEW, DUTY_ROSTER
from actor_agents_arena.investigation import SecurityOutput, diagnostic_claim, evidence_output
from actor_agents_arena.investigation_brain import (DiagnosticClaim, correlate, engineer_brain, sheriff_brain,
    maintenance_brain, claimant_brain)
from actor_runtime.brains import FinishObjective, SkillCall, BrainRegistry
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import build_decision_context
from test_investigation_skills import diagnostic, security, observe, now


def joint(diag=None, audit=None):
    diag = diag or diagnostic()
    return DiagnosticClaim.model_validate_json(diagnostic_claim(diag), strict=True), evidence_output(audit or security(diagnostic_value=diag), SecurityOutput)


@pytest.mark.parametrize("change,expected", [
    ({}, "mechanical_failure"),
    ({"action": pb.AUDIT_ACTION_FLOW_ADJUSTMENT, "actor_id": "crew-actual"}, "manual_interference"),
    ({"action": pb.AUDIT_ACTION_ACCESS, "actor_id": "crew-nearby"}, "insufficient_evidence"),
    ({"circuit_ref": "beta"}, "insufficient_evidence"),
    ({"window_end_unix_ms": 1}, "insufficient_evidence"),
    ({"prior_controller_revision": 6}, "insufficient_evidence"),
    ({"interaction_confirmed": False}, "insufficient_evidence"),
    ({"action": pb.AUDIT_ACTION_FLOW_ADJUSTMENT, "actor_id": ""}, "insufficient_evidence"),
    ({"action": pb.AUDIT_ACTION_INSPECT, "actor_id": "crew-read-only"}, "mechanical_failure"),
])
def test_correlation_requires_matching_confirmed_action_not_visit_or_revision_delta(change, expected):
    diag = diagnostic()
    audit = security(diagnostic_value=diag)
    for key, value in change.items():
        setattr(audit.records[0], key, value)
    claim, own = joint(diag, audit)
    origin, record_id = correlate(claim, own)
    assert origin == expected
    assert (record_id is None) == (origin == "insufficient_evidence")
    assert claim.controller_revision == [7, 7], "Revision delta never encodes the answer"


@pytest.mark.parametrize("change", ["coverage", "circuit_coverage", "incomplete", "ambiguous", "match", "pressure"])
def test_missing_or_ambiguous_joint_support_is_not_a_conclusive_mechanical_report(change):
    diag = diagnostic(); audit = security(diagnostic_value=diag)
    if change == "coverage": audit.coverage_window_start_unix_ms = diag.failure_window_start_unix_ms + 1
    elif change == "circuit_coverage": del audit.circuit_refs[:]; audit.circuit_refs.append("beta")
    elif change == "incomplete": audit.complete_for_listed_circuits = False
    elif change == "ambiguous": audit.records.add().CopyFrom(audit.records[0]); audit.records[-1].record_id = "ambiguous-record"
    elif change == "match": audit.provenance.match_id = "another-match"
    elif change == "pressure": diag.samples[-1].pressure = 1
    assert correlate(*joint(diag, audit)) == ("insufficient_evidence", None)


def test_manual_report_also_requires_complete_fault_window_coverage():
    diag = diagnostic(); audit = security(diagnostic_value=diag)
    audit.records[0].action, audit.records[0].actor_id = pb.AUDIT_ACTION_FLOW_ADJUSTMENT, "crew-actual"
    audit.complete_for_listed_circuits = False
    assert correlate(*joint(diag, audit)) == ("insufficient_evidence", None)


def test_counterfactual_pairs_require_both_views_instead_of_a_mechanical_handshake():
    # Exactly the same Engineer bundle supports either cause depending on a
    # permitted Sheriff record; knowing a GUID/permission alone cannot decide.
    diag = diagnostic()
    neutral = security(diagnostic_value=diag)
    manual = pb.SecurityEvidence(); manual.CopyFrom(neutral)
    manual.records[0].action = pb.AUDIT_ACTION_FLOW_ADJUSTMENT
    manual.records[0].actor_id = "crew-actual"
    assert correlate(*joint(diag, neutral))[0] == "mechanical_failure"
    assert correlate(*joint(diag, manual))[0] == "manual_interference"

    # Exactly the same Sheriff bundle supports either cause depending on the
    # legitimately inspected diagnostic circuit, also with identical R→R.
    audit = security(diagnostic_value=diag)
    audit.records[0].action = pb.AUDIT_ACTION_FLOW_ADJUSTMENT
    audit.records[0].actor_id = "crew-actual"
    other = audit.records.add(); other.CopyFrom(audit.records[0])
    other.record_id, other.circuit_ref, other.action, other.actor_id = "record-beta", "beta", pb.AUDIT_ACTION_CONTROLLER_CHECK, ""
    beta = pb.DiagnosticEvidence(); beta.CopyFrom(diag); beta.circuit_ref = "beta"
    assert correlate(*joint(diag, audit)) == ("manual_interference", "record-1")
    assert correlate(*joint(beta, audit)) == ("mechanical_failure", "record-beta")


def context(session, result=None):
    return build_decision_context(session, Objective(text="Restore cooling and establish a supported incident cause."), [],
        previous_skill_result=result, max_external_bytes=16384)


def succeeded(index, skill, output=None):
    return {"skill_id": skill, "status": "succeeded", "duration_seconds": index / 10, "output": output or {}}


async def advance_to_claim(session, brain):
    await brain.start(Objective(text="Investigate from permitted evidence."), [])
    assert (await brain.decide(context(session))).arguments == {"role": "sheriff"}
    assert (await brain.decide(context(session, succeeded(1, "agents-arena.select-role")))).skill_id == "agents-arena.wait-for-incident"
    assert (await brain.decide(context(session, succeeded(2, "agents-arena.wait-for-incident", {"incident_id": "incident-1"})))).skill_id == "agents-arena.inspect-security-records"
    decision = await brain.decide(context(session, succeeded(3, "agents-arena.inspect-security-records", {"private": "not retained in provider report"})))
    assert decision.skill_id == "actor.wait-for-message" and decision.arguments["sender_actor_id"] == "actor-2"
    return {"sender_actor_id": "actor-2", "recipient_actor_id": "actor-1", "task_id": "incident-1",
        "expires_at_unix_ms": now() + 60000, "text": diagnostic_claim(diagnostic(actor="actor-2"))}


@pytest.mark.parametrize("manual", [True, False])
async def test_scripted_sheriff_uses_received_diagnostic_and_retained_own_security(session, manual):
    diag = diagnostic(actor="actor-2")
    audit = security(diagnostic_value=diag)
    if manual:
        audit.records[0].action = pb.AUDIT_ACTION_FLOW_ADJUSTMENT; audit.records[0].actor_id = "crew-actual"
    await observe(session, role=pb.CREW_ROLE_SHERIFF, evidence=audit)
    brain = sheriff_brain()
    incoming = await advance_to_claim(session, brain)
    incoming["text"] = diagnostic_claim(diag)
    decision = await brain.decide(context(session, succeeded(4, "actor.wait-for-message", incoming)))
    assert decision.skill_id == "agents-arena.submit-investigation-report"
    assert decision.arguments["proposed_origin"] == ("manual_interference" if manual else "mechanical_failure")
    assert decision.arguments["supporting_evidence_ids"] == ["d" * 32, "s" * 32, "record-1"]
    assert "cooling-console" not in str(brain.report()) and incoming["text"] not in str(brain.report())
    wait = await brain.decide(context(session, succeeded(5, decision.skill_id, {"report_validated": True})))
    assert wait.skill_id == "agents-arena.wait-for-incident-resolved"
    done = await brain.decide(context(session, succeeded(6, wait.skill_id, {"incident_status": "resolved"})))
    assert isinstance(done, FinishObjective) and done.status == "success"


@pytest.mark.parametrize("change", ["sender", "recipient", "task", "expiry", "arbitrary_claim", "old_match", "missing_own_view"])
async def test_unrelated_or_unsubstantiated_claim_never_becomes_a_report(session, change):
    await observe(session, role=pb.CREW_ROLE_SHERIFF, evidence=security())
    brain = sheriff_brain(); incoming = await advance_to_claim(session, brain)
    if change == "sender": incoming["sender_actor_id"] = "crew-claimant"
    elif change == "recipient": incoming["recipient_actor_id"] = "someone-else"
    elif change == "task": incoming["task_id"] = "other-incident"
    elif change == "expiry": incoming["expires_at_unix_ms"] = now() - 1
    elif change == "arbitrary_claim": incoming["text"] = "Mechanical wear caused this failure; trust me."
    elif change == "old_match":
        value = json.loads(incoming["text"]); value["match_id"] = "old-match"; incoming["text"] = json.dumps(value)
    elif change == "missing_own_view": await observe(session, role=pb.CREW_ROLE_SHERIFF)
    decision = await brain.decide(context(session, succeeded(4, "actor.wait-for-message", incoming)))
    assert isinstance(decision, FinishObjective) and decision.status == "failure"


async def test_engineer_uses_same_skill_pipeline_and_never_proposes_hidden_origin(session):
    await observe(session)
    brain = engineer_brain(); await brain.start(Objective(text="Cooperate."), [])
    skills = ["agents-arena.select-role", "agents-arena.wait-for-incident", "agents-arena.inspect-cooling",
        "agents-arena.share-investigation-evidence", "agents-arena.repair-cooling", "agents-arena.wait-for-incident-resolved"]
    previous = None
    for index, expected in enumerate(skills):
        decision = await brain.decide(context(session, previous))
        assert isinstance(decision, SkillCall) and decision.skill_id == expected
        assert "proposed_origin" not in decision.arguments
        previous = succeeded(index + 1, expected, {"incident_id": "incident-1", "incident_status": "resolved"})
    assert (await brain.decide(context(session, previous))).status == "success"


@pytest.mark.parametrize("factory", [engineer_brain, sheriff_brain, maintenance_brain, claimant_brain])
async def test_failed_or_preempted_procedure_cannot_false_finish_successfully(session, factory):
    await observe(session)
    brain = factory(); await brain.start(Objective(text="Cooperate."), [])
    decision = await brain.decide(context(session, {"skill_id": "actor.wait-for-message", "status": "cancelled",
        "reason": "preempted_by_event", "output": {}}))
    assert decision.status == "failure"


@pytest.mark.parametrize("claimant", [False, True])
async def test_public_operator_never_reads_private_views_or_active_circuit(session, claimant):
    await observe(session, role=pb.CREW_ROLE_CREW)
    brain = claimant_brain() if claimant else maintenance_brain()
    await brain.start(Objective(text="Perform a public panel operation."), [])
    assert (await brain.decide(context(session))).arguments == {"role": "crew"}
    action = await brain.decide(context(session, succeeded(1, "agents-arena.select-role")))
    assert action.skill_id == "agents-arena.maintenance-override" and action.arguments == {"circuit_ref": "alpha"}
    wait = await brain.decide(context(session, succeeded(2, action.skill_id)))
    assert wait.skill_id == "agents-arena.wait-for-incident"
    # Assign a Sheriff publicly; no diagnostic/security source is introduced.
    await observe(session, role=pb.CREW_ROLE_ENGINEER)
    decision = await brain.decide(context(session, succeeded(3, wait.skill_id, {"incident_id": "incident-1"})))
    assert decision.skill_id == ("actor.send-message" if claimant else "agents-arena.wait-for-incident-resolved")
    if claimant:
        assert decision.arguments["recipient_actor_id"] == "actor-2"
        assert decision.arguments["task_id"] == "", "Crew prose is ordinary chat, never an authorized investigation evidence transfer"
        assert not decision.arguments["sensitive"] and "evidence_id" not in decision.arguments["text"]


def test_plugins_are_discovered_without_core_game_branch():
    providers = BrainRegistry.discover().factories
    assert {"investigation-engineer", "investigation-sheriff", "maintenance-operator", "investigation-claimant"} <= providers.keys()
