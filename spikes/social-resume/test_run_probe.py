"""Auth-free relay, strict acceptance and owned cleanup regressions."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

SPEC=importlib.util.spec_from_file_location('resume_probe',Path(__file__).with_name('run_probe.py'))
probe=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(probe)


def fixture():
    value={'schema':'actor.social-resume-client.v1','completed':True,'passed':True,
        'credential_memory_ready':True,'connected_resume_rejected':True,'disconnect_observed':True,
        'same_actor':True,'same_match':True,'same_private_assignment':True,'alive_preserved':True,
        'higher_generation':True,'hydrated':True,'stale_generation_rejected':True,'own_client_stopped':True,
        'process_kept_alive':True,'resumed_during_exploration':True,'stationary_samples':120,
        'stationary_seconds':2.01,'public_kill_cooldown_ms':120000,'reconnect_attempts':1,
        'ready_at_unix_ms':100000,'disconnected_at_unix_ms':130000,'resumed_at_unix_ms':132000}
    transport={'client_packets':200,'server_packets':300,'dropped_client':90,'dropped_server':80,'blackhole_seconds':30}
    cleanup={'started':10,'remaining':0,'remaining_groups':0}
    return value,transport,cleanup


class ProjectionTests(unittest.TestCase):
    def project(self,client=None,transport=None,cleanup=None,**kwargs):
        defaults=fixture()
        return probe.project(client if client is not None else defaults[0],transport if transport is not None else defaults[1],
            cleanup if cleanup is not None else defaults[2],ordinary_clients=kwargs.get('ordinary_clients',True),same_process=kwargs.get('same_process',True))
    def test_complete_live_proof_passes_without_exporting_private_input(self):
        client,transport,cleanup=fixture();client.update(actor_id='PRIVATE-ACTOR',alignment='PRIVATE-ROLE',credential='PRIVATE-TOKEN',position='PRIVATE-POSE')
        result=self.project(client,transport,cleanup);self.assertTrue(result['passed'])
        encoded=json.dumps(result)
        for forbidden in ('PRIVATE-ACTOR','PRIVATE-ROLE','PRIVATE-TOKEN','PRIVATE-POSE','actor_id','alignment','credential='):
            self.assertNotIn(forbidden,encoded)
        self.assertIn('No GUI/model',result['scope'])
    def test_each_required_claim_fails_closed_when_absent_false_or_truthy_string(self):
        client,_,_=fixture()
        keys=[key for key,value in client.items() if type(value) is bool]
        for key in keys:
            for invalid in (None,False,1,'true'):
                with self.subTest(key=key,invalid=invalid):
                    value=copy.deepcopy(client);value[key]=invalid;self.assertFalse(self.project(value)['passed'])
    def test_postgame_handoff_is_not_live_resume_proof(self):
        value,_,_=fixture();value['resumed_during_exploration']=False
        self.assertFalse(self.project(value)['passed'])
    def test_stationary_old_single_sample_or_nonfinite_duration_rejected(self):
        for key,invalid in (('stationary_samples',True),('stationary_samples',3),('stationary_seconds',1.99),('stationary_seconds',float('nan')),('stationary_seconds',float('inf'))):
            with self.subTest(key=key,invalid=invalid):
                value,_,_=fixture();value[key]=invalid;self.assertFalse(self.project(value)['passed'])
    def test_udp_fault_requires_actual_loss_in_both_directions_and_bounded_duration(self):
        for key,invalid in (('dropped_client',0),('dropped_server',0),('dropped_client',True),('blackhole_seconds',0),('blackhole_seconds',61)):
            with self.subTest(key=key,invalid=invalid):
                _,transport,_=fixture();transport[key]=invalid;self.assertFalse(self.project(transport=transport)['passed'])
    def test_time_order_cannot_be_fabricated_from_only_a_resume_timestamp(self):
        for times in ((0,2,3),(3,2,4),(1,3,3),(1,2,True)):
            value,_,_=fixture()
            for key,t in zip(('ready_at_unix_ms','disconnected_at_unix_ms','resumed_at_unix_ms'),times):value[key]=t
            self.assertFalse(self.project(value)['passed'])
    def test_cleanup_and_same_owned_process_are_required(self):
        self.assertFalse(self.project(same_process=False)['passed']);self.assertFalse(self.project(ordinary_clients=False)['passed'])
        for key,invalid in (('started',9),('remaining',True),('remaining',1),('remaining_groups',1),('remaining_groups',None)):
            _,_,cleanup=fixture();cleanup[key]=invalid
            self.assertFalse(self.project(cleanup=cleanup)['passed'])
    def test_numeric_projection_drops_unbounded_or_wrong_types(self):
        value,transport,_=fixture();value['reconnect_attempts']='secret';transport['server_packets']='secret';transport['blackhole_seconds']=float('nan')
        result=self.project(value,transport);self.assertIsNone(result['reconnect_attempts']);self.assertIsNone(result['transport']['server_packets'])
        self.assertIsNone(result['transport']['blackhole_seconds']);json.dumps(result,allow_nan=False)


class RoomGuardTests(unittest.TestCase):
    def setUp(self):
        self.match='a'*32;self.room={'lobbyId':self.match,'status':'Ready','gameMode':'SocialDeduction','gameVersion':'0.9.0','protocolVersion':'4',
            'gamePort':7781,'currentPlayers':0,'maxPlayers':6}
    def test_exact_fresh_m8_staging_room(self):self.assertTrue(probe.validate_room(self.room,self.match))
    def test_production_legacy_or_occupied_room_rejected(self):
        for key,invalid in (('lobbyId','b'*32),('lobbyId',None),('status','Running'),('gameMode','Sandbox'),('gameVersion','0.8.0'),
            ('protocolVersion','3'),('gamePort',7777),('currentPlayers',1),('currentPlayers',False),('maxPlayers',3),('maxPlayers',True)):
            with self.subTest(key=key,invalid=invalid):
                room=dict(self.room);room[key]=invalid;self.assertFalse(probe.validate_room(room,self.match))

    def test_peer_fixture_limits_satisfy_actual_runtime_config_before_admission(self):
        from actor_runtime.config import CognitiveConfig
        value=CognitiveConfig(brain={'max_decisions':probe.PEER_MAX_DECISIONS,'objective_timeout':400})
        self.assertEqual(value.brain.max_decisions,64)


class RelayTests(unittest.TestCase):
    def test_opaque_roundtrip_blackhole_restore_and_new_client_port(self):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as server,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as first,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as second:
            for endpoint in (server,first,second):endpoint.bind(('127.0.0.1',0));endpoint.settimeout(.3)
            relay=probe.OpaqueUdpRelay(0,server.getsockname())
            try:
                target=('127.0.0.1',relay.port)
                first.sendto(b'opaque-admission',target);data,source=server.recvfrom(65535);self.assertEqual(data,b'opaque-admission')
                self.assertNotEqual(source[1],relay.port)
                self.assertEqual(relay.upstream.getsockname()[0],'0.0.0.0')
                server.sendto(b'opaque-reply',source);self.assertEqual(first.recvfrom(65535)[0],b'opaque-reply')
                relay.blackhole.set();first.sendto(b'never-forward-client',target);server.sendto(b'never-forward-server',source)
                with self.assertRaises(socket.timeout):server.recvfrom(65535)
                with self.assertRaises(socket.timeout):first.recvfrom(65535)
                relay.blackhole.clear();second.sendto(b'new-transport-endpoint',target)
                data,source=server.recvfrom(65535);self.assertEqual(data,b'new-transport-endpoint')
                server.sendto(b'resumed',source);self.assertEqual(second.recvfrom(65535)[0],b'resumed')
                counts=relay.counts();self.assertGreaterEqual(counts['dropped_client'],1);self.assertGreaterEqual(counts['dropped_server'],1)
                self.assertEqual(set(counts),{'client_packets','server_packets','dropped_client','dropped_server'})
                self.assertFalse(any(isinstance(value,bytes) for value in vars(relay).values()))
            finally:relay.close()


class CleanupTests(unittest.TestCase):
    def test_owned_launcher_exit_does_not_leave_its_child_running(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'child.json'
            ready=Path(folder)/'ready';stopped=Path(folder)/'stopped'
            child_source="import signal,sys,time; from pathlib import Path; signal.signal(signal.SIGTERM,lambda *_:(Path(sys.argv[2]).write_text('cleaned'),sys.exit(0))); Path(sys.argv[1]).write_text('ready'); time.sleep(60)"
            source="import json,subprocess,sys; p=subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[3],sys.argv[4]]); open(sys.argv[1],'w').write(json.dumps(p.pid))"
            parent=subprocess.Popen([sys.executable,'-c',source,str(marker),child_source,str(ready),str(stopped)],start_new_session=True)
            child=None
            try:
                parent.wait(timeout=5);child=json.loads(marker.read_text());self.assertTrue(probe.owned_group_member(child,parent.pid))
                deadline=time.monotonic()+5
                while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertTrue(ready.exists())
                probe.stop_owned([parent],[child],grace_seconds=.05)
                self.assertEqual(stopped.read_text(),'cleaned')
                # Orphan zombies can briefly await the platform's init reaper;
                # a signalled non-running child is enough here. A new unrelated
                # session may never be treated as this launcher's descendant.
                unrelated=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
                try:
                    self.assertFalse(probe.owned_group_member(unrelated.pid,parent.pid))
                    self.assertIsNone(unrelated.poll())
                finally:unrelated.terminate();unrelated.wait(timeout=5)
            finally:
                if probe.group_alive(parent.pid):
                    try:os.killpg(parent.pid,9)
                    except ProcessLookupError:pass
                if parent.poll() is None:parent.kill();parent.wait(timeout=5)
    def test_metadata_pid_cannot_authorize_signalling_foreign_session(self):
        own=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
        foreign=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True)
        try:
            probe.stop_owned([own],[foreign.pid],grace_seconds=.05)
            self.assertIsNotNone(own.poll());self.assertIsNone(foreign.poll())
        finally:
            for process in (own,foreign):
                if process.poll() is None:process.kill();process.wait(timeout=5)

if __name__=='__main__':unittest.main()
