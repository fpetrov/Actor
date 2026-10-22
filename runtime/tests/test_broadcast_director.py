"""Pure presented-camera selection with fake clocks; no bridge/game calls."""
from dataclasses import replace
import json

import pytest

from actor_broadcast.contracts import PresentationAck, PresentedView, Scope
from actor_broadcast.director import ANCHORS, CameraDirector, ViewIntent


class Clock:
    def __init__(self): self.value = 100.0
    def monotonic(self): return self.value
    def wall(self): return 1000 + self.value
    def advance(self, seconds): self.value += seconds


class Rng:
    def __init__(self, value=18): self.value, self.calls = value, 0
    def uniform(self, low, high):
        assert (low, high) == (18, 26); self.calls += 1; return self.value


def view(clock, *, sequence=1, epoch=1, generation=1, actors=("Анна", "Борис", "Вера"),
         anchor="main-hall", focus="", phase="Exploration", terminal=False):
    scope = Scope("broadcast", "match", generation, epoch)
    return PresentedView(scope, sequence, sequence, int(clock.wall()*1000), int(clock.wall()*1000)+500,
        phase, {"camera": {"anchor_id": anchor, "focus_actor_id": focus},
            "visible_actor_ids": list(actors), "actors": [{"actor_id": actor} for actor in actors],
            # Neither a roll-call nor old facts grant focus permission.
            "meeting": {"participants": [{"actor_id": "hidden"}]}, "hidden_field": "never_used"},
        (), PresentationAck(scope, sequence, sequence, clock.monotonic()), terminal)


def system():
    clock, rng = Clock(), Rng()
    return CameraDirector(clock=clock.monotonic, wall_clock=clock.wall, rng=rng), clock, rng


def refresh(director, clock, **options):
    previous = director.view
    options.setdefault("sequence", previous.sequence + 1 if previous else 1)
    options.setdefault("epoch", previous.scope.view_epoch if previous else 1)
    return director.observe(view(clock, **options))


def due(director, clock, **options):
    clock.advance(18); assert refresh(director, clock, **options)
    return director.next_intent()


def test_one_dwell_sample_per_camera_not_per_frame_poll_and_never_before_server_dwell():
    director, clock, rng = system(); assert refresh(director, clock)
    for elapsed in (0, 7.99, .01, 9.99):
        clock.advance(elapsed); refresh(director, clock)
        assert director.next_intent() is None
    assert rng.calls == 1
    clock.advance(.01); refresh(director, clock)
    intent = director.next_intent()
    assert intent.focus_actor_id == "Анна" and intent.anchor_id == ""
    assert intent.scope == director.view.scope and intent.presentation_sequence == director.view.sequence
    assert intent.expires_at_unix_ms == director.view.expires_at_unix_ms
    assert director.next_intent() is None and rng.calls == 1


def test_only_matching_new_actually_presented_epoch_confirms_then_fair_visible_rotation():
    director, clock, rng = system(); refresh(director, clock)
    first = due(director, clock)
    assert director.selection_result(first, True)
    assert director.report()["confirmed_presented_switches"] == 0
    assert director.next_intent() is None
    refresh(director, clock, focus="Анна", anchor="", epoch=2)
    assert director.report()["confirmed_presented_switches"] == 1
    second = due(director, clock, focus="Анна", anchor="", epoch=2)
    assert second.focus_actor_id == "Борис"
    refresh(director, clock, focus="Борис", anchor="", epoch=3)
    third = due(director, clock, focus="Борис", anchor="", epoch=3)
    assert third.focus_actor_id == "Вера"
    refresh(director, clock, focus="Вера", anchor="", epoch=4)
    fourth = due(director, clock, focus="Вера", anchor="", epoch=4)
    assert fourth.focus_actor_id == "Анна"
    assert not director.selection_result(first, False)


def test_new_epoch_with_different_target_clears_pending_without_false_confirmation():
    director, clock, rng = system(); refresh(director, clock)
    intent = due(director, clock)
    refresh(director, clock, epoch=2, anchor="security")
    assert not director.report()["pending"]
    assert director.report()["confirmed_presented_switches"] == 0
    assert not director.selection_result(intent, True) and director.next_intent() is None


def test_latest_visible_set_replaces_old_actors_and_never_uses_public_rollcall():
    director, clock, rng = system(); refresh(director, clock, actors=("old",))
    intent = due(director, clock, actors=("new",))
    assert intent.focus_actor_id == "new"
    assert "hidden" not in (intent.focus_actor_id, intent.anchor_id)


def test_no_alternative_visible_actor_rotates_only_authored_anchors_fairly():
    director, clock, rng = system(); refresh(director, clock, actors=())
    choices = []
    for index in range(4):
        intent = due(director, clock, actors=(), anchor=director._target[0], epoch=index+1)
        assert intent.focus_actor_id == ""; choices.append(intent.anchor_id)
        refresh(director, clock, actors=(), anchor=intent.anchor_id, epoch=index+2)
    assert choices == ["engineering", "security", "storage", "main-hall"]


def test_single_current_focus_uses_public_anchor_instead_of_reselecting_same_actor():
    director, clock, rng = system(); refresh(director, clock, actors=("one",), focus="one", anchor="")
    intent = due(director, clock, actors=("one",), focus="one", anchor="")
    assert intent.anchor_id in ANCHORS and not intent.focus_actor_id


@pytest.mark.parametrize("phase", ["Waiting", "RoleAssignment", "Discussion", "Voting", "VoteResolution", "GameOver"])
def test_non_exploration_never_cuts_even_when_due(phase):
    director, clock, rng = system(); refresh(director, clock, phase=phase)
    clock.advance(18); refresh(director, clock, phase=phase)
    assert director.next_intent() is None


def test_postmeeting_rearms_dwell_without_an_immediate_cut():
    director, clock, rng = system(); refresh(director, clock)
    clock.advance(40); refresh(director, clock, phase="Discussion")
    clock.advance(60); refresh(director, clock)
    assert director.next_intent() is None
    assert due(director, clock) is not None and rng.calls == 2


def test_terminal_latches_and_cannot_reopen_on_later_nonterminal_view():
    director, clock, rng = system(); refresh(director, clock)
    intent = due(director, clock)
    assert refresh(director, clock, phase="GameOver", terminal=True)
    assert not refresh(director, clock) and director.next_intent() is None
    assert not director.selection_result(intent, True)


def test_stale_monotonic_or_wall_frame_does_not_select_or_replace_current():
    director, clock, rng = system(); refresh(director, clock)
    clock.advance(18)
    assert director.next_intent() is None
    stale = view(clock, sequence=2)
    future = replace(stale, observed_at_unix_ms=stale.observed_at_unix_ms+1)
    assert not director.observe(future)
    old = replace(stale, observed_at_unix_ms=stale.observed_at_unix_ms-1000,
        expires_at_unix_ms=stale.observed_at_unix_ms-500)
    assert not director.observe(old)
    assert director.view.sequence == 1
    assert director.observe(stale) and director.next_intent() is not None


def test_old_scope_sequence_and_generation_cannot_change_pending_or_confirm_camera():
    director, clock, rng = system(); refresh(director, clock)
    intent = due(director, clock)
    assert not director.observe(view(clock, sequence=1, epoch=1))
    foreign = view(clock, sequence=100)
    foreign_scope = replace(foreign.scope, match_id="foreign")
    foreign = replace(foreign, scope=foreign_scope, presentation=replace(foreign.presentation, scope=foreign_scope))
    assert not director.observe(foreign) and director.report()["pending"]
    assert refresh(director, clock, sequence=1, generation=2)
    assert not director.report()["pending"] and director.report()["confirmed_presented_switches"] == 0
    assert not director.observe(view(clock, sequence=999, epoch=100, generation=1))
    assert not director.selection_result(intent, True)


def test_rejection_and_timeout_rearm_once_without_retry_flood():
    director, clock, rng = system(); refresh(director, clock)
    intent = due(director, clock)
    assert director.selection_result(intent, False)
    assert director.next_intent() is None and rng.calls == 2
    second = due(director, clock)
    clock.advance(5); refresh(director, clock)
    assert director.next_intent() is None and rng.calls == 3
    assert not director.selection_result(second, True)
    for _ in range(100): assert director.next_intent() is None
    assert rng.calls == 3 and director.report()["selection_timeouts"] == 1


def test_actor_memory_is_bounded_and_report_contains_only_counts_and_flags():
    director, clock, rng = system()
    for index in range(20):
        refresh(director, clock, sequence=index+1, epoch=index+1, actors=(f"private-{index}",),
            anchor="", focus=f"private-{index}")
    report = director.report()
    assert report["remembered_focus_count"] == 6 and "private" not in json.dumps(report)
    assert all(type(value) in (int, bool) for value in report.values())


@pytest.mark.parametrize("mutation", [
    lambda current: current.update(actors=[{"actor_id":"one"}]*7),
    lambda current: current.update(actors=[{"actor_id":"one"}]*2, visible_actor_ids=["one","one"]),
    lambda current: current.update(visible_actor_ids=["hidden"]),
    lambda current: current.update(actors=[{}]),
    lambda current: current["camera"].update(anchor_id="private-room"),
    lambda current: current["camera"].update(focus_actor_id="one"),
])
def test_malformed_current_projection_fails_before_any_intent(mutation):
    director, clock, rng = system(); source = view(clock); mutation(source.current)
    with pytest.raises(ValueError): director.observe(source)
    assert director.view is None and director.report()["intents"] == 0


@pytest.mark.parametrize("bad", [True, 8, 27, float("nan")])
def test_invalid_rng_cannot_weaken_server_dwell(bad):
    director, clock, rng = system(); rng.value = bad
    with pytest.raises(ValueError): refresh(director, clock)


def test_clock_reversal_is_rejected_and_intent_has_exact_one_target():
    director, clock, rng = system(); refresh(director, clock); clock.advance(-1)
    with pytest.raises(ValueError): director.next_intent()
    scope = Scope("broadcast", "match", 1, 1)
    for options in ({}, {"anchor_id":"hidden"}, {"anchor_id":"main-hall", "focus_actor_id":"one"}):
        with pytest.raises(ValueError): ViewIntent(scope, 1, 1000, **options)


def empty_frames(director, clock, seconds, **options):
    options.setdefault("actors", ())
    for _ in range(round(seconds*4)):
        clock.advance(.25); assert refresh(director, clock, **options)


def test_empty_seek_waits_for_both_five_empty_seconds_and_ten_camera_seconds_without_rng_resampling():
    director, clock, rng = system(); refresh(director, clock, actors=())
    empty_frames(director, clock, 9.75)
    assert director.next_intent() is None and rng.calls == 1
    empty_frames(director, clock, .25)
    intent = director.next_intent()
    assert intent.anchor_id == "engineering" and not intent.focus_actor_id
    assert intent.presentation_sequence == director.view.sequence
    assert director.report()["empty_frame_seeks"] == 1 and rng.calls == 1
    for _ in range(100): assert director.next_intent() is None
    assert rng.calls == 1


def test_empty_period_begins_when_the_last_current_actor_disappears_not_from_camera_start():
    director, clock, rng = system(); refresh(director, clock)
    clock.advance(6); refresh(director, clock, actors=())
    empty_frames(director, clock, 4.75)
    assert director.next_intent() is None  # Camera >10s but empty <5s.
    empty_frames(director, clock, .25)
    assert director.next_intent().anchor_id == "engineering"


def test_one_visible_actor_resets_empty_interval_and_never_shortcuts_normal_actor_dwell():
    director, clock, rng = system(); refresh(director, clock, actors=())
    empty_frames(director, clock, 9.75)
    clock.advance(.25); refresh(director, clock, actors=("seen",))
    assert director.next_intent() is None
    clock.advance(.25); refresh(director, clock, actors=())
    empty_frames(director, clock, 4.75)
    assert director.next_intent() is None
    empty_frames(director, clock, .25)
    assert director.next_intent().anchor_id == "engineering"


def test_missing_frames_or_heartbeat_polls_do_not_certify_continuous_emptiness():
    director, clock, rng = system(); refresh(director, clock, actors=())
    clock.advance(10)
    for _ in range(100): assert director.next_intent() is None
    assert refresh(director, clock, actors=())
    assert director.next_intent() is None
    empty_frames(director, clock, 4.75)
    assert director.next_intent() is None
    empty_frames(director, clock, .25)
    assert director.next_intent().anchor_id == "engineering" and rng.calls == 1


def test_rejected_and_timed_out_empty_seek_retain_backoff_not_a_probe_loop():
    director, clock, rng = system(); refresh(director, clock, actors=())
    empty_frames(director, clock, 10); first = director.next_intent()
    assert director.selection_result(first, False)
    empty_frames(director, clock, 17.75)
    assert director.next_intent() is None
    empty_frames(director, clock, .25); second = director.next_intent()
    assert second is not None and director.selection_result(second, True)
    empty_frames(director, clock, 5)
    assert director.next_intent() is None  # Acceptance was not a new frame.
    empty_frames(director, clock, 17.75)
    assert director.next_intent() is None
    assert director.report()["intents"] == 2 and rng.calls == 3


def test_confirmed_new_empty_camera_restarts_both_intervals_and_old_result_cannot_accelerate_it():
    director, clock, rng = system(); refresh(director, clock, actors=())
    empty_frames(director, clock, 10); first = director.next_intent()
    assert director.selection_result(first, True)
    refresh(director, clock, actors=(), epoch=2, anchor=first.anchor_id)
    assert director.report()["confirmed_presented_switches"] == 1
    assert not director.selection_result(first, False)
    empty_frames(director, clock, 9.75, epoch=2, anchor=first.anchor_id)
    assert director.next_intent() is None
    empty_frames(director, clock, .25, epoch=2, anchor=first.anchor_id)
    assert director.next_intent().anchor_id == "security"


@pytest.mark.parametrize("phase", ["Discussion","Voting","VoteResolution","Waiting"])
def test_empty_frames_never_seek_during_held_phase_or_inherit_meeting_empty_duration(phase):
    director, clock, rng = system(); refresh(director, clock, actors=(), phase=phase)
    empty_frames(director, clock, 12, phase=phase)
    assert director.next_intent() is None
    refresh(director, clock, actors=())
    empty_frames(director, clock, 4.75)
    assert director.next_intent() is None
    empty_frames(director, clock, .25)
    assert director.next_intent().anchor_id == "engineering"
