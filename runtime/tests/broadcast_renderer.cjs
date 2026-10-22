// Production renderer in a deterministic fake DOM/WebAudio/WebSocket host.
// No browser, hardware, network, OBS control or fabricated DSP execution.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
const html=fs.readFileSync(process.argv[3],'utf8');
let checks=0;
const scope={broadcast_session_id:'b',match_id:'m',connection_generation:1,view_epoch:1};
const key={utterance_id:'u',utterance_epoch:1,segment_index:0};
const settle=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
function frame(type,value={},pcm=new Uint8Array(0),extra={}) {
  const header=new TextEncoder().encode(JSON.stringify({type,value,...extra}));
  const buffer=new ArrayBuffer(8+header.length+pcm.length), view=new DataView(buffer);
  view.setUint32(0,header.length);view.setUint32(4,pcm.length);
  new Uint8Array(buffer,8,header.length).set(header);
  new Uint8Array(buffer,8+header.length).set(pcm);return buffer;
}
function host(options={}) {
  let now=0, resumeMode=options.resumeMode||'running';
  const elements={caption:{textContent:''},enable:{hidden:true},safe:{hidden:false}};
  const sockets=[],contexts=[],nodes=[],timers=[];
  class Socket {
    static OPEN=1;static CLOSED=3;
    constructor(url){this.url=url;this.readyState=0;this.sent=[];this.closed=[];sockets.push(this);}
    send(raw){this.sent.push(JSON.parse(raw));}
    close(code,reason){this.closed.push({code,reason});this.readyState=3;if(this.onclose)this.onclose();}
  }
  class Context {
    constructor(){this.state='suspended';this.sampleRate=48000;this.destination={};contexts.push(this);
      this.audioWorklet={addModule:async name=>{assert.equal(name,'audio-worklet.js');
        if(options.moduleFailure)throw Error('synthetic module failure');
        if(options.moduleGate)await options.moduleGate;}};}
    async resume(){if(resumeMode==='reject')throw Error('autoplay denied');
      if(resumeMode==='running'){this.state='running';if(this.onstatechange)this.onstatechange();}}
  }
  class Worklet {
    constructor(context,name){assert.equal(name,'actor-voice');this.messages=[];this.disconnected=false;
      this.port={postMessage:x=>this.messages.push(x)};nodes.push(this);}
    connect(){}
    disconnect(){this.disconnected=true;}
  }
  const environment={document:{getElementById:id=>elements[id]},
    location:{hash:'#cap='+'c'.repeat(43),hostname:'127.0.0.1',origin:'http://127.0.0.1:44444',...options.location},
    performance:{now:()=>now},WebSocket:Socket,AudioContext:Context,AudioWorkletNode:Worklet,
    URLSearchParams,ArrayBuffer,DataView,Uint8Array,Float32Array,TextEncoder,TextDecoder,Number,JSON,Error,
    setInterval:(fn,interval)=>timers.push({fn,interval,next:now+interval})};
  vm.runInNewContext(source,environment);
  return {elements,sockets,contexts,nodes,timers,
    async open(){const ws=sockets[0];ws.readyState=1;ws.onopen();await settle();},
    packet(type,value,pcm,extra){sockets[0].onmessage({data:frame(type,value,pcm,extra)});},
    async click(){await elements.enable.onclick();await settle();},
    resume(mode){resumeMode=mode;},
    async advance(ms){const end=now+ms;
      while(true){const next=Math.min(...timers.map(x=>x.next));if(next>end)break;now=next;
        for(const timer of timers.filter(x=>x.next===next)){timer.next+=timer.interval;timer.fn();}await settle();}
      now=end;await settle();},
    dsp(data){nodes[0].port.onmessage({data});},
    safe(){return elements.safe.hidden===false;},
    sent(type){return sockets[0].sent.filter(x=>x.type===type);}};
}
(async()=>{
  // The browser initially paints opaque Safe even before JS/capability setup.
  assert.match(html,/<div id="safe" role="status">/);
  assert.match(html,/#safe\{[^}]*position:fixed;inset:0;[^}]*background:#101a22;/);
  assert.match(html,/#safe\[hidden\]\{display:none\}/);
  for(const location of [{hash:''},{hostname:'foreign.invalid'}]){
    const h=host({location});assert(h.safe());assert.equal(h.sockets.length,0);assert.equal(h.contexts.length,0);}
  checks++;

  // Ready graph without an authenticated heartbeat is still opaque at t=0.
  const ready=host();assert(ready.safe());await ready.open();assert(ready.safe());
  assert.equal(ready.sent('authenticate').length,1);assert.equal(ready.sent('ready').length,1);
  ready.packet('heartbeat',{});assert(!ready.safe());assert.equal(ready.timers[0].interval,250);
  checks++;

  // A running socket/heartbeat cannot hide Safe while autoplay is blocked.
  const blocked=host({resumeMode:'blocked'});await blocked.open();
  for(let i=0;i<6;i++){blocked.packet('heartbeat',{});await blocked.advance(250);assert(blocked.safe());}
  assert.equal(blocked.sent('ready').length,0);assert.equal(blocked.elements.enable.hidden,false);
  blocked.resume('running');await blocked.click();assert(!blocked.safe());
  assert.equal(blocked.contexts.length,1);assert.equal(blocked.nodes.length,1);
  checks++;

  // Async graph completion must re-check heartbeat freshness. Failed module
  // loading can never become ready by clicking a second time with context!=null.
  let release;const gate=new Promise(resolve=>release=resolve);
  const pending=host({moduleGate:gate});await pending.open();pending.packet('heartbeat',{});
  await pending.advance(1250);assert(pending.safe());assert.equal(pending.nodes.length,0);
  release();await settle();assert(pending.safe());pending.packet('heartbeat',{});assert(!pending.safe());
  const broken=host({moduleFailure:true});await broken.open();broken.packet('heartbeat',{});
  broken.contexts[0].state='running';await broken.click();assert(broken.safe());
  assert.equal(broken.nodes.length,0);assert.equal(broken.sent('ready').length,0);
  checks++;

  // Controller hard hang: socket stays OPEN, but 1s expiry at the next 250ms
  // watchdog tick paints opaque Safe and sends only the existing lost control.
  const hung=host();await hung.open();hung.packet('heartbeat',{});
  hung.packet('begin',{scope,key,text:'Permitted caption'});
  hung.packet('pcm',{scope,key,chunk_seq:0,source_frame_offset:0},new Uint8Array(new Float32Array(512).buffer));
  hung.dsp({type:'progress',scope,key,consumed_frames:128,underrun_frames:0});
  assert.equal(hung.elements.caption.textContent,'Permitted caption');
  await hung.advance(999);assert(!hung.safe());await hung.advance(1);assert(hung.safe());
  assert.equal(hung.sockets[0].readyState,1);assert.equal(hung.elements.caption.textContent,'');
  assert.equal(hung.nodes[0].messages.at(-1).type,'lost');
  assert.equal(hung.sent('complete').length,0);assert.equal(hung.sent('interrupted').length,0);
  hung.packet('heartbeat',{});assert(!hung.safe());
  checks++;

  // Close, transport error and malformed input are terminal fences. Queued
  // post-close heartbeats cannot unhide the overlay or manufacture audio ACKs.
  for(const fault of ['close','error','nonbinary','heartbeat_pcm','heartbeat_data','extra_header','primitive_value','injected_type']){
    const h=host();await h.open();h.packet('heartbeat',{});assert(!h.safe());
    if(fault==='close'){h.sockets[0].readyState=3;h.sockets[0].onclose();}
    else if(fault==='error')h.sockets[0].onerror();
    else if(fault==='nonbinary')h.sockets[0].onmessage({data:'invalid'});
    else if(fault==='heartbeat_pcm')h.packet('heartbeat',{},new Uint8Array([1,2,3,4]));
    else if(fault==='heartbeat_data')h.packet('heartbeat',{unknown:true});
    else if(fault==='primitive_value')h.packet('pcm','invalid',new Uint8Array([0,0,0,0]));
    else if(fault==='injected_type')h.packet('end',{type:'alive'});
    else h.packet('heartbeat',{},undefined,{unknown:true});
    assert(h.safe());h.packet('heartbeat',{});assert(h.safe());
    assert.equal(h.sent('complete').length,0);assert.equal(h.nodes[0].messages.at(-1).type,'lost');
  }
  checks++;

  // Context suspension/processor errors immediately fence visual+audio; a fresh
  // heartbeat alone cannot override either failure.
  for(const fault of ['suspend','processor','worklet_error']){
    const h=host();await h.open();h.packet('heartbeat',{});assert(!h.safe());
    if(fault==='suspend'){h.contexts[0].state='suspended';h.contexts[0].onstatechange();}
    else if(fault==='processor')h.nodes[0].onprocessorerror();
    else h.dsp({type:'error'});
    assert(h.safe());h.packet('heartbeat',{});assert(h.safe());
    assert.equal(h.nodes[0].messages.at(-1).type,'lost');
  }
  checks++;

  // Consumption/flush messages are forwarded unchanged ONLY when emitted by
  // the processor. Timer/heartbeat/clear never synthesize those acknowledgments.
  const ack=host();await ack.open();ack.packet('heartbeat',{});
  ack.packet('begin',{scope,key,text:'Exact text'});
  ack.packet('pcm',{scope,key,chunk_seq:0,source_frame_offset:0},new Uint8Array(new Float32Array(512).buffer));
  ack.packet('end',{scope,key,total_source_frames:512,complete:true});
  const actual={type:'complete',scope,key,consumed_frames:512,underrun_frames:0};
  ack.dsp(actual);assert.deepEqual(ack.sent('complete'),[actual]);
  assert.equal(ack.elements.caption.textContent,'');
  const flush={type:'flushed',scope,utterance_id:'u',utterance_epoch:1};
  ack.dsp(flush);assert.deepEqual(ack.sent('flushed'),[flush]);
  checks++;
  console.log(JSON.stringify({passed:true,checks,scope:'production_renderer_synthetic_host_no_browser_device'}));
})().catch(error=>{console.error(error);process.exitCode=1;});
