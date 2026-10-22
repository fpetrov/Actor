"""Installed onboarding, release integrity and exact owned-child cleanup."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from actor_runtime import control
from actor_runtime.errors import ActorError
from actor_runtime.launching import resolve_client, api_url
from actor_agents_arena.launcher import game_profile


def executable(path,source="print('client fixture')\n"):
    path.write_text(f"#!{sys.executable}\n"+source)
    path.chmod(0o755)
    return path


def manifest(tmp_path,client,**changes):
    value={"schema":"actor.client.v1","game_id":"agents-arena","game_version":"0.9.0",
        "client":{"executable":client.name,"sha256":hashlib.sha256(client.read_bytes()).hexdigest()},**changes}
    path=tmp_path/"client-manifest.json";path.write_text(json.dumps(value));return path


def test_doctor_outside_checkout_does_not_import_optional_provider_or_authenticate(tmp_path):
    source='''import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in ('openai_codex','typesafe_sdk'):
            raise AssertionError('optional provider imported during doctor')
sys.meta_path.insert(0,Block())
from actor_runtime.control import main
raise SystemExit(main(['doctor']))
'''
    result=subprocess.run([sys.executable,"-c",source],cwd=tmp_path,capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report["runtime_version"]=="0.7.0" and report["authentication"]=="not_checked"
    from actor_runtime import __version__
    assert report["runtime_version"]==__version__
    assert report["game_profiles"]==["agents-arena"]


def test_release_client_checksum_version_and_scope_are_validated(tmp_path):
    client=executable(tmp_path/"client")
    release=manifest(tmp_path,client)
    path,info=resolve_client(game_profile(),manifest=release)
    assert path==client and info["integrity"]=="sha256_verified" and info["manifest_version"]=="0.9.0"
    client.write_text("changed executable")
    with pytest.raises(ActorError,match="client_hash_mismatch"):resolve_client(game_profile(),manifest=release)
    release=manifest(tmp_path,client,game_version="0.8.0")
    with pytest.raises(ActorError,match="client_version_mismatch"):resolve_client(game_profile(),manifest=release)


def test_arena_name_rules_are_game_owned_and_fail_before_starting_any_child(tmp_path,monkeypatch):
    from actor_agents_arena.launcher import arguments
    options=dict(room="ABC123",endpoint="http://127.0.0.1:50051",report=tmp_path/"client.json",
        log=tmp_path/"client.log",headless=True,api_url=None)
    for name in ("AA","Женя_2","A"*24):
        assert arguments(player_name=name,**options)[3]==name
    for name in ("A","A"*25,"Name ","<Name>","A\tB","¹A","𐐀A"):
        with pytest.raises(ActorError,match="player_name_invalid"):arguments(player_name=name,**options)
    client=executable(tmp_path/"client")
    def forbidden(*_,**__):pytest.fail("A child started before game-owned validation")
    monkeypatch.setattr(control.subprocess,"Popen",forbidden)
    monkeypatch.setattr(control,"process_identity",lambda pid:{"pid":pid,"started":"fixture","command_sha256":"fixture"})
    output=tmp_path/"invalid-name-run"
    assert control.main(["join","ABC123","--brain","social-scripted","--client",str(client),
        "--player-name","<Name>","--output",str(output)])==1
    state=json.loads((output/"launch.json").read_text())
    assert state["cleanup_complete"] and state["reason"]=="player_name_invalid"
    assert set(state["processes"])=={"launcher"}


def test_client_manifest_cannot_escape_release_directory(tmp_path):
    directory=tmp_path/"release";directory.mkdir()
    client=executable(tmp_path/"other-client")
    release=manifest(directory,client)
    value=json.loads(release.read_text());value["client"]["executable"]="../other-client";release.write_text(json.dumps(value))
    with pytest.raises(ActorError,match="client_manifest_invalid"):resolve_client(game_profile(),manifest=release)
    link=directory/"client-link";link.symlink_to(client)
    value["client"]["executable"]="client-link";release.write_text(json.dumps(value))
    with pytest.raises(ActorError,match="client_manifest_invalid"):resolve_client(game_profile(),manifest=release)


@pytest.mark.parametrize("value",["https://name:secret@example.com","https://example.com?q=private","https://example.com/#secret","file:///tmp/game"])
def test_lobby_url_cannot_smuggle_credentials_or_a_non_http_scheme(value):
    with pytest.raises(ActorError):api_url(value)


def test_stop_does_not_signal_a_reused_or_foreign_pid(tmp_path,monkeypatch,capsys):
    from actor_runtime.launching import write_state
    write_state(tmp_path,{"schema":"actor.launch.v1","owner_uid":os.getuid(),
        "processes":{"launcher":{"pid":123456,"started":"old","command_sha256":"wrong"}}})
    monkeypatch.setattr(control,"owned_process",lambda identity:False)
    killed=[];monkeypatch.setattr(os,"kill",lambda *args:killed.append(args))
    assert control.stop(SimpleNamespace(run=tmp_path))==0
    assert killed==[] and json.loads(capsys.readouterr().out)["already_stopped"]


@pytest.mark.skipif(os.name!="posix",reason="Exact ps identity and desktop Unix signal lifecycle")
def test_actorctl_sigterm_cleans_owned_runtime_and_client_without_token_artifacts(tmp_path):
    runtime=tmp_path/"runtime_fixture.py"
    runtime.write_text('''import os, signal, socket, sys, time
from pathlib import Path
root=Path(os.environ['ACTORCTL_TEST_ROOT'])
def stop(*_):raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
listener=socket.socket();listener.bind(('127.0.0.1',int(sys.argv[1])));listener.listen();listener.settimeout(.05)
(root/'credential-valid').write_text(str(len(os.environ.get('ACTOR_BRIDGE_TOKEN',''))==64))
try:
    while True:
        try:connection,_=listener.accept();connection.close()
        except socket.timeout:pass
finally:
    time.sleep(.1)
    listener.close();(root/'runtime-cleanup').write_text('closed')
''')
    client=executable(tmp_path/"client",'''import os, signal, time
from pathlib import Path
root=Path(os.environ['ACTORCTL_TEST_ROOT'])
def stop(*_):raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
(root/'client-ready').write_text('ready')
try:
    while True:time.sleep(.02)
finally:(root/'client-cleanup').write_text('closed')
''')
    harness=tmp_path/"supervisor.py"
    harness.write_text('''import subprocess, sys
from actor_runtime import control
real_popen=subprocess.Popen
def fixture_popen(args,**kwargs):
    if '-m' in args and 'actor_runtime' in args:
        args=[sys.executable,sys.argv[1],args[args.index('--port')+1]]
    return real_popen(args,**kwargs)
control.subprocess.Popen=fixture_popen
raise SystemExit(control.main(['join','ABC123','--brain','social-scripted','--client',sys.argv[2],'--output',sys.argv[3],'--headless']))
''')
    output=tmp_path/"run"
    env=dict(os.environ,ACTORCTL_TEST_ROOT=str(tmp_path))
    parent=subprocess.Popen([sys.executable,str(harness),str(runtime),str(client),str(output)],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            if parent.poll() is not None:
                out,err=parent.communicate();pytest.fail(out+err)
            if (tmp_path/"client-ready").exists():break
            time.sleep(.02)
        assert (tmp_path/"client-ready").exists()
        state=json.loads((output/"launch.json").read_text())
        assert state["state"]=="running" and state["processes"]["launcher"]["pid"]==parent.pid
        assert control.main(["stop","--run",str(output)])==0
        time.sleep(.03)
        if parent.poll() is None:parent.send_signal(signal.SIGTERM)
        out,err=parent.communicate(timeout=10)
        assert parent.returncode==130,(out,err)
        state=json.loads((output/"launch.json").read_text())
        assert state["cleanup_complete"] and state["state"]=="interrupted"
        assert (tmp_path/"runtime-cleanup").exists() and (tmp_path/"client-cleanup").exists()
        assert (tmp_path/"credential-valid").read_text()=="True"
        assert "ACTOR_BRIDGE_TOKEN" not in json.dumps(state)
        assert not any(key in json.dumps(state) for key in ("auth_token","resume_token","objective"))
        assert all(not control.owned_process(value) for value in state["processes"].values())
    finally:
        if parent.poll() is None:parent.terminate();parent.wait(timeout=10)
