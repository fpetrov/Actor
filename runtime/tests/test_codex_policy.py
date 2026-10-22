"""No SDK/account/model calls: fake public preflight, real private-file guards."""

import asyncio
import hashlib
import json
from pathlib import Path
import stat
from types import SimpleNamespace as NS
import tomllib

import pytest

from actor_codex import policy
from actor_runtime.errors import ActorError


SECRET = "never-publish-test-config-credential"


class Client:
    def __init__(self, config, *, workspace, names=None, account_type="chatgpt", model="gpt-6-luna"):
        self.config = config
        self.workspace = workspace
        self.names = {"docs.with.dot": {"headers": {"Authorization": SECRET}}} if names is None else names
        self.account_type = account_type
        self.model = model
        self.closed = False
        self.calls = []
        self.effective_changes = {}

    async def start(self): self.calls.append("start")
    async def initialize(self): self.calls.append("initialize")
    async def request(self, method, params, *, response_model):
        assert method == "config/read" and params == {"cwd": str(self.workspace), "includeLayers": False}
        self.calls.append("config/read")
        return NS(config=NS(model_extra={"mcp_servers": self.names}, **self.effective_changes))

    async def account_read(self, params):
        assert params == {"refreshToken": False}
        self.calls.append("account/read")
        return NS(account=NS(root=NS(type=self.account_type, email=SECRET)))

    async def model_list(self):
        self.calls.append("model/list")
        return NS(data=[NS(id=self.model, model=self.model, supported_reasoning_efforts=[
            NS(reasoning_effort="low"), NS(reasoning_effort="medium")])])

    async def close(self):
        # A preflight child must be closed before the new static catalogue exists.
        assert not (self.workspace / policy.CATALOG_FILENAME).exists()
        self.closed = True
        self.calls.append("close")


def fixture(tmp_path, **options):
    tmp_path.chmod(0o700)
    auth_home = tmp_path / "isolated-auth-home"
    auth_home.mkdir(exist_ok=True)
    original = NS(config_overrides=('features.shell_tool=false',), launch_args_override=None, codex_bin=None,
        env={"CODEX_HOME": str(auth_home), "HOME": str(tmp_path)})
    clients = []

    def factory(config):
        client = Client(config, workspace=tmp_path, **options)
        clients.append(client)
        return client

    return original, clients, factory


async def prepare(tmp_path, **options):
    config, clients, factory = fixture(tmp_path, **options)
    result = await policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path,
        owner=policy.PolicyPreflight(), client_factory=factory, response_model=object)
    return result, config, clients[0]


@pytest.mark.asyncio
async def test_public_preflight_closes_before_restricted_file_and_never_copies_config_values(tmp_path):
    result, config, client = await prepare(tmp_path)
    assert client.calls == ["start", "initialize", "config/read", "account/read", "model/list", "close"]
    assert client.closed and result.live_verified and result.reasoning_efforts == ("low", "medium")
    assert config.config_overrides == ('features.shell_tool=false',) and config.launch_args_override is None
    assert client.config is not config and "agents.enabled=false" in client.config.config_overrides
    assert client.config.cwd == str(tmp_path)
    assert stat.S_IMODE(Path(result.catalog_path).stat().st_mode) == 0o600
    outputs = Path(result.catalog_path).read_text() + repr(result) + json.dumps(result.report()) + repr(result.config_overrides) + repr(result.thread_config)
    assert SECRET not in outputs
    assert "docs.with.dot" not in repr(result) + json.dumps(result.report())
    assert result.thread_config["mcp_servers"] == {"docs.with.dot": {"enabled": False}}
    assert result.thread_config["tools.experimental_request_user_input.enabled"] is False
    assert result.thread_config["include_environment_context"] is False
    assert result.thread_config["project_doc_max_bytes"] == 0


def test_pinned_model_asset_exact_hash_and_only_reviewed_capability_fields_change():
    asset = Path(policy.__file__).with_name("gpt6_luna_policy_model.json").read_bytes()
    assert hashlib.sha256(asset).hexdigest() == policy.MODEL_RESOURCE_SHA256
    original = json.loads(asset)
    output = policy.restricted_catalog()
    assert len(output["models"]) == 1
    row = output["models"][0]
    changes = {name for name in original if row[name] != original[name]}
    assert changes == {"apply_patch_tool_type", "shell_type", "tool_mode", "experimental_supported_tools", "multi_agent_version"}
    assert row["slug"] == "gpt-6-luna" and row["apply_patch_tool_type"] is None
    assert row["shell_type"] == "disabled" and row["tool_mode"] == "direct"
    assert row["experimental_supported_tools"] == [] and row["multi_agent_version"] is None
    assert row["model_messages"] == original["model_messages"]
    row["model_messages"]["instructions_template"] = "changed by caller"
    assert policy.restricted_catalog()["models"][0]["model_messages"] == original["model_messages"]


@pytest.mark.asyncio
async def test_quoted_mcp_names_cannot_escape_toml_key_and_both_startup_thread_disable(tmp_path):
    names = {'quote" and\\slash': {"secret": SECRET}, "a.b": {"env": SECRET}, 'same=x': {}, 'same=y': {}}
    result, _, _ = await prepare(tmp_path, names=names)
    parsed = {}
    for override in result.config_overrides:
        if override.startswith("mcp_servers="):
            data = tomllib.loads(override)["mcp_servers"]
            parsed.update(data)
    assert parsed == {name: {"enabled": False} for name in names}
    assert result.thread_config["mcp_servers"] == parsed
    assert "mcp_servers={}" not in result.config_overrides
    assert len(result.config_overrides) <= 64


@pytest.mark.asyncio
async def test_many_short_mcp_names_fit_launch_bounds_without_dropping_any(tmp_path):
    names = {"mcp" + str(index): {"secret": SECRET} for index in range(64)}
    result, _, _ = await prepare(tmp_path, names=names)
    assert len(result.config_overrides) <= 64 and all(len(value) <= 8192 for value in result.config_overrides)
    assignment = next(value for value in reversed(result.config_overrides) if value.startswith("mcp_servers="))
    assert tomllib.loads(assignment)["mcp_servers"] == {name: {"enabled": False} for name in names}


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [{"bad\nname": {}}, {"x" * 129: {}}, {"": {}}, {str(x): {} for x in range(65)}, []])
async def test_malformed_or_unbounded_mcp_keys_fail_closed_and_child_closes(tmp_path, names):
    config, clients, factory = fixture(tmp_path, names=names)
    with pytest.raises(ActorError) as caught:
        await policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path, owner=policy.PolicyPreflight(), client_factory=factory, response_model=object)
    assert caught.value.code == "commentary_policy_config_invalid"
    assert clients[0].closed and not (tmp_path / policy.CATALOG_FILENAME).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code", [
    ({"model_catalog_json": "/a/static/file"}, "commentary_policy_static_preflight_forbidden"),
    ({"model_provider": "untrusted-local"}, "commentary_policy_provider_forbidden"),
])
async def test_static_or_nonofficial_preflight_cannot_invent_live_availability(tmp_path, change, code):
    config, clients, factory = fixture(tmp_path)
    def altered(value):
        client = factory(value); client.effective_changes = change; return client
    with pytest.raises(ActorError) as caught:
        await policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path, owner=policy.PolicyPreflight(), client_factory=altered, response_model=object)
    assert caught.value.code == code and clients[0].closed
    assert clients[0].calls == ["start", "initialize", "config/read", "close"]


@pytest.mark.asyncio
@pytest.mark.parametrize("options,code", [
    ({"model": "gpt-other"}, "commentary_model_unavailable"),
    ({"account_type": "apiKey"}, "commentary_policy_chatgpt_signin_required"),
])
async def test_no_model_or_paid_provider_fallback(tmp_path, options, code):
    config, clients, factory = fixture(tmp_path, **options)
    with pytest.raises(ActorError) as caught:
        await policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path, owner=policy.PolicyPreflight(), client_factory=factory, response_model=object)
    assert caught.value.code == code and clients[0].closed
    assert not (tmp_path / policy.CATALOG_FILENAME).exists()


@pytest.mark.asyncio
async def test_foreign_requested_model_rejected_before_client_factory(tmp_path):
    with pytest.raises(ActorError) as caught:
        await policy.prepare_policy(NS(), "gpt-other", tmp_path, owner=policy.PolicyPreflight(), client_factory=lambda _: pytest.fail("no child"))
    assert caught.value.code == "commentary_policy_model_unsupported"


@pytest.mark.asyncio
async def test_sdk_error_never_publishes_exception_or_account_content(tmp_path):
    config, clients, factory = fixture(tmp_path)
    def broken(value):
        client = factory(value)
        async def request(*_args, **_kwargs): raise RuntimeError(SECRET)
        client.request = request
        return client
    with pytest.raises(ActorError) as caught:
        await policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path, owner=policy.PolicyPreflight(), client_factory=broken, response_model=object)
    assert SECRET not in str(caught.value) + repr(caught.value.as_dict())
    assert caught.value.code == "commentary_policy_preflight_failed" and clients[0].closed


@pytest.mark.parametrize("case", ["symlink", "existing_foreign_content", "open_directory", "open_file"])
def test_catalog_install_refuses_unsafe_or_nonowned_target_without_overwrite(tmp_path, case):
    tmp_path.chmod(0o700)
    target = tmp_path / policy.CATALOG_FILENAME
    original = tmp_path / "other"; original.write_text(SECRET)
    if case == "symlink": target.symlink_to(original)
    elif case == "open_directory": tmp_path.chmod(0o755)
    else:
        target.write_text(SECRET); target.chmod(0o644 if case == "open_file" else 0o600)
    with pytest.raises(ActorError): policy._install_catalog(tmp_path)
    assert original.read_text() == SECRET
    if target.is_file() and not target.is_symlink(): assert target.read_text() == SECRET


def test_exact_existing_catalog_is_idempotent_without_replacing_inode(tmp_path):
    tmp_path.chmod(0o700)
    path = Path(policy._install_catalog(tmp_path)); inode = path.stat().st_ino
    assert policy._install_catalog(tmp_path) == str(path) and path.stat().st_ino == inode


@pytest.mark.asyncio
async def test_preflight_cancellation_closes_child_and_writes_no_catalog(tmp_path):
    config, clients, factory = fixture(tmp_path)
    entered = asyncio.Event()
    def blocking(value):
        client = factory(value)
        async def read(_params): entered.set(); await asyncio.Future()
        client.account_read = read
        return client
    task = asyncio.create_task(policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path,
        owner=policy.PolicyPreflight(), client_factory=blocking, response_model=object))
    await entered.wait(); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert clients[0].closed and not (tmp_path / policy.CATALOG_FILENAME).exists()


@pytest.mark.asyncio
async def test_failed_close_cannot_report_ready_or_install_catalog(tmp_path):
    config, _, factory = fixture(tmp_path)
    def failed(value):
        client = factory(value)
        async def close(): raise RuntimeError(SECRET)
        client.close = close
        return client
    with pytest.raises(ActorError) as caught:
        await policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path, owner=policy.PolicyPreflight(), client_factory=failed, response_model=object)
    assert caught.value.code == "commentary_policy_cleanup_failed" and SECRET not in str(caught.value)
    assert not (tmp_path / policy.CATALOG_FILENAME).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,symlink", [("AGENTS.md", False), ("AGENTS.override.md", False), ("AGENTS.md", True)])
async def test_global_instruction_presence_refused_metadata_only_before_any_child(tmp_path, monkeypatch, name, symlink):
    config, _, _ = fixture(tmp_path)
    target = Path(config.env["CODEX_HOME"]) / name
    if symlink:
        target.symlink_to(tmp_path / "does-not-exist")
    else:
        target.write_text(SECRET)
    monkeypatch.setattr(Path, "read_bytes", lambda *_: pytest.fail("No instruction contents may be read"))
    with pytest.raises(ActorError) as caught:
        await policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path, owner=policy.PolicyPreflight(),
            client_factory=lambda _: pytest.fail("Must refuse before child"), response_model=object)
    assert caught.value.code == "commentary_policy_global_instructions_present" and SECRET not in str(caught.value)


def test_global_guard_uses_effective_filtered_home_and_fails_closed_unknown_metadata(tmp_path, monkeypatch):
    config, _, _ = fixture(tmp_path)
    inherited = tmp_path / "inherited-codex"; inherited.mkdir()
    (inherited / "AGENTS.md").write_text(SECRET)
    monkeypatch.setenv("CODEX_HOME", str(inherited))
    # Supplied config.env takes precedence exactly as in official SDK merge,
    # then launch.py removes all keys outside the shared allowlist.
    policy._guard_instruction_home(config)
    config.env.pop("CODEX_HOME")
    with pytest.raises(ActorError) as caught: policy._guard_instruction_home(config)
    assert caught.value.code == "commentary_policy_global_instructions_present"
    monkeypatch.delenv("CODEX_HOME")
    default = Path(config.env["HOME"]) / ".codex"; default.mkdir()
    (default / "AGENTS.override.md").write_text(SECRET)
    with pytest.raises(ActorError): policy._guard_instruction_home(config)
    original = Path.lstat
    def denied(path, *args, **kwargs):
        if path.name in ("AGENTS.md", "AGENTS.override.md"): raise PermissionError(SECRET)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(ActorError) as caught: policy._guard_instruction_home(config)
    assert caught.value.code == "commentary_policy_home_unverifiable" and SECRET not in str(caught.value)


@pytest.mark.asyncio
async def test_late_start_remains_owned_and_close_retries_only_after_registration(tmp_path, monkeypatch):
    config, clients, factory = fixture(tmp_path)
    owner = policy.PolicyPreflight(); entered = asyncio.Event(); release = asyncio.Event()
    monkeypatch.setattr(policy, "CLOSE_TIMEOUT_SECONDS", 0.005)
    def delayed(value):
        client = factory(value)
        async def start():
            assert owner.client is client and not owner.closed
            entered.set(); await release.wait(); client.registered = True
        async def close():
            assert client.registered
            client.closed = True
        client.start = start; client.close = close
        return client
    task = asyncio.create_task(policy.prepare_policy(config, policy.PINNED_MODEL, tmp_path,
        owner=owner, client_factory=delayed, response_model=object))
    await entered.wait(); task.cancel()
    with pytest.raises(ActorError) as caught: await task
    assert caught.value.code == "commentary_policy_cleanup_failed"
    assert owner.client is clients[0] and not owner.closed and not owner.startup_task.done()
    assert not (tmp_path / policy.CATALOG_FILENAME).exists()
    release.set(); await owner.close()
    assert owner.closed and clients[0].closed and owner.client is None


@pytest.mark.asyncio
async def test_cancelled_close_survives_repeated_caller_cancellation_and_is_not_duplicated():
    entered = asyncio.Event(); release = asyncio.Event(); count = 0
    async def close():
        nonlocal count
        count += 1; entered.set(); await release.wait()
    owner = policy.PolicyPreflight(); owner.register(NS(close=close))
    first = asyncio.create_task(owner.close()); await entered.wait(); first.cancel()
    with pytest.raises(asyncio.CancelledError): await first
    second = asyncio.create_task(owner.close()); await asyncio.sleep(0); second.cancel()
    with pytest.raises(asyncio.CancelledError): await second
    assert not owner.closed and not owner.cleanup_task.done() and count == 1
    release.set(); await owner.close()
    assert owner.closed and count == 1 and owner.client is None


@pytest.mark.asyncio
async def test_failed_public_close_retains_same_client_for_explicit_retry():
    calls = 0
    async def close():
        nonlocal calls
        calls += 1
        if calls == 1: raise RuntimeError(SECRET)
    client = NS(close=close); owner = policy.PolicyPreflight(); owner.register(client)
    with pytest.raises(ActorError): await owner.close()
    assert not owner.closed and owner.client is client and owner.report() == {"closed": False, "preflight_pending": True}
    await owner.close()
    assert owner.closed and calls == 2


@pytest.mark.asyncio
async def test_timed_out_public_close_is_retained_without_cancellation_or_second_close(monkeypatch):
    release = asyncio.Event(); calls = 0
    monkeypatch.setattr(policy, "CLOSE_TIMEOUT_SECONDS", 0.005)
    async def close():
        nonlocal calls
        calls += 1; await release.wait()
    client = NS(close=close); owner = policy.PolicyPreflight(); owner.register(client)
    with pytest.raises(ActorError): await owner.close()
    assert owner.client is client and not owner.closed and not owner.close_task.done()
    pending = owner.close_task
    release.set(); await owner.close()
    assert owner.closed and owner.close_task is pending and calls == 1
