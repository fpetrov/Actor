"""Authored benchmark inputs stay coherent; no native/model/authentication."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from actor_broadcast.budget import BudgetLedger
from actor_broadcast.config import CommentaryConfig
from actor_broadcast.contracts import ProviderSegment, ProviderTerminal


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


def editorial_requests():
    return [benchmark.editorial_request(index, case, now=1_000_000, config=CommentaryConfig(),
        session="authored-session", match="authored-match")
        for index, case in enumerate(benchmark.EDITORIAL_CASES, 1)]


def test_editorial_suite_has_eight_richer_cases_exactly_one_urgent_and_real_framing_contract():
    values = editorial_requests()
    assert len(values) == 8 and sum(request.candidate.priority == 0 for request in values) == 1
    assert [request.candidate.mode for request in values] == ["reactive", "reactive", "memory_callback",
        "analysis", "reactive", "reactive", "idle_recap", "outro"]
    for request in values:
        state = request.external_state
        assert state["actually_spoken"] == [] and state["public_notes"] == []
        assert "presentation" not in state and "presentation_ack" not in state
        assert state["speech_presentation"]["max_segments"] <= 4
        assert tuple(request.candidate.evidence) == tuple(benchmark.EvidenceRef(**row["ref"]) for row in state["evidence"])
        assert all(row["origin"] == "game" and row["age_at_presented_view_seconds"] ==
            (state["presented_view_observed_at_unix_ms"]-row["observed_at_unix_ms"])/1000 for row in state["evidence"])
        assert all("alignment" not in row and "own_ballot" not in row for row in state["evidence"])
    assert values[0].external_state["editorial_frame"]["watched_player"] == {"actor_id":"lena", "display_name":"Лена"}
    assert values[0].external_state["evidence"][0]["age_at_presented_view_seconds"] == 120
    assert values[1].external_state["evidence"][1]["value"]["value"] == 9
    assert values[2].external_state["historical_callback"] is True
    assert values[6].external_state["editorial_frame"]["watched_player"] is None
    assert values[6].external_state["current_view"]["visible_actor_ids"] == ["oleg"]
    assert values[7].external_state["speech_presentation"]["max_segments"] == 1


def test_public_argument_authorship_reply_and_uncertainty_survive_analysis_and_voting():
    values = editorial_requests(); analysis, voting, tally = values[3:6]
    claims = analysis.external_state["evidence"]
    assert len(claims) == 4 and {row["subjects"][0] for row in claims} == {"lena", "oleg", "nina"}
    assert all(row["kind"] == "player_claim" and row["predicate"] == "discussion-claim" for row in claims)
    assert "отвечает Лене" in claims[1]["value"]["text"] and "уточняет Нине" in claims[3]["value"]["text"]
    assert analysis.reasoning_effort == "medium" and analysis.max_output_tokens == 768
    assert all(request.reasoning_effort == "default" for request in values if request is not analysis)
    assert analysis.external_state["editorial_frame"]["public_arguments"] == [row["ref"] for row in claims]
    assert analysis.external_state["speech_presentation"]["purpose"] == "meeting_arguments"
    assert voting.external_state["current_view"]["meeting"]["result"] == []
    assert voting.external_state["current_view"]["meeting"]["ejected_actor_id"] == ""
    assert tally.external_state["current_view"]["meeting"]["result"] == [
        {"candidate_actor_id":"oleg", "votes":2}, {"candidate_actor_id":"", "votes":1}]
    assert tally.external_state["current_view"]["meeting"]["participants"] == []
    assert "Кто как голосовал, не раскрыто" in tally.external_state["evidence"][-1]["value"]["text"]


@pytest.mark.parametrize("index,case", [(0,"focused_movement_history"),(9,"terminal_result"),(1,"terminal_result"),(True,"focused_movement_history")])
def test_editorial_unknown_or_mismatched_cases_cannot_create_a_provider_request(index,case):
    with pytest.raises(ValueError): benchmark.editorial_request(index,case,now=1_000_000,
        config=CommentaryConfig(),session="authored",match="authored")


@pytest.mark.asyncio
@pytest.mark.parametrize("start,limit",[(1,8),(4,3)])
async def test_editorial_run_uses_real_shared_ledger_bounded_single_turns_and_one_meeting_analysis(tmp_path,monkeypatch,start,limit):
    class Clock:
        now = 1000.0
        def monotonic(self): return self.now
        def time(self): return self.now
    clock = Clock(); logs = []; reports = []; ledgers = []; providers = []
    class Lease:
        def __init__(self,channel): assert channel == "fedorr_codex"
        def acquire(self): logs.append("lease_acquired"); return self
        def release(self): logs.append("lease_released")
    class Provider:
        max_provider_calls = 1
        def __init__(self,**options): self.requests=[]; self.closed=False; self.last_terminal=None; providers.append(self)
        async def prepare(self): logs.append("provider_prepare"); return {"available":True}
        async def generate(self,request):
            self.requests.append(request); self.last_terminal=None
            clock.now += .1
            yield ProviderSegment(0,"Короткая авторская проверка.",request.candidate.evidence[:1])
            clock.now += 44.9
            self.last_terminal=ProviderTerminal("completed",100,10,0,0,1,0,"sdk_item_lifecycle")
            yield self.last_terminal
        async def close(self): self.closed=True; logs.append("provider_closed")
        def report(self): return {"closed":self.closed,"provider_turns":len(self.requests)}
    def ledger(path,**options):
        assert path == tmp_path/"quota.sqlite" and options["quota_scope_id"] == "installation:authored-installation"
        result = BudgetLedger(path,clock=clock.time,**options); ledgers.append(result); return result
    monkeypatch.setattr(benchmark,"time",clock)
    monkeypatch.setattr(benchmark,"private_directory",lambda path:tmp_path)
    monkeypatch.setattr(benchmark,"installation_identity",lambda path:"authored-installation")
    monkeypatch.setattr(benchmark,"ChannelLease",Lease)
    monkeypatch.setattr(benchmark,"BudgetLedger",ledger)
    monkeypatch.setattr(benchmark,"CodexCommentaryProvider",Provider)
    monkeypatch.setattr(benchmark,"save",lambda path,value:reports.append(json.loads(json.dumps(value))))
    args = SimpleNamespace(suite="editorial",start_case=start,limit=limit,channel="fedorr_codex",codex_home=tmp_path/"no-auth-used",
        total_timeout=1800,output=tmp_path/"no-report-written.json")
    assert await benchmark.run(args) == 0
    final=reports[-1]
    assert final["completed"] and final["cleanup_completed"] and final["max_provider_turns"] == limit
    assert final["suite"] == "editorial" and final["authored_fixture_version"] == 3
    assert len(providers[0].requests) == len(final["attempts"]) == limit and len(ledgers) == 1
    assert [row["case"] for row in final["attempts"]]==list(range(start,start+limit))
    assert final["budget"]["calls"] == limit and final["budget"]["unknown_usage_calls"] == 0
    assert logs[0:2] == ["lease_acquired","provider_prepare"] and logs[-2:] == ["provider_closed","lease_released"]
    # Inspect only the temporary fake run's real durable ledger: one normal
    # shared charge is tagged for its authored meeting, never a parallel quota.
    import sqlite3
    with sqlite3.connect(tmp_path/"quota.sqlite") as connection:
        assert connection.execute("SELECT meeting,calls FROM analyses").fetchall() == [("authored-meeting-one",1)]
        assert connection.execute("SELECT SUM(calls) FROM charges").fetchone()[0] == limit
    assert all(final[field] is False for field in ("native_presentation","tts","playback","obs_recording","twitch_ingest"))


def test_editorial_cli_rejects_a_ninth_call_before_provider_or_budget_access(monkeypatch,tmp_path):
    def forbidden(*args,**kwargs): raise AssertionError("must reject before provisioning")
    monkeypatch.setattr(benchmark,"CodexCommentaryProvider",forbidden)
    monkeypatch.setattr(benchmark,"BudgetLedger",forbidden)
    monkeypatch.setattr("sys.argv",["broadcast-benchmark.py","--execute-real","--suite","editorial",
        "--limit","9","--output",str(tmp_path/"never-created.json")])
    with pytest.raises(SystemExit) as error: benchmark.main()
    assert error.value.code == 2 and not (tmp_path/"never-created.json").exists()


@pytest.mark.parametrize("extra,suite,limit", [([],"standard",20),(["--suite","editorial"],"editorial",8),
    (["--suite","editorial","--limit","3"],"editorial",3)])
def test_cli_default_and_editorial_limits_preserve_legacy_and_require_explicit_real_execution(monkeypatch,tmp_path,extra,suite,limit):
    def intercept(coroutine):
        args=coroutine.cr_frame.f_locals["args"]
        assert args.suite == suite and args.limit == limit
        coroutine.close()  # No body/provider/account/budget executes.
        return 0
    monkeypatch.setattr(benchmark.asyncio,"run",intercept)
    monkeypatch.setattr("sys.argv",["broadcast-benchmark.py","--execute-real","--output",str(tmp_path/"unused.json"),*extra])
    assert benchmark.main() == 0
