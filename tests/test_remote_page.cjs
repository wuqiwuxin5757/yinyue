// 手机录音的异步事件回归；使用 DOM/MediaRecorder 替身，不启动浏览器或麦克风。
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'remote.py'), 'utf8');
const script = source.match(/PAGE = """([\s\S]*?)"""/)[1].match(/<script>([\s\S]*?)<\/script>/)[1];

function setup() {
  const elements = new Map(), timers = new Map(), recordings = [];
  let grant, requests = 0, tracksStopped = 0;
  function element() {
    return {value: '', checked: false, handlers: {}, children: [], classList: {add(){}, remove(){}},
      addEventListener(name, fn) {this.handlers[name] = fn},
      setPointerCapture(){}, appendChild(x) {this.children.push(x)}};
  }
  const document = {hidden: false, handlers: {},
    getElementById(id) {if (!elements.has(id)) elements.set(id, element()); return elements.get(id)},
    createElement: element, querySelectorAll() {return []},
    addEventListener(name, fn) {this.handlers[name] = fn}};
  class Recorder {
    constructor(stream) {this.state = 'inactive'; this.mimeType = 'audio/webm'; recordings.push(this)}
    start() {this.state = 'recording'}
    stop() {this.state = 'inactive'; this.onstop()}
  }
  const context = vm.createContext({document, location: {search: '?token=test'}, URLSearchParams,
    window: {isSecureContext: true}, navigator: {mediaDevices: {getUserMedia() {
      requests++; return new Promise(resolve => {grant = resolve});
    }}}, MediaRecorder: Recorder, Blob, Image: class {},
    setTimeout(fn) {const id = timers.size + 1; timers.set(id, fn); return id},
    clearTimeout(id) {timers.delete(id)}});
  vm.runInContext(script, context);
  const mic = document.getElementById('mic'), event = {preventDefault(){}, pointerId: 1};
  return {document, mic, event, recordings, timers,
    grant() {grant({getTracks() {return [{stop() {tracksStopped++}}]}})},
    stats() {return {requests, tracksStopped}}};
}

test('松手后才获得麦克风权限，不会继续录音', async () => {
  const s = setup();
  const pending = s.mic.handlers.pointerdown(s.event);
  s.mic.handlers.pointerup(s.event);
  s.grant(); await pending;
  assert.equal(s.recordings.length, 0);
  assert.equal(s.stats().tracksStopped, 1);
});

test('等待权限期间重复按下，不会创建两个录音器', async () => {
  const s = setup();
  const pending = s.mic.handlers.pointerdown(s.event);
  await s.mic.handlers.pointerdown(s.event);
  assert.equal(s.stats().requests, 1);
  s.grant(); await pending;
  assert.equal(s.recordings.length, 1);
  s.mic.handlers.pointerup(s.event);
});

for (const eventName of ['pointercancel', 'lostpointercapture']) {
  test(`${eventName} 会停止录音并释放麦克风`, async () => {
    const s = setup(), pending = s.mic.handlers.pointerdown(s.event);
    s.grant(); await pending;
    s.mic.handlers[eventName](s.event);
    assert.equal(s.recordings[0].state, 'inactive');
    assert.equal(s.stats().tracksStopped, 1);
    assert.equal(s.timers.size, 0);
  });
}

test('切到后台会停止录音', async () => {
  const s = setup(), pending = s.mic.handlers.pointerdown(s.event);
  s.grant(); await pending;
  s.document.hidden = true;
  s.document.handlers.visibilitychange();
  assert.equal(s.stats().tracksStopped, 1);
});

test('录音达到上限会自动停止', async () => {
  const s = setup(), pending = s.mic.handlers.pointerdown(s.event);
  s.grant(); await pending;
  [...s.timers.values()][0]();
  assert.equal(s.recordings[0].state, 'inactive');
  assert.equal(s.stats().tracksStopped, 1);
});
