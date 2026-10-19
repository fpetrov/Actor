"""Real typed Session/Scheduler paths; navigation's control loop is tested separately."""
import asyncio
import json
import time

import pytest
from google.protobuf.any_pb2 import Any
from extensions.agents_arena.v1 import incidents_pb2 as pb

from actor.core.v1 import actor_pb2 as core
from actor_agents_arena import *
from actor_agents_arena.investigation import TerminalSkill, skill_package
from actor_runtime.skills import SkillRegistry, SkillScheduler
from actor_standard import TRANSFORM, MOTION, LOCOMOTION, Transform2DObservation, Motion2DObservation, Locomotion2DCommand
from actor_standard_communication import (PEERS, MESSAGE_DELIVERY_RESULT, MESSAGE_RECEIVED,
    CommunicationPeersObservation, CommunicationPeer, SendMessageCommand, MessageDeliveryResultEvent,
    MessageReceivedEvent, MESSAGE_DELIVERY_STAGE_SERVER_ACCEPTED)
from actor_standard_communication.skills import skill_package as communication_skills
from conftest import envelope, extension_version, message

_TERMINAL_SERVICE_APPROACH = TerminalSkill.approach


def now():
    return int(time.time() * 1000)


def diagnostic(*, actor="actor-1", circuit="alpha", **changes):
    timestamp = now()
    value = pb.DiagnosticEvidence(provenance=pb.EvidenceProvenance(evidence_id="d" * 32,
        incident_id="incident-1", source_entity_id="cooling-console", observed_at_unix_ms=timestamp,
        expires_at_unix_ms=timestamp + 120000, server_revision=10, match_id="match-1",
        recipient_actor_id=actor, recipient_role=pb.CREW_ROLE_ENGINEER, visibility=pb.EVIDENCE_VISIBILITY_ROLE_PRIVATE),
        circuit_ref=circuit, failure_window_start_unix_ms=timestamp - 5000, failure_window_end_unix_ms=timestamp - 4000,
        samples=[pb.PressureSample(at_unix_ms=timestamp - 5000, pressure=1),
            pb.PressureSample(at_unix_ms=timestamp - 4000, pressure=.4)], equipment_wear=.5,
        controller_revision_before=7, controller_revision_after=7)
    for key, item in changes.items():
        setattr(value, key, item)
    return value


def security(*, actor="actor-1", diagnostic_value=None):
    diag = diagnostic_value or diagnostic()
    provenance = pb.EvidenceProvenance(); provenance.CopyFrom(diag.provenance)
    provenance.evidence_id = "s" * 32
    provenance.source_entity_id = "security-console"
    provenance.recipient_actor_id = actor
    provenance.recipient_role = pb.CREW_ROLE_SHERIFF
    return pb.SecurityEvidence(provenance=provenance, records=[pb.MaintenanceRecord(record_id="record-1",
        source_entity_id="cooling-console", circuit_ref=diag.circuit_ref,
        window_start_unix_ms=diag.failure_window_start_unix_ms, window_end_unix_ms=diag.failure_window_end_unix_ms,
        action=pb.AUDIT_ACTION_CONTROLLER_CHECK, interaction_confirmed=True,
        prior_controller_revision=7, resulting_controller_revision=7)],
        coverage_window_start_unix_ms=diag.failure_window_start_unix_ms - 1000,
        coverage_window_end_unix_ms=diag.failure_window_end_unix_ms + 1000,
        complete_for_listed_circuits=True, circuit_refs=["alpha", "beta"])


async def observe(session, *, role=pb.CREW_ROLE_ENGINEER, incident="incident-1", evidence=None,
                  status=pb.INCIDENT_STATUS_ACTIVE, cooling=pb.COOLING_STATUS_FAILURE, validated=False):
    sequence = session.last_received_sequence + 1
    state = pb.CoolingStateObservation(incident_id=incident, incident_status=status, cooling_status=cooling,
        expires_at_unix_ms=now() + 300000, match_id="match-1", server_revision=sequence,
        report_validated=validated, location_id="engineering")
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence, observations=[
        envelope(OWN_ROLE, "agents-arena", pb.OwnRoleObservation(actor_id="actor-1", role=role,
            match_id="match-1", server_revision=sequence)),
        envelope(COOLING_STATE, "agents-arena", state),
        envelope(DUTY_ROSTER, "agents-arena", pb.DutyRosterObservation(match_id="match-1", server_revision=sequence,
            entries=[pb.DutyRoleEntry(actor_id="actor-1", role=role), pb.DutyRoleEntry(actor_id="actor-2",
                role=pb.CREW_ROLE_SHERIFF if role == pb.CREW_ROLE_ENGINEER else pb.CREW_ROLE_ENGINEER)])),
        envelope(ROOM_MEMBERSHIP, "agents-arena", RoomMembershipObservation(room_id="engineering")),
        envelope(TRANSFORM, "actor.standard", Transform2DObservation(x=-.3, y=-7.15)),
        envelope(MOTION, "actor.standard", Motion2DObservation()),
        envelope(PEERS, "actor.standard.communication", CommunicationPeersObservation(match_id="match-1",
            peers=[CommunicationPeer(actor_id="actor-2", display_name="Partner")], allowed_channels=["direct", "station"]))])))
    view = pb.OwnInvestigationViewObservation(recipient_actor_id="actor-1", incident_id=incident,
        match_id="match-1", server_revision=sequence)
    if evidence is not None:
        getattr(view, "diagnostic" if isinstance(evidence, pb.DiagnosticEvidence) else "security").CopyFrom(evidence)
        view.expires_at_unix_ms = evidence.provenance.expires_at_unix_ms
    await observe_view(session, view)
    return state


async def observe_view(session, view):
    sequence = session.last_received_sequence + 1
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence,
        observations=[envelope(OWN_INVESTIGATION_VIEW, "agents-arena", view)])))


async def event(session, capability, value, *, extension="agents-arena"):
    sequence = session.last_received_sequence + 1
    packed = Any(); packed.Pack(value)
    await session.accept(message(sequence, event=core.GameEventEnvelope(entity_id="actor-1",
        capability_id=capability, extension_id=extension, extension_version=extension_version(extension),
        revision=sequence, payload=packed)))


async def command(session, cls):
    async with asyncio.timeout(1):
        async for outgoing in session.outgoing():
            if outgoing.WhichOneof("body") == "command" and outgoing.command.payload.Is(cls.DESCRIPTOR):
                value = cls(); outgoing.command.payload.Unpack(value)
                return value
    pytest.fail("No expected typed command")


def scheduler(session):
    registry = SkillRegistry()
    for definition in (*skill_package(), *communication_skills()):
        registry.register(definition)
    return SkillScheduler(session, registry)


@pytest.fixture(autouse=True)
def already_at_terminal(monkeypatch):
    async def approach(self, context):
        pass
    monkeypatch.setattr(TerminalSkill, "approach", approach)


@pytest.mark.parametrize("role,skill", [
    (pb.CREW_ROLE_SHERIFF, "agents-arena.inspect-cooling"),
    (pb.CREW_ROLE_SHERIFF, "agents-arena.repair-cooling"),
    (pb.CREW_ROLE_ENGINEER, "agents-arena.inspect-security-records"),
])
async def test_other_role_cannot_start_private_procedure(session, role, skill):
    await observe(session, role=role)
    result = await scheduler(session).run(skill, {"incident_id": "incident-1"})
    assert result.status == "failed" and result.reason == "role_not_authorized"
    assert session.next_command_id == 0


async def test_select_role_requires_receipt_and_authoritative_own_state(session):
    await observe(session, role=pb.CREW_ROLE_CREW)
    running = asyncio.create_task(scheduler(session).run("agents-arena.select-role", {"role": "engineer"}))
    request = await command(session, pb.SelectRoleCommand)
    await event(session, ROLE_RESULT, pb.RoleResultEvent(correlation_id=request.correlation_id,
        accepted=True, match_id="match-1", state=pb.OwnRoleObservation(actor_id="actor-1", role=pb.CREW_ROLE_ENGINEER, match_id="match-1")))
    await asyncio.sleep(.02)
    assert not running.done()
    await observe(session)
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.output["role"] == "engineer"


async def inspect(session, owner, value):
    running = asyncio.create_task(owner.run("agents-arena.inspect-cooling", {"incident_id": "incident-1"}))
    request = await command(session, pb.InspectCoolingCommand)
    result = pb.DiagnosticEvidenceEvent(correlation_id=request.correlation_id, accepted=True,
        evidence=value, match_id="match-1", recipient_actor_id="actor-1", server_revision=20,
        expires_at_unix_ms=value.provenance.expires_at_unix_ms)
    await event(session, DIAGNOSTIC_EVIDENCE, result)
    await asyncio.sleep(.01)
    assert not running.done(), "Receipt alone must not race Inspect→Share before the own view arrives"
    await observe(session, evidence=value)
    return await asyncio.wait_for(running, 1), request


async def test_private_inspection_retains_own_evidence_across_turns_and_reinspection(session):
    from actor_runtime.objectives import Objective
    from actor_runtime.snapshots import build_decision_context
    await observe(session)
    owner = scheduler(session)
    value = diagnostic()
    result, first = await inspect(session, owner, value)
    assert result.status == "succeeded" and result.data_policy == "session_only"
    assert result.expires_at_unix_ms == value.provenance.expires_at_unix_ms
    assert result.output["provenance"]["evidence_id"] == value.provenance.evidence_id
    context = build_decision_context(session, Objective(text="Use only my evidence."), [], max_external_bytes=16384)
    own = next(item for item in context.observations if item.capability_id == OWN_INVESTIGATION_VIEW)
    assert own.value["diagnostic"]["circuitRef"] == "alpha" and own.data_policy == "session_only"
    assert "cooling-console" not in str(session.privacy.public(context))
    assert value.provenance.evidence_id not in str(owner.report())
    # Stable evidence identity is not a request identity: a second inspection is valid.
    await observe(session)
    repeated, second = await inspect(session, owner, value)
    assert repeated.status == "succeeded" and first.correlation_id != second.correlation_id


async def test_private_view_rejects_foreign_role_recipient_and_clears_old_knowledge(session):
    from actor_runtime.objectives import Objective
    from actor_runtime.snapshots import build_decision_context
    value = diagnostic()
    await observe(session, evidence=value)
    old = session.world.latest(pb.OwnInvestigationViewObservation, capability_id=OWN_INVESTIGATION_VIEW)
    foreign = pb.OwnInvestigationViewObservation(); foreign.CopyFrom(old)
    foreign.diagnostic.provenance.recipient_actor_id = "actor-2"
    foreign.diagnostic.circuit_ref = "forbidden-foreign-circuit"
    await observe_view(session, foreign)
    assert session.world.latest(pb.OwnInvestigationViewObservation, capability_id=OWN_INVESTIGATION_VIEW) == old
    await observe(session, role=pb.CREW_ROLE_SHERIFF, evidence=value)
    assert "forbidden-foreign-circuit" not in str(build_decision_context(session, Objective(text="Observe."), [], max_external_bytes=16384))
    await observe(session, role=pb.CREW_ROLE_SHERIFF)  # explicit empty oneof, not omission
    view = session.world.latest(pb.OwnInvestigationViewObservation, capability_id=OWN_INVESTIGATION_VIEW)
    assert view.WhichOneof("evidence") is None
    assert "cooling-console" not in build_decision_context(session, Objective(text="Observe."), [], max_external_bytes=16384).external_json()


async def test_expired_own_evidence_cannot_start_repair_or_remain_in_context(session):
    from actor_runtime.objectives import Objective
    from actor_runtime.snapshots import build_decision_context
    value = diagnostic(); value.provenance.expires_at_unix_ms = now() - 1
    await observe(session, evidence=value)
    result = await scheduler(session).run("agents-arena.repair-cooling", {"incident_id": "incident-1"})
    assert result.status == "failed" and result.reason == "evidence_unavailable" and session.next_command_id == 0
    context = build_decision_context(session, Objective(text="Fresh evidence only."), [], max_external_bytes=16384)
    own = next(item for item in context.observations if item.capability_id == OWN_INVESTIGATION_VIEW)
    assert own.expired and own.value is None


async def test_repair_needs_completed_same_correlation_phase_and_restored_observation(session):
    await observe(session, evidence=diagnostic())
    owner = scheduler(session)
    running = asyncio.create_task(owner.run("agents-arena.repair-cooling", {"incident_id": "incident-1"}))
    request = await command(session, pb.RepairCoolingCommand)
    assert request.diagnostic_evidence_id == "d" * 32
    fields = dict(correlation_id=request.correlation_id, accepted=True, incident_id="incident-1", match_id="match-1")
    await event(session, REPAIR_RESULT, pb.RepairResultEvent(**fields, server_revision=20,
        cooling_status=pb.COOLING_STATUS_REPAIRING, incident_status=pb.INCIDENT_STATUS_ACTIVE))
    await asyncio.sleep(.02)
    assert not running.done(), "Started receipt is not physical repair completion"
    await event(session, REPAIR_RESULT, pb.RepairResultEvent(**fields, server_revision=21, completed=True,
        cooling_status=pb.COOLING_STATUS_RESTORED, incident_status=pb.INCIDENT_STATUS_ACTIVE))
    await asyncio.sleep(.02)
    assert not running.done(), "A completed result must also converge with public authoritative state"
    await observe(session, evidence=diagnostic(), cooling=pb.COOLING_STATUS_RESTORED)
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.output["cooling_status"] == "restored"
    assert result.output["incident_status"] == "active", "Repair alone must not imply report validation"


async def test_repair_cancellation_sends_typed_cancel_and_zero_before_lease_release(session):
    await observe(session, evidence=diagnostic())
    owner = scheduler(session)
    running = asyncio.create_task(owner.run("agents-arena.repair-cooling", {"incident_id": "incident-1"}))
    request = await command(session, pb.RepairCoolingCommand)
    await owner.cancel()
    result = await asyncio.wait_for(running, 1)
    assert result.status == "cancelled" and owner.report()["active_skill_id"] == ""
    cancel = await command(session, pb.CancelCoolingRepairCommand)
    assert cancel.incident_id == request.incident_id and cancel.match_id == request.match_id
    zero = await command(session, Locomotion2DCommand)
    assert zero.desired_move_x == zero.desired_move_y == 0


async def test_repair_rejection_never_claims_success_or_cancels_another_accepted_operation(session):
    await observe(session, evidence=diagnostic())
    running = asyncio.create_task(scheduler(session).run("agents-arena.repair-cooling", {"incident_id": "incident-1"}))
    request = await command(session, pb.RepairCoolingCommand)
    await event(session, REPAIR_RESULT, pb.RepairResultEvent(correlation_id=request.correlation_id,
        accepted=False, reason_code="repair_in_progress", incident_id="incident-1", match_id="match-1", server_revision=20))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "failed" and result.reason == "repair_in_progress" and result.commands_sent == 2
    zero = await command(session, Locomotion2DCommand)
    assert zero.desired_move_x == zero.desired_move_y == 0


@pytest.mark.parametrize("remaining_ms", [30000, 120000, 400000])
async def test_share_uses_actual_direct_transport_receipt_and_bounded_claim(session, remaining_ms):
    value = diagnostic(); value.provenance.expires_at_unix_ms = now() + remaining_ms
    await observe(session, evidence=value)
    owner = scheduler(session)
    running = asyncio.create_task(owner.run("agents-arena.share-investigation-evidence", {"incident_id": "incident-1"}))
    request = await command(session, SendMessageCommand)
    assert request.recipient_actor_id == "actor-2" and request.channel_id == "direct" and request.sensitive
    assert request.task_id == "incident-1" and len(request.text.encode("utf-8")) <= 512
    assert 1000 <= request.ttl_ms <= min(300000, remaining_ms), "A shared claim must not outlive its inspected diagnostic evidence or the five-minute transport bound"
    if remaining_ms >= 120000:
        assert request.ttl_ms > 60000, "Valid evidence must not be discarded after an artificial one-minute message lifetime"
    if remaining_ms > 300000:
        assert request.ttl_ms == 300000
    claim = json.loads(request.text)
    assert claim == {"kind": "diagnostic_claim", "evidence_id": "d" * 32, "incident_id": "incident-1", "match_id": "match-1",
        "circuit_ref": "alpha", "window": [value.failure_window_start_unix_ms, value.failure_window_end_unix_ms],
        "controller_revision": [7, 7], "pressure_delta": -.6}
    await asyncio.sleep(.02); assert not running.done()
    await event(session, MESSAGE_DELIVERY_RESULT, MessageDeliveryResultEvent(correlation_id=request.correlation_id,
        accepted_by_server=True, stage=MESSAGE_DELIVERY_STAGE_SERVER_ACCEPTED, message_id="message-1",
        task_id="incident-1", match_id="match-1", recipient_actor_id="actor-2"), extension="actor.standard.communication")
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.data_policy == "session_only"
    assert request.text not in str(owner.report()) and "cooling-console" not in str(owner.report())


async def test_sharing_nearly_expired_evidence_does_not_extend_it_to_minimum_message_ttl(session):
    value = diagnostic(); value.provenance.expires_at_unix_ms = now() + 500
    await observe(session, evidence=value)
    result = await scheduler(session).run("agents-arena.share-investigation-evidence", {"incident_id": "incident-1"})
    assert result.status == "failed" and result.reason == "evidence_unavailable"
    assert session.next_command_id == 0


@pytest.mark.parametrize("refs", [["d", "s"], ["d", "s", "r", "extra"], ["d", "s", "s"]])
async def test_report_schema_requires_exactly_three_distinct_references(session, refs):
    await observe(session, role=pb.CREW_ROLE_SHERIFF, evidence=security())
    result = await scheduler(session).run("agents-arena.submit-investigation-report", {"incident_id": "incident-1",
        "proposed_origin": "mechanical_failure", "supporting_evidence_ids": refs})
    assert result.status == "failed" and result.reason == "invalid_arguments" and session.next_command_id == 0


async def test_report_cites_own_security_and_one_record_and_awaits_server_validation(session):
    await observe(session, role=pb.CREW_ROLE_SHERIFF, evidence=security())
    owner = scheduler(session)
    args = {"incident_id": "incident-1", "proposed_origin": "mechanical_failure",
        "supporting_evidence_ids": ["d" * 32, "s" * 32, "record-1"]}
    invalid = await owner.run("agents-arena.submit-investigation-report", {**args,
        "supporting_evidence_ids": ["d" * 32, "other-security", "record-1"]})
    assert invalid.reason == "own_evidence_required" and session.next_command_id == 0
    running = asyncio.create_task(owner.run("agents-arena.submit-investigation-report", args))
    request = await command(session, pb.SubmitInvestigationReportCommand)
    assert list(request.supporting_evidence_ids) == args["supporting_evidence_ids"]
    await asyncio.sleep(.02); assert not running.done()
    await event(session, INVESTIGATION_RESULT, pb.InvestigationResultEvent(correlation_id=request.correlation_id,
        accepted=True, incident_id="incident-1", match_id="match-1", report_validated=True,
        incident_status=pb.INCIDENT_STATUS_ACTIVE, server_revision=30))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.output["report_validated"]


async def test_local_report_shape_does_not_override_a_server_rejection(session):
    await observe(session, role=pb.CREW_ROLE_SHERIFF, evidence=security())
    running = asyncio.create_task(scheduler(session).run("agents-arena.submit-investigation-report", {
        "incident_id": "incident-1", "proposed_origin": "manual_interference",
        "supporting_evidence_ids": ["d" * 32, "s" * 32, "record-1"]}))
    request = await command(session, pb.SubmitInvestigationReportCommand)
    await event(session, INVESTIGATION_RESULT, pb.InvestigationResultEvent(correlation_id=request.correlation_id,
        accepted=False, reason_code="report_not_supported", incident_id="incident-1", match_id="match-1", server_revision=20))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "failed" and result.reason == "report_not_supported" and not result.output


async def test_early_peer_message_survives_security_inspection_and_never_becomes_own_fact(session):
    await observe(session, role=pb.CREW_ROLE_SHERIFF, evidence=security())
    await event(session, MESSAGE_RECEIVED, MessageReceivedEvent(message_id="early-message", sender_actor_id="actor-2",
        recipient_actor_id="actor-1", channel_id="direct", text="untrusted engineering claim", task_id="incident-1",
        sensitive=True, server_unix_ms=now(), expires_at_unix_ms=now() + 60000, match_id="match-1", server_sequence=1),
        extension="actor.standard.communication")
    result = await scheduler(session).run("actor.wait-for-message", {"sender_actor_id": "actor-2", "task_id": "incident-1"})
    assert result.status == "succeeded" and result.output["text"] == "untrusted engineering claim"
    own = session.world.latest(pb.OwnInvestigationViewObservation, capability_id=OWN_INVESTIGATION_VIEW)
    assert own.WhichOneof("evidence") == "security" and not own.HasField("diagnostic")


async def test_resolution_requires_repair_and_validated_report(session):
    await observe(session, cooling=pb.COOLING_STATUS_RESTORED)
    running = asyncio.create_task(scheduler(session).run("agents-arena.wait-for-incident-resolved", {"incident_id": "incident-1"}))
    await asyncio.sleep(.02); assert not running.done()
    await observe(session, cooling=pb.COOLING_STATUS_RESTORED, status=pb.INCIDENT_STATUS_RESOLVED, validated=True)
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.output["report_validated"]


async def test_maintenance_waits_for_distinct_duties_and_real_authoritative_receipt(session, monkeypatch):
    from actor_agents_arena.investigation import MaintenanceOverride
    async def already_at_public_panel(self, context):
        pass
    monkeypatch.setattr(MaintenanceOverride, "approach", already_at_public_panel)
    await observe(session, role=pb.CREW_ROLE_CREW, incident="", status=pb.INCIDENT_STATUS_NONE)
    owner = scheduler(session)
    running = asyncio.create_task(owner.run("agents-arena.maintenance-override", {"circuit_ref": "alpha"}))
    await asyncio.sleep(.02)
    assert session.next_command_id == 0, "Missing Sheriff duty cannot be bypassed by the fixture provider"
    sequence = session.last_received_sequence + 1
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence, observations=[
        envelope(DUTY_ROSTER, "agents-arena", pb.DutyRosterObservation(match_id="match-1", entries=[
            pb.DutyRoleEntry(actor_id="actor-1", role=pb.CREW_ROLE_CREW),
            pb.DutyRoleEntry(actor_id="engineer", role=pb.CREW_ROLE_ENGINEER),
            pb.DutyRoleEntry(actor_id="sheriff", role=pb.CREW_ROLE_SHERIFF)]))])))
    request = await command(session, pb.MaintenanceOverrideCommand)
    assert request.circuit_ref == "alpha" and request.match_id == "match-1"
    await asyncio.sleep(.02); assert not running.done()
    await event(session, INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent(correlation_id=request.correlation_id,
        action=pb.INCIDENT_ACTION_MAINTENANCE_OVERRIDE, accepted=True, match_id="match-1", server_revision=20))
    result = await asyncio.wait_for(running, 1)
    assert result.status == "succeeded" and result.output["accepted"] and "origin" not in result.output


async def test_objective_cancel_releases_investigation_with_bridge_still_open(session):
    from actor_runtime.brains import SequenceBrain, SkillCall
    from actor_runtime.objectives import Objective, ObjectiveRunner
    await observe(session, role=pb.CREW_ROLE_SHERIFF)
    owner = scheduler(session)
    runner = ObjectiveRunner(session, owner.registry, SequenceBrain([SkillCall(skill_id="actor.wait-for-message", arguments={
        "sender_actor_id": "actor-2", "task_id": "incident-1", "timeout_seconds": 240.0})]), scheduler=owner)
    running = asyncio.create_task(runner.run(Objective(text="Wait for Engineer evidence.")))
    async with asyncio.timeout(1):
        while owner.report()["active_skill_id"] != "actor.wait-for-message":
            await asyncio.sleep(0)
    cancellation = asyncio.create_task(runner.cancel())
    request = await command(session, pb.CancelInvestigationCommand)
    await observe(session, role=pb.CREW_ROLE_SHERIFF, status=pb.INCIDENT_STATUS_CANCELLED)
    await event(session, INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent(correlation_id=request.correlation_id,
        action=pb.INCIDENT_ACTION_CANCEL_INVESTIGATION, accepted=True, incident_id="incident-1", match_id="match-1",
        incident_status=pb.INCIDENT_STATUS_CANCELLED, cooling_status=pb.COOLING_STATUS_FAILURE, server_revision=20))
    result = await asyncio.wait_for(running, 2); await asyncio.wait_for(cancellation, 1)
    assert result.status == "cancelled" and not session.closed.is_set() and not owner.report()["active_skill_id"]
    assert not runner.report()["cleanup_errors"]


@pytest.mark.parametrize("role", [pb.CREW_ROLE_ENGINEER, pb.CREW_ROLE_SHERIFF])
@pytest.mark.parametrize("terminal", ["cancelled", "failed"])
async def test_pre_alarm_unsuccessful_objective_releases_selected_duty_without_closing_bridge(session, role, terminal):
    from actor_runtime.brains import SequenceBrain, SkillCall, FinishObjective
    from actor_runtime.objectives import Objective, ObjectiveRunner
    await observe(session, role=role, incident="", status=pb.INCIDENT_STATUS_NONE)
    owner = scheduler(session)
    decisions = [SkillCall(skill_id="agents-arena.wait-for-incident", arguments={})] if terminal == "cancelled" else [FinishObjective(status="failure")]
    runner = ObjectiveRunner(session, owner.registry, SequenceBrain(decisions), scheduler=owner)
    running = asyncio.create_task(runner.run(Objective(text="Select duty and await the next incident.")))
    cancellation = None
    if terminal == "cancelled":
        async with asyncio.timeout(1):
            while owner.report()["active_skill_id"] != "agents-arena.wait-for-incident":
                await asyncio.sleep(0)
        cancellation = asyncio.create_task(runner.cancel())
    request = await command(session, pb.SelectRoleCommand)
    assert request.role == pb.CREW_ROLE_CREW and request.match_id == "match-1"
    await observe(session, role=pb.CREW_ROLE_CREW, incident="", status=pb.INCIDENT_STATUS_NONE)
    await event(session, ROLE_RESULT, pb.RoleResultEvent(correlation_id=request.correlation_id, accepted=True,
        match_id="match-1", state=pb.OwnRoleObservation(actor_id="actor-1", role=pb.CREW_ROLE_CREW, match_id="match-1")))
    result = await asyncio.wait_for(running, 2)
    if cancellation is not None: await asyncio.wait_for(cancellation, 1)
    assert result.status == terminal and not session.closed.is_set()
    assert not owner.report()["active_skill_id"] and not runner.report()["cleanup_errors"]
    assert session.world.latest(pb.OwnRoleObservation, capability_id=OWN_ROLE).role == pb.CREW_ROLE_CREW
    assert session.world.latest(pb.CoolingStateObservation, capability_id=COOLING_STATE).incident_status == pb.INCIDENT_STATUS_NONE


async def test_successful_role_only_objective_preserves_selected_duty_and_pending_cycle(session):
    from actor_runtime.brains import SequenceBrain, FinishObjective
    from actor_runtime.objectives import Objective, ObjectiveRunner
    await observe(session, incident="", status=pb.INCIDENT_STATUS_NONE)
    owner = scheduler(session)
    runner = ObjectiveRunner(session, owner.registry, SequenceBrain([FinishObjective(status="success")]), scheduler=owner)
    result = await runner.run(Objective(text="Keep the selected Engineer duty for future work."))
    assert result.status == "succeeded" and not runner.report()["cleanup_errors"]
    assert session.next_command_id == 0 and not session.closed.is_set()
    assert session.world.latest(pb.OwnRoleObservation, capability_id=OWN_ROLE).role == pb.CREW_ROLE_ENGINEER


async def test_incident_start_during_pre_alarm_release_is_cancelled_using_its_authoritative_identity(session):
    from actor_runtime.brains import SequenceBrain, SkillCall
    from actor_runtime.objectives import Objective, ObjectiveRunner
    await observe(session, incident="", status=pb.INCIDENT_STATUS_NONE)
    owner = scheduler(session)
    runner = ObjectiveRunner(session, owner.registry, SequenceBrain([SkillCall(skill_id="agents-arena.wait-for-incident", arguments={})]), scheduler=owner)
    running = asyncio.create_task(runner.run(Objective(text="Wait on my selected duty.")))
    async with asyncio.timeout(1):
        while owner.report()["active_skill_id"] != "agents-arena.wait-for-incident":
            await asyncio.sleep(0)
    cancellation = asyncio.create_task(runner.cancel())
    release = await command(session, pb.SelectRoleCommand)
    # The server starts an incident before it receives the role-release request;
    # its lifecycle event is available before the next periodic observation.
    sequence = session.last_received_sequence + 1
    await event(session, INCIDENT_STARTED, pb.IncidentStartedEvent(state=pb.CoolingStateObservation(
        incident_id="incident-new", incident_status=pb.INCIDENT_STATUS_ACTIVE, match_id="match-1",
        server_revision=sequence, expires_at_unix_ms=now() + 300000)))
    await event(session, ROLE_RESULT, pb.RoleResultEvent(correlation_id=release.correlation_id,
        accepted=False, reason_code="role_selection_locked", match_id="match-1"))
    await asyncio.sleep(.02)
    assert not running.done() and session.next_command_id == 1, "A start event must wait for the ordinary Skill's public-state precondition"
    # CancelInvestigation's normal precondition uses the latest state frame.
    await observe(session, incident="incident-new")
    cancel = await command(session, pb.CancelInvestigationCommand)
    assert cancel.incident_id == "incident-new" and cancel.match_id == "match-1"
    await observe(session, incident="incident-new", status=pb.INCIDENT_STATUS_CANCELLED)
    await event(session, INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent(correlation_id=cancel.correlation_id,
        action=pb.INCIDENT_ACTION_CANCEL_INVESTIGATION, accepted=True, incident_id="incident-new", match_id="match-1",
        incident_status=pb.INCIDENT_STATUS_CANCELLED, server_revision=30))
    result = await asyncio.wait_for(running, 2); await asyncio.wait_for(cancellation, 1)
    assert result.status == "cancelled" and not session.closed.is_set() and not runner.report()["cleanup_errors"]


async def test_private_inspection_results_and_events_are_omitted_from_sqlite(tmp_path, session):
    from types import SimpleNamespace
    from actor_runtime.memory import MemoryStore
    value = diagnostic(); await observe(session)
    result, _ = await inspect(session, scheduler(session), value)
    private_event = next(item for item in session.world.events if item.capability_id == DIAGNOSTIC_EVIDENCE)
    path = str(tmp_path / "investigation.sqlite3")
    async with MemoryStore(game_id="test-game", session_id="session-1", actor_id="actor-1", path=path, profile="engineer") as memory:
        await memory.remember_skill_result(result, objective_id="objective-1")
        await memory.remember_event(SimpleNamespace(entry=private_event, id="event-1", session_id="session-1"))
        entries = await memory.retrieve()
        assert entries and "cooling-console" not in str(entries) and value.provenance.evidence_id not in str(entries)
    raw = (tmp_path / "investigation.sqlite3").read_bytes()
    assert b"cooling-console" not in raw and value.provenance.evidence_id.encode() not in raw


async def test_maintenance_requests_public_operation_only_after_right_lane_service_access(session, monkeypatch):
    from actor_agents_arena.skills import NavigateToRoom
    calls = []
    async def service(self, context, target, approach, *, lane_x):
        calls.append((target, approach.x, approach.y, lane_x))
    async def forbidden_shared_anchor(self, *args, **kwargs):
        pytest.fail("Maintenance must not first converge on the shared room anchor")
    monkeypatch.setattr(NavigateToRoom, "execute_to_approach", service)
    monkeypatch.setattr(NavigateToRoom, "execute", forbidden_shared_anchor)
    await observe(session, role=pb.CREW_ROLE_CREW, incident="", status=pb.INCIDENT_STATUS_NONE)
    sequence = session.last_received_sequence + 1
    await session.accept(message(sequence, observation=core.ObservationFrame(revision=sequence, observations=[
        envelope(DUTY_ROSTER, "agents-arena", pb.DutyRosterObservation(match_id="match-1", entries=[
            pb.DutyRoleEntry(actor_id="actor-1", role=pb.CREW_ROLE_CREW),
            pb.DutyRoleEntry(actor_id="engineer", role=pb.CREW_ROLE_ENGINEER),
            pb.DutyRoleEntry(actor_id="sheriff", role=pb.CREW_ROLE_SHERIFF)]))])))
    running = asyncio.create_task(scheduler(session).run("agents-arena.maintenance-override", {"circuit_ref": "alpha"}))
    request = await command(session, pb.MaintenanceOverrideCommand)
    assert calls == [("engineering", .3, -7.15, .3)]
    assert request.circuit_ref == "alpha" and request.match_id == "match-1"
    await event(session, INCIDENT_ACTION_RESULT, pb.IncidentActionResultEvent(correlation_id=request.correlation_id,
        action=pb.INCIDENT_ACTION_MAINTENANCE_OVERRIDE, accepted=True, match_id="match-1"))
    assert (await asyncio.wait_for(running, 1)).status == "succeeded"


@pytest.mark.parametrize("role,skill,request_type,result_type,capability,target,x,y", [
    (pb.CREW_ROLE_ENGINEER, "agents-arena.inspect-cooling", pb.InspectCoolingCommand,
     pb.DiagnosticEvidenceEvent, DIAGNOSTIC_EVIDENCE, "engineering", -.3, -7.15),
    (pb.CREW_ROLE_SHERIFF, "agents-arena.inspect-security-records", pb.InspectSecurityRecordsCommand,
     pb.SecurityEvidenceEvent, SECURITY_EVIDENCE, "main-hall", -1.5, -1.9),
])
async def test_private_inspection_uses_own_service_access_before_typed_request(
        session, monkeypatch, role, skill, request_type, result_type, capability, target, x, y):
    from actor_agents_arena.skills import NavigateToRoom
    calls = []
    async def service(self, context, destination, approach, *, lane_x):
        calls.append((destination, approach.x, approach.y, lane_x))
    async def forbidden_shared_anchor(self, *args, **kwargs):
        pytest.fail("Inspection must not first converge on the shared room anchor")
    monkeypatch.setattr(TerminalSkill, "approach", _TERMINAL_SERVICE_APPROACH)
    monkeypatch.setattr(NavigateToRoom, "execute_to_approach", service)
    monkeypatch.setattr(NavigateToRoom, "execute", forbidden_shared_anchor)
    await observe(session, role=role)
    running = asyncio.create_task(scheduler(session).run(skill, {"incident_id": "incident-1"}))
    request = await command(session, request_type)
    assert calls == [(target, x, y, -.3)]
    evidence = diagnostic() if role == pb.CREW_ROLE_ENGINEER else security()
    await event(session, capability, result_type(correlation_id=request.correlation_id, accepted=True,
        evidence=evidence, recipient_actor_id="actor-1", match_id="match-1",
        expires_at_unix_ms=evidence.provenance.expires_at_unix_ms, server_revision=10))
    await observe(session, role=role, evidence=evidence)
    assert (await asyncio.wait_for(running, 1)).status == "succeeded"
