"""Routine own work updates must not invalidate a prepared continuation."""
import pytest

from actor_agents_arena import SOCIAL_OWN_STATE_CHANGED
from actor_runtime.objectives import Objective
from actor_runtime.config import RouterConfig
from extensions.agents_arena.v1 import social_pb2 as pb
from extensions.agents_arena.v1 import incidents_pb2 as incidents
from test_social_runtime import event, observe
from test_events import spin


async def own_event(session, **changes):
    value = pb.SocialOwnStateObservation(
        match_id="match-social", actor_id="actor-1", assigned=True,
        alignment=pb.SOCIAL_ALIGNMENT_CREW, life=pb.SOCIAL_LIFE_ALIVE,
        connection_generation=1, can_move=True, server_revision=session.last_received_sequence + 1)
    for key, item in changes.items():
        setattr(value, key, item)
    await event(session, SOCIAL_OWN_STATE_CHANGED, pb.SocialOwnStateChangedEvent(state=value))


async def baseline(session, classifier=None):
    await observe(session)
    session.event_router.configure(config=RouterConfig(decision_cooldown=0))
    if classifier is not None:
        session.event_router.configure(classifier=classifier)
    await session.event_router.start(Objective(text="Continue own work"), background_planning=True)
    await own_event(session)
    router = session.event_router
    assert router.execution_revision > 0
    ids = [v.id for v in router.take_trigger_events()]
    router.acknowledge_context(ids, router.cognitive_revision)
    router.acknowledge_decision(ids, router.cognitive_revision)
    router.clear_replan(router.cognitive_revision)
    return router


@pytest.mark.parametrize("changes", [
    {}, {"can_move": False}, {"ready": False},
    {"kill_ready_at_unix_ms": 1000}, {"sabotage_ready_at_unix_ms": 1000},
    {"emergency_ready_at_unix_ms": 1000}, {"remaining_meetings": 1},
])
async def test_work_ready_and_cooldown_updates_are_not_hard_boundaries(session, changes):
    router = await baseline(session)
    epoch = router.execution_revision
    classified = router.report()["counts"]["events_classified"]
    await own_event(session, **changes)
    # Even a delayed asynchronous assessment must not take the critical route.
    await spin(lambda: router.report()["counts"].get("events_classified", 0) > classified)
    assert router.execution_revision == epoch
    assert not router.hard_replan.is_set()
    current = session.world.events[-1]
    assert current.capability_id == SOCIAL_OWN_STATE_CHANGED
    assert getattr(current.payload, "state").server_revision > 0


@pytest.mark.parametrize("changes", [
    {"life": pb.SOCIAL_LIFE_DEAD}, {"life": pb.SOCIAL_LIFE_EJECTED},
    {"alignment": pb.SOCIAL_ALIGNMENT_IMPOSTOR},
    {"duty": incidents.CREW_ROLE_SHERIFF}, {"assigned": False},
    {"connection_generation": 2},
])
async def test_life_identity_role_and_assignment_changes_stay_critical(session, changes):
    router = await baseline(session)
    epoch = router.execution_revision
    await own_event(session, **changes)
    assert router.execution_revision > epoch
    assert router.hard_replan.is_set()
