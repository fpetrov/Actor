"""Authored benchmark inputs stay coherent; no native/model/authentication."""
import importlib.util
from pathlib import Path

from actor_broadcast.config import CommentaryConfig


SPEC = importlib.util.spec_from_file_location("broadcast_benchmark", Path(__file__).parents[1] / "broadcast-benchmark.py")
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def requests():
    return [benchmark.authored_request(index, case, now=1_000_000, config=CommentaryConfig(),
        session="authored-session", match="authored-match")
        for index, case in enumerate(benchmark.CASES, 1)]


def test_earlier_dispute_and_transition_words_agree_with_actual_evidence_age():
    values = requests()
    for index, age in ((12, 90), (16, 120)):
        state = values[index].external_state
        assert state["evidence"][0]["age_at_presented_view_seconds"] == age
        assert state["presented_view_observed_at_unix_ms"] - state["evidence"][0]["observed_at_unix_ms"] == age * 1000
    assert "Полторы минуты" in values[12].external_state["evidence"][0]["value"]["text"]
    assert "Две минуты" in values[16].external_state["evidence"][0]["value"]["text"]


def test_authored_reactive_urgency_and_all_modes_use_current_editorial_sizing_without_heard_fiction():
    values = requests()
    assert len(values) == 20
    assert values[0].candidate.mode == "intro" and values[-1].candidate.mode == "outro"
    assert values[3].candidate.priority == 0  # The publicly started alarm is urgent.
    assert values[1].candidate.priority == 2  # A visible transition permits a fuller comment.
    assert values[3].external_state["speech_presentation"]["max_segments"] == 1
    assert values[1].external_state["speech_presentation"]["max_segments"] > 1
    for request in values:
        assert request.max_output_tokens == 512
        assert request.external_state["actually_spoken"] == []
        assert "presentation" not in request.external_state
        assert len(request.candidate.evidence) == len(request.external_state["evidence"])
    conflicting = values[7].external_state["evidence"]
    assert len(conflicting) == 2 and all(row["kind"] == "player_claim" for row in conflicting)
    assert conflicting[0]["ref"] != conflicting[1]["ref"]
