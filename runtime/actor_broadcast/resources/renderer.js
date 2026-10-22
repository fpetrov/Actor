"use strict";
// Owned OBS renderer only. No getUserMedia, OBS API, external resources or tools.
(() => {
  const caption = document.getElementById("caption"), enable = document.getElementById("enable"), safe = document.getElementById("safe");
  const capability = new URLSearchParams(location.hash.slice(1)).get("cap");
  if (!capability || capability.length > 128 || location.hostname !== "127.0.0.1") return;
  let ws, context, node, graph, latestHeartbeat = null, activeText = "", activeId = "", authenticated = false;
  let workletReady = false, failed = false;
  const id = (scope, key) => JSON.stringify([scope.broadcast_session_id,scope.match_id,scope.connection_generation,scope.view_epoch,key.utterance_id,key.utterance_epoch,key.segment_index]);
  const clear = () => {
    safe.hidden = false;
    caption.textContent = ""; activeText = ""; activeId = "";
    if (node) {
      try { node.port.postMessage({type:"lost"}); }
      catch (_) { workletReady = false; try { node.disconnect(); } catch (_) {} }
    }
  };
  const live = () => {
    const now = performance.now();
    return !failed && authenticated && ws && ws.readyState === WebSocket.OPEN && workletReady
      && context && context.state === "running" && Number.isFinite(latestHeartbeat)
      && now >= latestHeartbeat && now-latestHeartbeat < 1000;
  };
  const update = () => { if (live()) safe.hidden = true; else clear(); };
  const reject = reason => {
    failed = true; authenticated = false; clear();
    if (ws && ws.readyState !== WebSocket.CLOSED) ws.close(1008,reason);
  };
  const send = x => {
    try { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(x)); }
    catch (_) { reject("audio_transport_failed"); }
  };
  async function running() {
    if (failed) { clear(); return false; }
    // Keep a failed/incomplete graph distinct from a merely suspended context.
    // A second enable click cannot claim readiness after addModule failed.
    if (!graph) graph = (async () => {
      context = new AudioContext({sampleRate:48000,latencyHint:"interactive"});
      if (context.sampleRate !== 48000 || !context.audioWorklet) throw Error("unsupported_audio_graph");
      await context.audioWorklet.addModule("audio-worklet.js");
      if (failed) throw Error("audio_connection_closed");
      node = new AudioWorkletNode(context,"actor-voice",{numberOfInputs:0,numberOfOutputs:1,outputChannelCount:[1]});
      node.connect(context.destination);
      node.port.onmessage = ({data}) => {
        try {
          if (data.type === "error") { reject("audio_graph_rejected"); return; }
          if (!["progress","complete","interrupted","flushed"].includes(data.type)) throw Error("ack_type");
          if (["progress","complete","interrupted"].includes(data.type)) {
            if (live() && id(data.scope,data.key) === activeId && data.consumed_frames > 0) caption.textContent = activeText;
            if (data.type !== "progress") { caption.textContent = ""; activeText = ""; activeId = ""; }
          }
          // Only actual processor messages produce consumption/flush ACKs.
          send(data);
        } catch (_) { reject("audio_graph_rejected"); }
      };
      node.onprocessorerror = () => { workletReady = false; reject("audio_graph_rejected"); };
      context.onstatechange = () => {
        if (context.state !== "running") {
          clear(); enable.hidden = false;
          if (authenticated) send({type:"not_ready"});
        } else update();
      };
      workletReady = true;
    })();
    await graph;
    if (failed) { clear(); return false; }
    await context.resume();
    if (failed || !workletReady || context.state !== "running") { clear(); enable.hidden = false; return false; }
    enable.hidden = true;
    send({type:"ready",rate:context.sampleRate,worklet:true,context_state:context.state});
    update();
    return true;
  }
  enable.onclick = () => running().catch(() => { clear(); send({type:"not_ready"}); });
  ws = new WebSocket(location.origin.replace("http:","ws:") + "/audio");
  ws.binaryType = "arraybuffer";
  ws.onopen = () => { send({type:"authenticate",capability}); running().catch(() => { clear(); enable.hidden = false; }); };
  ws.onclose = () => { failed = true; authenticated = false; clear(); };
  ws.onerror = () => reject("audio_transport_failed");
  ws.onmessage = ({data}) => {
    try {
      if (failed || ws.readyState !== WebSocket.OPEN) throw Error("closed");
      if (!(data instanceof ArrayBuffer) || data.byteLength < 8) throw Error("frame");
      const view = new DataView(data), h = view.getUint32(0), n = view.getUint32(4);
      if (!h || h > 16384 || n > 131072 || data.byteLength !== 8+h+n) throw Error("bound");
      const packet = JSON.parse(new TextDecoder("utf-8",{fatal:true}).decode(new Uint8Array(data,8,h)));
      if (!packet || Array.isArray(packet) || typeof packet !== "object"
          || Object.keys(packet).length !== 2 || typeof packet.type !== "string" || !("value" in packet)) throw Error("header");
      const x = packet.value;
      if (!x || Array.isArray(x) || typeof x !== "object" || "type" in x) throw Error("value");
      if (packet.type === "heartbeat") {
        if (n || Object.keys(x).length) throw Error("heartbeat");
        // The server emits heartbeats only after authenticating the capability.
        authenticated = true; latestHeartbeat = performance.now(); update(); return;
      }
      if (!live()) throw Error("not_ready");
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
    } catch (_) { reject("audio_frame_rejected"); }
  };
  setInterval(() => {
    try {
      update();
      if (live()) node.port.postMessage({type:"alive"});
    } catch (_) { reject("audio_graph_rejected"); }
  },250);
})();
