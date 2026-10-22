'use strict';
// Execute the shipped operator script against a synthetic DOM/HTTP boundary.
// No browser, account, network or native APIs are involved.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8'),html=fs.readFileSync(process.argv[3],'utf8');
class Element {
  constructor(id=''){this.id=id;this.value='';this.checked=false;this.disabled=false;this.textContent='';this.listeners={};this.children=[];}
  addEventListener(name,fn){this.listeners[name]=fn;}
  replaceChildren(...items){this.children=items;}
  event(name){assert.equal(typeof this.listeners[name],'function');return this.listeners[name]({preventDefault(){}});}
  set innerHTML(_){throw Error('HTML injection is forbidden');}
}
const nodes={};
for(const tag of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)){
  const element=nodes[tag[1]]=new Element(tag[1]);
  element.checked=/\bchecked\b/.test(tag[0]);element.type=(/\btype="([^"]+)"/.exec(tag[0])||[])[1];
  element.value=(/\bvalue="([^"]*)"/.exec(tag[0])||[])[1]||'';
}
assert.equal(nodes['memory-callbacks'].type,'checkbox');
const state={state:'PreviewReady',live:false,channel:'fedorr_codex',room_code:'ABC123',broadcast_session_id:'synthetic-session',
  rooms:[],ready:{observer:true,model:true,tts:true,obs:true,audio:true,chat:false},chat_enabled:false,
  commentary:{idle_enabled:true,idle_after_seconds:20,allow_memory_callbacks:true,pending_speech_segments:2,
    calls_by_mode:{reactive:7,idle_recap:2,memory_callback:1}},usage:{calls:10},last_spoken:null};
const requests=[];let response=()=>({ok:true,json:async()=>structuredClone(state)});
const timers=[];
const context=vm.createContext({document:{getElementById:id=>{assert.ok(nodes[id],`missing ${id}`);return nodes[id];},
    querySelector:()=>({content:'synthetic-csrf'}),createElement:()=>new Element(),activeElement:null},
  fetch:async(url,options={})=>{requests.push({url,options});return await response(url,options);},
  setTimeout:fn=>{timers.push(fn);return timers.length;},console,Number,JSON,String,Object,Error});
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));};
async function main(){
  vm.runInContext(source,context);await settle();
  assert.equal(nodes['memory-callbacks'].checked,true);
  assert.match(nodes['speech-queue'].textContent,/2/);
  for(const n of [7,2,1])assert.ok(nodes['mode-calls'].textContent.includes(String(n)));
  assert.match(nodes['live'].textContent,/не подтверждён/);
  assert.ok(nodes.publication.textContent.includes('ABC123'));
  assert.equal(nodes['public-start'].disabled,true);
  assert.ok(nodes.dependencies.children.some(row=>row.textContent==='Отключён'));

  // A poll cannot overwrite an unsubmitted toggle or number after blur.
  nodes['memory-callbacks'].checked=false;nodes['memory-callbacks'].event('change');
  nodes['idle-seconds'].value='35';nodes['idle-seconds'].event('input');
  context.render(structuredClone(state));
  assert.equal(nodes['memory-callbacks'].checked,false);assert.equal(nodes['idle-seconds'].value,'35');
  let resolve;response=()=>new Promise(done=>{resolve=done;});
  nodes['idle-form'].event('submit');await settle();
  const saved=JSON.parse(requests.at(-1).options.body);
  assert.deepEqual(saved,{action:'configure',payload:{idle_enabled:true,idle_after_seconds:35,allow_memory_callbacks:false}});
  assert.equal(requests.at(-1).options.headers['X-CSRF-Token'],'synthetic-csrf');
  assert.equal(requests.at(-1).options.credentials,'same-origin');
  // A new edit during Apply is not erased by that older response or a poll.
  nodes['memory-callbacks'].checked=true;nodes['memory-callbacks'].event('change');
  resolve({ok:true,json:async()=>({...structuredClone(state),commentary:{...state.commentary,allow_memory_callbacks:false}})});
  await settle();context.render({...structuredClone(state),commentary:{...state.commentary,allow_memory_callbacks:false}});
  assert.equal(nodes['memory-callbacks'].checked,true);
  response=()=>({ok:true,json:async()=>structuredClone(state)});
  nodes['idle-form'].event('submit');await settle();
  context.render({...structuredClone(state),commentary:{...state.commentary,allow_memory_callbacks:false}});
  assert.equal(nodes['memory-callbacks'].checked,false); // Clean form follows server truth again.

  // Failure keeps the draft, and status counters cannot render arbitrary text.
  nodes['memory-callbacks'].checked=true;nodes['memory-callbacks'].event('change');
  response=()=>({ok:false,json:async()=>({error:'broadcast_config_invalid'})});
  nodes['idle-form'].event('submit');await settle();
  assert.equal(nodes['memory-callbacks'].checked,true);assert.match(nodes.error.textContent,/broadcast_config_invalid/);
  context.render({...structuredClone(state),live:true,commentary:{...state.commentary,pending_speech_segments:true,
    calls_by_mode:{reactive:'PRIVATE RAW TEXT',idle_recap:-1,memory_callback:1000001}}});
  assert.ok(nodes['live'].textContent.includes('Live подтверждён'));
  assert.match(nodes['speech-queue'].textContent,/неизвестно/);
  assert.equal((nodes['mode-calls'].textContent.match(/неизвестно/g)||[]).length,3);
  assert.ok(!nodes['mode-calls'].textContent.includes('PRIVATE'));
  const actions=requests.filter(x=>x.url==='/api/action').map(x=>JSON.parse(x.options.body).action);
  assert.deepEqual(actions,['configure','configure','configure']); // No automatic publication.
  console.log(JSON.stringify({passed:true,checks:6,scope:'production_operator_synthetic_dom_no_browser_network'}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
