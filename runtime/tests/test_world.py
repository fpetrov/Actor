import asyncio

import pytest

from actor_runtime.errors import ActorError
from actor_runtime.world import WorldState, CachedPayload
from actor_standard import Transform2DObservation


async def test_cache_and_event_history_are_bounded():
    world = WorldState("self", event_capacity=4, observation_capacity=8)
    for i in range(20):
        item = CachedPayload(f"entity-{i}", "capability", i + 1, i + 1, Transform2DObservation(x=i))
        world.apply_observations([item], i + 1, i + 1)
        world.apply_event(item)
    assert len(world.observations) == 8
    assert len(world.events) == 4
    assert world.events[-1].payload.x == 19


async def test_world_waits_on_async_notification():
    world = WorldState("self")
    task = asyncio.create_task(world.wait_until(lambda state: state.revision == 10, timeout=1))
    await asyncio.sleep(0)
    world.apply_observations([], 1, 10)
    await world.notify()
    assert await task is world


async def test_reused_payload_type_requires_explicit_capability():
    world = WorldState("self")
    items = [CachedPayload("self", capability, 1, 1, Transform2DObservation(x=x))
             for capability, x in (("first", 1), ("second", 2))]
    world.apply_observations(items, 1, 1)
    with pytest.raises(ActorError, match="ObservationAmbiguous"):
        world.latest(Transform2DObservation)
    assert world.latest(Transform2DObservation, capability_id="second").x == 2


async def test_empty_or_other_observation_frames_do_not_refresh_a_cached_value():
    from actor_standard import Motion2DObservation
    world = WorldState("self")
    transform = CachedPayload("self", "transform", 1, 1, Transform2DObservation(x=1), received_at=12)
    world.apply_observations([transform], 1, 1)
    motion = CachedPayload("self", "motion", 2, 2, Motion2DObservation(), received_at=34)
    world.apply_observations([motion], 2, 2)
    world.apply_observations([], 3, 3)
    assert world.latest_entry(Transform2DObservation).received_at == 12
    assert world.latest_entry(Motion2DObservation).received_at == 34
    assert world.latest_entry(Transform2DObservation).sequence == 1
