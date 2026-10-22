"""Explicit official sign-in for the commentator's separate Codex home.

Importing this module performs no SDK, credential, directory or account action.
Only login may create the selected home. No auth file is read, copied or parsed;
the official SDK exclusively owns credential persistence and browser login.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import stat
import tempfile
import unicodedata
from urllib.parse import urlsplit

from actor_runtime.errors import ActorError
from .launch import filtered_environment

SDK_VERSION="0.162.1"
_RETAINED_SESSIONS=set()


def _fail(code):
    return ActorError(code,"Separate commentator Codex sign-in is required; use actorctl broadcast codex-login")


def _shared_homes():
    homes={Path.home()/".codex"}
    inherited=os.environ.get("CODEX_HOME")
    if inherited and Path(inherited).is_absolute():homes.add(Path(inherited))
    return homes


def _home_path(home):
    try:
        path=Path(home).expanduser()
        if not path.is_absolute() or ".." in path.parts or len(os.fsencode(path))>4096:raise ValueError
        # Never log into or modify the default/inherited player Codex home.
        def key(value):return unicodedata.normalize("NFC",str(value.resolve(strict=False))).casefold()
        if key(path) in {key(value) for value in _shared_homes()}:raise ValueError
        return path
    except (TypeError,ValueError,OSError,RuntimeError):
        raise _fail("commentary_codex_home_invalid") from None


def _safe_ancestor(info):
    sticky_root=info.st_uid==0 and bool(info.st_mode&stat.S_ISVTX)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0,os.geteuid())
            or info.st_mode&0o022 and not sticky_root):
        raise _fail("commentary_codex_home_invalid")


def _validate_home(home,*,create=False):
    path=_home_path(home)
    descriptor=None
    try:
        flags=os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC
        descriptor=os.open(path.anchor,flags)
        _safe_ancestor(os.fstat(descriptor))
        for part in path.parts[1:]:
            try:child=os.open(part,flags,dir_fd=descriptor)
            except FileNotFoundError:
                if not create:raise _fail("commentary_codex_signin_required") from None
                try:os.mkdir(part,0o700,dir_fd=descriptor)
                except FileExistsError:pass
                child=os.open(part,flags,dir_fd=descriptor)
            try:_safe_ancestor(os.fstat(child))
            except BaseException:
                os.close(child);raise
            os.close(descriptor);descriptor=child
        info=os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise _fail("commentary_codex_home_invalid")
        # macOS may resolve different casing to the same directory; the main
        # CODEX_HOME may itself be a symlink. Follow only these known directory
        # metadata paths and compare identity with the opened target descriptor.
        for shared in _shared_homes():
            try:other=os.stat(shared)
            except FileNotFoundError:continue
            if not stat.S_ISDIR(other.st_mode) or (info.st_dev,info.st_ino)==(other.st_dev,other.st_ino):
                raise _fail("commentary_codex_home_invalid")
        return path
    except ActorError:raise
    except (OSError,ValueError):raise _fail("commentary_codex_home_invalid") from None
    finally:
        if descriptor is not None:os.close(descriptor)


def commentary_environment(home,*,create=False):
    """Filtered child env, with one validated explicit non-player CODEX_HOME."""
    if type(create)is not bool:raise _fail("commentary_codex_home_invalid")
    path=_validate_home(home,create=create)
    return {**filtered_environment(),"CODEX_HOME":str(path)}


def commentary_home_status(home):
    """Directory metadata only; private means no claim about sign-in state."""
    try:_validate_home(home)
    except ActorError as error:
        return "absent" if error.code=="commentary_codex_signin_required" else "invalid"
    return "private"


class _Session:
    def __init__(self,home,*,sdk=None,create=False):
        self.environment=commentary_environment(home,create=create)
        self.sdk=sdk;self.workspace=self.client=self.owner=None
        self.cleanup_task=None

    async def start(self):
        from .policy import PolicyPreflight,RESTRICTIVE_OVERRIDES,_guard_instruction_home
        from .launch import codex_launch_args
        official=self.sdk is None
        if official:
            from .adapter import sdk_module
            self.sdk=await asyncio.to_thread(sdk_module)
        self.workspace=tempfile.TemporaryDirectory(prefix="actor-commentary-auth-")
        config=self.sdk.CodexConfig(cwd=self.workspace.name,env=self.environment,client_name="actor_broadcast_auth",
            client_title="ACTOR Broadcast Sign-in",client_version="1",config_overrides=RESTRICTIVE_OVERRIDES)
        _guard_instruction_home(config)
        if official:config.launch_args_override=codex_launch_args(config)
        self.client=self.sdk.AsyncCodex(config)
        client=self.client
        class PublicSession:
            async def start(self):await client.__aenter__()
            async def close(self):await client.close()
        self.owner=PolicyPreflight()
        self.owner.register(PublicSession())
        await self.owner.start()

    async def _cleanup(self):
        try:
            if self.owner is not None:await self.owner.close()
            if self.workspace is not None:
                self.workspace.cleanup();self.workspace=None
            self.client=None
        except BaseException:
            _RETAINED_SESSIONS.add(self)
            raise
        _RETAINED_SESSIONS.discard(self)

    async def close(self):
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task=asyncio.create_task(self._cleanup())
            self.cleanup_task.add_done_callback(lambda task:None if task.cancelled() else task.exception())
        await asyncio.shield(self.cleanup_task)


async def _close_pending():
    for session in tuple(_RETAINED_SESSIONS):await session.close()


def _account_report(account):
    entry=getattr(account,"account",None)
    kind=getattr(getattr(entry,"root",None),"type",None)
    authenticated=kind=="chatgpt"
    return {"sdk_version":SDK_VERSION,"home_status":"private","authenticated":authenticated,
        "authentication_mode":"chatgpt" if authenticated else "unsupported" if entry is not None else None,
        "signin_required":not authenticated,"model_calls":0,"cleanup_completed":False}


def _interaction_url(value):
    if not isinstance(value,str) or not 1<=len(value)<=8192 or any(ord(c)<32 or ord(c)==127 for c in value):
        raise _fail("commentary_codex_login_url_invalid")
    try:
        parsed=urlsplit(value)
        valid=(parsed.scheme=="https" and parsed.hostname in ("auth.openai.com","auth0.openai.com")
            and parsed.port in (None,443) and not parsed.fragment and not parsed.username and not parsed.password)
    except ValueError:valid=False
    if not valid:
        raise _fail("commentary_codex_login_url_invalid")
    return value


async def _authenticate(home,*,login=False,sdk=None,on_authorization=None,timeout_seconds=180):
    if type(timeout_seconds)not in (int,float) or not 1<=timeout_seconds<=300:
        raise _fail("commentary_codex_timeout_invalid")
    await _close_pending()
    session=_Session(home,sdk=sdk,create=login)
    handle=None;completed=False
    try:
        async with asyncio.timeout(timeout_seconds):
            await session.start()
            account=await session.client.account(refresh_token=False)
            report=_account_report(account)
            if login and not report["authenticated"]:
                handle=await session.client.login_chatgpt()
                url=_interaction_url(handle.auth_url)
                callback=on_authorization or (lambda value:print("Open this official ChatGPT sign-in URL:\n"+value,flush=True))
                callback(url)
                result=await handle.wait()
                if getattr(result,"success",None)is not True:raise _fail("commentary_codex_login_failed")
                completed=True
                report=_account_report(await session.client.account(refresh_token=False))
                if not report["authenticated"]:raise _fail("commentary_codex_signin_required")
    except asyncio.CancelledError:raise
    except ActorError:raise
    except TimeoutError:raise _fail("commentary_codex_login_timeout" if login else "commentary_codex_status_timeout") from None
    except Exception:raise _fail("commentary_codex_login_failed" if login else "commentary_codex_status_failed") from None
    finally:
        try:
            if handle is not None and not completed:
                try:
                    async with asyncio.timeout(5):await handle.cancel()
                except Exception:pass
        finally:await session.close()
    report["cleanup_completed"]=True
    return report


async def codex_status(home,*,sdk=None):
    """Missing home returns before importing an SDK or reading authentication."""
    status=commentary_home_status(home)
    if status=="absent":
        return {"sdk_version":SDK_VERSION,"home_status":"absent","authenticated":False,
            "authentication_mode":None,"signin_required":True,"model_calls":0,"cleanup_completed":True}
    if status!="private":raise _fail("commentary_codex_home_invalid")
    return await _authenticate(home,sdk=sdk,timeout_seconds=30)


async def codex_login(home,*,sdk=None,on_authorization=None,timeout_seconds=180):
    return await _authenticate(home,login=True,sdk=sdk,on_authorization=on_authorization,timeout_seconds=timeout_seconds)
