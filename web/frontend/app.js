'use strict';
const $ = (id) => document.getElementById(id);
const rows = new Map();
let version = null;
let refreshing = false;
let creating = false;
let waitingForRoom = null;
const feedback = (message, error = false) => { $('feedback').textContent = message; $('feedback').classList.toggle('error', error); };
function connected(online) { $('connection').className = `connection ${online ? 'online' : 'offline'}`; $('connection').lastElementChild.textContent = online ? 'Connected to arena' : 'Server unavailable'; }
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`/api/v1${path}`, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...options.headers } });
    let body;
    try { body = await response.json(); }
    catch { throw new Error('The server is temporarily unavailable. Please try again.'); }
    if (!response.ok) throw new Error(body.error?.message || `Request failed (${response.status}).`);
    return body;
  } finally { clearTimeout(timeout); }
}
function roomRow(room) {
  let row = rows.get(room.roomCode);
  if (!row) {
    row = document.createElement('tr');
    const name = document.createElement('td');
    name.append(Object.assign(document.createElement('span'), { className: 'room-title' }), Object.assign(document.createElement('span'), { className: 'room-code' }));
    const players = document.createElement('td');
    const state = document.createElement('td');
    state.append(Object.assign(document.createElement('span'), { className: 'badge' }));
    const action = document.createElement('td');
    const copy = Object.assign(document.createElement('button'), { className: 'button secondary copy-button', type: 'button', textContent: 'Copy code' });
    copy.addEventListener('click', () => copyCode(room.roomCode));
    action.append(copy); row.append(name, players, state, action);
    rows.set(room.roomCode, row); $('room-list').append(row);
  }
  row.children[0].children[0].textContent = room.displayName;
  row.children[0].children[1].textContent = `${room.roomCode} · ${room.gameMode === 'SocialDeduction' ? 'Social Deduction' : 'Sandbox / Investigation'}`;
  row.children[1].textContent = `${room.currentPlayers}/${room.maxPlayers}`;
  row.children[2].firstElementChild.textContent = room.status;
  row.children[2].firstElementChild.className = `badge ${room.status.toLowerCase()}`;
  row.children[3].firstElementChild.setAttribute('aria-label', `Copy room code ${room.roomCode}`);
}
async function refresh(manual = false) {
  if (refreshing) return;
  refreshing = true; $('refresh').disabled = true;
  try {
    const body = await api('/lobbies');
    const present = new Set(body.lobbies.map(room => room.roomCode));
    for (const [code, row] of rows) if (!present.has(code)) { row.remove(); rows.delete(code); }
    body.lobbies.forEach(roomRow);
    const createdRoom = body.lobbies.find(room => room.roomCode === waitingForRoom);
    if (createdRoom && ['Ready', 'Running'].includes(createdRoom.status)) {
      feedback(`Room ${createdRoom.roomCode} is ready. Copy the code and join from the game.`);
      waitingForRoom = null;
    }
    $('empty').hidden = body.lobbies.length > 0;
    $('room-count').textContent = body.lobbies.length;
    connected(true);
    if (manual) feedback('Room list updated.');
  } catch (error) { connected(false); feedback(error.name === 'AbortError' ? 'The server took too long to respond. Try again.' : error.message, true); }
  finally { refreshing = false; $('refresh').disabled = false; }
}
async function copyCode(code) {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(code);
    else {
      const field = document.createElement('input'); field.value = code; field.style.position = 'fixed'; field.style.left = '-9999px';
      document.body.append(field); field.select(); const copied = document.execCommand('copy'); field.remove();
      if (!copied) throw new Error('Copy is unavailable. Select the room code and copy it manually.');
    }
    feedback(`Room code ${code} copied. Paste it in the game lobby.`);
  } catch (error) { feedback(error.message, true); }
}
$('refresh').addEventListener('click', () => refresh(true));
$('create-form').addEventListener('submit', async event => {
  event.preventDefault(); if (creating) return;
  creating = true; $('create').disabled = true; $('create').textContent = 'Creating…';
  try {
    version ||= await api('/game/version');
    const room = await api('/lobbies', { method: 'POST', body: JSON.stringify({ displayName: $('room-name').value.trim(), gameMode: $('game-mode').value, maxPlayers: $('game-mode').value === 'SocialDeduction' ? (version.socialMaxPlayers || 6) : (version.defaultMaxPlayers || 8), gameVersion: version.gameVersion, protocolVersion: version.protocolVersion }) });
    $('created-code').textContent = room.roomCode; $('created').hidden = false;
    waitingForRoom = room.roomCode;
    $('copy-created').onclick = () => copyCode(room.roomCode);
    feedback(`Room ${room.roomCode} is starting. It will be ready to join shortly.`);
    await refresh();
  } catch (error) { feedback(error.name === 'AbortError' ? 'The request timed out. Refresh the list before trying again.' : error.message, true); }
  finally { creating = false; $('create').disabled = false; $('create').textContent = 'Create room ↗'; }
});
api('/game/version').then(value => { version = value; $('limits').textContent = `${value.maxConcurrentGameServers || 5} rooms · Up to ${value.defaultMaxPlayers || 8} players per room`; }).catch(() => {});
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
