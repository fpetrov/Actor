// Pure Node DOM fixtures: no browser, HTTP listener or external network.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const frontend = path.resolve(__dirname, '../frontend');
const code = fs.readFileSync(path.join(frontend, 'app.js'), 'utf8');
const html = fs.readFileSync(path.join(frontend, 'index.html'), 'utf8');
const version = { gameVersion: '0.9.0', protocolVersion: '4', maxConcurrentGameServers: 5,
  defaultMaxPlayers: 8, socialMinPlayers: 4, socialMaxPlayers: 6 };
const room = { roomCode: 'ABC234', displayName: 'A quiet station', gameMode: 'SocialDeduction',
  maxPlayers: 6, currentPlayers: 1, status: 'Ready' };
const settle = () => new Promise(resolve => setImmediate(resolve));

function fixture(options = {}) {
  const state = { requests: [], rooms: [], post: room, copy: true, failList: false, version: { ...version },
    clipboard: null, handlers: new Map() };
  Object.assign(state, options);
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {};
      this.hidden = false; this.disabled = false; this.className = ''; this.textContent = ''; this.value = ''; }
    append(...children) { for (const child of children) { child.parent = this; this.children.push(child); } }
    remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); }
    get firstElementChild() { return this.children[0]; }
    get lastElementChild() { return this.children.at(-1); }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(name, action) { this.listeners[name] = action; }
    focus() { document.activeElement = this; }
    select() { this.focus(); }
    get classList() { return { toggle: (name, on) => {
      const values = new Set(this.className.split(' ').filter(Boolean));
      if (on) values.add(name); else values.delete(name); this.className = [...values].join(' ');
    } }; }
  }
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element('div')]));
  const document = { hidden: false, body: new Element('body'), activeElement: null,
    getElementById: id => { assert.ok(elements.has(id), `Missing actual HTML node ${id}`); return elements.get(id); },
    createElement: name => new Element(name), execCommand: () => state.copy,
    addEventListener: (name, action) => state.handlers.set(name, action) };
  elements.get('connection').append(new Element('span'), new Element('span'));
  elements.get('room-name').value = 'New expedition';
  elements.get('game-mode').value = 'SocialDeduction';
  elements.get('game-mode').options = [new Element('option'), new Element('option')];
  elements.get('empty-title').textContent = 'Finding your crew';
  elements.get('empty-description').textContent = 'Loading available rooms…';
  const context = vm.createContext({ document, window: { isSecureContext: false }, navigator: {}, AbortController,
    setTimeout: () => 1, clearTimeout: () => {}, setInterval: () => 1,
    fetch: async (url, options) => {
      state.requests.push({ url, options });
      const method = options.method || 'GET';
      const handler = state.handlers.get(`${method} ${url}`);
      if (handler) return handler(url, options);
      if (url === '/api/v1/lobbies' && method === 'GET' && state.failList) throw new Error('Connection unavailable');
      const body = url.endsWith('/game/version') ? state.version : method === 'POST' ? state.post : { lobbies: state.rooms };
      return { ok: true, status: 200, json: async () => structuredClone(body) };
    } });
  vm.runInContext(code, context, { filename: 'app.js' });
  return { state, context, document, get: id => elements.get(id),
    submit: () => elements.get('create-form').listeners.submit({ preventDefault() {} }) };
}

test('release channel and transport limits remain honest after version arrives', async () => {
  const f = fixture(); await settle();
  assert.match(html, /href="https:\/\/github.com\/fpetrov\/Actor\/releases">View macOS releases/);
  assert.match(html, /when available/); assert.match(html, /Windows build unavailable/);
  assert.match(html, /Limited test RC/); assert.match(html, /Unencrypted HTTP\/UDP gameplay/);
  assert.match(f.get('limits').textContent, /Game 0\.9\.0 · 5 rooms · Social 4–6 players/);
  assert.doesNotMatch(code, /\$\('limits'\).*HTTP\/UDP/); // The immutable notice is a separate node.
  assert.match(f.get('game-mode').options[1].textContent, /Up to 8 players/);
});

test('offline initial request is not represented as an empty healthy station', async () => {
  const f = fixture({ failList: true }); await settle();
  assert.match(f.get('empty-title').textContent, /unavailable/);
  assert.match(f.get('room-list-note').textContent, /does not mean there are no rooms/);
  assert.equal(f.get('rooms').attributes['aria-busy'], 'false');
  assert.match(f.get('connection').className, /offline/);
});

test('last room list remains visible with a stale warning after connection loss', async () => {
  const f = fixture(); await settle(); f.state.rooms = [room]; await f.context.refresh();
  f.state.failList = true; await f.context.refresh();
  assert.equal(f.get('room-list').children.length, 1);
  assert.match(f.get('room-list-note').textContent, /out of date/);
  f.state.failList = false; f.state.rooms = []; await f.context.refresh();
  assert.equal(f.get('room-list').children.length, 0);
  assert.match(f.get('empty-description').textContent, /No rooms are online/);
});

test('untrusted room names stay text and malformed lists do not partly replace the UI', async () => {
  const f = fixture(); await settle();
  const name = '<img src=x onerror=alert(1)>';
  f.state.rooms = [{ ...room, displayName: name }]; await f.context.refresh();
  const row = f.get('room-list').firstElementChild;
  assert.equal(row.children[0].children[0].textContent, name);
  f.state.rooms = [{ ...room, roomCode: 'DEF567' }, { ...room, status: null }]; await f.context.refresh();
  assert.equal(f.get('room-list').children.length, 1);
  assert.equal(f.get('room-list').firstElementChild, row);
  assert.match(f.get('connection').className, /offline/);
});

test('trimmed invalid names never allocate a room', async () => {
  const f = fixture(); await settle();
  for (const value of ['  ', ' x ', 'a\u0001b', 'x'.repeat(49)]) {
    f.get('room-name').value = value; await f.submit();
  }
  assert.equal(f.state.requests.filter(r => r.options.method === 'POST').length, 0);
  assert.equal(f.document.activeElement, f.get('room-name'));
  assert.match(f.get('feedback').textContent, /2–48/);
});

test('one in-flight create is fenced and uses advertised social capacity', async () => {
  const f = fixture(); await settle(); let release;
  f.state.handlers.set('POST /api/v1/lobbies', () => new Promise(resolve => { release = resolve; }));
  const pending = f.submit(); await settle(); await f.submit();
  assert.equal(f.state.requests.filter(r => r.options.method === 'POST').length, 1);
  assert.equal(f.get('create-form').attributes['aria-busy'], 'true');
  assert.equal(f.get('room-name').disabled, true); assert.equal(f.get('game-mode').disabled, true);
  const request = f.state.requests.find(r => r.options.method === 'POST');
  assert.equal(JSON.parse(request.options.body).maxPlayers, 6);
  release({ ok: true, status: 201, json: async () => room }); await pending;
  assert.equal(f.get('create-form').attributes['aria-busy'], 'false'); assert.equal(f.get('create').disabled, false);
  assert.equal(f.get('room-name').disabled, false);
  assert.equal(f.get('created-code').textContent, room.roomCode);
});

test('version mismatch invalidates the cached version before another create', async () => {
  const f = fixture(); await settle(); let count = 0;
  f.state.handlers.set('POST /api/v1/lobbies', async () => {
    if (++count === 1) return { ok: false, status: 409, json: async () => ({ error: { code: 'version_mismatch', message: 'Game updated; retry.' } }) };
    return { ok: true, status: 201, json: async () => room };
  });
  await f.submit(); f.state.version.gameVersion = '0.9.1'; await f.submit();
  const requests = f.state.requests.filter(r => r.options.method === 'POST');
  assert.equal(JSON.parse(requests[0].options.body).gameVersion, '0.9.0');
  assert.equal(JSON.parse(requests[1].options.body).gameVersion, '0.9.1');
  assert.ok(f.state.requests.every(r => r.options.redirect === 'error'));
});

test('denied modern clipboard uses fallback and restores keyboard focus', async () => {
  const f = fixture(); await settle();
  f.context.window.isSecureContext = true;
  f.context.navigator.clipboard = { writeText: async () => { throw new Error('permission denied'); } };
  f.get('refresh').focus(); await f.context.copyCode(room.roomCode);
  assert.equal(f.document.activeElement, f.get('refresh'));
  assert.equal(f.document.body.children.length, 0);
  assert.match(f.get('feedback').textContent, /copied/);
  f.state.copy = false; await f.context.copyCode(room.roomCode);
  assert.equal(f.document.activeElement, f.get('refresh'));
  assert.match(f.get('feedback').textContent, /copy it manually/);
  assert.doesNotMatch(f.get('feedback').textContent, /permission denied/);
});

test('HTML has keyboard/live-region/table semantics and no inline executable handlers', () => {
  assert.match(html, /class="skip-link" href="#main"/); assert.match(html, /id="main"[^>]*tabindex="-1"/);
  assert.match(html, /id="connection"[^>]*role="status"/); assert.match(html, /<caption class="sr-only">/);
  assert.match(html, /class="table-wrap"[^>]*tabindex="0"/); assert.match(html, /<noscript>/);
  assert.doesNotMatch(html, /\son(?:click|submit|load|error)=/i);
  assert.match(html, /Content-Security-Policy/); assert.doesNotMatch(html, /unsafe-inline|unsafe-eval/);
});
