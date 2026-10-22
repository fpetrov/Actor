"""Ordinary-player public SDK policy; only fake IO, no auth/model/process."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS
import tomllib

import pytest

from actor_codex import adapter as module
from actor_codex.adapter import OfficialCodexAdapter
from actor_codex.policy import RESTRICTIVE_OVERRIDES
from actor_runtime.errors import ActorError
from test_codex import FakeSDK


PRIVATE = "private-config-value-must-not-leave-preflight"


class KeysOnly(dict):
    def values(self): raise AssertionError("Config values must not be inspected")
    def items(self): raise AssertionError("Config values must not be copied")
    def __getitem__(self, _key): raise AssertionError("Config values must not be inspected")


class ConfigClient:
    def __init__(self, config, events, *, response=None, error=None, gate=None):
        self.config, self.events = config, events
        self.response, self.error, self.gate = response, error, gate
        self.closed = False
        self.read_entered = asyncio.Event()
        self.close_failures = 0

    async def start(self): self.events.append("preflight_start")
    async def initialize(self): self.events.append("initialize")
    async def request(self, method, params, *, response_model):
        assert method == "config/read" and response_model is object
        assert params == {"cwd": self.config.cwd, "includeLayers": False}
        self.events.append("config/read")
        self.read_entered.set()
        if self.gate is not None: await asyncio.wait_for(self.gate.wait(), 2)
        if self.error is not None: raise self.error
        return self.response
    async def close(self):
        self.events.append("preflight_close")
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError(PRIVATE)
        self.closed = True


def setup(monkeypatch, *, servers=None, response=None, error=None, gate=None, model=None):
    events, clients = [], []
    sdk = FakeSDK()
    real_factory = sdk.AsyncCodex
    def high_level(config):
        assert clients and clients[0].closed, "No main child before actual preflight close"
        events.append("main_child")
        return real_factory(config)
    sdk.AsyncCodex = high_level
    if response is None:
        response = NS(config=NS(model_extra={"mcp_servers": {} if servers is None else servers}))
    def factory(config):
        client = ConfigClient(config, events, response=response, error=error, gate=gate)
        clients.append(client)
        return client
    # The actual launcher is independently tested with a real stand-in child.
    # This suite asserts its adapter integration without resolving/spawning SDKs.
    def launcher(config):
        events.append("filtered_launcher")
        assert set(config.env) <= module.filtered_environment().keys()
        return ("trusted-filtered-launcher", *config.config_overrides)
    monkeypatch.setattr(module, "codex_launch_args", launcher)
    adapter = OfficialCodexAdapter(model=model, sdk=sdk,
        config_client_factory=factory, config_response_model=object)
    return adapter, sdk, clients, events


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [None, "explicit-ordinary-model"])
async def test_recursive_inherited_mcp_disabled_before_thread_without_copying_values(monkeypatch, model):
    # A recursive empty overlay keeps every original server; the returned
    # per-name False overlay is what actually prevents their initialization.
    inherited = {"docs.with.dot": {"enabled": True, "credential": PRIVATE},
                 'quote" slash\\': {"credential": PRIVATE}}
    adapter, sdk, clients, events = setup(monkeypatch, servers=KeysOnly(inherited), model=model)
    await adapter.start_episode("private-role objective", [])
    try:
        options = sdk.thread_options[0]
        names = {name: {"enabled": False} for name in inherited}
        assert options["config"]["mcp_servers"] == names
        effective = {key: dict(value) for key, value in inherited.items()}
        for name, overlay in options["config"]["mcp_servers"].items(): effective[name].update(overlay)
        assert all(row["enabled"] is False for row in effective.values())
        assert events.index("preflight_close") < events.index("main_child")
        assert options["model"] == model and "reasoning_effort" not in options
        assert options["sandbox"] == "read-only" and options["approval_mode"] == "deny_all" and options["ephemeral"]
        assert not sdk.inputs and not adapter.turn_ids
        assignment = next(value for value in reversed(sdk.clients[0].config.config_overrides) if value.startswith("mcp_servers="))
        assert tomllib.loads(assignment)["mcp_servers"] == names
        assert PRIVATE not in repr(options) + repr(adapter.report()) + repr(sdk.clients[0].config)
        assert "private-role" not in repr(clients[0].config)
    finally: await adapter.close()


@pytest.mark.asyncio
async def test_game_only_feature_and_final_environment_policy_applies_to_both_children(monkeypatch):
    monkeypatch.setenv("ACTOR_BRIDGE_TOKEN", PRIVATE)
    monkeypatch.setenv("TWITCH_ACCESS_TOKEN", PRIVATE)
    monkeypatch.setenv("SSH_AUTH_SOCK", PRIVATE)
    monkeypatch.setenv("CODEX_HOME", "/ordinary-existing-codex-home")
    adapter, sdk, clients, events = setup(monkeypatch)
    await adapter.start_episode("Wait", [])
    try:
        for config in (clients[0].config, sdk.clients[0].config):
            assert set(RESTRICTIVE_OVERRIDES) <= set(config.config_overrides)
            assert config.launch_args_override[0] == "trusted-filtered-launcher"
            assert config.env["CODEX_HOME"] == "/ordinary-existing-codex-home"
            assert PRIVATE not in repr(config.env)
        assert events.count("filtered_launcher") == 2
        policy = sdk.thread_options[0]["config"]
        for feature in ("plugins", "apps", "browser_use", "computer_use", "view_image", "hooks", "memories", "shell_tool"):
            assert policy["features." + feature] is False
        assert policy["features.skip_host_skill_discovery"] is True
        assert policy["skills.include_instructions"] is False
        assert policy["project_doc_max_bytes"] == 0
    finally: await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("servers", [[], None, {"": {}}, {"bad\nname": {}}, {"é": {}},
    {"x" * 129: {}}, {str(i): {} for i in range(65)}])
async def test_invalid_effective_names_refuse_before_main_thread_or_model(monkeypatch, servers):
    response = NS(config=NS(model_extra={"mcp_servers": servers}))
    adapter, sdk, clients, _ = setup(monkeypatch, response=response)
    with pytest.raises(ActorError) as caught: await adapter.start_episode("Wait", [])
    assert caught.value.code == "codex_policy_config_invalid"
    assert clients[0].closed and not sdk.clients and not sdk.thread_options and not sdk.inputs
    assert adapter.workspace is None and adapter.closed
    assert PRIVATE not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [NS(), NS(config=None), NS(config=NS(model_extra=[]))])
async def test_unknown_config_shape_fails_closed(monkeypatch, response):
    adapter, sdk, clients, _ = setup(monkeypatch, response=response)
    with pytest.raises(ActorError, match="codex_policy_config_invalid"):
        await adapter.start_episode("Wait", [])
    assert clients[0].closed and not sdk.clients and adapter.workspace is None


@pytest.mark.asyncio
async def test_public_config_error_is_scrubbed_and_preflight_is_closed(monkeypatch):
    adapter, sdk, clients, _ = setup(monkeypatch, error=RuntimeError(PRIVATE))
    with pytest.raises(ActorError) as caught: await adapter.start_episode("Wait", [])
    assert caught.value.code == "codex_policy_preflight_failed"
    assert PRIVATE not in str(caught.value) + json.dumps(caught.value.as_dict())
    assert clients[0].closed and not sdk.clients and adapter.workspace is None


@pytest.mark.asyncio
async def test_cancelled_config_read_closes_before_workspace_and_never_starts_thread(monkeypatch):
    gate = asyncio.Event()
    adapter, sdk, clients, _ = setup(monkeypatch, gate=gate)
    task = asyncio.create_task(adapter.start_episode("Wait", []))
    try:
        async with asyncio.timeout(.5):
            while not clients: await asyncio.sleep(0)
        await asyncio.wait_for(clients[0].read_entered.wait(), .5)
        workspace = Path(adapter.workspace.name)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 1)
        assert clients[0].closed and not sdk.clients and not workspace.exists()
    finally:
        gate.set()
        await asyncio.gather(task, return_exceptions=True)
        await adapter.close()


@pytest.mark.asyncio
async def test_failed_preflight_close_retains_owner_workspace_and_requires_retry(monkeypatch):
    adapter, sdk, clients, _ = setup(monkeypatch)
    factory = adapter._config_client_factory
    def failing(config):
        client = factory(config)
        client.close_failures = 2  # read-finally and start_episode cleanup
        return client
    adapter._config_client_factory = failing
    with pytest.raises(ActorError): await adapter.start_episode("Wait", [])
    workspace = Path(adapter.workspace.name)
    assert workspace.exists() and not clients[0].closed and not sdk.clients
    with pytest.raises(ActorError, match="codex_episode_busy"):
        await adapter.start_episode("No replacement workspace", [])
    assert Path(adapter.workspace.name) == workspace
    await adapter.close()
    assert clients[0].closed and not workspace.exists() and adapter.workspace is None


@pytest.mark.asyncio
async def test_maximum_server_count_is_disabled_without_dropping_names(monkeypatch):
    adapter, sdk, _, _ = setup(monkeypatch, servers={"mcp" + str(i): None for i in range(64)})
    await adapter.start_episode("Wait", [])
    try:
        assert len(sdk.thread_options[0]["config"]["mcp_servers"]) == 64
        assert adapter.report()["mcp_servers_disabled"] == 64
        assert adapter.report()["unique_model_turns"] == 0
    finally: await adapter.close()


@pytest.mark.asyncio
async def test_ordinary_signin_required_still_blocks_thread_and_closes_main_client(monkeypatch):
    adapter, sdk, preflight, _ = setup(monkeypatch)
    factory = sdk.AsyncCodex
    def unsigned(config):
        client = factory(config)
        async def account():
            return NS(account=None, requires_openai_auth=True)
        client.account = account
        return client
    sdk.AsyncCodex = unsigned
    with pytest.raises(ActorError) as caught: await adapter.start_episode("Wait", [])
    assert caught.value.code == "codex_authentication_required"
    assert preflight[0].closed and sdk.clients[0].closed
    assert not sdk.thread_options and not sdk.inputs and adapter.workspace is None


@pytest.mark.asyncio
async def test_cancel_during_preflight_close_keeps_workspace_until_owned_cleanup_finishes(monkeypatch):
    adapter, sdk, clients, _ = setup(monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    factory = adapter._config_client_factory
    def delayed(config):
        client = factory(config)
        async def close():
            entered.set()
            await asyncio.wait_for(release.wait(), 2)
            client.closed = True
        client.close = close
        return client
    adapter._config_client_factory = delayed
    task = asyncio.create_task(adapter.start_episode("Wait", []))
    try:
        await asyncio.wait_for(entered.wait(), .5)
        workspace = Path(adapter.workspace.name)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and workspace.exists() and not sdk.clients
        assert not adapter.closed and adapter.report()["policy_preflight_pending"]
        release.set()
        with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 1)
        assert clients[0].closed and not workspace.exists() and adapter.closed
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await adapter.close()
