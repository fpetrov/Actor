'use strict';
const $ = id => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
let current = null;
let busy = false;
let configureRevision = 0;
let appliedConfigureRevision = 0;
const labels = {observer:'Наблюдатель / Game',model:'Codex',tts:'Qwen BF16',obs:'OBS',audio:'Маршрут голоса',chat:'Twitch chat'};
const count = (value, maximum=1000000) => Number.isSafeInteger(value) && value >= 0 && value <= maximum ? String(value) : 'неизвестно';
function render(state) {
  current = state;
  $('state').textContent = state.state + (state.reason ? ' · ' + state.reason : '');
  const rows = [];
  for (const [key,label] of Object.entries(labels)) {
    const term = document.createElement('dt'); term.textContent = label;
    const value = document.createElement('dd'); value.textContent = key === 'chat' && state.chat_enabled === false ? 'Отключён' : (state.ready?.[key] ? 'Готово' : 'Не готово');
    rows.push(term,value);
  }
  $('dependencies').replaceChildren(...rows);
  const selected = $('rooms').value;
  const placeholder = document.createElement('option'); placeholder.value=''; placeholder.textContent='Выберите матч';
  const options = [placeholder];
  for (const room of state.rooms ?? []) {
    const option = document.createElement('option'); option.value=room.room_code;
    option.textContent = `${room.room_code} · ${room.mode ?? ''} · ${room.players ?? '?'}/${room.capacity ?? '?'}`;
    options.push(option);
  }
  $('rooms').replaceChildren(...options); $('rooms').value=selected;
  $('publication').textContent = state.channel ? `Публичный канал: ${state.channel}. Матч: ${state.room_code ?? 'не выбран'}.` : 'Канал пока не подтверждён.';
  $('live').textContent = state.live === true ? 'Live подтверждён.' : 'Live не подтверждён; текущий этап указан выше.';
  $('public-start').textContent = state.channel ? `Начать публичный эфир на ${state.channel}` : 'Начать публичный эфир';
  $('public-start').disabled = busy || state.state !== 'PreviewReady' || !$('confirm-public').checked || !state.channel;
  const commentary = state.commentary ?? {};
  $('silence').textContent = commentary.idle_reason ? `Причина тишины: ${commentary.idle_reason}` : '';
  if (configureRevision === appliedConfigureRevision) {
    if (commentary.idle_after_seconds !== undefined) $('idle-seconds').value=commentary.idle_after_seconds;
    if (typeof commentary.idle_enabled === 'boolean') $('idle-enabled').checked=commentary.idle_enabled;
    if (typeof commentary.allow_memory_callbacks === 'boolean') $('memory-callbacks').checked=commentary.allow_memory_callbacks;
  }
  $('speech-queue').textContent = `Очередь речи: ${count(commentary.pending_speech_segments, 2)} сегм.`;
  const modes = commentary.calls_by_mode ?? {};
  $('mode-calls').textContent = `Допущенные запросы — реакции: ${count(modes.reactive)} · сводки: ${count(modes.idle_recap)} · напоминания: ${count(modes.memory_callback)}`;
  const usage=state.usage ?? {};
  $('usage').textContent=`Вызовы: ${usage.calls ?? usage.calls_reserved ?? 'неизвестно'} · input: ${usage.input_tokens ?? usage.input_tokens_charged ?? 'неизвестно'} · output: ${usage.output_tokens ?? usage.output_tokens_charged ?? 'неизвестно'} · unknown: ${usage.unknown_usage ?? usage.unknown_usage_calls ?? 'неизвестно'}`;
  $('last-spoken').textContent=state.last_spoken?.text || 'Пока ничего не прозвучало.';
}
async function request(action,payload) {
  if (busy && action !== 'emergency_stop') return;
  const submittedRevision = configureRevision;
  busy=true; $('error').textContent=''; if(current) render(current);
  try {
    const response=await fetch('/api/action',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify({action,payload})});
    const value=await response.json();
    if(!response.ok) throw new Error(value.error || 'operator_action_unavailable');
    if(action === 'configure' && configureRevision === submittedRevision) appliedConfigureRevision = configureRevision;
    render(value);
  } catch(error) { $('error').textContent='Действие не выполнено: '+error.message; }
  finally {busy=false; if(current) render(current);}
}
$('rooms').addEventListener('change',()=>{$('room').value=$('rooms').value;});
$('preview-form').addEventListener('submit',event=>{event.preventDefault(); request('preview',{room_code:$('room').value.trim().toUpperCase()});});
$('confirm-public').addEventListener('change',()=>{if(current)render(current);});
$('public-start').addEventListener('click',()=>{if(current && $('confirm-public').checked) request('public_start',{channel:current.channel,broadcast_session_id:current.broadcast_session_id,confirmation:'START PUBLIC STREAM'});});
$('stop').addEventListener('click',()=>request('stop',{}));
$('emergency').addEventListener('click',()=>request('emergency_stop',{}));
for (const id of ['idle-enabled','idle-seconds','memory-callbacks']) {
  for (const event of ['input','change']) $(id).addEventListener(event,()=>{configureRevision += 1;});
}
$('idle-form').addEventListener('submit',event=>{event.preventDefault();request('configure',{idle_enabled:$('idle-enabled').checked,idle_after_seconds:Number($('idle-seconds').value),allow_memory_callbacks:$('memory-callbacks').checked});});
async function poll(){try{const response=await fetch('/api/status',{credentials:'same-origin',cache:'no-store'}); const value=await response.json(); if(!response.ok)throw new Error(value.error || 'operator_unavailable');render(value);}catch(error){$('error').textContent='Контроллер недоступен: '+error.message;$('public-start').disabled=true;}finally{setTimeout(poll,1000);}}
poll();
