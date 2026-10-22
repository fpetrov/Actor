'use strict';
const $ = (id) => document.getElementById(id);
const rows = new Map();
let version = null;
let refreshing = false;
let creating = false;
let waitingForRoom = null;
const statuses = new Set(['Starting', 'Ready', 'Running', 'Stopping', 'Finished', 'Failed']);
const validName = (value) => typeof value === 'string' && value.length >= 2 && value.length <= 48 && !/[\u0000-\u001f\u007f-\u009f]/.test(value);
const roomCode = (value) => typeof value === 'string' && /^[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{6}$/.test(value);
const feedback = (message, error = false) => { if ($('feedback').textContent !== message) $('feedback').textContent = message; $('feedback').classList.toggle('error', error); };
function connected(online) {
  $('connection').className = `connection ${online ? 'online' : 'offline'}`;
  const label = online ? 'Connected to arena' : 'Server unavailable';
  if ($('connection').lastElementChild.textContent !== label) $('connection').lastElementChild.textContent = label;
}
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(`/api/v1${path}`, { ...options, redirect: 'error', signal: controller.signal, headers: { 'Content-Type': 'application/json', ...options.headers } });
    let body;
    try { body = await response.json(); }
    catch { throw new Error('The server is temporarily unavailable. Please try again.'); }
    if (!response.ok) {
      const message = typeof body?.error?.message === 'string' ? body.error.message.slice(0, 240) : `Request failed (${response.status}).`;
      const error = new Error(message); error.code = body?.error?.code; throw error;
    }
    return body;
  } finally { clearTimeout(timeout); }
}
function validateRoom(room) {
  if (!room || !roomCode(room.roomCode) || !validName(room.displayName) || !statuses.has(room.status) ||
      !['Sandbox', 'SocialDeduction'].includes(room.gameMode) || !Number.isInteger(room.maxPlayers) ||
      room.maxPlayers < 2 || room.maxPlayers > 16 || !Number.isInteger(room.currentPlayers) ||
      room.currentPlayers < 0 || room.currentPlayers > room.maxPlayers) throw new Error('The room list could not be read. Please refresh.');
  return room;
}
function applyVersion(value) {
  if (!value || !['gameVersion', 'protocolVersion'].every(key => typeof value[key] === 'string' && value[key].length > 0 && value[key].length <= 64) ||
      !Number.isInteger(value.maxConcurrentGameServers) || value.maxConcurrentGameServers < 1 || value.maxConcurrentGameServers > 128 ||
      ![value.defaultMaxPlayers, value.socialMinPlayers, value.socialMaxPlayers].every(number => Number.isInteger(number) && number >= 2 && number <= 16) ||
      value.socialMinPlayers > value.socialMaxPlayers) throw new Error('Room settings are unavailable. Please try again.');
  version = value;
  $('limits').textContent = `Game ${value.gameVersion} · ${value.maxConcurrentGameServers} ${value.maxConcurrentGameServers === 1 ? 'room' : 'rooms'} · Social ${value.socialMinPlayers}–${value.socialMaxPlayers} players`;
  $('game-mode').options[0].textContent = `Social Deduction · ${value.socialMinPlayers}–${value.socialMaxPlayers} players`;
  $('game-mode').options[1].textContent = `Sandbox / Investigation · Up to ${value.defaultMaxPlayers} players`;
  return value;
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
  row.children[3].firstElementChild.disabled = ['Stopping', 'Finished', 'Failed'].includes(room.status);
}
async function refresh(manual = false) {
  if (refreshing) return;
  refreshing = true; $('refresh').disabled = true; $('rooms').setAttribute('aria-busy', 'true');
  try {
    const body = await api('/lobbies');
    if (!Array.isArray(body?.lobbies) || body.lobbies.length > 128) throw new Error('The room list could not be read. Please refresh.');
    const lobbies = body.lobbies.map(validateRoom);
    const present = new Set(lobbies.map(room => room.roomCode));
    if (present.size !== lobbies.length) throw new Error('The room list could not be read. Please refresh.');
    for (const [code, row] of rows) if (!present.has(code)) { row.remove(); rows.delete(code); }
    lobbies.forEach(roomRow);
    const createdRoom = lobbies.find(room => room.roomCode === waitingForRoom);
    if (createdRoom && ['Ready', 'Running'].includes(createdRoom.status)) {
      feedback(`Room ${createdRoom.roomCode} is ready. Copy the code and join from the game.`);
      waitingForRoom = null;
    }
    $('empty').hidden = lobbies.length > 0;
    $('empty-title').textContent = 'Room for a new beginning';
    $('empty-description').textContent = 'No rooms are online yet. Create one and invite your crew.';
    $('room-list-note').textContent = 'Rooms refresh automatically. Empty rooms close after a few minutes.';
    $('room-count').textContent = lobbies.length;
    connected(true);
    if (manual && !createdRoom) feedback('Room list updated.');
  } catch (error) {
    connected(false);
    $('empty-title').textContent = 'The station is unavailable';
    $('empty-description').textContent = 'We could not load rooms. Refresh to try again, or return to the game lobby.';
    $('room-list-note').textContent = rows.size ? 'Showing the last room list. It may be out of date; refresh before joining.' : 'The room list is unavailable. This does not mean there are no rooms.';
    feedback(error.name === 'AbortError' ? 'The server took too long to respond. Try again.' : error.message, true);
  }
  finally { refreshing = false; $('refresh').disabled = false; $('rooms').setAttribute('aria-busy', 'false'); }
}
async function copyCode(code) {
  try {
    let copied = false;
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(code); copied = true; } catch { /* Try the HTTP-compatible fallback. */ }
    }
    if (!copied) {
      const previous = document.activeElement;
      const field = document.createElement('input'); field.value = code; field.className = 'copy-helper'; field.readOnly = true; field.tabIndex = -1;
      field.setAttribute('aria-hidden', 'true');
      document.body.append(field);
      try { field.select(); copied = document.execCommand('copy'); }
      finally { field.remove(); previous?.focus({ preventScroll: true }); }
      if (!copied) throw new Error('copy_unavailable');
    }
    feedback(`Room code ${code} copied. Paste it in the game lobby.`);
  } catch { feedback(`Copy is unavailable. Select room code ${code} and copy it manually.`, true); }
}
$('refresh').addEventListener('click', () => refresh(true));
$('create-form').addEventListener('submit', async event => {
  event.preventDefault(); if (creating) return;
  const name = $('room-name').value.trim(); const mode = $('game-mode').value;
  if (!validName(name)) { feedback('Use 2–48 characters for the room name.', true); $('room-name').focus(); return; }
  if (!['SocialDeduction', 'Sandbox'].includes(mode)) { feedback('Choose a game mode.', true); return; }
  creating = true; $('create').disabled = true; $('create').textContent = 'Creating…';
  $('create-form').setAttribute('aria-busy', 'true'); $('room-name').disabled = true; $('game-mode').disabled = true;
  try {
    if (!version) applyVersion(await api('/game/version'));
    const room = validateRoom(await api('/lobbies', { method: 'POST', body: JSON.stringify({ displayName: name, gameMode: mode, maxPlayers: mode === 'SocialDeduction' ? version.socialMaxPlayers : version.defaultMaxPlayers, gameVersion: version.gameVersion, protocolVersion: version.protocolVersion }) }));
    $('created-code').textContent = room.roomCode; $('created').hidden = false;
    waitingForRoom = room.roomCode;
    $('copy-created').onclick = () => copyCode(room.roomCode);
    feedback(`Room ${room.roomCode} is starting. It will be ready to join shortly.`);
    await refresh();
  } catch (error) {
    if (error.code === 'version_mismatch') version = null;
    feedback(error.name === 'AbortError' ? 'The request timed out. Refresh the list before trying again.' : error.message, true);
  }
  finally { creating = false; $('create').disabled = false; $('create').textContent = 'Create room ↗'; $('create-form').setAttribute('aria-busy', 'false'); $('room-name').disabled = false; $('game-mode').disabled = false; }
});
api('/game/version').then(applyVersion).catch(() => {});
refresh(); setInterval(() => { if (!document.hidden) refresh(); }, 4000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
