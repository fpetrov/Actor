"""Separate-home authentication contracts; fake public SDK, no real accounts."""
import asyncio
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace as NS

import pytest

from actor_codex import commentary_auth as auth
from actor_runtime.errors import ActorError


class SDK:
    def __init__(self,*,signed_in=False,kind="chatgpt",url="https://auth.openai.com/oauth/authorize?state=private-state"):
        self.signed_in,self.kind,self.url=signed_in,kind,url
        self.calls=[];self.config=None;self.closed=False;self.block=False
        self.waiting=asyncio.Event()
    def CodexConfig(self,**options):self.config=NS(**options);return self.config
    def AsyncCodex(self,config):
        sdk=self
        class Client:
            async def __aenter__(self):sdk.calls.append("start");return self
            async def account(self,*,refresh_token):
                assert refresh_token is False
                sdk.calls.append("account")
                return NS(account=NS(root=NS(type=sdk.kind,email="private-email",account_id="private-account")) if sdk.signed_in else None,
                    requires_openai_auth=True)
            async def login_chatgpt(self):
                sdk.calls.append("login")
                class Handle:
                    auth_url=sdk.url
                    async def wait(self):
                        sdk.calls.append("wait");sdk.waiting.set()
                        if sdk.block:await asyncio.Future()
                        sdk.signed_in=True
                        return NS(success=True,login_id="private-login",error=None)
                    async def cancel(self):sdk.calls.append("cancel")
                return Handle()
            async def close(self):sdk.calls.append("close");sdk.closed=True
            async def thread_start(self,**_):pytest.fail("Auth started a thread")
            async def models(self):pytest.fail("Auth listed models")
        return Client()


def private_home(tmp_path):
    home=tmp_path/"commentator";home.mkdir(mode=0o700)
    return home


async def test_missing_home_status_is_metadata_only_and_never_creates_or_imports_sdk(tmp_path,monkeypatch):
    home=tmp_path/"missing"/"codex"
    import actor_codex.adapter as adapter
    monkeypatch.setattr(adapter,"sdk_module",lambda:pytest.fail("Missing home imported SDK"))
    report=await auth.codex_status(home)
    assert not home.parent.exists() and report["home_status"]=="absent"
    assert report["model_calls"]==0 and report["authenticated"] is False and report["cleanup_completed"] is True
    assert auth.commentary_home_status(home)=="absent"
    with pytest.raises(ActorError,match="signin_required"):auth.commentary_environment(home)


def test_explicit_login_home_creation_is_private_and_environment_never_copies_tokens(tmp_path,monkeypatch):
    monkeypatch.setenv("ACTOR_OBSERVER_ADMISSION","private-observer")
    monkeypatch.setenv("OPENAI_API_KEY","private-api")
    monkeypatch.setenv("SSH_AUTH_SOCK","private-agent")
    home=tmp_path/"new"/"codex"
    environment=auth.commentary_environment(home,create=True)
    assert home.stat().st_mode&0o777==0o700 and home.parent.stat().st_mode&0o777==0o700
    assert environment["CODEX_HOME"]==str(home) and auth.commentary_home_status(home)=="private"
    assert not any(value in str(environment) for value in ("private-observer","private-api","private-agent"))


@pytest.mark.parametrize("kind",["symlink","parent_symlink","world_readable","file"])
def test_unsafe_home_rejected_without_overwriting_it(tmp_path,kind):
    target=private_home(tmp_path);home=target
    if kind=="symlink":home=tmp_path/"alias";home.symlink_to(target)
    elif kind=="parent_symlink":
        alias=tmp_path/"alias";alias.symlink_to(target);home=alias/"nested"
    elif kind=="world_readable":target.chmod(0o755)
    else:target.rmdir();target.write_text("private-not-auth-content")
    with pytest.raises(ActorError,match="home_invalid"):auth.commentary_environment(home,create=True)
    if kind=="parent_symlink":assert not (target/"nested").exists()
    if kind=="world_readable":assert target.stat().st_mode&0o777==0o755
    if kind=="file":assert target.read_text()=="private-not-auth-content"


def test_shared_player_home_never_created_or_modified(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,"home",lambda:tmp_path)
    monkeypatch.delenv("CODEX_HOME",raising=False)
    with pytest.raises(ActorError):auth.commentary_environment(tmp_path/".codex",create=True)
    assert not (tmp_path/".codex").exists()
    inherited=tmp_path/"shared";monkeypatch.setenv("CODEX_HOME",str(inherited))
    with pytest.raises(ActorError):auth.commentary_environment(inherited,create=True)
    assert not inherited.exists()


@pytest.mark.parametrize("selected",[".CODEX",".CoDeX"])
def test_absent_case_variant_of_player_home_cannot_create_it(tmp_path,monkeypatch,selected):
    monkeypatch.setattr(Path,"home",lambda:tmp_path)
    monkeypatch.delenv("CODEX_HOME",raising=False)
    with pytest.raises(ActorError):auth.commentary_environment(tmp_path/selected,create=True)
    assert not (tmp_path/selected).exists() and not (tmp_path/".codex").exists()


def test_dangling_inherited_symlink_target_is_rejected_before_creation(tmp_path,monkeypatch):
    selected=tmp_path/"missing-real-home";shared=tmp_path/"main-alias"
    shared.symlink_to(selected);monkeypatch.setenv("CODEX_HOME",str(shared))
    with pytest.raises(ActorError):auth.commentary_environment(selected,create=True)
    assert not selected.exists()


def test_group_writable_ancestor_is_rejected_before_creating_child(tmp_path):
    parent=tmp_path/"unsafe-parent";parent.mkdir(mode=0o770);parent.chmod(0o770)
    with pytest.raises(ActorError,match="home_invalid"):
        auth.commentary_environment(parent/"new-home",create=True)
    assert not (parent/"new-home").exists()


def test_foreign_owned_ancestor_is_rejected_before_mkdir(tmp_path,monkeypatch):
    parent=tmp_path/"foreign";parent.mkdir(mode=0o755)
    identity=os.stat(parent);actual=os.fstat
    def metadata(fd):
        info=actual(fd)
        if (info.st_dev,info.st_ino)==(identity.st_dev,identity.st_ino):
            return NS(st_mode=info.st_mode,st_uid=99999)
        return info
    monkeypatch.setattr(os,"fstat",metadata)
    with pytest.raises(ActorError,match="home_invalid"):
        auth.commentary_environment(parent/"new-home",create=True)
    assert not (parent/"new-home").exists()


def test_only_root_owned_sticky_writable_ancestor_is_permitted():
    auth._safe_ancestor(NS(st_mode=stat.S_IFDIR|0o1777,st_uid=0))
    with pytest.raises(ActorError):auth._safe_ancestor(NS(st_mode=stat.S_IFDIR|0o777,st_uid=0))
    with pytest.raises(ActorError):auth._safe_ancestor(NS(st_mode=stat.S_IFDIR|0o1777,st_uid=99999))


def test_case_insensitive_default_home_alias_is_rejected_by_opened_identity(tmp_path,monkeypatch):
    target=private_home(tmp_path);actual=os.stat;identity=actual(target)
    default=tmp_path/"user"/".codex"
    monkeypatch.setattr(Path,"home",lambda:tmp_path/"user")
    monkeypatch.delenv("CODEX_HOME",raising=False)
    def metadata(path,*args,**kwargs):
        if Path(path)==default:return identity  # Portable case-insensitive filesystem seam.
        return actual(path,*args,**kwargs)
    monkeypatch.setattr(os,"stat",metadata)
    with pytest.raises(ActorError,match="home_invalid"):auth.commentary_environment(target)


def test_inherited_player_home_symlink_to_selected_directory_is_rejected(tmp_path,monkeypatch):
    target=private_home(tmp_path);shared=tmp_path/"main-codex"
    shared.symlink_to(target);monkeypatch.setenv("CODEX_HOME",str(shared))
    with pytest.raises(ActorError,match="home_invalid"):auth.commentary_environment(target)


def test_unknown_shared_home_metadata_fails_closed_without_reading_contents(tmp_path,monkeypatch):
    target=private_home(tmp_path);shared=tmp_path/"unverifiable";actual=os.stat
    monkeypatch.setenv("CODEX_HOME",str(shared))
    def metadata(path,*args,**kwargs):
        if Path(path)==shared:raise PermissionError("private metadata diagnostic")
        return actual(path,*args,**kwargs)
    monkeypatch.setattr(os,"stat",metadata)
    monkeypatch.setattr(Path,"read_bytes",lambda *_:pytest.fail("Read home contents"))
    with pytest.raises(ActorError,match="home_invalid") as error:auth.commentary_environment(target)
    assert "private metadata" not in str(error.value)


async def test_status_uses_only_public_account_and_omits_identity_or_auth_contents(tmp_path,monkeypatch):
    home=private_home(tmp_path);(home/"auth.json").write_text("private-credential-never-read")
    monkeypatch.setattr(Path,"read_bytes",lambda *_:pytest.fail("Read credential contents"))
    monkeypatch.setattr(Path,"read_text",lambda *_:pytest.fail("Read credential contents"))
    sdk=SDK(signed_in=True)
    report=await auth.codex_status(home,sdk=sdk)
    assert sdk.calls==["start","account","close"] and sdk.closed
    assert report["authenticated"] is True and report["authentication_mode"]=="chatgpt"
    assert report["model_calls"]==0 and report["cleanup_completed"] is True
    assert "private" not in json.dumps({key:value for key,value in report.items() if key!="home_status"})
    assert str(home) not in json.dumps(report)
    assert "project_doc_max_bytes=0" in sdk.config.config_overrides
    assert "features.plugins=false" in sdk.config.config_overrides


async def test_login_alone_creates_home_and_printable_url_is_not_returned_or_persisted(tmp_path):
    home=tmp_path/"new-home";sdk=SDK();interaction=[]
    report=await auth.codex_login(home,sdk=sdk,on_authorization=interaction.append)
    assert sdk.calls==["start","account","login","wait","account","close"]
    assert interaction==[sdk.url] and report["authenticated"] and report["cleanup_completed"]
    assert not list(home.iterdir()) and "private-state" not in json.dumps(report)
    assert not Path(sdk.config.cwd).exists()


async def test_login_cancellation_cancels_official_attempt_and_closes_owned_client(tmp_path):
    sdk=SDK();sdk.block=True
    task=asyncio.create_task(auth.codex_login(tmp_path/"home",sdk=sdk,on_authorization=lambda _:None))
    await sdk.waiting.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert sdk.calls[-2:]==["cancel","close"] and sdk.closed
    assert not Path(sdk.config.cwd).exists() and not auth._RETAINED_SESSIONS


async def test_untrusted_login_url_is_not_presented_and_has_safe_failure_only(tmp_path):
    sdk=SDK(url="https://untrusted.example/?access_token=private")
    with pytest.raises(ActorError) as error:
        await auth.codex_login(tmp_path/"home",sdk=sdk,on_authorization=lambda _:pytest.fail("Untrusted URL shown"))
    assert error.value.code=="commentary_codex_login_url_invalid" and "private" not in str(error.value)
    assert sdk.closed and sdk.calls[-2:]==["cancel","close"]


@pytest.mark.parametrize("url",[
    "https://auth.openai.com:8443/oauth/authorize",
    "https://auth.openai.com:bad/oauth/authorize",
    "https://auth.openai.com/oauth/authorize#private",
    "https://[invalid/oauth/authorize",
])
def test_login_interaction_url_rejects_unexpected_port_fragment_or_invalid_authority(url):
    with pytest.raises(ActorError,match="login_url_invalid"):auth._interaction_url(url)
    assert auth._interaction_url("https://auth.openai.com:443/oauth/authorize?state=official").startswith("https://auth.openai.com:443/")


async def test_global_instruction_guard_precedes_sdk_start_for_auth_too(tmp_path):
    home=private_home(tmp_path);(home/"AGENTS.md").write_text("private-instruction")
    sdk=SDK()
    with pytest.raises(ActorError,match="global_instructions_present"):await auth.codex_status(home,sdk=sdk)
    assert sdk.calls==[] and not Path(sdk.config.cwd).exists()


async def test_failed_auth_cleanup_retains_workspace_and_exact_owner_for_retry(tmp_path):
    sdk=SDK(signed_in=True);factory=sdk.AsyncCodex
    def broken(config):
        client=factory(config)
        async def close():raise RuntimeError("private-close-prose")
        client.close=close;return client
    sdk.AsyncCodex=broken
    try:
        with pytest.raises(ActorError,match="cleanup_failed"):await auth.codex_status(private_home(tmp_path),sdk=sdk)
        assert len(auth._RETAINED_SESSIONS)==1
        owner=next(iter(auth._RETAINED_SESSIONS))
        assert Path(owner.workspace.name).exists() and not owner.owner.closed
        async def close():sdk.closed=True
        owner.client.close=close
        await auth._close_pending()
        assert not auth._RETAINED_SESSIONS and owner.workspace is None and sdk.closed
    finally:
        for session in tuple(auth._RETAINED_SESSIONS):
            async def close():pass
            session.client.close=close
        await auth._close_pending()
