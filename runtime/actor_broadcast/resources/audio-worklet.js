"use strict";
// Streaming 2x FIR interpolation. History persists across PCM chunks; no SAB,
// microphone, dynamic code or browser-control API is required.
class ActorVoice extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ring=new Float32Array(72000); this.read=0; this.write=0; this.queued=0;
    this.history=new Float32Array(17); this.historyAt=0; this.phases=[[],[]];
    const taps=33, mid=16;
    for(let phase=0;phase<2;phase++) {
      for(let n=phase;n<taps;n+=2) {
        const x=(n-mid)/2, sinc=x===0?1:Math.sin(Math.PI*x)/(Math.PI*x);
        this.phases[phase].push(sinc*(.5-.5*Math.cos(2*Math.PI*n/(taps-1))));
      }
      const sum=this.phases[phase].reduce((a,b)=>a+b,0); this.phases[phase]=this.phases[phase].map(x=>x/sum);
    }
    this.active=null; this.seen=new Set(); this.aliveAt=currentFrame; this.flush=null;
    this.port.onmessage=({data})=>{try{this.message(data);}catch(_){this.clear();this.port.postMessage({type:"error"});}};
  }
  identity(scope,key) { return JSON.stringify([scope.broadcast_session_id,scope.match_id,scope.connection_generation,scope.view_epoch,key.utterance_id,key.utterance_epoch,key.segment_index]); }
  matches(x) { return this.active && this.identity(x.scope,x.key)===this.active.id; }
  clear() { this.active=null;this.queued=0;this.read=this.write=0;this.history.fill(0);this.historyAt=0; }
  report(type) {
    const a=this.active;if(!a)return;
    this.port.postMessage({type,scope:a.scope,key:a.key,consumed_frames:a.consumed,underrun_frames:a.underrun});a.underrun=0;a.lastReport=currentFrame;
  }
  message(x) {
    if(x.type==="alive") {this.aliveAt=currentFrame;return;}
    if(x.type==="lost") {if(this.active)this.report("interrupted");this.clear();return;}
    if(x.type==="flush") {
      if(!Number.isInteger(x.fade_frames)||x.fade_frames<0||x.fade_frames>4800)throw Error("fade");
      if(this.active && (JSON.stringify(x.scope)!==JSON.stringify(this.active.scope)||x.utterance_id!==this.active.key.utterance_id||x.utterance_epoch!==this.active.key.utterance_epoch)) {
        // An old cancellation is already empty. Acknowledge only that old
        // identity; it cannot fade or clear a newer authorized utterance.
        this.port.postMessage({type:"flushed",scope:x.scope,utterance_id:x.utterance_id,utterance_epoch:x.utterance_epoch});return;
      }
      this.flush={scope:x.scope,utterance_id:x.utterance_id,utterance_epoch:x.utterance_epoch,remaining:x.fade_frames,total:x.fade_frames};return;
    }
    if(x.type==="begin") {
      const id=this.identity(x.scope,x.key);
      if(this.active||this.seen.has(id)||this.seen.size>=512)throw Error("replay_or_overlap");
      this.seen.add(id);this.clear();
      this.active={id,scope:x.scope,key:x.key,received:0,consumed:0,next:0,phase:0,hasAudio:false,ended:false,complete:false,tail:0,underrun:0,lastReport:currentFrame};return;
    }
    if(!this.matches(x))return; // Late chunks from fenced scopes never play.
    const a=this.active;
    if(x.type==="pcm") {
      if(a.ended||x.chunk_seq!==a.next||x.source_frame_offset!==a.received||!(x.pcm instanceof Float32Array)||!x.pcm.length||x.pcm.length>32768||this.queued+x.pcm.length>this.ring.length)throw Error("pcm_order_bound");
      for(const v of x.pcm){if(!Number.isFinite(v)||Math.abs(v)>1.0001)throw Error("pcm_value");this.ring[this.write]=v;this.write=(this.write+1)%this.ring.length;}
      this.queued+=x.pcm.length;a.received+=x.pcm.length;a.next++;return;
    }
    if(x.type==="end") {
      if(a.ended||!Number.isInteger(x.total_source_frames)||x.total_source_frames!==a.received||typeof x.complete!=="boolean")throw Error("end");
      a.ended=true;a.complete=x.complete;a.tail=17;return;
    }
    throw Error("unknown");
  }
  sample() {
    const a=this.active;if(!a)return 0;
    if(a.phase===0) {
      if(this.queued){this.historyAt=(this.historyAt+1)%17;this.history[this.historyAt]=this.ring[this.read];this.read=(this.read+1)%this.ring.length;this.queued--;a.consumed++;a.hasAudio=true;}
      else if(a.ended&&a.tail>0){this.historyAt=(this.historyAt+1)%17;this.history[this.historyAt]=0;a.tail--;}
      else if(a.ended&&a.tail===0){this.report(a.complete?"complete":"interrupted");this.clear();return 0;}
      else {if(a.hasAudio)a.underrun++;return 0;}
    }
    let value=0;const taps=this.phases[a.phase];
    for(let k=0;k<taps.length;k++)value+=taps[k]*this.history[(this.historyAt-k+17)%17];
    a.phase=1-a.phase;
    return Math.max(-1,Math.min(1,value));
  }
  process(inputs,outputs) {
    const out=outputs[0][0];
    if(currentFrame-this.aliveAt>sampleRate){if(this.active)this.report("interrupted");this.clear();}
    for(let i=0;i<out.length;i++) {
      if(this.flush&&this.flush.remaining<=0) {
        if(this.active)this.report("interrupted");this.clear();
        const f=this.flush;this.flush=null;
        this.port.postMessage({type:"flushed",scope:f.scope,utterance_id:f.utterance_id,utterance_epoch:f.utterance_epoch});
      }
      let value=this.sample();
      if(this.flush&&this.flush.remaining>0){value*=this.flush.remaining/Math.max(1,this.flush.total);this.flush.remaining--;}
      out[i]=value;
    }
    if(this.active&&this.active.consumed&&currentFrame-this.active.lastReport>=4096)this.report("progress");
    return true;
  }
}
registerProcessor("actor-voice",ActorVoice);
