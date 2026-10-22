#!/usr/bin/env python3
"""Owned headless client + opaque UDP relay. No SSH/model/hidden-state input.

Only an explicitly owned fresh staging room on loopback API:5081 and UDP:7781
is eligible. The relay never parses or stores payload bytes.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import urllib.request

ROOT=Path(__file__).resolve().parents[2]
SCHEMA='actor.social-resume-supervision.v1'
PEER_MAX_DECISIONS=64

def read(path):
    try:
        if path.stat().st_size>4*1024*1024:return {}
        value=json.loads(path.read_text());return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n');temp.replace(path)

def number(value,lo=0,hi=10**13):return type(value) in (int,float) and math.isfinite(value) and lo<=value<=hi

class OpaqueUdpRelay:
    def __init__(self,port,target):
        self.target=(socket.gethostbyname(target[0]),target[1]);self.client=None
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.socket.bind(('127.0.0.1',port))
        # A socket bound to loopback cannot route a packet to the real VPS.
        # Keep the ordinary client local, and let the OS select an external
        # source address on a distinct ephemeral upstream socket.
        self.upstream=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.upstream.bind(('0.0.0.0',0))
        self.port=self.socket.getsockname()[1];self.stop=threading.Event();self.blackhole=threading.Event()
        self.client_packets=self.server_packets=self.dropped_client=self.dropped_server=0
        self.thread=threading.Thread(target=self._run,daemon=True);self.thread.start()
    def _run(self):
        while not self.stop.is_set():
            try:
                for ready in select.select([self.socket,self.upstream],[],[],.05)[0]:
                    data,source=ready.recvfrom(65535)
                    if ready is self.upstream:
                        if source!=self.target:continue
                        self.server_packets+=1
                        if self.blackhole.is_set():self.dropped_server+=1;continue
                        if self.client is not None:self.socket.sendto(data,self.client)
                    elif source[0]=='127.0.0.1':
                        self.client=source;self.client_packets+=1
                        if self.blackhole.is_set():self.dropped_client+=1;continue
                        self.upstream.sendto(data,self.target)
            except OSError:
                if not self.stop.is_set():self.stop.set()
    def close(self):
        self.stop.set();self.thread.join(timeout=1);self.socket.close();self.upstream.close()
    def counts(self):return {name:getattr(self,name) for name in ('client_packets','server_packets','dropped_client','dropped_server')}

def validate_room(value,match):
    return (isinstance(value,dict) and isinstance(value.get('lobbyId'),str) and value['lobbyId'].replace('-','')==match
        and value.get('status')=='Ready' and value.get('gameMode')=='SocialDeduction'
        and value.get('gameVersion')=='0.9.0' and value.get('protocolVersion')=='4'
        and value.get('gamePort')==7781 and type(value.get('currentPlayers')) is int and value['currentPlayers']==0
        and type(value.get('maxPlayers')) is int and 4<=value['maxPlayers']<=6)

def project(client,transport,cleanup,ordinary_clients=False,same_process=False):
    booleans=('credential_memory_ready','connected_resume_rejected','disconnect_observed','same_actor','same_match',
        'same_private_assignment','alive_preserved','higher_generation','hydrated','stale_generation_rejected',
        'own_client_stopped','process_kept_alive','resumed_during_exploration')
    checks={name:client.get(name) is True for name in booleans}
    checks['completed_probe']=client.get('schema')=='actor.social-resume-client.v1' and client.get('completed') is True and client.get('passed') is True
    checks['public_fault_profile']=type(client.get('public_kill_cooldown_ms')) is int and client['public_kill_cooldown_ms']>=120000
    checks['stationary_fresh_samples']=type(client.get('stationary_samples')) is int and client['stationary_samples']>=4 and number(client.get('stationary_seconds'),2,30)
    checks['actual_udp_blackhole']=all(type(transport.get(k)) is int and transport[k]>0 for k in ('dropped_client','dropped_server')) and number(transport.get('blackhole_seconds'),1,60)
    times=[client.get(k) for k in ('ready_at_unix_ms','disconnected_at_unix_ms','resumed_at_unix_ms')]
    checks['ordered_actual_reconnect_times']=all(type(t) is int and t>0 for t in times) and times[0]<=times[1]<times[2]
    checks['ordinary_four_owned_clients']=ordinary_clients is True
    checks['same_owned_process_survived_fault']=same_process is True
    checks['owned_process_cleanup']=cleanup.get('remaining')==0 and type(cleanup.get('remaining')) is int and cleanup.get('started')==10 \
        and type(cleanup.get('remaining_groups')) is int and cleanup['remaining_groups']==0
    return {'schema':SCHEMA,'passed':all(checks.values()),'checks':checks,
        'reconnect_attempts':client.get('reconnect_attempts') if type(client.get('reconnect_attempts')) is int else None,
        'transport':{k:transport.get(k) if number(transport.get(k)) else None for k in ('client_packets','server_packets','dropped_client','dropped_server','blackhole_seconds')},
        'owned_processes_remaining':cleanup.get('remaining') if type(cleanup.get('remaining')) is int else None,
        'scope':'Actual ordinary own Human-mode headless client stays alive across UDP loss, resumes a live own identity and rejects old-generation intent; server-accepted duplicate-connected denial. No GUI/model, ghost-vulnerability kill, post-death resume or full-match claim.',
        'privacy':'No credentials, roles, identities, messages, poses, context, paths, PID or payload bytes.'}

def fetch(api,room):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*_args,**_kwargs):raise ValueError('redirect_not_allowed')
    request=urllib.request.Request(api+'/api/v1/lobbies/'+room)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=5) as response:
        if response.geturl()!=request.full_url:raise ValueError('redirect_not_allowed')
        value=json.load(response)
    return value

def alive(pid):
    try:os.kill(pid,0);return True
    except (ProcessLookupError,PermissionError):return False

def owned_group_member(pid,group):
    try:return type(pid) is int and pid>0 and os.getpgid(pid)==group and os.getsid(pid)==group
    except (ProcessLookupError,PermissionError):return False

def group_alive(group):
    try:os.killpg(group,0);return True
    except (ProcessLookupError,PermissionError):return False

def stop_owned(processes,children,grace_seconds=20):
    """Stop only sessions created by this supervisor, including orphan children.

    Child metadata cannot authorize signalling an unrelated process: both its
    process group and session must match an owned Popen's start_new_session ID.
    """
    for process in processes:
        # The group was created by start_new_session, not supplied in metadata.
        # Signal it even if its launcher exited before writing launch.json.
        try:os.killpg(process.pid,signal.SIGTERM)
        except ProcessLookupError:pass
        except PermissionError:
            # macOS may return EPERM for a just-reaped empty group. An active
            # known member cannot be silently treated as cleaned up.
            if process.poll() is None or any(owned_group_member(pid,process.pid) for pid in children):raise
    deadline=time.monotonic()+grace_seconds
    while time.monotonic()<deadline and (any(process.poll() is None for process in processes)
        or any(owned_group_member(pid,process.pid) for process in processes for pid in children)):
        for process in processes:process.poll()
        time.sleep(.05)
    for process in processes:
        if process.poll() is None or any(owned_group_member(pid,process.pid) for pid in children):
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        try:process.wait(timeout=2)
        except subprocess.TimeoutExpired:pass

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--room',required=True);parser.add_argument('--match-id',required=True)
    parser.add_argument('--api-url',default='http://127.0.0.1:5081');parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--client',type=Path,default=ROOT/'build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena')
    parser.add_argument('--proxy-port',type=int,default=19090);parser.add_argument('--base-port',type=int,default=19100)
    parser.add_argument('--timeout',type=float,default=220)
    args=parser.parse_args();url=urlsplit(args.api_url);room=args.room.upper();match=args.match_id.lower()
    if (url.scheme!='http' or url.hostname!='127.0.0.1' or url.port!=5081 or url.path not in ('','/') or url.query or url.fragment or url.username):parser.error('Only the explicit loopback staging API:5081 is allowed')
    if len(room)!=6 or not room.isascii() or not room.isalnum():parser.error('A six-character owned room is required')
    if len(match)!=32 or any(c not in '0123456789abcdef' for c in match):parser.error('Exact owned match GUID is required')
    if not math.isfinite(args.timeout) or not 90<=args.timeout<=300:parser.error('Timeout must be90–300 seconds')
    if not 1024<=args.proxy_port<=65535 or not 1024<=args.base_port<=65533 or args.proxy_port in range(args.base_port,args.base_port+3):parser.error('Distinct valid local ports required')
    output=args.output.resolve();client=args.client.resolve();api=args.api_url.rstrip('/')
    if output.exists() and any(output.iterdir()):parser.error('A fresh empty output is required')
    if not client.is_file():parser.error('Build the ordinary M8 client first')
    from actor_runtime.config import CognitiveConfig
    from actor_runtime.episodes import TRUSTED_MATCH_OBJECTIVE
    # Validate fixture limits before admission or owned child creation. The
    # per-Brain bound differs from the whole-match participation budget.
    CognitiveConfig(brain={'max_decisions':PEER_MAX_DECISIONS,'objective_timeout':400})
    lobby=fetch(api,room)
    if not validate_room(lobby,match):parser.error('Fresh exact staging Social room/version/UDP/capacity guards failed')
    output.mkdir(parents=True,exist_ok=True)
    relay=OpaqueUdpRelay(args.proxy_port,(lobby['publicHost'],7781));processes=[];logs=[];primary=None;failure='';same_process=False;blackhole_seconds=0
    interrupted=False
    def interrupt(_sig,_frame):raise KeyboardInterrupt
    old_handler=signal.signal(signal.SIGTERM,interrupt)
    try:
        log=(output/'client-stdout.log').open('w');logs.append(log)
        env=dict(os.environ);env.pop('ACTOR_BRIDGE_TOKEN',None)
        primary=subprocess.Popen([str(client),'--auto-room',room,'--player-name','Resume-Player','--control-mode','human',
            '--api-url',api,'--actor-report',str(output/'client.json'),'--social-resume-report',str(output/'resume.json'),
            '--resume-probe-proxy-port',str(args.proxy_port),'-batchmode','-nographics','-screen-fullscreen','0',
            '-screen-width','800','-screen-height','600','-logFile',str(output/'client.log')],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        processes.append(primary);first=time.monotonic()+45
        while fetch(api,room).get('currentPlayers')!=1:
            if primary.poll() is not None or time.monotonic()>first:raise RuntimeError('primary_admission_failed')
            time.sleep(.2)
        for i in range(3):
            folder=output/f'peer-{i+1:02}';log=(output/f'peer-{i+1:02}.log').open('w');logs.append(log)
            command=[sys.executable,str(ROOT/'scripts/launch-agent.py'),'--room',room,'--operation','match','--brain','social-fixture-parity',
                '--objective',TRUSTED_MATCH_OBJECTIVE,'--player-name',f'Resume-Peer{i+1}','--headless-client','--client',str(client),
                '--api-url',api,'--port',str(args.base_port+i),'--max-decisions',str(PEER_MAX_DECISIONS),'--objective-timeout','400','--output',str(folder)]
            p=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True);processes.append(p)
        deadline=time.monotonic()+args.timeout;drop_at=None;drop_stopped=False
        while time.monotonic()<deadline:
            value=read(output/'resume.json')
            if value.get('completed') is True:
                same_process=primary.poll() is None;break
            if primary.poll() is not None or any(p.poll() is not None for p in processes[1:]):raise RuntimeError('owned_participant_exited_early')
            if value.get('stage')=='ready_for_udp_blackhole' and drop_at is None:
                drop_at=time.monotonic();relay.blackhole.set()
            if drop_at is not None and not drop_stopped:
                if value.get('disconnect_observed') is True:
                    relay.blackhole.clear();blackhole_seconds=time.monotonic()-drop_at;drop_stopped=True
                elif time.monotonic()-drop_at>55:raise RuntimeError('actual_transport_disconnect_not_observed')
            time.sleep(.1)
        else:raise RuntimeError('bounded_probe_timeout')
    except KeyboardInterrupt:failure='interrupted';interrupted=True
    except (OSError,ValueError,RuntimeError) as error:failure=str(error) if isinstance(error,RuntimeError) else 'bounded_io_failed'
    finally:
        relay.blackhole.clear();relay.close();signal.signal(signal.SIGTERM,old_handler)
        children=[]
        for i in range(3):
            launch=read(output/f'peer-{i+1:02}'/'launch.json')
            for key in ('runtime_pid','client_pid'):
                pid=launch.get(key)
                if type(pid) is int and pid>0:children.append(pid)
        stop_owned(processes,children)
        cleanup={'started':len(processes)+len(children),'remaining':sum(p.poll() is None for p in processes)+sum(alive(pid) for pid in children),
            'remaining_groups':sum(group_alive(p.pid) for p in processes)}
        for log in logs:log.close()
        write(output/'cleanup.json',cleanup)
    transport=relay.counts();transport['blackhole_seconds']=blackhole_seconds
    result=project(read(output/'resume.json'),transport,cleanup,ordinary_clients=len(processes)==4 and len(children)==6,same_process=same_process)
    if failure:result['passed']=False;result['reason_code']=failure
    write(output/'acceptance.json',result);print(json.dumps({'passed':result['passed'],'checks':result['checks'],'reason_code':failure or None}))
    return 0 if result['passed'] else 130 if interrupted else 1

if __name__=='__main__':raise SystemExit(main())
