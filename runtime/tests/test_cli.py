import asyncio
import json
import signal
from types import SimpleNamespace

import grpc
import pytest

from actor.core.v1 import actor_pb2 as core
from actor.core.v1 import bridge_pb2_grpc
from actor_runtime import cli
from actor_runtime.brains import BrainRegistry, SequenceBrain, SkillCall, FinishObjective
from actor_runtime.server import RuntimeServer
from conftest import message

TOKEN = "test-only-runtime-cli-bridge-token-32chars"


def arguments(tmp_path):
    return SimpleNamespace(operation="agent", brain="test-wait", objective="Wait briefly and finish",
        port=0, command_timeout_ms=250, report=tmp_path / "runtime.json", exit_on_complete=True,
        connect_timeout=5, decision_timeout=2, objective_timeout=5, max_decisions=3, model=None)


@pytest.fixture
async def cli_server(monkeypatch):
    ready = asyncio.Event()
    captured, handlers = {}, {}
    class Server(RuntimeServer):
        async def start(self):
            await super().start()
            captured["server"] = self
            ready.set()
    monkeypatch.setattr(cli, "RuntimeServer", Server)
    monkeypatch.setenv("ACTOR_BRIDGE_TOKEN", TOKEN)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_signal_handler", lambda number, callback: handlers.__setitem__(number, callback))
    registry = BrainRegistry()
    registry.register("test-wait", lambda: SequenceBrain([
        SkillCall(skill_id="actor.wait", arguments={"duration_seconds": 0.01}), FinishObjective(status="success")]))
    monkeypatch.setattr(BrainRegistry, "discover", classmethod(lambda cls: registry))
    return ready, captured, handlers


async def test_conditional_observation_capabilities_do_not_block_generic_wait(cli_server, manifest, tmp_path):
    ready, captured, _ = cli_server
    task = asyncio.create_task(cli.serve(arguments(tmp_path)))
    await ready.wait()
    async with grpc.aio.insecure_channel(f"127.0.0.1:{captured['server'].port}") as channel:
        call = bridge_pb2_grpc.ActorBridgeStub(channel).Connect(metadata=(("x-actor-bridge-token", TOKEN),))
        await call.write(message(1, hello=core.GameHello(game_id=manifest.game_id, game_version=manifest.game_version,
            self_actor_id=manifest.self_actor_id, protocol_version=manifest.protocol_version)))
        await call.write(message(2, manifest=manifest))
        assert (await call.read()).hello.accepted
        # A legitimate conditional sensor declares a capability but emits no frame.
        assert await asyncio.wait_for(task, timeout=4) == 0
        call.cancel()
    report = json.loads((tmp_path / "runtime.json").read_text())
    assert report["objective"]["result"]["status"] == "succeeded"
    assert report["cognitive"]["scheduler"]["skill_count"] == 1
    assert report["cognitive"]["skills"][0]["skill_id"] == "actor.wait"


async def test_shutdown_before_game_session_is_cancelled_not_false_success(cli_server, tmp_path):
    ready, _, handlers = cli_server
    task = asyncio.create_task(cli.serve(arguments(tmp_path)))
    await ready.wait()
    handlers[signal.SIGTERM]()
    assert await asyncio.wait_for(task, timeout=2) == 1
    result = json.loads((tmp_path / "runtime.json").read_text())["objective"]["result"]
    assert result["status"] == "cancelled" and result["reason"] == "runtime_shutdown"
    assert result["decision_count"] == 0 and result["skill_results"] == []
