"""Installed game launch recipes and guarded ownership of local child processes.

These files are process diagnostics, never an action/observation transport.
Bridge credentials remain per-launch environment variables.
"""
from dataclasses import dataclass
import hashlib
from importlib import metadata
import json
import inspect
import os
from pathlib import Path
import re
import subprocess
from typing import Callable
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field
from .errors import ActorError


@dataclass(frozen=True)
class GameLaunchProfile:
    game_id: str
    game_version: str
    match_policy: str
    default_brain: str
    client_candidates: tuple[str, ...]
    client_arguments: Callable[..., list[str]]


def profiles():
    result={}
    for point in metadata.entry_points(group="actor.games"):
        factory=point.load()
        if not callable(factory) or inspect.iscoroutinefunction(factory):
            raise ActorError("game_profile_invalid","Game launch profile factories must be synchronous")
        value=factory()
        if inspect.iscoroutine(value):value.close()
        if (not isinstance(value,GameLaunchProfile) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}",point.name)
                or point.name in result or not callable(value.client_arguments) or inspect.iscoroutinefunction(value.client_arguments)
                or not all(isinstance(item,str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}",item)
                    for item in (value.game_id,value.match_policy,value.default_brain))):
            raise ActorError("game_profile_invalid","Installed game launch profiles must be unique and typed")
        result[point.name]=value
    return result


class ClientArtifact(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    executable: str=Field(min_length=1,max_length=1024)
    sha256: str=Field(pattern=r"^[a-f0-9]{64}$")


class ClientManifest(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    schema_id: str=Field(alias="schema",pattern=r"^actor\.client\.v1$")
    game_id: str=Field(min_length=1,max_length=128)
    game_version: str=Field(min_length=1,max_length=64)
    client: ClientArtifact


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b""): digest.update(chunk)
    return digest.hexdigest()


def resolve_client(profile,*,client=None,manifest=None,expected_sha256=None):
    path=Path(client).expanduser().resolve() if client else None
    if manifest is None:
        candidates=[Path.cwd()/"client-manifest.json"] if path is None else []
        if path is not None:
            candidates=[*(parent/"client-manifest.json" for parent in (path.parent,*list(path.parents)[:4])),*candidates]
        manifest=next((value for value in candidates if value.is_file()),None)
    document=None
    if manifest is not None:
        manifest=Path(manifest).expanduser().resolve()
        if manifest.stat().st_size>65536:
            raise ActorError("client_manifest_invalid","Client manifest is too large")
        document=ClientManifest.model_validate_json(manifest.read_bytes())
        if (document.game_id,document.game_version)!=(profile.game_id,profile.game_version):
            raise ActorError("client_version_mismatch","Client manifest does not match this installed game profile")
        relative=Path(document.client.executable)
        candidate=(manifest.parent/relative).resolve()
        if relative.is_absolute() or not candidate.is_relative_to(manifest.parent):
            raise ActorError("client_manifest_invalid","Client executable must remain inside its release directory")
        if path is not None and path!=candidate:
            raise ActorError("client_manifest_mismatch","Explicit client differs from the verified release executable")
        path=candidate
        if expected_sha256 and expected_sha256!=document.client.sha256:
            raise ActorError("client_hash_mismatch","The two expected client checksums differ")
        expected_sha256=document.client.sha256
    if path is None:
        candidates=([os.environ["ACTOR_GAME_CLIENT"]] if os.environ.get("ACTOR_GAME_CLIENT") else [])+list(profile.client_candidates)
        path=next((Path(value).expanduser().resolve() for value in candidates if Path(value).expanduser().is_file()),None)
    if path is None or not path.is_file() or not os.access(path,os.X_OK):
        raise ActorError("client_unavailable","Provide --client-manifest from a verified release or --client EXECUTABLE")
    if expected_sha256 is not None:
        if not re.fullmatch(r"[a-f0-9]{64}",expected_sha256) or sha256(path)!=expected_sha256:
            raise ActorError("client_hash_mismatch","Client executable checksum does not match")
    return path,{"integrity":"sha256_verified" if expected_sha256 else "unverified_local",
        "manifest_version":document.game_version if document else None}


def api_url(value):
    if value is None:return None
    try:
        url=urlsplit(value)
        valid=(len(value)<=2048 and url.scheme in ("http","https") and url.hostname and not url.username and
            not url.password and not url.query and not url.fragment and all(ord(c)>=33 for c in value) and
            (url.port is None or 1<=url.port<=65535))
    except ValueError:valid=False
    if not valid:raise ActorError("api_url_invalid","Use an absolute HTTP(S) lobby URL without credentials, query or fragment")
    return value


def process_identity(pid):
    if type(pid) is not int or pid<=1 or os.name!="posix":return None
    try:
        result=subprocess.run(["ps","-p",str(pid),"-o","lstart=","-o","command="],capture_output=True,text=True,timeout=2,check=False)
    except (OSError,subprocess.TimeoutExpired):return None
    fields=result.stdout.strip().split(maxsplit=5)
    if result.returncode or len(fields)!=6:return None
    return {"pid":pid,"started":" ".join(fields[:5]),"command_sha256":hashlib.sha256(fields[5].encode()).hexdigest()}


def owned_process(identity):
    return bool(identity and identity==process_identity(identity.get("pid")))


def write_state(directory,value):
    path=Path(directory)/"launch.json"
    temporary=path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        os.chmod(temporary,0o600)
        json.dump(value,stream,indent=2);stream.write("\n")
    temporary.replace(path)


def read_state(directory):
    path=Path(directory)/"launch.json"
    if not path.is_file() or path.stat().st_size>65536:
        raise ActorError("launch_state_invalid","A bounded owned launch.json is required")
    value=json.loads(path.read_text())
    if value.get("schema")!="actor.launch.v1" or value.get("owner_uid")!=getattr(os,"getuid",lambda:None)():
        raise ActorError("launch_state_invalid","Launch record belongs to another format or OS owner")
    return value
