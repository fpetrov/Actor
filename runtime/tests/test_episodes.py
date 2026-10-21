import asyncio

import pytest

from actor_runtime.brains import SequenceBrain, FinishObjective, SkillCall
from actor_runtime.builtin_skills import skill_package
from actor_runtime.episodes import MatchEpisodeController, EpisodeDirective, EpisodeLimits
from actor_runtime.knowledge import WorkingKnowledge, KnowledgeRecord
from actor_runtime.memory import MemoryStore
from actor_runtime.objectives import ObjectiveRunner, Objective
from actor_runtime.skills import SkillRegistry, SkillScheduler, SkillDefinition, ActorSkill, SkillModel
from actor_runtime.errors import ActorError
from actor_standard import LOCOMOTION, Locomotion2DCommand
from actor_runtime.skills import CapabilityRequirement


async def eventually(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.005)


class Policy:
    def __init__(self):
        self.key, self.state = "first", "active"

    def evaluate(self, session):
        return EpisodeDirective(scope_id="test-scope", episode_key=self.key, state=self.state,
            reason="server_finished" if self.state == "terminal" else "phase")


def registry():
    result = SkillRegistry()
    for definition in skill_package(): result.register(definition)
    return result


@pytest.mark.asyncio
async def test_finished_objective_rotates_fresh_brain_but_does_not_finish_match(session, monkeypatch):
    policy, providers, finalizers = Policy(), [], []
    class Brain(SequenceBrain):
        async def start(self, objective, catalog):
            assert not session.event_router._closed
            if providers[:-1]: assert providers[-2].closed
            await super().start(objective, catalog)
    def factory():
        brain = Brain([FinishObjective(status="success")]); providers.append(brain); return brain
    async def finalize(self, status, reason): finalizers.append((status, reason))
    monkeypatch.setattr(ObjectiveRunner, "_finalize_objective", finalize)
    controller = MatchEpisodeController(session, registry(), factory, policy,
        limits=EpisodeLimits(restart_cooldown_seconds=.02))
    task = asyncio.create_task(controller.run())
    await eventually(lambda: len(providers) >= 2 and providers[-1].closed)
    assert not task.done()
    assert session.event_router._worker is not None and not session.event_router._closed
    assert controller.report()["episodes"] == []
    policy.state = "terminal"; await session.world.notify()
    result = await task
    assert result.status == "completed" and result.authoritative_terminal
    assert result.episodes >= 2 and result.decisions >= 2
    assert len(finalizers) == 1
    assert all(brain.closed for brain in providers)


@pytest.mark.asyncio
async def test_phase_change_closes_old_provider_before_new_episode_and_death_suspends(session):
    policy, providers = Policy(), []
    def factory():
        if providers: assert providers[-1].closed
        brain = SequenceBrain([SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 30.0})])
        providers.append(brain); return brain
    controller = MatchEpisodeController(session, registry(), factory, policy,
        limits=EpisodeLimits(restart_cooldown_seconds=.01))
    task = asyncio.create_task(controller.run())
    await eventually(lambda: controller.scheduler.current_skill_metadata() is not None)
    policy.key = "meeting"; await session.world.notify()
    await eventually(lambda: len(providers) == 2)
    policy.state = "suspended"; await session.world.notify()
    await eventually(lambda: providers[-1].closed)
    count = len(providers)
    await asyncio.sleep(.05)
    assert len(providers) == count and not task.done()
    assert controller.scheduler.current_skill_metadata() is None
    policy.state = "terminal"; await session.world.notify()
    assert (await task).status == "completed"


@pytest.mark.asyncio
async def test_terminal_during_reasoning_discards_answer_without_skill_dispatch(session):
    policy, entered = Policy(), asyncio.Event()
    class Slow(SequenceBrain):
        async def decide(self, context):
            entered.set()
            await asyncio.sleep(30)
            return SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 1.0})
    brain = Slow([])
    controller = MatchEpisodeController(session, registry(), lambda: brain, policy)
    task = asyncio.create_task(controller.run())
    await entered.wait()
    policy.state = "terminal"; await session.world.notify()
    result = await task
    assert result.authoritative_terminal and brain.closed
    assert controller.scheduler.report()["skill_count"] == 0


@pytest.mark.asyncio
async def test_participation_cancel_is_terminal_and_private_activity_hidden(session):
    policy = Policy()
    controller = MatchEpisodeController(session, registry(),
        lambda: SequenceBrain([SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 30.0})]), policy)
    task = asyncio.create_task(controller.run())
    await eventually(lambda: controller.scheduler.current_skill_metadata() is not None)
    assert controller.scheduler.current_skill_metadata()["skill_id"] == "[private_activity]"
    assert controller.router.current_skill()["skill_id"] == "actor.wait"
    await controller.cancel()
    result = await task
    assert result.status == "cancelled" and not result.authoritative_terminal
    assert controller.report()["episodes"] == []
    assert session.privacy.public({"skill_id": "private-action"})["skill_id"] == "[private_activity]"


@pytest.mark.asyncio
async def test_total_budget_cancels_long_skill_without_claiming_game_over(session):
    controller = MatchEpisodeController(session, registry(),
        lambda: SequenceBrain([SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 30.0})]), Policy(),
        limits=EpisodeLimits(duration_seconds=1))
    result = await controller.run()
    assert result.reason == "participation_timeout" and not result.authoritative_terminal
    assert controller.scheduler.current_skill_metadata() is None


@pytest.mark.asyncio
async def test_availability_is_rechecked_after_preflight_before_lease(session):
    allowed = True
    class Empty(SkillModel): pass
    class Changed(ActorSkill):
        def preflight(self, context, arguments):
            nonlocal allowed
            allowed = False
        async def execute(self, context, arguments):
            pytest.fail("Unavailable Skill acquired execution")
    definition = SkillDefinition("test.dynamic", "1.0.0", "Synthetic policy boundary", Empty, Empty,
        Changed, availability=lambda context: allowed)
    reg = registry(); reg.register(definition)
    assert any(item["id"] == definition.skill_id for item in reg.catalog(session))
    scheduler = SkillScheduler(session, reg)
    result = await scheduler.run(definition.skill_id, {})
    assert result.reason == "skill_unavailable" and scheduler.report()["skill_count"] == 0
    assert not any(item["id"] == definition.skill_id for item in reg.catalog(session))
    assert (await scheduler.run("actor.wait", {"duration_seconds": 0.0})).status == "succeeded"


def note(scope="one", identity="fact", kind="player_claim", expires=100000):
    return KnowledgeRecord(id=identity, scope_id=scope, kind=kind, source_event_id="received-message-1",
        source_actor_id="peer", observed_at_unix_ms=1000, expires_at_unix_ms=expires,
        content={"text": "ordinary unverified claim"})


def test_private_knowledge_distinguishes_claim_hypothesis_and_expiry():
    now = [2.0]
    knowledge = WorkingKnowledge("one", clock=lambda: now[0])
    assert knowledge.remember(note())
    hypothesis = knowledge.hypothesize("This peer may be mistaken", .4, ["fact"])
    records = {record["id"]: record for record in knowledge.context()}
    assert records["fact"]["kind"] == "player_claim"
    assert records[hypothesis]["kind"] == "hypothesis"
    assert records[hypothesis]["evidence_refs"] == ["fact"]
    assert "unverified" in records[hypothesis]["content"]["epistemic_status"]
    with pytest.raises(ActorError, match="Working evidence"):
        knowledge.remember(note(scope="foreign"))
    with pytest.raises(ActorError): knowledge.hypothesize("I invented evidence", 1.0, ["hidden"])
    now[0] = 101
    assert knowledge.context() == []
    assert "ordinary unverified claim" not in str(knowledge.report())


@pytest.mark.asyncio
async def test_same_profile_cannot_retrieve_other_match_evidence(tmp_path):
    options = dict(game_id="game", actor_id="actor", path=str(tmp_path / "notes.sqlite"), profile="alice")
    first = MemoryStore(session_id="first", **options)
    first.bind_scope("match-one")
    await first.remember(evidence_kind="observed_fact", source_event_id="same-native-id", content={"fact": 1},
                         summary="Prior match", tags=tuple("tag" + str(i) for i in range(30)))
    assert len(await first.retrieve()) == 1
    await first.close()
    second = MemoryStore(session_id="second", **options)
    second.bind_scope("match-two")
    try:
        assert await second.retrieve() == []
        await second.remember(evidence_kind="observed_fact", source_event_id="same-native-id", content={"fact": 2}, summary="Current")
        assert [entry.content for entry in await second.retrieve()] == [{"fact": 2}]
    finally:
        await second.close()


async def test_repeated_external_cancel_during_router_close_always_releases_owner(session, monkeypatch):
    policy, entered, release = Policy(), asyncio.Event(), asyncio.Event()
    policy.state = "waiting"
    registry_value = registry()
    original_filter = lambda session, definition: True
    registry_value.availability_filter = original_filter
    controller = MatchEpisodeController(session, registry_value, lambda: SequenceBrain([]), policy)
    original_close = session.event_router.close
    async def slow_close():
        entered.set()
        await release.wait()
        await original_close()
    monkeypatch.setattr(session.event_router, "close", slow_close)
    task = asyncio.create_task(controller.run())
    await eventually(lambda: session.event_router._worker is not None)
    task.cancel()
    await entered.wait()
    task.cancel()
    release.set()
    result = await asyncio.wait_for(task, 1)
    assert result.status == "cancelled"
    assert session.participation_controller is None and registry_value.availability_filter is original_filter
    assert not controller._running and controller._finished.is_set()
    await asyncio.wait_for(controller.cancel(), .2)


async def test_cancellation_suppressing_close_cannot_retain_participation_ownership(session, monkeypatch):
    policy, release = Policy(), asyncio.Event()
    policy.state = "waiting"
    controller = MatchEpisodeController(session, registry(), lambda: SequenceBrain([]), policy)
    original_bound = controller._bounded_cleanup
    async def short(operation, name, **kwargs):
        return await original_bound(operation, name, timeout=.03)
    async def unresponsive():
        while not release.is_set():
            try: await release.wait()
            except asyncio.CancelledError: pass
    monkeypatch.setattr(controller, "_bounded_cleanup", short)
    monkeypatch.setattr(session.event_router, "close", unresponsive)
    task = asyncio.create_task(controller.run())
    await eventually(lambda: controller._running)
    task.cancel()
    result = await asyncio.wait_for(task, .5)
    assert result.status == "cancelled" and session.participation_controller is None
    assert controller._finished.is_set() and "event_router_close_timeout" in controller.report()["cleanup_errors"]
    release.set()
    # The test's deliberately broken close did not own the actual router task.
    from actor_runtime.events import EventRouter
    await EventRouter.close(session.event_router)


def test_routine_claim_flood_cannot_evict_personally_observed_important_evidence():
    store = WorkingKnowledge("one", max_entries=3, clock=lambda: 2.0)
    witnessed = note(identity="witness", kind="historical_observation").model_copy(update={"importance": .95})
    assert store.remember(witnessed)
    for index in range(100):
        store.remember(note(identity="claim-" + str(index)).model_copy(update={"observed_at_unix_ms": 1001 + index, "importance": .4}))
    assert "witness" in store.records and len(store.records) == 3 and store.dropped == 98


async def test_new_authoritative_boundary_prevents_even_starting_another_brain_turn(session):
    permitted = True
    class Brain(SequenceBrain):
        async def start(self, objective, catalog):
            nonlocal permitted
            await super().start(objective, catalog)
            permitted = False
        async def decide(self, context):
            pytest.fail("A provider turn started after the authoritative boundary changed")
    brain = Brain([])
    runner = ObjectiveRunner(session, registry(), brain, execution_guard=lambda: permitted)
    result = await runner.run(Objective(text="Bounded episode"))
    assert result.reason == "episode_superseded" and brain.closed


async def test_terminal_detail_grace_is_passive_and_bounded(session):
    class TerminalPolicy(Policy):
        complete = False
        def evaluate(self, session):
            return EpisodeDirective(scope_id="test-scope", episode_key="end", state="terminal",
                reason="server_finished", terminal_complete=self.complete,
                outcome={"final_detail_received": self.complete})
    policy = TerminalPolicy()
    def forbidden(): pytest.fail("Terminal detail delivery started a Brain")
    controller = MatchEpisodeController(session, registry(), forbidden, policy,
        limits=EpisodeLimits(terminal_grace_seconds=.2))
    task = asyncio.create_task(controller.run())
    await asyncio.sleep(.02)
    policy.complete = True; await session.world.notify()
    result = await task
    assert result.status == "completed" and result.outcome["final_detail_received"] and result.decisions == 0


async def test_changed_execution_boundary_revokes_late_commands_but_allows_cleanup_zero(session):
    entered, release, permitted = asyncio.Event(), asyncio.Event(), True
    class Empty(SkillModel): pass
    class Motor(ActorSkill):
        async def execute(self, context, arguments):
            context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
            entered.set()
            await release.wait()
            context.command(LOCOMOTION, Locomotion2DCommand(desired_move_x=1))
            pytest.fail("A stale epoch renewed its actuator intent")
        async def cleanup(self, context):
            context.command(LOCOMOTION, Locomotion2DCommand())
    reg = registry()
    reg.register(SkillDefinition("test.motor", "1.0.0", "Synthetic guarded actuator", Empty, Empty, Motor,
        (CapabilityRequirement(LOCOMOTION, Locomotion2DCommand, "command"),), (LOCOMOTION,)))
    scheduler = SkillScheduler(session, reg)
    scheduler.execution_guard = lambda: permitted
    task = asyncio.create_task(scheduler.run("test.motor", {}))
    await entered.wait()
    permitted = False; release.set()
    result = await task
    assert result.reason == "execution_boundary_changed"
    assert session.next_command_id == 2  # Initial intent and guaranteed cleanup zero.
    stream = session.outgoing()
    while True:
        outgoing = await anext(stream)
        if outgoing.WhichOneof("body") == "command":
            value = Locomotion2DCommand(); outgoing.command.payload.Unpack(value)
            assert value.desired_move_x == 0 and value.desired_move_y == 0
            break
    await stream.aclose()


async def test_private_hypothesis_skill_uses_active_scheduler_and_catalog_cannot_write_notes(session):
    session.working_knowledge = WorkingKnowledge("one", clock=lambda: 2.0)
    session.working_knowledge.remember(note())
    reg = SkillRegistry.discover()
    definition = reg.definition("actor.wait")
    impure = SkillDefinition("test.impure-availability", "1.0.0", "A rejected impure plugin",
        definition.input_model, definition.result_model, definition.factory,
        availability=lambda context: context.remember_hypothesis("Should not be recorded", .5, ["fact"]))
    reg.register(impure)
    assert not any(item["id"] == impure.skill_id for item in reg.catalog(session))
    assert len(session.working_knowledge.records) == 1
    scheduler = SkillScheduler(session, reg)
    result = await scheduler.run("actor.remember-hypothesis", {"statement": "This claim is uncertain", "confidence": .5,
                                                           "evidence_refs": ["fact"]})
    assert result.status == "succeeded"
    assert session.working_knowledge.context()[0]["kind"] == "hypothesis"
    assert "This claim is uncertain" not in str(scheduler.report())
