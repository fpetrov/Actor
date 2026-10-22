"""Execute the real activation transaction against a disposable filesystem.

Only fixed system commands and the read-only HTTP guard are substituted; the
transaction, backups, symlink/environment/web mutations and EXIT rollback run.
No SSH, server, credentials or production paths are used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest

SOURCE=Path(__file__).parents[1]/'deploy-activate.sh'
RELEASE='20261022T000000Z-abcdef0'


@pytest.fixture
def activation(tmp_path):
    root=tmp_path/'root';root.mkdir()
    paths=('/opt/actor/control-plane/releases/old','/opt/actor/agents-arena/releases/old',
        f'/opt/actor/control-plane/releases/{RELEASE}',f'/opt/actor/agents-arena/releases/{RELEASE}',
        f'/opt/actor/staging/{RELEASE}/web','/etc/actor','/etc/systemd/system','/etc/nginx/sites-available',
        '/etc/nginx/sites-enabled','/var/www/actor','/run','/sys/fs/cgroup/system.slice/nginx.service')
    for path in paths:(root/path[1:]).mkdir(parents=True,exist_ok=True)
    def file(path,value,mode=0o644):
        target=root/path[1:];target.write_text(value);target.chmod(mode);return target
    for group,binary in (('control-plane','Actor.ControlPlane'),('agents-arena','AgentsArenaServer')):
        file(f'/opt/actor/{group}/releases/{RELEASE}/{binary}','candidate',0o755)
        (root/f'opt/actor/{group}/current').symlink_to(root/f'opt/actor/{group}/releases/old')
    stage=f'/opt/actor/staging/{RELEASE}'
    file(stage+'/actor.conf','new-nginx')
    file(stage+'/actor-control-plane.service','new-unit')
    file(stage+'/web/index.html','new-web')
    file(stage+'/web/new-only.js','new-asset')
    file('/sys/fs/cgroup/cgroup.controllers','cpu memory')
    file('/sys/fs/cgroup/system.slice/nginx.service/cgroup.events','populated 1\n')
    file('/etc/actor/control-plane.env','Match__GameVersion=0.8.0\nMatch__ProtocolVersion=3\nPRIVATE_TEST_SENTINEL=never-print\n',0o600)
    file('/etc/systemd/system/actor-control-plane.service','old-unit')
    file('/etc/nginx/sites-available/actor.conf','old-nginx')
    file('/etc/nginx/sites-available/default','distro-default')
    file('/etc/nginx/sites-enabled/unrelated.conf','untouched-other-host')
    (root/'etc/nginx/sites-enabled/default').symlink_to(root/'etc/nginx/sites-available/default')
    (root/'etc/nginx/sites-enabled/actor.conf').symlink_to(root/'etc/nginx/sites-available/actor.conf')
    file('/var/www/actor/index.html','old-web');file('/var/www/actor/old-download.zip','retained-download')
    state=tmp_path/'state.json';state.write_text(json.dumps({'active':True,'enabled':True,'nginx_active':True,'trace':[]}))
    binary=tmp_path/'bin';binary.mkdir()
    real_rsync=shutil.which('rsync');assert real_rsync
    stub=binary/'command'
    stub.write_text('#!'+sys.executable+'\n'+r'''
import json,os,sys,subprocess
from pathlib import Path
name=Path(sys.argv[0]).name;args=sys.argv[1:];root=Path(os.environ['FAKE_ROOT']);state_path=Path(os.environ['FAKE_STATE'])
s=json.loads(state_path.read_text());fail=os.environ.get('FAKE_FAIL','')
def save():state_path.write_text(json.dumps(s))
def new():return str((root/'opt/actor/control-plane/current').resolve()).endswith(os.environ['FAKE_RELEASE'])
if name=='python3':
 if args and args[0]=='-':
  code=sys.stdin.read()
  if 'urllib.request' in code:
   assert (root/'run/actor-admission-drain').exists()
   s['trace'].append('final_guard');s['guard_calls']=s.get('guard_calls',0)+1;save()
   if fail=='guard' or fail=='late_guard' and s['guard_calls']>=2:print('Deployment refused at activation: production rooms are active',file=sys.stderr);sys.exit(1)
   sys.exit(0)
  sys.argv=sys.argv[1:];exec(compile(code,'<fixture-stdin>','exec'),{'__name__':'__main__'});sys.exit(0)
 os.execv(os.environ['FAKE_PYTHON'],[os.environ['FAKE_PYTHON'],*args])
if name=='timeout':os.execvp(args[2],args[2:])
if name=='flock':sys.exit(1 if fail=='lock' else 0)
if name=='sleep':sys.exit(0)
if name=='nginx':
 candidate=(root/'etc/nginx/sites-available/actor.conf').read_text()=='new-nginx'
 sys.exit(1 if fail=='nginx' and candidate else 0)
if name=='systemctl':
 action=args[0];s['trace'].append('systemctl:'+action+':'+args[-1]);save()
 if action=='show':
  print(('process' if fail=='kill-mode' else 'control-group') if '--property=KillMode' in args else '/system.slice/nginx.service');sys.exit(0)
 if action=='is-active':sys.exit(0 if s['nginx_active' if args[-1]=='nginx' else 'active'] else 3)
 if action=='is-enabled':sys.exit(0 if s['enabled'] else 1)
 if action=='stop':
  if args[-1]=='nginx':
   s['nginx_active']=False
   if fail!='workers':(root/'sys/fs/cgroup/system.slice/nginx.service/cgroup.events').write_text('populated 0\n')
  else:s['active']=False
 elif action=='start':
  if args[-1]=='nginx':
   s['nginx_active']=True;(root/'sys/fs/cgroup/system.slice/nginx.service/cgroup.events').write_text('populated 1\n');save();sys.exit(0)
  if fail=='start' and new() or fail=='rollback' and not new():sys.exit(1)
  s['active']=True
 elif action=='enable':s['enabled']=True
 elif action=='disable':s['enabled']=False
 elif action=='daemon-reload' and fail=='reload' and new():sys.exit(1)
 save();sys.exit(0)
if name=='curl':
 if args[-1].endswith('/health'):sys.exit(22 if fail in ('health','rollback') and new() else 0)
 version='0.7.0' if fail=='version' else '0.9.0'
 print(json.dumps({'gameVersion':version,'protocolVersion':'4'}));sys.exit(0)
if name=='rsync':
 result=subprocess.run([os.environ['FAKE_RSYNC'],*args])
 if fail=='web' and args[-1]==str(root/'var/www/actor')+'/':
  s['web_failures']=s.get('web_failures',0)+1;save()
  if s['web_failures']==1:sys.exit(24)
 sys.exit(result.returncode)
raise RuntimeError(name)
''');stub.chmod(0o755)
    for command in ('python3','flock','sleep','nginx','systemctl','curl','rsync','timeout'):(binary/command).symlink_to(stub)
    script=tmp_path/'activate.sh';source=SOURCE.read_text()
    # Only fixed root path prefixes change. Shell flow and every production
    # transaction command are otherwise identical to the deployed source.
    for prefix in ('/opt/actor','/etc/actor','/etc/systemd','/etc/nginx','/var/www/actor','/run/actor','/sys/fs/cgroup'):
        source=source.replace(prefix,str(root)+prefix)
    script.write_text(source)
    env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ['PATH'],FAKE_ROOT=str(root),FAKE_STATE=str(state),
        FAKE_RELEASE=RELEASE,FAKE_PYTHON=sys.executable,FAKE_RSYNC=real_rsync)
    def run(fail='',arguments=None):
        return subprocess.run(['bash',str(script),*(arguments or (RELEASE,'example.test','0.9.0'))],env=env|{'FAKE_FAIL':fail},capture_output=True,text=True,timeout=25)
    return root,state,run


def assert_old(root,state):
    assert (root/'opt/actor/control-plane/current').resolve().name=='old'
    assert (root/'opt/actor/agents-arena/current').resolve().name=='old'
    assert (root/'etc/systemd/system/actor-control-plane.service').read_text()=='old-unit'
    assert (root/'etc/nginx/sites-available/actor.conf').read_text()=='old-nginx'
    assert (root/'etc/nginx/sites-enabled/actor.conf').is_symlink()
    assert (root/'etc/nginx/sites-enabled/default').is_symlink()
    assert (root/'etc/nginx/sites-enabled/unrelated.conf').read_text()=='untouched-other-host'
    assert (root/'etc/actor/control-plane.env').read_text().startswith('Match__GameVersion=0.8.0\nMatch__ProtocolVersion=3\n')
    assert (root/'etc/actor/control-plane.env').stat().st_mode&0o777==0o600
    assert (root/'var/www/actor/index.html').read_text()=='old-web'
    assert (root/'var/www/actor/old-download.zip').read_text()=='retained-download'
    assert not (root/'var/www/actor/new-only.js').exists()
    assert not (root/'run/actor-admission-drain').exists()
    value=json.loads(state.read_text());assert value['active'] is True and value['enabled'] is True and value['nginx_active'] is True


def test_success_requires_matching_health_version_and_keeps_prior_backup(activation):
    root,state,run=activation;result=run();assert result.returncode==0,result.stderr
    assert (root/'opt/actor/control-plane/current').resolve().name==RELEASE
    assert (root/'opt/actor/agents-arena/current').resolve().name==RELEASE
    assert (root/'var/www/actor/index.html').read_text()=='new-web'
    assert (root/'var/www/actor/old-download.zip').read_text()=='retained-download'
    assert (root/'var/www/actor/new-only.js').exists()
    assert 'Match__GameVersion=0.9.0' in (root/'etc/actor/control-plane.env').read_text()
    assert 'Match__AdmissionDrainFile='+str(root/'run/actor-admission-drain') in (root/'etc/actor/control-plane.env').read_text()
    assert not (root/'run/actor-admission-drain').exists()
    assert 'never-print' not in result.stdout+result.stderr


@pytest.mark.parametrize('failure',['nginx','guard','late_guard','workers','kill-mode','start','health','version','reload','web'])
def test_each_failed_activation_restores_exact_prior_deployment(activation,failure):
    root,state,run=activation;result=run(failure);assert result.returncode!=0
    assert 'never-print' not in result.stdout+result.stderr
    assert_old(root,state)
    if failure in ('nginx','guard','late_guard','workers','kill-mode'):
        # Detecting a room after drain must not stop the CP's live games.
        assert 'systemctl:stop:actor-control-plane.service' not in json.loads(state.read_text())['trace']
    assert 'Previous deployment restored.' in result.stderr


def test_preexisting_drain_is_not_owned_or_removed(activation):
    root,state,run=activation;marker=root/'run/actor-admission-drain';marker.write_text('another-owner')
    result=run();assert result.returncode!=0;assert marker.read_text()=='another-owner'
    assert not (root/f'opt/actor/staging/{RELEASE}/backup').exists()
    assert 'systemctl:stop:actor-control-plane.service' not in json.loads(state.read_text())['trace']


def test_exclusive_activation_lock_refuses_before_any_backup_or_mutation(activation):
    root,state,run=activation;result=run('lock');assert result.returncode!=0
    assert not (root/f'opt/actor/staging/{RELEASE}/backup').exists()
    assert (root/'etc/nginx/sites-available/actor.conf').read_text()=='old-nginx'
    assert 'systemctl:stop:actor-control-plane.service' not in json.loads(state.read_text())['trace']


def test_rollback_preserves_previously_disabled_inactive_service(activation):
    root,state,run=activation;value=json.loads(state.read_text());value.update(active=False,enabled=False);state.write_text(json.dumps(value))
    result=run('health');assert result.returncode!=0
    value=json.loads(state.read_text());assert value['active'] is False and value['enabled'] is False
    assert (root/'opt/actor/control-plane/current').resolve().name=='old'
    assert (root/'var/www/actor/index.html').read_text()=='old-web'


def test_failed_old_service_recovery_is_explicit_not_a_false_rollback_success(activation):
    root,state,run=activation;result=run('rollback');assert result.returncode!=0
    assert 'Rollback failed: ingress remains drained;' in result.stderr
    assert 'Previous deployment restored.' not in result.stderr
    assert (root/'opt/actor/control-plane/current').resolve().name=='old'
    assert json.loads(state.read_text())['active'] is False
    assert (root/'run/actor-admission-drain').exists()
    assert json.loads(state.read_text())['nginx_active'] is False


@pytest.mark.parametrize('arguments',[('bad;release','example.test','0.9.0'),(RELEASE,'bad;host','0.9.0'),(RELEASE,'example.test','0.9.0;echo')])
def test_compound_argument_guards_explicitly_fail_on_macos_bash32(activation,arguments):
    root,state,run=activation;result=run(arguments=arguments);assert result.returncode==2
    assert not (root/f'opt/actor/staging/{RELEASE}/backup').exists()
    assert json.loads(state.read_text())['trace']==[]


@pytest.mark.parametrize('invalid',['host','user','key'])
def test_local_deploy_arguments_rejected_before_ssh_on_bash32(tmp_path,invalid):
    repo=tmp_path/'repo';scripts=repo/'scripts';scripts.mkdir(parents=True)
    shutil.copy(SOURCE.parent/'deploy-vps.sh',scripts/'deploy-vps.sh')
    shutil.copy(SOURCE.parent/'common.sh',scripts/'common.sh')
    for name in ('control-plane/Actor.ControlPlane','agents-arena-server/AgentsArenaServer'):
        path=repo/'build'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture');path.chmod(0o755)
    (repo/'build/web').mkdir();(repo/'build/web/index.html').write_text('fixture')
    (repo/'build/agents-arena-server/build-info.json').write_text(json.dumps({'development':False,'options':'None','game_version':'0.9.0'}))
    binary=tmp_path/'bin';binary.mkdir();(binary/'git').write_text('#!/bin/sh\nprintf abcdef0\n');(binary/'git').chmod(0o755)
    marker=tmp_path/'unexpected-network'
    for name in ('ssh','rsync','scp'):
        command=binary/name;command.write_text('#!/bin/sh\ntouch "'+str(marker)+'"\nexit 99\n');command.chmod(0o755)
    key=tmp_path/("invalid'key" if invalid=='key' else 'fixture-key');key.write_text('not-a-real-key')
    host='bad;host' if invalid=='host' else 'example.test';user='bad;user' if invalid=='user' else 'root'
    result=subprocess.run(['bash',str(scripts/'deploy-vps.sh'),host,user,str(key)],env=dict(os.environ,PATH=str(binary)+os.pathsep+os.environ['PATH']),capture_output=True,text=True,timeout=5)
    assert result.returncode==2;assert not marker.exists()
