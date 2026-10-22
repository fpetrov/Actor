// Real production processor in a fake AudioWorklet host. No browser/device.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const source=fs.readFileSync(process.argv[2],'utf8');
function host() {
  let Processor;
  const context={Float32Array,Math,Number,JSON,Set,Error,sampleRate:48000,currentFrame:0,
    AudioWorkletProcessor:class{constructor(){this.messages=[];this.port={postMessage:x=>this.messages.push(x)};}},
    registerProcessor:(name,value)=>{assert.equal(name,'actor-voice');Processor=value;}};
  vm.runInNewContext(source,context);
  const processor=new Processor();
  function step(alive=true){if(alive)processor.message({type:'alive'});const out=new Float32Array(128);processor.process([] ,[[out]]);context.currentFrame+=128;return Array.from(out);}
  return {processor,context,step};
}
const scope={broadcast_session_id:'b',match_id:'m',connection_generation:1,view_epoch:1};
const key={utterance_id:'u',utterance_epoch:1,segment_index:0};
function begin(p,k=key){p.message({type:'begin',scope,key:k});}
function pcm(p,data,seq=0,offset=0,k=key){p.message({type:'pcm',scope,key:k,chunk_seq:seq,source_frame_offset:offset,pcm:data});}
function end(p,count,complete=true,k=key){p.message({type:'end',scope,key:k,total_source_frames:count,complete});}
const wave=Float32Array.from({length:1024},(_,i)=>.2*Math.sin(i*2*Math.PI*440/24000));
// FIR state and output are identical when a stream crosses a real chunk edge.
const whole=host(), split=host();begin(whole.processor);begin(split.processor);
pcm(whole.processor,wave);end(whole.processor,wave.length);
pcm(split.processor,wave.slice(0,512));
const a=[],b=[];for(let i=0;i<8;i++){a.push(...whole.step());b.push(...split.step());}
pcm(split.processor,wave.slice(512),1,512);end(split.processor,wave.length);
for(let i=0;i<10;i++){a.push(...whole.step());b.push(...split.step());}
assert.deepEqual(a,b);assert.equal(whole.processor.messages.at(-1).type,'complete');
assert.equal(whole.processor.messages.at(-1).consumed_frames,1024);
assert(a.some(x=>Math.abs(x)>.05));assert(a.every(Number.isFinite));
// Truncation emits interrupted even after every generated sample was consumed.
const truncated=host();begin(truncated.processor);pcm(truncated.processor,wave);end(truncated.processor,1024,false);
for(let i=0;i<20;i++)truncated.step();
assert.equal(truncated.processor.messages.at(-1).type,'interrupted');
// Fade ACK is emitted by render processing, never by control-message receipt.
const flushed=host();begin(flushed.processor);pcm(flushed.processor,wave);flushed.step();
flushed.processor.message({type:'flush',scope,utterance_id:'u',utterance_epoch:1,fade_frames:480});
assert(!flushed.processor.messages.some(x=>x.type==='flushed'));
for(let i=0;i<5;i++)flushed.step();
assert(flushed.processor.messages.some(x=>x.type==='flushed'));
assert.equal(flushed.processor.active,null);assert.equal(flushed.processor.queued,0);
// Fenced PCM cannot play after new utterance admission; replay begin rejected.
const newKey={...key,utterance_id:'next'};begin(flushed.processor,newKey);
pcm(flushed.processor,wave,0,0,key);assert.equal(flushed.processor.queued,0);
pcm(flushed.processor,wave,0,0,newKey);assert.equal(flushed.processor.queued,1024);
flushed.processor.message({type:'flush',scope,utterance_id:'u',utterance_epoch:1,fade_frames:0});
flushed.step();assert.equal(flushed.processor.active.key.utterance_id,'next');
assert.equal(flushed.processor.messages.at(-1).type,'flushed');
const replay=host();begin(replay.processor);replay.processor.clear();assert.throws(()=>begin(replay.processor),/replay/);
// Independent 1-second renderer watchdog suppresses stale output.
const lost=host();begin(lost.processor);pcm(lost.processor,wave);lost.context.currentFrame=48001;
assert(lost.step(false).every(x=>x===0));assert.equal(lost.processor.active,null);
assert.equal(lost.processor.messages.at(-1).type,'interrupted');
console.log(JSON.stringify({passed:true,checks:6,scope:'production_worklet_synthetic_host_no_device'}));
