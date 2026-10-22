"""Public-SDK launcher with a filtered final app-server environment.

The pinned SDK merges CodexConfig.env into os.environ instead of replacing it.
Its public launch_args_override starts a trusted isolated stdlib trampoline;
the trampoline briefly inherits that merged environment, then execve replaces
it before the official app-server starts. This is not an OS security boundary
against the Python interpreter/loader itself. No credential value is serialized
into argv, a file, or a log, and the parent environment is never mutated.
"""
from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version
import os
from pathlib import Path
import sys

from actor_runtime.errors import ActorError


PINNED_SDK_VERSION = "0.162.1"
ENVIRONMENT_KEYS = frozenset({
    "HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "CODEX_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE", "SYSTEMROOT", "WINDIR",
})

# Only stdlib, no package/site/customisation imports. -I ignores PYTHON* and
# cwd import paths; -S also excludes sitecustomize before the filtering step.
_TRAMPOLINE = """import os, sys
allowed = frozenset(%r)
environment = {key: value for key, value in os.environ.items() if key in allowed}
executable, support = sys.argv[1:3]
if support:
    previous = environment.get('PATH', '')
    environment['PATH'] = support + (os.pathsep + previous if previous else '')
os.execve(executable, [executable, *sys.argv[3:]], environment)
""" % (tuple(sorted(ENVIRONMENT_KEYS)),)


def filtered_environment(environ=None):
    """Allow OS/locale/auth-home and certificate paths; no token/proxy/agent env."""
    source = os.environ if environ is None else environ
    return {key: value for key, value in source.items() if key in ENVIRONMENT_KEYS}


def codex_launch_args(config):
    """Build CodexConfig.launch_args_override without launching any process.

    Callers must pass the same trusted CodexConfig whose config_overrides they
    intend to use. Custom binaries/already-overridden launches are refused.
    The supported bundle is resolved only through its public package API.
    """
    if getattr(config, "codex_bin", None) is not None or getattr(config, "launch_args_override", None) is not None:
        raise ActorError("codex_launch_override_forbidden", "The pinned official runtime is required")
    overrides = getattr(config, "config_overrides", ())
    if (not isinstance(overrides, (tuple, list)) or len(overrides) > 64
            or any(not isinstance(value, str) or not 1 <= len(value) <= 8192 or "\0" in value for value in overrides)):
        raise ActorError("codex_launch_config_invalid", "Bounded trusted configuration overrides are required")
    try:
        if any(version(name) != PINNED_SDK_VERSION for name in ("openai-codex", "openai-codex-cli-bin")):
            raise ActorError("codex_version_mismatch", "ACTOR requires the pinned official Codex SDK and runtime")
        bundle = importlib.import_module("codex_cli_bin")
        executable = Path(bundle.bundled_codex_path()).absolute()
        support = bundle.bundled_path_dir()
        support = None if support is None else Path(support).absolute()
        if not executable.is_file() or not os.access(executable, os.X_OK) or support is not None and not support.is_dir():
            raise ValueError
        if not sys.executable or not Path(sys.executable).is_file(): raise ValueError
    except (ImportError, PackageNotFoundError, OSError, ValueError, AttributeError):
        raise ActorError("codex_launch_unavailable", "The pinned bundled Codex runtime is unavailable") from None
    args = [sys.executable, "-I", "-S", "-c", _TRAMPOLINE, str(executable), str(support) if support is not None else ""]
    for value in overrides: args.extend(("--config", value))
    args.extend(("app-server", "--listen", "stdio://"))
    return tuple(args)
