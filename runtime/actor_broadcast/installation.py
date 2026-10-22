"""Non-secret local installation configuration, independent of editorial policy."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import CommentaryConfig


class SshForwardSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9.-]+$")
    user: str = Field(default="root", pattern=r"^[a-z_][a-z0-9_-]{0,31}$")
    port: int = Field(default=22, ge=1, le=65535)
    identity_file: Path
    local_port: int = Field(default=56251, ge=1024, le=65535)
    remote_port: int = Field(default=5080, ge=1024, le=65535)


class BroadcastInstallation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    channel: str = Field(default="fedorr_codex", pattern=r"^fedorr_codex$")
    game_id: str = Field(default="agents-arena", pattern=r"^agents-arena$")
    game_version: str = Field(default="0.10.0", pattern=r"^0\.10\.0$")
    admission_protocol: str = Field(default="4", pattern=r"^4$")
    operator_forward_url: str = "http://127.0.0.1:56251"
    ssh: SshForwardSettings | None = None
    client_manifest: Path
    tts_python: Path
    tts_model_path: Path
    twitch_client_id: str = Field(default="", pattern=r"^(?:[A-Za-z0-9]{8,128})?$")
    chat_enabled: bool = True
    obs_endpoint: str = "ws://127.0.0.1:4455"
    firewall_socket: Path = Path("/private/var/db/actor-broadcast-firewall-run/control.sock")
    protected_obs_backup: Path
    state_directory: Path = Path.home() / ".local/state/actorctl/broadcast"
    codex_home: Path = Field(default_factory=lambda: Path.home() / ".actor/broadcast/codex-home")
    ffmpeg_executable: Path = Path("/opt/homebrew/bin/ffmpeg")
    commentary: CommentaryConfig = Field(default_factory=CommentaryConfig)

    @model_validator(mode="after")
    def local_boundaries(self):
        for value, scheme in ((self.operator_forward_url, "http"), (self.obs_endpoint, "ws")):
            p = urlsplit(value)
            if (p.scheme != scheme or p.hostname not in ("127.0.0.1", "::1") or not p.port
                    or p.username or p.password or p.query or p.fragment or p.path not in ("", "/")):
                raise ValueError("Control endpoints must use direct loopback without credentials")
        if urlsplit(self.obs_endpoint).port != 4455:
            raise ValueError("The OBS endpoint must match the verified firewall port 4455")
        if self.ssh is not None and urlsplit(self.operator_forward_url).port != self.ssh.local_port:
            raise ValueError("The operator endpoint must match the owned SSH forward")
        return self

    @classmethod
    def load(cls, path):
        path = Path(path).expanduser()
        if not path.is_file() or path.stat().st_size > 16384:
            raise ValueError("A bounded local broadcast configuration is required")
        result = cls.model_validate_json(path.read_bytes())
        for name in ("client_manifest", "tts_python", "tts_model_path", "firewall_socket", "protected_obs_backup",
                     "state_directory", "codex_home", "ffmpeg_executable"):
            setattr(result, name, getattr(result, name).expanduser().absolute())
        if result.ssh is not None: result.ssh.identity_file = result.ssh.identity_file.expanduser().absolute()
        return result
