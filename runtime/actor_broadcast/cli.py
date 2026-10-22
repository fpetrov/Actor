"""Installed actorctl broadcast commands. Credentials never enter arguments."""
from __future__ import annotations

import argparse
import asyncio
from importlib import metadata
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from actor_runtime.launching import process_identity, owned_process, read_state, write_state

from .coordinator import BroadcastCoordinator, CoordinatorError, reason_code
from .installation import BroadcastInstallation

DEFAULT_CONFIG = Path.home() / ".config/actor/broadcast.json"


def _write_guardian_state(directory, value):
    """Small private diagnostic receipt; its failure never gates cleanup."""
    path=Path(directory)/"guardian.json";pending=path.with_suffix(".partial");created=False
    try:
        raw=(json.dumps(value,allow_nan=False,separators=(",",":"))+"\n").encode()
        if len(raw)>8192:return False
        if path.exists() or path.is_symlink():
            info=path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid() or info.st_nlink!=1 or info.st_mode&0o077:return False
        fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);created=True
        with os.fdopen(fd,"wb")as stream:
            os.fchmod(stream.fileno(),0o600);stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.replace(pending,path);created=False;return True
    except Exception:return False
    finally:
        if created:
            try:pending.unlink()
            except OSError:pass


def _guardian_pending(directory):
    try:
        fd=os.open(Path(directory)/"guardian.json",os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,"rb")as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode)or info.st_uid!=os.geteuid()or info.st_nlink!=1 or info.st_mode&0o077 or info.st_size>8192:return None
            value=json.load(stream)
        if (value.get("schema")!="actor.broadcast-guardian.v1"or value.get("owner_uid")!=os.geteuid()
                or value.get("closed")is not False or not owned_process(value.get("processes",{}).get("guardian"))):return None
        reason=value.get("reason")
        if not isinstance(reason,str)or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",reason):reason="broadcast_guardian_cleanup_pending"
        stages=value.get("cleanup_failures",[])
        if not isinstance(stages,list)or len(stages)>8 or any(x not in {"operation","run","group","services","transport"}for x in stages):stages=[]
        return {"state":"Failed","reason":reason,"guardian":{"lease_held":value.get("lease_held")is True,
            "closed":False,"cleanup_failures":stages}}
    except (OSError,ValueError,TypeError,AttributeError):return None


def add_parser(commands):
    parser = commands.add_parser("broadcast", help="Local observer preview, commentary and explicit public OBS output")
    actions = parser.add_subparsers(dest="broadcast_action", required=True)
    for name in ("ui", "preview", "doctor", "codex-status", "codex-login", "twitch-login"):
        entry = actions.add_parser(name)
        entry.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
        if name in ("ui", "preview"):
            entry.add_argument("--output", type=Path)
            entry.add_argument("--port", type=int, default=0)
        if name == "preview": entry.add_argument("room")
    for name in ("status", "start", "stop", "emergency-stop"):
        entry = actions.add_parser(name)
        entry.add_argument("--run", type=Path, required=True)
        if name == "start":
            entry.add_argument("--session", required=True)
            entry.add_argument("--channel", default="fedorr_codex", choices=("fedorr_codex",))
            entry.add_argument("--confirm-public", action="store_true", required=True)


class OwnedSshForward:
    def __init__(self, settings, log_path):
        self.settings, self.log_path, self.process = settings, Path(log_path), None

    def _listener_owned(self):
        result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-p", str(self.process.pid),
            "-iTCP@127.0.0.1:" + str(self.settings.local_port), "-sTCP:LISTEN", "-Fpn"],
            capture_output=True, text=True, timeout=2, check=False)
        lines = result.stdout.splitlines()
        return result.returncode == 0 and "p" + str(self.process.pid) in lines and any(
            line == "n127.0.0.1:" + str(self.settings.local_port) for line in lines)

    async def start(self):
        s = self.settings
        if not s.identity_file.is_file(): raise CoordinatorError("broadcast_ssh_identity_missing")
        command = ["/usr/bin/ssh", "-N", "-T", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3",
            "-i", str(s.identity_file), "-p", str(s.port), "-L",
            f"127.0.0.1:{s.local_port}:127.0.0.1:{s.remote_port}", f"{s.user}@{s.host}"]
        environment = {k: v for k, v in os.environ.items() if k in
            {"HOME", "PATH", "USER", "LOGNAME", "LANG", "TMPDIR", "SSH_AUTH_SOCK"} or k.startswith("LC_")}
        with self.log_path.open("w") as log:
            os.chmod(self.log_path, 0o600)
            self.process = subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=log)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if self.process.poll() is not None: raise CoordinatorError("broadcast_ssh_forward_failed")
            if await asyncio.to_thread(self._listener_owned): return
            await asyncio.sleep(.1)
        raise CoordinatorError("broadcast_ssh_forward_timeout")

    async def aclose(self):
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try: await asyncio.wait_for(asyncio.to_thread(process.wait), 4)
            except TimeoutError: process.kill(); await asyncio.to_thread(process.wait)


def _doctor(config):
    from actor_runtime.launching import profiles, resolve_client
    from actor_codex.commentary_auth import commentary_home_status
    result = {"configuration": "valid", "game_version": config.game_version, "channel": config.channel,
        "provider": "not_checked", "authentication": "not_checked", "model_calls": 0,
        "tts_python_present": config.tts_python.is_file(), "tts_model_present": config.tts_model_path.is_dir(),
        "chat_client_registered": bool(config.twitch_client_id), "chat_enabled": config.chat_enabled,
        "ffmpeg_present": config.ffmpeg_executable.is_file(), "client_verified": False,
        "codex_home_status": commentary_home_status(config.codex_home)}
    try:
        _, info = resolve_client(profiles()[config.game_id], manifest=config.client_manifest)
        result["client_verified"] = info["integrity"] == "sha256_verified"
    except Exception as error: result["client_reason"] = reason_code(error, "broadcast_client_invalid")
    for package, key in (("openai-codex", "codex_sdk_version"), ("websockets", "websockets_version")):
        try: result[key] = metadata.version(package)
        except metadata.PackageNotFoundError: result[key] = None
    return result


async def _serve(args, config, *, guardian=None):
    from .native import NativeBroadcastFactory
    from .operator import OperatorServer
    from .secrets import MacOSKeychainSecretStore
    root = config.state_directory
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = (args.output or root / (time.strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(4).hex())).expanduser().absolute()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.is_symlink() or (output / "launch.json").exists(): raise CoordinatorError("broadcast_launch_exists")
    state = {"schema": "actor.launch.v1", "owner_uid": os.getuid(), "game": config.game_id,
        "brain": "broadcast", "state": "starting", "processes": {"launcher": process_identity(os.getpid())}}
    write_state(output, state)
    forward = OwnedSshForward(config.ssh, output / "ssh.log") if config.ssh is not None and guardian is None else None
    controller = server = factory = None
    startup_failure = None
    stopping = asyncio.Event(); loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(signum, stopping.set)
    try:
        if forward is not None:
            await forward.start(); state["processes"]["ssh"] = process_identity(forward.process.pid)
        store = MacOSKeychainSecretStore()
        factory = NativeBroadcastFactory(config, store, guardian=guardian) if guardian is not None else NativeBroadcastFactory(config, store)
        controller = BroadcastCoordinator(factory, channel=config.channel, config=config.commentary, chat_enabled=config.chat_enabled)
        server = OperatorServer(controller, secret_store=store, port=args.port)
        await server.start()
        state.update(state="running", operator_port=server.port)
        write_state(output, state)
        print(json.dumps({"operator_url": server.url, "run": str(output), "publication": "explicit_start_required"}), flush=True)
        if args.broadcast_action == "preview":
            await controller.execute("preview", {"room_code": args.room.upper()})
        await stopping.wait()
        return 0
    except BaseException as error:
        startup_failure = reason_code(error, "broadcast_interrupted" if isinstance(error, asyncio.CancelledError) else "broadcast_startup_failed")
        raise
    finally:
        # Stop the owned public output while the bridge and SSH transport still
        # exist. Closing the local operator page never disconnects a foreign OBS.
        cleanup_failed = False
        operations = [(server, "aclose", 6), (controller, "aclose", 80), (factory, "aclose", 35), (forward, "aclose", 8)]
        for resource, method, timeout in operations:
            if resource is None or not hasattr(resource, method): continue
            try: await asyncio.wait_for(getattr(resource, method)(), timeout)
            except BaseException: cleanup_failed = True
        state["state"] = "failed" if cleanup_failed or startup_failure else "stopped"
        if cleanup_failed: state["reason"] = "broadcast_cleanup_failed"
        elif startup_failure: state["reason"] = startup_failure
        write_state(output, state)
        for signum in (signal.SIGINT, signal.SIGTERM): loop.remove_signal_handler(signum)
        if cleanup_failed: raise CoordinatorError("broadcast_cleanup_failed")


async def _supervised_serve(args, config):
    """The public CLI's surviving owner; no native/SDK work in its child pre-hello."""
    from .guardian_services import GuardianServices, controller_group_members
    from .native import private_directory
    from .secrets import MacOSKeychainSecretStore
    from .supervisor import FramedSocket, GuardianHost, RetainedControllerGroup
    output = (args.output or config.state_directory / (time.strftime("%Y%m%dT%H%M%S") + "-" + os.urandom(4).hex())).expanduser().absolute()
    private_directory(output)
    if (output / "launch.json").exists() or (output / "guardian.json").exists():
        raise CoordinatorError("broadcast_launch_exists")
    forward = OwnedSshForward(config.ssh, output / "ssh.log") if config.ssh is not None else None
    services = GuardianServices(config, MacOSKeychainSecretStore(), forward=forward)
    parent, child = socket.socketpair(); process = group = host = transport = None
    receipts=None;receipt_stop=asyncio.Event();receipt_failed=False;identities={"guardian":None,"controller":None}
    async def receipt():
        nonlocal receipt_failed
        document={"schema":"actor.broadcast-guardian.v1","owner_uid":os.geteuid(),"processes":identities,
            "identity_complete":all(x is not None for x in identities.values()),
            "terminal":host is not None and host.terminal,"reason":host.reason if host is not None else None,
            "closed":host is not None and host.closed,
            "lease_held":host is not None and getattr(host,"lease",None)is not None,
            "cleanup_failures":list(host.cleanup_failures)if host is not None else []}
        if not await asyncio.to_thread(_write_guardian_state,output,document):
            receipt_failed=True
            if host is not None and not host.closed:host.fail("supervisor_receipt_failed")
    async def receipt_loop():
        while not receipt_stop.is_set():
            await receipt()
            try:await asyncio.wait_for(receipt_stop.wait(),.5)
            except TimeoutError:pass
    loop = asyncio.get_running_loop()
    stop_requested = False
    def stop_owner():
        nonlocal stop_requested
        stop_requested = True
        if host is not None: host.fail("supervisor_operator_stop")
    # Signal callbacks run on the loop, after the synchronous Popen->group
    # registration, never in that boundary. Install before any owned spawn.
    for signum in (signal.SIGINT, signal.SIGTERM): loop.add_signal_handler(signum, stop_owner)
    try:
        await services.start_forward()
        if stop_requested: return 130
        allowed = {"HOME", "USER", "LOGNAME", "PATH", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
        environment = {key: value for key, value in os.environ.items() if key in allowed}
        environment["ACTOR_BROADCAST_GUARDIAN_FD"] = str(child.fileno())
        command = [sys.executable, "-m", "actor_broadcast.guardian_entry", "--config", str(args.config.expanduser().absolute()),
            "--output", str(output), "--port", str(args.port), "--action", args.broadcast_action]
        if args.broadcast_action == "preview": command.extend(("--room", args.room))
        # Popen returns only after setsid. No await, log close, diagnostic write
        # or signal handler can run before this exact new group is registered.
        process = subprocess.Popen(command, env=environment, pass_fds=(child.fileno(),), start_new_session=True)
        child.close(); environment.clear()
        group = RetainedControllerGroup(process, group_members=controller_group_members)
        host = GuardianHost(config.channel, group, services)
        transport = FramedSocket(parent)
        identities["guardian"]=await asyncio.to_thread(process_identity,os.getpid())
        identities["controller"]=await asyncio.to_thread(process_identity,process.pid)
        if not all(identities.values()):
            receipt_failed=True;host.fail("supervisor_identity_unverified")
        receipts=asyncio.create_task(receipt_loop(),name="broadcast-guardian-receipts")
        if stop_requested: host.fail("supervisor_operator_stop")
        closed = await host.serve(transport)
        # An unresolved transaction/resource must keep the actual lease
        # owner alive. Retrying retained cleanup cannot create a new run.
        while not closed:
            await asyncio.sleep(1)
            closed = await host.retry_cleanup()
        return 0 if host.reason in ("supervisor_shutdown", "supervisor_operator_stop") else 1
    finally:
        child.close()
        if host is not None:
            if not host.closed:
                # Cancellation of an outer waiter does not cancel the retained
                # host cleanup. Keep this coroutine alive until proof succeeds.
                while not await host.retry_cleanup(): await asyncio.sleep(1)
        elif group is not None:
            await group.terminate(); await services.close()
        else:
            # The child has not passed its handshake and cannot start native
            # resources. Only its exact returned handle is eligible here.
            if process is not None:
                process.terminate()
                try: await asyncio.to_thread(process.wait, 2)
                except subprocess.TimeoutExpired: process.kill(); await asyncio.to_thread(process.wait)
            await services.close()
        if transport is not None: await transport.aclose()
        else: parent.close()
        receipt_stop.set()
        if receipts is not None:await asyncio.shield(receipts)
        if host is not None:await receipt()
        for signum in (signal.SIGINT, signal.SIGTERM): loop.remove_signal_handler(signum)
        if receipt_failed:raise CoordinatorError("broadcast_guardian_receipt_failed")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): raise CoordinatorError("operator_redirect_rejected")


def _control(args):
    from .secrets import MacOSKeychainSecretStore
    try:state = read_state(args.run)
    except Exception:
        guardian=_guardian_pending(args.run)
        if guardian is not None and args.broadcast_action=="status":print(json.dumps(guardian,indent=2));return 0
        raise
    if state.get("brain") != "broadcast" or not owned_process(state.get("processes", {}).get("launcher")):
        guardian=_guardian_pending(args.run)
        if guardian is not None:
            if args.broadcast_action=="status":print(json.dumps(guardian,indent=2));return 0
            raise CoordinatorError("broadcast_guardian_cleanup_pending")
        raise CoordinatorError("broadcast_controller_not_running")
    port = state.get("operator_port")
    if type(port) is not int or not 1024 <= port <= 65535: raise CoordinatorError("broadcast_operator_port_invalid")
    secret = MacOSKeychainSecretStore().get("operator-http")
    if secret is None: raise CoordinatorError("broadcast_operator_secret_missing")
    action = args.broadcast_action
    if action == "status": method, path, body = "GET", "/api/status", None
    else:
        payload = {"channel": args.channel, "broadcast_session_id": args.session,
                   "confirmation": "START PUBLIC STREAM"} if action == "start" else {}
        method, path = "POST", "/api/action"
        body = json.dumps({"action": {"start": "public_start", "stop": "stop", "emergency-stop": "emergency_stop"}[action],
                           "payload": payload}).encode()
    headers = {"Authorization": "Bearer " + secret, "Accept": "application/json"}
    if body is not None: headers["Content-Type"] = "application/json"
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(Request(f"http://127.0.0.1:{port}" + path, data=body, headers=headers, method=method), timeout=10) as response:
            raw = response.read(16385)
            if len(raw) > 16384: raise CoordinatorError("operator_response_limit")
            value = json.loads(raw)
            if action == "status":
                from .operator import public_status
                value = public_status(value)
            else:
                value = {key: value[key] for key in ("accepted", "state") if key in value}
            print(json.dumps(value, indent=2, ensure_ascii=False)); return 0
    except (HTTPError, URLError): raise CoordinatorError("broadcast_operator_request_failed") from None


def command(args):
    try:
        if args.broadcast_action in ("status", "start", "stop", "emergency-stop"): return _control(args)
        config = BroadcastInstallation.load(args.config)
        if args.broadcast_action == "doctor": print(json.dumps(_doctor(config), indent=2)); return 0
        if args.broadcast_action in ("codex-status", "codex-login"):
            from actor_codex.commentary_auth import codex_login, codex_status
            operation=codex_login if args.broadcast_action=="codex-login" else codex_status
            report=asyncio.run(operation(config.codex_home))
            print(json.dumps(report,indent=2));return 0 if report["authenticated"] else 1
        if args.broadcast_action == "twitch-login":
            from .twitch_login import twitch_login
            report=asyncio.run(twitch_login(config.twitch_client_id,expected_login=config.channel))
            print(json.dumps(report,indent=2));return 0
        if not 0 <= args.port <= 65535 or 0 < args.port < 1024: raise CoordinatorError("operator_port_invalid")
        return asyncio.run(_supervised_serve(args, config))
    except KeyboardInterrupt: return 130
    except Exception as error:
        print(json.dumps({"code": reason_code(error, "broadcast_configuration_invalid")})); return 1
