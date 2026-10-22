"""Installed actorctl onboarding and local supervision; no repository required."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

from .errors import ActorError
from .launching import profiles,resolve_client,api_url,process_identity,owned_process,write_state,read_state


def installed(name):
    try:return metadata.version(name)
    except metadata.PackageNotFoundError:return None


def doctor(args):
    games=profiles()
    game=args.game or (next(iter(games)) if len(games)==1 else None)
    client={"available":False,"reason":"select_game"}
    if game in games:
        try:
            path,client=resolve_client(games[game],client=args.client,manifest=args.client_manifest)
            client.update(available=True,executable=str(path))
        except (ActorError,ValueError,OSError) as error:
            client={"available":False,"reason":error.code if isinstance(error,ActorError) else "client_manifest_invalid"}
    result={"runtime_version":installed("actor-runtime"),"python_supported":sys.version_info>=(3,12),
        "game_profiles":sorted(games),"brains":sorted(point.name for point in metadata.entry_points(group="actor.brains")),
        "optional_providers":{"codex_sdk":installed("openai-codex"),"jev_sdk":installed("typesafe-sdk")},
        "authentication":"not_checked","client":client,
        "process_control":"posix_identity_guard" if os.name=="posix" else "foreground_owned_children_only",
        "next_steps":["actorctl codex status","actorctl join ROOMCODE --brain codex --client-manifest PATH"]}
    print(json.dumps(result,indent=2));return 0


def status(args):
    state=read_state(args.run)
    print(json.dumps({"state":state.get("state"),"brain":state.get("brain"),"game":state.get("game"),
        "bridge_port":state.get("bridge_port"),"processes":{name:owned_process(identity)
            for name,identity in state.get("processes",{}).items()},"reason":state.get("reason")},indent=2))
    return 0


def stop(args):
    state=read_state(args.run)
    identities=state.get("processes",{})
    launcher=identities.get("launcher")
    if owned_process(launcher):
        os.kill(launcher["pid"],signal.SIGTERM)
        print(json.dumps({"stop_requested":True,"scope":"owned_launcher","cleanup":"awaiting_supervisor"}));return 0
    live=[identity for name,identity in identities.items() if name!="launcher" and owned_process(identity)]
    for identity in live:os.kill(identity["pid"],signal.SIGTERM)
    print(json.dumps({"stop_requested":bool(live),"scope":"exact_owned_children","already_stopped":not live}));return 0


def join(args):
    games=profiles()
    name=args.game or (next(iter(games)) if len(games)==1 else None)
    if name not in games:raise ActorError("game_profile_unavailable","Choose an installed --game shown by actorctl doctor")
    profile=games[name]
    brain=args.brain or profile.default_brain
    if len(metadata.entry_points(group="actor.brains",name=brain))!=1:
        raise ActorError("brain_unavailable","Choose one installed Brain shown by actorctl doctor")
    if brain=="codex" and installed("openai-codex") is None:
        raise ActorError("codex_sdk_missing","Install the codex extra for this verified Runtime wheel, then actorctl codex login")
    if not args.room.isascii() or len(args.room)!=6 or not args.room.isalnum():
        raise ActorError("room_invalid","Room codes contain six ASCII letters/digits")
    if not 0<=args.port<=65535 or args.port and args.port<1024:
        raise ActorError("port_invalid","Local bridge port must be zero (automatic) or 1024–65535")
    client,integrity=resolve_client(profile,client=args.client,manifest=args.client_manifest,expected_sha256=args.client_sha256)
    url=api_url(args.api_url)
    from .config import CognitiveConfig
    CognitiveConfig.load(args.config)
    from .episodes import TRUSTED_MATCH_OBJECTIVE
    objective=args.objective or TRUSTED_MATCH_OBJECTIVE
    if not objective.strip() or len(objective)>4096:raise ActorError("objective_invalid","Objective must contain 1–4096 characters")
    if not 1<=len(args.player_name.strip())<=64:raise ActorError("player_name_invalid","Player name must contain 1–64 characters")
    with socket.socket() as probe:
        try:probe.bind(("127.0.0.1",args.port))
        except PermissionError as error:raise ActorError("bridge_permission_denied","The OS policy denied this loopback socket") from error
        except OSError as error:raise ActorError("port_unavailable","Local bridge port is occupied") from error
        port=probe.getsockname()[1]
    output=(args.output or Path.home()/".local/state/actorctl"/(time.strftime("%Y%m%dT%H%M%S")+"-"+secrets.token_hex(4))).expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (output/"launch.json").exists():raise ActorError("launch_exists","Choose a fresh output directory; existing ownership records are not overwritten")
    environment=dict(os.environ,ACTOR_BRIDGE_TOKEN=secrets.token_hex(32))
    state={"schema":"actor.launch.v1","owner_uid":getattr(os,"getuid",lambda:None)(),"game":name,"brain":brain,
        "bridge_port":port,"state":"starting","reason":None,"client_integrity":integrity,
        "processes":{"launcher":process_identity(os.getpid())}}
    write_state(output,state)
    runtime=[sys.executable,"-m","actor_runtime",args.operation,"--brain",brain,"--objective",objective,
        "--port",str(port),"--report",str(output/"runtime.json"),"--exit-on-complete","--memory",args.memory]
    if args.operation=="match":runtime += ["--match-policy",args.match_policy or profile.match_policy]
    if args.config:runtime += ["--config",str(args.config.resolve())]
    if args.model:runtime += ["--model",args.model]
    processes=[]
    previous_handlers={}
    def interrupted(*_):raise KeyboardInterrupt
    for signum in (signal.SIGINT,signal.SIGTERM):
        previous_handlers[signum]=signal.signal(signum,interrupted)
    result=1
    try:
        client_arguments=profile.client_arguments(room=args.room.upper(),player_name=args.player_name,
            endpoint=f"http://127.0.0.1:{port}",report=output/"client.json",log=output/"client.log",headless=args.headless,api_url=url)
        if (not isinstance(client_arguments,(tuple,list)) or len(client_arguments)>128 or
                any(not isinstance(value,str) or "\x00" in value or len(value)>8192 for value in client_arguments)):
            raise ActorError("game_profile_invalid","Installed game profile returned an invalid client argument vector")
        with (output/"runtime.log").open("w") as log:
            server=subprocess.Popen(runtime,env=environment,stdout=log,stderr=subprocess.STDOUT)
        processes.append(server);state["processes"]["runtime"]=process_identity(server.pid)
        write_state(output,state)
        deadline=time.monotonic()+20
        while True:
            if server.poll() is not None:raise ActorError("runtime_start_failed","Runtime did not start; inspect this run's runtime.log")
            try:
                with socket.create_connection(("127.0.0.1",port),timeout=.2):break
            except OSError:
                if time.monotonic()>=deadline:raise ActorError("runtime_start_timeout","Runtime did not open its loopback endpoint")
                time.sleep(.1)
        command=[str(client),*client_arguments]
        with (output/"client-stdout.log").open("w") as log:
            game=subprocess.Popen(command,env=environment,stdout=log,stderr=subprocess.STDOUT)
        processes.append(game);state["processes"]["client"]=process_identity(game.pid)
        state["state"]="running";write_state(output,state)
        print(f"Joining {args.room.upper()} with {brain}. Diagnostics: {output}",flush=True)
        print("Ctrl-C stops this owned Runtime and client. actorctl status --run PATH checks them.",flush=True)
        while server.poll() is None:
            if game.poll() is not None:raise ActorError("client_exited","Game client exited before Runtime completion")
            time.sleep(.2)
        if server.returncode:raise ActorError("participation_failed","Runtime stopped safely; inspect runtime.json for its bounded result")
        result=0;state["state"]="completed"
        if args.keep_client_open and not args.headless:
            write_state(output,state)
            print("Round finished; the client remains open until you close it or press Ctrl-C.",flush=True)
            game.wait()
    except KeyboardInterrupt:
        result=130;state.update(state="interrupted",reason="user_stop")
    except ActorError as error:
        state.update(state="failed",reason=error.code)
        print(json.dumps(error.as_dict()),file=sys.stderr)
    except (OSError,ValueError) as error:
        state.update(state="failed",reason="local_launch_failed")
        print(json.dumps({"code":"local_launch_failed","exception_type":type(error).__name__}),file=sys.stderr)
    finally:
        # A repeated signal must not skip the second child's cleanup.
        for signum in previous_handlers:signal.signal(signum,signal.SIG_IGN)
        try:
            for index,process in enumerate(processes):
                try:
                    if process.poll() is None:
                        process.terminate()
                        try:process.wait(timeout=16 if index==0 else 4)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=4)
                except (OSError,subprocess.TimeoutExpired):
                    state.setdefault("cleanup_errors",[]).append("owned_process_stop_failed")
            state["cleanup_complete"]=all(process.poll() is not None for process in processes)
            if not state["cleanup_complete"]:result=1;state.update(state="failed",reason="cleanup_failed")
            write_state(output,state)
        finally:
            for signum,handler in previous_handlers.items():signal.signal(signum,handler)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(prog="actorctl",description="Run an installed ACTOR game client and Brain over a private loopback bridge.")
    parser.add_argument("--version",action="version",version=installed("actor-runtime") or "unknown")
    commands=parser.add_subparsers(dest="command",required=True)
    doctor_parser=commands.add_parser("doctor",help="Check installed packages/client without authentication or model calls")
    for entry in (doctor_parser,):
        entry.add_argument("--game");entry.add_argument("--client",type=Path);entry.add_argument("--client-manifest",type=Path)
    join_parser=commands.add_parser("join",help="Join with an ordinary game client and an installed Brain")
    join_parser.add_argument("room");join_parser.add_argument("--game");join_parser.add_argument("--brain")
    join_parser.add_argument("--operation",choices=("match","agent"),default="match")
    join_parser.add_argument("--match-policy");join_parser.add_argument("--objective");join_parser.add_argument("--model")
    join_parser.add_argument("--config",type=Path);join_parser.add_argument("--client",type=Path)
    join_parser.add_argument("--client-manifest",type=Path);join_parser.add_argument("--client-sha256")
    join_parser.add_argument("--player-name",default="Agent Pilot");join_parser.add_argument("--port",type=int,default=0)
    join_parser.add_argument("--headless",action="store_true");join_parser.add_argument("--keep-client-open",action="store_true")
    join_parser.add_argument("--output",type=Path);join_parser.add_argument("--api-url")
    join_parser.add_argument("--memory",choices=("none","sqlite"),default="none")
    for action in ("status","stop"):
        entry=commands.add_parser(action,help="Inspect or stop exact processes owned by one launch record")
        entry.add_argument("--run",type=Path,required=True)
    auth=commands.add_parser("codex",help="Official SDK account status/login; no game or model is started")
    auth.add_argument("action",choices=("status","login"))
    from actor_broadcast.cli import add_parser as add_broadcast_parser
    add_broadcast_parser(commands)
    args=parser.parse_args(argv)
    try:
        if args.command=="broadcast":
            from actor_broadcast.cli import command as broadcast_command
            return broadcast_command(args)
        if args.command=="codex":
            import asyncio
            from actor_codex.auth import command
            return asyncio.run(command(args.action))
        return {"doctor":doctor,"join":join,"status":status,"stop":stop}[args.command](args)
    except ActorError as error:
        print(json.dumps(error.as_dict()),file=sys.stderr);return 1
    except (OSError,ValueError) as error:
        print(json.dumps({"code":"local_configuration_invalid","exception_type":type(error).__name__}),file=sys.stderr);return 1


if __name__=="__main__":raise SystemExit(main())
