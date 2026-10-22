"use strict";
// Owned OBS renderer only. No getUserMedia, OBS API, external resources or tools.
(() => {
  const caption = document.getElementById("caption"), enable = document.getElementById("enable");
  const capability = new URLSearchParams(location.hash.slice(1)).get("cap");
  if (!capability || capability.length > 128 || location.hostname !== "127.0.0.1") return;
  let ws, context, node, latestHeartbeat = 0, activeText = "", activeId = "", authenticated = false;
  const id = (scope, key) => JSON.stringify([scope.broadcast_session_id,scope.match_id,scope.connection_generation,scope.view_epoch,key.utterance_id,key.utterance_epoch,key.segment_index]);
  const send = x => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(x)); };
  const clear = () => { caption.textContent = ""; activeText = ""; activeId = ""; if (node) node.port.postMessage({type:"lost"}); };
  async function running() {
    if (!context) {
      context = new AudioContext({sampleRate:48000,latencyHint:"interactive"});
      if (context.sampleRate !== 48000 || !context.audioWorklet) throw Error("unsupported_audio_graph");
      await context.audioWorklet.addModule("audio-worklet.js");
      node = new AudioWorkletNode(context,"actor-voice",{numberOfInputs:0,numberOfOutputs:1,outputChannelCount:[1]});
      node.connect(context.destination);
      node.port.onmessage = ({data}) => {
        if (data.type === "error") { clear(); ws.close(1008,"audio_graph_rejected"); return; }
        if (["progress","complete","interrupted"].includes(data.type)) {
          if (id(data.scope,data.key) === activeId && data.consumed_frames > 0) caption.textContent = activeText;
          if (data.type !== "progress") { caption.textContent = ""; activeText = ""; activeId = ""; }
        }
        send(data);
      };
      context.onstatechange = () => {
        if (authenticated && context.state !== "running") { clear(); send({type:"not_ready"}); }
      };
    }
    await context.resume();
    if (context.state !== "running") { enable.hidden = false; return false; }
    enable.hidden = true;
    send({type:"ready",rate:context.sampleRate,worklet:true,context_state:context.state});
    return true;
  }
  enable.onclick = () => running().catch(() => { clear(); send({type:"not_ready"}); });
  ws = new WebSocket(location.origin.replace("http:","ws:") + "/audio");
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { send({type:"authenticate",capability}); authenticated = true; running().catch(() => { enable.hidden = false; }); };
  ws.onclose = clear; ws.onerror = clear;
  ws.onmessage = ({data}) => {
    try {
      if (!(data instanceof ArrayBuffer) || data.byteLength < 8) throw Error("frame");
      const view = new DataView(data), h = view.getUint32(0), n = view.getUint32(4);
      if (!h || h > 16384 || n > 131072 || data.byteLength !== 8+h+n) throw Error("bound");
      const packet = JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(new Uint8Array(data,8,h)));
      const x = packet.value;
      if (packet.type === "heartbeat") { latestHeartbeat = performance.now(); return; }
      if (!node || context.state !== "running") throw Error("not_ready");
      if (packet.type === "begin") {
        if (n || typeof x.text !== "string" || new TextEncoder().encode(x.text).length > 4096) throw Error("text_bound");
        activeText = x.text; activeId = id(x.scope,x.key); caption.textContent = "";
        node.port.postMessage({type:"begin",scope:x.scope,key:x.key});
      } else if (packet.type === "pcm") {
        if (!n || n%4) throw Error("pcm_bound");
        const floats = new Float32Array(n/4);
        for (let i=0;i<floats.length;i++) { const v=view.getFloat32(8+h+i*4,true); if (!Number.isFinite(v)||Math.abs(v)>1.0001) throw Error("pcm_value"); floats[i]=v; }
        node.port.postMessage({type:"pcm",...x,pcm:floats},[floats.buffer]);
      } else if (packet.type === "end" || packet.type === "flush") {
        if (n) throw Error("control_pcm"); node.port.postMessage({type:packet.type,...x});
      } else throw Error("unknown");
    } catch (_) { clear(); ws.close(1008,"audio_frame_rejected"); }
  };
  setInterval(() => {
    if (!node) return;
    if (ws.readyState === WebSocket.OPEN && performance.now()-latestHeartbeat < 1000)
      node.port.postMessage({type:"alive"});
    else clear();
  },250);
})();
