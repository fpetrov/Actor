#!/usr/bin/env python3
"""Offline, bounded real standalone rendering probe. Starts no room or model."""
import argparse,json,os,signal,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--timeout',type=int,default=50);a=p.parse_args()
 if not 10<=a.timeout<=120:p.error('timeout must be10..120 seconds')
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
 client=ROOT/'build/agents-arena-client/AgentsArena.app/Contents/MacOS/Agents Arena'
 if not client.is_file():p.error('Build the ordinary macOS client first')
 command=[str(client),'--ui-compat-probe',str(out),'-screen-fullscreen','0','-screen-width','800','-screen-height','600','-logFile',str(out/'client.log')]
 process=None;timed_out=False
 try:
  with (out/'stdout.log').open('w') as log:process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  try:process.wait(timeout=a.timeout)
  except subprocess.TimeoutExpired:timed_out=True
 finally:
  if process is not None and process.poll() is None:
   os.killpg(process.pid,signal.SIGTERM)
   try:process.wait(timeout=8)
   except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)
 if timed_out:raise SystemExit('Probe timed out; owned client stopped')
 path=out/'compatibility.json'
 if not path.is_file():raise SystemExit('Missing rendered probe result')
 result=json.loads(path.read_text());result['owned_client_stopped']=process.poll() is not None
 result['fullscreen']=False;result['headless']=False
 (out/'acceptance.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result))
 return 0 if result.get('passed') is True and process.returncode==0 else 1
if __name__=='__main__':raise SystemExit(main())
