import time

from actor.core.v1 import actor_pb2 as core
from actor_agents_arena import CALIBRATION_STATE, CalibrationTaskStateObservation
from actor_runtime.classifiers import EventRule, EventRuleRegistry
from actor_runtime.extensions import type_url
from actor_runtime.objectives import Objective
from actor_runtime.snapshots import build_decision_context
from conftest import envelope, message


async def test_observation_policy_is_private_without_routing_or_classification(session):
    def never(*args):
        raise AssertionError("Observation frames must not call event semantics")
    session.event_router.rules = EventRuleRegistry([EventRule(CALIBRATION_STATE, "agents-arena",
        type_url(CalibrationTaskStateObservation), never, never,
        data_policy="session_only", expiry_field="expires_at_unix_ms")])
    private = "legitimately-inspected-private-evidence"
    state = CalibrationTaskStateObservation(task_id=private,
        expires_at_unix_ms=int(time.time() * 1000) + 60000)
    before = session.event_router.report()["counts"].copy()
    await session.accept(message(3, observation=core.ObservationFrame(revision=3,
        observations=[envelope(CALIBRATION_STATE, "agents-arena", state)])))
    cached = session.world.latest_entry(CalibrationTaskStateObservation, capability_id=CALIBRATION_STATE)
    assert cached.data_policy == "session_only" and cached.expires_at_unix_ms == state.expires_at_unix_ms
    assert session.privacy.has_private_data
    context = build_decision_context(session, Objective(text="Use my permitted observations."), [])
    assert context.observations[0].value["taskId"] == private
    assert private not in str(session.privacy.public(context.model_dump(mode="json")))
    assert session.event_router.report()["counts"] == before and not session.world.events

    # An authoritative clear replaces the old value; omission would retain it.
    await session.accept(message(4, observation=core.ObservationFrame(revision=4,
        observations=[envelope(CALIBRATION_STATE, "agents-arena", CalibrationTaskStateObservation())])))
    assert session.world.latest(CalibrationTaskStateObservation, capability_id=CALIBRATION_STATE).task_id == ""
    assert private not in build_decision_context(session, Objective(text="Observe cleared state."), []).external_json()


async def test_private_observation_expiry_omits_working_value(session):
    session.event_router.rules = EventRuleRegistry([EventRule(CALIBRATION_STATE, "agents-arena",
        type_url(CalibrationTaskStateObservation), lambda _: None, lambda *_: None,
        data_policy="session_only", expiry_field="expires_at_unix_ms")])
    await session.accept(message(3, observation=core.ObservationFrame(revision=3, observations=[
        envelope(CALIBRATION_STATE, "agents-arena", CalibrationTaskStateObservation(
            task_id="expired-private-detail", expires_at_unix_ms=int(time.time() * 1000) - 1))])))
    context = build_decision_context(session, Objective(text="Use unexpired evidence only."), [])
    assert context.observations[0].expired and context.observations[0].value is None
