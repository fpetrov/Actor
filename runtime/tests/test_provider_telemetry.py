"""No authentication/model calls: bounded public metadata survives failed episodes."""
from copy import deepcopy
from types import SimpleNamespace

from actor_codex.adapter import OfficialCodexAdapter
from actor_codex.brain import CodexBrain
from actor_runtime.episodes import MatchEpisodeController
from actor_runtime.telemetry import provider_telemetry
from test_codex import FakeSDK, fake_codex_launch
from test_episodes import Policy, registry


def test_provider_metadata_whitelist_rejects_unknown_counters_and_private_content():
    report={"brain":{"provider":"untrusted-private-name", "turns":[{"tool_calls":0,"turn_id":"private-turn"}],
        "usage_totals":{"input_tokens":90,"cached_input_tokens":60,"output_tokens":10,"total_tokens":100},
        "adapter":{"sdk_version":"0.162.1","unique_model_turns":1,"sandbox":"read-only",
            "ephemeral":True,"closed":True,"decision_active":False,"authentication_mode":"private-auth"},
        "context":{"own_alignment":"private-role"}},"skills":[{"output":"private-output"}]}
    result=provider_telemetry([report,report])
    assert result["unique_model_turns"]==2 and result["tool_calls"]==0
    assert result["sdk_versions"]==["0.162.1"] and result["sdk_versions_complete"]
    assert result["usage_totals"]=={"input_tokens":180,"cached_input_tokens":120,"output_tokens":20,"total_tokens":200}
    assert result["read_only_episodes"]==result["ephemeral_episodes"]==result["closed_providers"]==2
    assert result["active_decisions"]==0 and "private-" not in str(result)
    malformed=deepcopy(report)
    malformed["brain"]["adapter"].update(sdk_version="0.162.1-private",unique_model_turns=True,decision_active=0)
    malformed["brain"]["turns"][0]["tool_calls"]=False
    malformed["brain"]["usage_totals"]["total_tokens"]=True
    unknown=provider_telemetry([report,malformed])
    assert unknown["sdk_versions"]==["0.162.1"] and not unknown["sdk_versions_complete"]
    assert unknown["unique_model_turns"] is None and unknown["tool_calls"] is None
    assert unknown["active_decisions"] is None and unknown["usage_totals"]["total_tokens"] is None
    incomplete=deepcopy(report);incomplete["brain"]["turns"]=[]
    assert provider_telemetry([incomplete])["tool_calls"] is None
    huge=deepcopy(report);huge["brain"]["adapter"]["unique_model_turns"]=2**53-1
    huge["brain"]["turns"][0]["tool_calls"]=2**53-1
    huge["brain"]["usage_totals"]["total_tokens"]=2**53-1
    overflow=provider_telemetry([huge,huge])
    assert overflow["unique_model_turns"] is None and overflow["tool_calls"] is None
    assert overflow["usage_totals"]["total_tokens"] is None


async def test_failed_official_adapter_episode_exposes_only_aggregate_counts(session):
    usage=SimpleNamespace(model_dump=lambda **_: {"last":{"total_tokens":100},
        "total":{"input_tokens":90,"cached_input_tokens":60,"output_tokens":10,"total_tokens":100}})
    sdk=FakeSDK(usage=usage)
    adapter=sdk.adapter()
    controller=MatchEpisodeController(session,registry(),lambda:CodexBrain(adapter=adapter,
        usage_warning_tokens=80,usage_limit_tokens=100),Policy())
    result=await controller.run("Use only permitted observations")
    assert result.status=="failed" and result.reason=="brain_failure" and not result.authoritative_terminal
    report=controller.report()
    assert report["episodes"]==[] and report["episode_count"]==1
    telemetry=report["provider_telemetry"]
    assert telemetry["sdk_versions"]==["0.162.1"] and telemetry["sdk_versions_complete"]
    assert telemetry["unique_model_turns"]==1 and telemetry["tool_calls"]==0 and telemetry["tool_count_complete"]
    assert telemetry["usage_totals"]["total_tokens"]==100
    assert telemetry["closed_providers"]==telemetry["ephemeral_episodes"]==telemetry["read_only_episodes"]==1
    assert telemetry["active_decisions"]==0 and sdk.clients[0].closed
    assert not any(value in str(report) for value in ("turn_id", "turn-0", "authentication_mode", "arguments"))
    fresh=MatchEpisodeController(session,registry(),lambda:CodexBrain(),Policy()).report()["provider_telemetry"]
    assert fresh["closed_episode_reports"]==0 and fresh["unique_model_turns"] is None
    assert fresh["sdk_versions"]==[] and fresh["usage_totals"]["total_tokens"] is None
