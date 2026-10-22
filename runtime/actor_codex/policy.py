"""Supported, pinned tool policy for the audience-only commentator.

No SDK, authentication, account or subprocess is opened at import time.  The
official public low-level client is used only when ``prepare_policy`` is awaited.
The preparation starts no thread and submits no model turn.

Primary sources (Apache-2.0, OpenAI Codex, rust-v0.162.1):
* codex-rs/models-manager/models.json (the adjacent unchanged selected row)
* codex-rs/core/src/tools/spec_plan.rs (actual registration gates)
* codex-rs/core/src/config/mod.rs and model-provider/src/provider.rs
* codex-rs/tui/src/temporary_structured_request.rs (explicit MCP-name disabling)
https://github.com/openai/codex/tree/rust-v0.162.1
https://learn.chatgpt.com/docs/config-file/config-reference

An authenticated model listing is verified BEFORE installing the static local
catalog.  The static catalog is a capability ceiling, not availability proof.
No claim that an observed zero tool count proves a zero-tool request is made.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from importlib import resources
from importlib.metadata import version
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any, Callable

from actor_runtime.errors import ActorError


PINNED_SDK_VERSION = "0.162.1"
PINNED_MODEL = "gpt-6-luna"
MODEL_SOURCE_URL = "https://github.com/openai/codex/blob/rust-v0.162.1/codex-rs/models-manager/models.json"
MODEL_RESOURCE_SHA256 = "4bb2118cdfa559484e9d78bf23e19150a083a0f4a52ebd770e111fc51ce73c03"
CATALOG_FILENAME = "commentary-model-policy.json"
MAX_MCP_SERVERS = 64
MAX_MCP_NAME_BYTES = 128
MAX_CATALOG_BYTES = 128 * 1024
PREPARE_TIMEOUT_SECONDS = 30.0
CLOSE_TIMEOUT_SECONDS = 5.0
_REASONING_EFFORTS = frozenset(("none", "minimal", "low", "medium", "high", "xhigh", "max"))

# All are supported configuration keys in the pinned source.  Disabling shell
# alone does NOT disable ApplyPatch; the model catalog below supplies that gate.
# Model-selected code mode, async UI and clock tools are disabled in the catalog.
_FALSE_FEATURES = (
    "apps", "plugins", "multi_agent", "multi_agent_v2", "shell_tool",
    "view_image", "code_mode", "code_mode_only", "image_generation",
    "browser_use", "computer_use", "tool_suggest", "recommended_plugins",
    "hooks", "shell_snapshot", "memories", "context_management",
    "current_time_reminder", "sleep_tool", "token_budget", "deferred_executor",
    "request_permissions_tool", "send_message_to_user_async", "stable_environment_tools",
    "standalone_web_search", "enable_fanout", "goals",
)
RESTRICTIVE_OVERRIDES = tuple("features." + key + "=false" for key in _FALSE_FEATURES) + (
    "features.skip_host_skill_discovery=true",
    "agents.enabled=false",
    "cloud.skills.enabled=false",
    "skills.include_instructions=false",
    "tools.experimental_request_user_input.enabled=false",
    "tools.update_plan.enabled=false",
    "include_permissions_instructions=false",
    "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    "include_environment_context=false",
    "project_doc_max_bytes=0",
    "memories.use_memories=false",
    "memories.generate_memories=false",
    'web_search="disabled"',
)


@dataclass(frozen=True)
class PreparedPolicy:
    """Contains no raw config, authentication response, account or model prompts."""

    model: str
    reasoning_efforts: tuple[str, ...]
    live_verified: bool
    config_overrides: tuple[str, ...] = field(repr=False)
    thread_config: dict[str, Any] = field(repr=False)
    catalog_path: str = field(repr=False)
    mcp_server_count: int = 0

    def report(self) -> dict[str, Any]:
        # In particular do not publish private MCP names or workspace paths.
        return {
            "schema": "actor.codex-commentary-policy.v1",
            "sdk_version": PINNED_SDK_VERSION,
            "model": self.model,
            "live_model_verified": self.live_verified,
            "mcp_servers_disabled": self.mcp_server_count,
            "pinned_catalog": True,
            "global_instruction_metadata_checked": True,
            "tool_policy_scope": "supported_configuration_source_audit_not_observed_request_capture",
        }


class PolicyPreflight:
    """Caller-owned SDK child and retryable cleanup, registered before start.

    Store this object before awaiting preparation and retain the workspace until
    ``closed`` is true. Public async SDK start/close use worker threads; cancelling
    their awaiting coroutine does not prove the underlying child has stopped.
    """

    def __init__(self) -> None:
        self.client: Any = None
        self.startup_task: asyncio.Task | None = None
        self.close_task: asyncio.Task | None = None
        self.cleanup_task: asyncio.Task | None = None
        self.closed = True

    @staticmethod
    def _consume(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    def register(self, client: Any) -> None:
        if not self.closed or self.client is not None:
            raise _fail("commentary_policy_preflight_in_use")
        self.client = client
        self.startup_task = None
        self.close_task = None
        self.cleanup_task = None
        self.closed = False

    async def start(self) -> None:
        if self.client is None or self.closed or self.startup_task is not None:
            raise _fail("commentary_policy_preflight_in_use")
        self.startup_task = asyncio.create_task(self.client.start())
        self.startup_task.add_done_callback(self._consume)
        await asyncio.shield(self.startup_task)

    async def _cleanup(self) -> None:
        if self.startup_task is not None and not self.startup_task.done():
            done, _ = await asyncio.wait((self.startup_task,), timeout=CLOSE_TIMEOUT_SECONDS)
            if not done:
                # Do not cancel/drop an async wrapper around a still-running
                # public SDK launch. A later close retries after registration.
                raise _fail("commentary_policy_cleanup_failed")
        if self.close_task is None:
            self.close_task = asyncio.create_task(self.client.close())
            self.close_task.add_done_callback(self._consume)
        done, _ = await asyncio.wait((self.close_task,), timeout=CLOSE_TIMEOUT_SECONDS)
        if not done:
            # The pending operation stays owned and may finish between retries.
            raise _fail("commentary_policy_cleanup_failed")
        if self.close_task.cancelled() or self.close_task.exception() is not None:
            self.close_task = None
            raise _fail("commentary_policy_cleanup_failed")
        self.client = None
        self.closed = True

    async def close(self) -> None:
        if self.closed:
            return
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self._cleanup())
            self.cleanup_task.add_done_callback(self._consume)
        await asyncio.shield(self.cleanup_task)

    def report(self) -> dict[str, bool]:
        return {"closed": self.closed, "preflight_pending": not self.closed}


def _fail(code: str) -> ActorError:
    return ActorError(code, "Commentary capability policy could not be verified")


def _startup_base(config: Any) -> tuple[str, ...]:
    originals = getattr(config, "config_overrides", ())
    if not isinstance(originals, (tuple, list)) or len(originals) > 64:
        raise _fail("commentary_policy_config_invalid")
    protected = {value.split("=", 1)[0] for value in RESTRICTIVE_OVERRIDES}
    retained = []
    for value in originals:
        if not isinstance(value, str) or not 1 <= len(value) <= 8192 or "\0" in value:
            raise _fail("commentary_policy_config_invalid")
        name = value.split("=", 1)[0].strip()
        if name == "model_catalog_json":
            raise _fail("commentary_policy_static_preflight_forbidden")
        if name not in protected:
            retained.append(value)
    return tuple(retained) + RESTRICTIVE_OVERRIDES


def restricted_catalog() -> dict[str, Any]:
    """A fresh single-model copy; the adjacent source asset is never rewritten.

    The original base instructions stay byte-equivalent in the source resource.
    The caller's official ThreadStart.base_instructions overrides that template;
    ordinary low reasoning does not activate persistent-mode instructions (pinned
    Features.persistent_execution_enabled requires ReasoningEffort::Persistent).
    We therefore do not silently alter any model-owned instruction template.
    """
    try:
        raw = resources.files("actor_codex").joinpath("gpt6_luna_policy_model.json").read_bytes()
        if len(raw) > MAX_CATALOG_BYTES or hashlib.sha256(raw).hexdigest() != MODEL_RESOURCE_SHA256:
            raise ValueError
        row = json.loads(raw)
        if row["slug"] != PINNED_MODEL:
            raise ValueError
        row["apply_patch_tool_type"] = None
        row["shell_type"] = "disabled"
        row["tool_mode"] = "direct"
        row["experimental_supported_tools"] = []
        row["multi_agent_version"] = None
        return {"models": [row]}
    except Exception:
        raise _fail("commentary_policy_catalog_invalid") from None


def _mcp_names(response: Any) -> tuple[str, ...]:
    """Read only dictionary keys; never copy, dump or log server config values."""
    config = getattr(response, "config", None)
    if config is None:
        raise _fail("commentary_policy_config_invalid")
    if getattr(config, "model_catalog_json", None) is not None:
        # A local catalogue is authoritative even when model/list is called;
        # it cannot serve as the independent availability preflight.
        raise _fail("commentary_policy_static_preflight_forbidden")
    if getattr(config, "model_provider", None) not in (None, "openai"):
        raise _fail("commentary_policy_provider_forbidden")
    servers = getattr(config, "mcp_servers", None)
    if servers is None:
        extras = getattr(config, "model_extra", None)
        servers = extras.get("mcp_servers", {}) if isinstance(extras, dict) else {}
    if not isinstance(servers, dict) or len(servers) > MAX_MCP_SERVERS:
        raise _fail("commentary_policy_config_invalid")
    names = []
    for name in servers:
        if not isinstance(name, str) or not name or len(name.encode("utf-8")) > MAX_MCP_NAME_BYTES:
            raise _fail("commentary_policy_config_invalid")
        # Quoted TOML keys accept punctuation; control/non-ASCII names fail closed
        # instead of risking a JSON-vs-TOML Unicode escape interpretation mismatch.
        if any(ord(character) < 32 or ord(character) > 126 for character in name):
            raise _fail("commentary_policy_config_invalid")
        names.append(name)
    return tuple(sorted(names))


def mcp_disable_override(names: tuple[str, ...]) -> str:
    """One bounded CLI assignment; public for auth-free exact-binary testing."""
    if not isinstance(names, tuple) or len(names) > MAX_MCP_SERVERS:
        raise _fail("commentary_policy_config_invalid")
    for name in names:
        if not isinstance(name, str) or not 1 <= len(name) <= MAX_MCP_NAME_BYTES or any(ord(c) < 32 or ord(c) > 126 for c in name):
            raise _fail("commentary_policy_config_invalid")
    if len(set(names)) != len(names):
        raise _fail("commentary_policy_config_invalid")
    result = "mcp_servers={" + ",".join(json.dumps(name) + "={enabled=false}" for name in sorted(names)) + "}"
    if len(result) > 8192:
        raise _fail("commentary_policy_config_invalid")
    return result


def _guard_instruction_home(config: Any) -> None:
    from .launch import filtered_environment
    effective = filtered_environment()
    supplied = getattr(config, "env", None)
    if supplied is not None:
        if not isinstance(supplied, dict):
            raise _fail("commentary_policy_home_invalid")
        effective.update(filtered_environment(supplied))
    base = effective.get("CODEX_HOME")
    if base is None:
        home = effective.get("HOME")
        if not isinstance(home, str) or not home or not Path(home).is_absolute():
            raise _fail("commentary_policy_home_invalid")
        base = str(Path(home) / ".codex")
    if not isinstance(base, str) or not base or "\0" in base or not Path(base).is_absolute():
        raise _fail("commentary_policy_home_invalid")
    for name in ("AGENTS.md", "AGENTS.override.md"):
        try:
            # lstat observes presence even for a dangling link. No contents are
            # opened; project_doc_max_bytes does not suppress global instructions.
            (Path(base) / name).lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise _fail("commentary_policy_home_unverifiable") from None
        raise _fail("commentary_policy_global_instructions_present")


def _live_efforts(account: Any, models: Any, selected: str) -> tuple[str, ...]:
    entry = getattr(account, "account", None)
    root = getattr(entry, "root", None)
    if root is None or getattr(root, "type", None) != "chatgpt":
        raise _fail("commentary_policy_chatgpt_signin_required")
    rows = getattr(models, "data", None)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 128:
        raise _fail("commentary_policy_models_invalid")
    selected_rows = [row for row in rows if selected in (getattr(row, "id", None), getattr(row, "model", None))]
    if len(selected_rows) != 1:
        raise _fail("commentary_model_unavailable")
    levels = getattr(selected_rows[0], "supported_reasoning_efforts", None)
    if not isinstance(levels, list) or not 1 <= len(levels) <= 16:
        raise _fail("commentary_policy_models_invalid")
    result = set()
    for item in levels:
        effort = getattr(item, "reasoning_effort", None)
        effort = getattr(effort, "value", effort)
        if not isinstance(effort, str) or effort not in _REASONING_EFFORTS:
            raise _fail("commentary_policy_models_invalid")
        result.add(effort)
    return tuple(sorted(result))


def _install_catalog(workspace: str | os.PathLike[str]) -> str:
    path = Path(workspace)
    if not path.is_absolute():
        raise _fail("commentary_policy_workspace_invalid")
    data = (json.dumps(restricted_catalog(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_CATALOG_BYTES:
        raise _fail("commentary_policy_catalog_invalid")
    directory = None
    descriptor = None
    created = False
    try:
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        owner = os.fstat(directory)
        if owner.st_uid != os.getuid() or stat.S_IMODE(owner.st_mode) != 0o700:
            raise ValueError
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            descriptor = os.open(CATALOG_FILENAME, flags, 0o600, dir_fd=directory)
            created = True
        except FileExistsError:
            descriptor = os.open(CATALOG_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError
        if created:
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(descriptor)
        elif info.st_size != len(data) or os.read(descriptor, MAX_CATALOG_BYTES + 1) != data:
            raise ValueError
        return str(path / CATALOG_FILENAME)
    except Exception:
        if created and directory is not None:
            try:
                os.unlink(CATALOG_FILENAME, dir_fd=directory)
            except OSError:
                pass
        raise _fail("commentary_policy_workspace_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory is not None:
            os.close(directory)


async def prepare_policy(
    config: Any,
    model: str,
    workspace: str | os.PathLike[str],
    *,
    owner: PolicyPreflight,
    client_factory: Callable[[Any], Any] | None = None,
    response_model: type | None = None,
) -> PreparedPolicy:
    """Verify live availability, close the preflight, then prepare startup policy.

    ``client_factory``/``response_model`` are an explicit offline test seam; real
    integrations leave both omitted.  All actual SDK calls are public.  The real
    selected model is never changed or replaced by an automatic fallback.
    """
    if model != PINNED_MODEL:
        raise _fail("commentary_policy_model_unsupported")
    if not isinstance(owner, PolicyPreflight) or not owner.closed:
        raise _fail("commentary_policy_preflight_in_use")
    _guard_instruction_home(config)
    client = None
    try:
        candidate = copy.copy(config)
        candidate.cwd = str(workspace)
        candidate.config_overrides = _startup_base(config)
        if client_factory is None:
            if version("openai-codex") != PINNED_SDK_VERSION or version("openai-codex-cli-bin") != PINNED_SDK_VERSION:
                raise _fail("codex_version_mismatch")
            from openai_codex.async_client import AsyncCodexClient
            from openai_codex.generated.v2_all import ConfigReadResponse
            from .launch import codex_launch_args
            candidate.launch_args_override = None
            candidate.launch_args_override = codex_launch_args(candidate)
            client_factory = AsyncCodexClient
            response_model = ConfigReadResponse
        if response_model is None:
            raise _fail("commentary_policy_config_invalid")
        client = client_factory(candidate)
        owner.register(client)
        async with asyncio.timeout(PREPARE_TIMEOUT_SECONDS):
            await owner.start()
            await client.initialize()
            effective = await client.request("config/read", {"cwd": str(workspace), "includeLayers": False}, response_model=response_model)
            names = _mcp_names(effective)
            account = await client.account_read({"refreshToken": False})
            models = await client.model_list()
            efforts = _live_efforts(account, models, model)
    except asyncio.CancelledError:
        raise
    except ActorError:
        raise
    except Exception:
        raise _fail("commentary_policy_preflight_failed") from None
    finally:
        if client is not None:
            await owner.close()

    path = str(Path(workspace) / CATALOG_FILENAME)
    # A single bounded inline table stays within the official launch helper's
    # argument-count ceiling even with many MCP servers. Each name is quoted as
    # one literal key (including embedded dots, equals signs and quotation marks).
    mcp_override = mcp_disable_override(names)
    startup = _startup_base(config) + (mcp_override,
        "model_catalog_json=" + json.dumps(path, ensure_ascii=False),
    )
    if len(startup) > 64 or any(len(value) > 8192 for value in startup):
        raise _fail("commentary_policy_config_invalid")
    _install_catalog(workspace)
    thread_config: dict[str, Any] = {
        key.rsplit("=", 1)[0]: key.rsplit("=", 1)[1] == "true"
        for key in RESTRICTIVE_OVERRIDES if key.endswith(("=true", "=false"))
    }
    thread_config["project_doc_max_bytes"] = 0
    thread_config["web_search"] = "disabled"
    thread_config["mcp_servers"] = {name: {"enabled": False} for name in names}
    return PreparedPolicy(model, efforts, True, startup, thread_config, path, len(names))
