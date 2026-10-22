"""Installed CLI/config boundaries with no native store, network or accounts."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from actor_broadcast import cli
from actor_broadcast.coordinator import CoordinatorError
from actor_broadcast.installation import BroadcastInstallation
from actor_runtime.launching import write_state


def settings(tmp_path, **changes):
    value={"client_manifest":str(tmp_path/"client-manifest.json"),"tts_python":sys.executable,
        "tts_model_path":str(tmp_path/"tts-model"),"protected_obs_backup":str(tmp_path/"obs-backup"),
        "state_directory":str(tmp_path/"state"),**changes}
    return value


def installation(tmp_path, **changes):
    return BroadcastInstallation.model_validate_json(json.dumps(settings(tmp_path,**changes)))


def test_installed_broadcast_doctor_is_auth_free_and_does_not_import_native_or_optional_providers(tmp_path):
    client=tmp_path/"client";client.write_bytes(b"fixture executable is never launched");client.chmod(0o755)
    manifest={"schema":"actor.client.v1","game_id":"agents-arena","game_version":"0.10.0",
        "client":{"executable":"client","sha256":hashlib.sha256(client.read_bytes()).hexdigest()}}
    (tmp_path/"client-manifest.json").write_text(json.dumps(manifest))
    config=tmp_path/"broadcast.json";config.write_text(json.dumps(settings(tmp_path,chat_enabled=False)))
    source='''import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'openai_codex','mlx','mlx_audio','Quartz','websockets'} or fullname in {'actor_broadcast.native','actor_broadcast.secrets'}:
            raise AssertionError('doctor imported a native, credential or optional provider module')
sys.meta_path.insert(0,Block())
from actor_runtime.control import main
raise SystemExit(main(['broadcast','doctor','--config',sys.argv[1]]))
'''
    repo=Path(__file__).resolve().parents[2]
    environment=dict(os.environ,PYTHONPATH=os.pathsep.join((str(repo/"runtime"),str(repo/"generated/python"))))
    result=subprocess.run([sys.executable,"-c",source,str(config)],cwd=tmp_path,env=environment,
        capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report["authentication"]=="not_checked" and report["provider"]=="not_checked"
    assert report["model_calls"]==0 and report["client_verified"] is True and report["chat_enabled"] is False


@pytest.mark.parametrize("changes",[
    {"operator_forward_url":"http://public.example:5080"},
    {"operator_forward_url":"http://127.0.0.1:5080?secret=x"},
    {"obs_endpoint":"ws://name:secret@127.0.0.1:4455"},
    {"obs_endpoint":"wss://127.0.0.1:4455"},
    {"obs_endpoint":"ws://localhost:4455"},
    {"obs_endpoint":"ws://127.0.0.1:4455/private"},
    {"obs_endpoint":"ws://127.0.0.1:4456"},
    {"chat_enabled":"false"},
    {"twitch_client_secret":"must-not-be-configurable"},
    {"channel":"different_destination"},
    {"ssh":{"host":"owned.example","identity_file":"/unused/key","local_port":55000}},
])
def test_configuration_rejects_remote_control_credentials_implicit_destination_and_mismatched_forward(tmp_path,changes):
    with pytest.raises(ValidationError):installation(tmp_path,**changes)


def test_explicit_chat_optout_and_config_load_require_no_credentials(tmp_path):
    config=tmp_path/"broadcast.json";config.write_text(json.dumps(settings(tmp_path,chat_enabled=False)))
    result=BroadcastInstallation.load(config)
    assert result.chat_enabled is False and result.twitch_client_id=="" and result.admission_protocol=="4"
    assert result.codex_home==Path.home()/".actor/broadcast/codex-home"
    config.write_bytes(b" "*16385)
    with pytest.raises(ValueError):BroadcastInstallation.load(config)


def test_explicit_codex_home_load_resolves_without_creating_it(tmp_path):
    config=tmp_path/"broadcast.json"
    config.write_text(json.dumps(settings(tmp_path,codex_home="./separate-broadcast-codex")))
    result=BroadcastInstallation.load(config)
    assert result.codex_home==Path("./separate-broadcast-codex").absolute()
    assert not result.codex_home.exists()


@pytest.mark.parametrize("action",["codex-status","codex-login"])
def test_broadcast_auth_commands_pass_only_explicit_home_without_native_start(tmp_path,monkeypatch,capsys,action):
    from actor_codex import commentary_auth
    from actor_runtime.control import main
    home=tmp_path/"separate-codex";config=tmp_path/"broadcast.json"
    config.write_text(json.dumps(settings(tmp_path,codex_home=str(home))))
    called=[]
    async def operation(value):
        called.append(value)
        return {"authenticated":True,"model_calls":0,"cleanup_completed":True}
    monkeypatch.setattr(commentary_auth,"codex_status" if action=="codex-status" else "codex_login",operation)
    monkeypatch.setattr(cli,"_serve",lambda *_:pytest.fail("Auth started native broadcast"))
    assert main(["broadcast",action,"--config",str(config)])==0
    assert called==[home] and not home.exists()
    assert json.loads(capsys.readouterr().out)=={"authenticated":True,"model_calls":0,"cleanup_completed":True}


def test_missing_broadcast_codex_status_uses_no_optional_sdk_or_native_modules(tmp_path):
    config=tmp_path/"broadcast.json";home=tmp_path/"never-created"
    config.write_text(json.dumps(settings(tmp_path,codex_home=str(home))))
    source='''import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in {'openai_codex','mlx','mlx_audio','Quartz','websockets'} or fullname in {'actor_broadcast.native','actor_broadcast.secrets'}:
            raise AssertionError('missing status imported native/provider SDK')
sys.meta_path.insert(0,Block())
from actor_runtime.control import main
raise SystemExit(main(['broadcast','codex-status','--config',sys.argv[1]]))
'''
    repo=Path(__file__).resolve().parents[2]
    environment=dict(os.environ,PYTHONPATH=os.pathsep.join((str(repo/"runtime"),str(repo/"generated/python"))))
    result=subprocess.run([sys.executable,"-c",source,str(config)],cwd=tmp_path,env=environment,
        capture_output=True,text=True,timeout=15)
    assert result.returncode==1,result.stderr
    report=json.loads(result.stdout)
    assert report["home_status"]=="absent" and report["model_calls"]==0 and report["authenticated"] is False
    assert not home.exists()


def test_explicit_twitch_login_passes_public_config_only_without_native_start(tmp_path,monkeypatch,capsys):
    from actor_broadcast import twitch_login
    from actor_runtime.control import main
    config=tmp_path/"broadcast.json"
    config.write_text(json.dumps(settings(tmp_path,twitch_client_id="publicClient123")))
    called=[]
    async def login(client_id,*,expected_login):
        called.append((client_id,expected_login))
        return {"authenticated":True,"token_saved":True,"eventsub_connected":False,"model_calls":0}
    monkeypatch.setattr(twitch_login,"twitch_login",login)
    monkeypatch.setattr(cli,"_serve",lambda *_:pytest.fail("Twitch login started native broadcast"))
    assert main(["broadcast","twitch-login","--config",str(config)])==0
    assert called==[("publicClient123","fedorr_codex")]
    assert json.loads(capsys.readouterr().out)=={
        "authenticated":True,"token_saved":True,"eventsub_connected":False,"model_calls":0}


def test_twitch_login_empty_client_id_is_actionable_before_native_keychain(tmp_path,monkeypatch,capsys):
    from actor_runtime.control import main
    from actor_broadcast import secrets
    config=tmp_path/"broadcast.json";config.write_text(json.dumps(settings(tmp_path)))
    monkeypatch.setattr(secrets,"MacOSKeychainSecretStore",lambda:pytest.fail("empty ID opened Keychain"))
    assert main(["broadcast","twitch-login","--config",str(config)])==1
    assert json.loads(capsys.readouterr().out)=={"code":"twitch_client_id_required"}


def test_control_rejects_foreign_owner_before_accessing_secret_store(tmp_path,monkeypatch):
    write_state(tmp_path,{"schema":"actor.launch.v1","owner_uid":os.getuid(),"brain":"broadcast",
        "operator_port":50000,"processes":{"launcher":{"pid":12345}}})
    monkeypatch.setattr(cli,"owned_process",lambda _:False)
    from actor_broadcast import secrets
    monkeypatch.setattr(secrets,"MacOSKeychainSecretStore",lambda:pytest.fail("foreign controller read credentials"))
    with pytest.raises(CoordinatorError,match="not_running"):
        cli._control(NS(run=tmp_path,broadcast_action="stop"))


def test_control_uses_only_loopback_header_auth_and_does_not_print_secret_or_unknown_status(tmp_path,monkeypatch,capsys):
    write_state(tmp_path,{"schema":"actor.launch.v1","owner_uid":os.getuid(),"brain":"broadcast",
        "operator_port":50000,"processes":{"launcher":{"pid":12345}}})
    monkeypatch.setattr(cli,"owned_process",lambda _:True)
    from actor_broadcast import secrets
    monkeypatch.setattr(secrets,"MacOSKeychainSecretStore",lambda:NS(get=lambda name:"private-header-secret"))
    seen=[]
    class Response:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,size):
            assert size==16385
            return json.dumps({"state":"Idle","private_token":"must-not-print","chat_enabled":False}).encode()
    def open_request(request,**kwargs):
        seen.append(request)
        assert request.full_url=="http://127.0.0.1:50000/api/status"
        assert request.get_header("Authorization")=="Bearer private-header-secret"
        return Response()
    monkeypatch.setattr(cli,"build_opener",lambda *handlers:NS(open=open_request))
    assert cli._control(NS(run=tmp_path,broadcast_action="status"))==0
    output=capsys.readouterr().out
    assert "private-header-secret" not in output and "private_token" not in output
    assert json.loads(output)["chat_enabled"] is False and len(seen)==1
    with pytest.raises(CoordinatorError,match="redirect_rejected"):
        cli._NoRedirect().redirect_request(None,None,None,None,None,None)


@pytest.mark.parametrize("cleanup_failure",[False,True])
async def test_startup_failure_attempts_every_cleanup_and_preserves_failed_launch_state(tmp_path,monkeypatch,cleanup_failure):
    operations=[]
    class Factory:
        def __init__(self,*_):pass
        async def aclose(self):operations.append("factory")
    class Controller:
        def __init__(self,*_,**__):pass
        async def aclose(self):
            operations.append("controller")
            if cleanup_failure:raise RuntimeError("private cleanup diagnostic")
    class Server:
        def __init__(self,*_,**__):pass
        async def start(self):raise CoordinatorError("operator_bind_failed")
        async def aclose(self):operations.append("server")
    from actor_broadcast import operator,secrets
    monkeypatch.setitem(sys.modules,"actor_broadcast.native",NS(NativeBroadcastFactory=Factory))
    monkeypatch.setattr(cli,"BroadcastCoordinator",Controller)
    monkeypatch.setattr(operator,"OperatorServer",Server)
    monkeypatch.setattr(secrets,"MacOSKeychainSecretStore",lambda:object())
    loop=asyncio.get_running_loop();registered=[]
    monkeypatch.setattr(loop,"add_signal_handler",lambda signum,*_:registered.append(signum))
    monkeypatch.setattr(loop,"remove_signal_handler",lambda signum:registered.remove(signum))
    output=tmp_path/"run"
    reason="broadcast_cleanup_failed" if cleanup_failure else "operator_bind_failed"
    with pytest.raises(CoordinatorError,match=reason):
        await cli._serve(NS(output=output,port=0,broadcast_action="ui"),installation(tmp_path))
    assert operations==["server","controller","factory"] and registered==[]
    state=json.loads((output/"launch.json").read_text())
    assert state["state"]=="failed" and state["reason"]==reason
    assert "private cleanup diagnostic" not in str(state)
