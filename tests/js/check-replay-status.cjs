'use strict';

// Node >=18, no dependencies. Run after the Python fixture test (see release checklist).
// Only document/Plotly event boundaries are doubled. The generated adapter is real.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

assert.ok(Number(process.versions.node.split('.')[0]) >= 18, 'Node >=18 required');
const root = process.argv[2];
assert.ok(root, 'Usage: node tests/js/check-replay-status.cjs <pytest-basetemp> [missing-event|stale]');
const contracts = fs.readdirSync(root, {withFileTypes: true})
  .filter(entry => entry.isDirectory())
  .map(entry => path.join(root, entry.name, 'contract.json'))
  .filter(file => fs.existsSync(file));
assert.equal(contracts.length, 1, 'Generate exactly one fresh contract fixture');
const {script, expected, slider_targets: targets} = JSON.parse(fs.readFileSync(contracts[0], 'utf8'));
const fields = Object.fromEntries(['replay-time', 'replay-speed', 'replay-ttc']
  .map(id => [id, {textContent: '載入中'}]));
const handlers = {};
let code = script;
const mutation = process.argv[3];
assert.ok([undefined, 'missing-event', 'stale'].includes(mutation), 'Unknown mutation');
if (mutation) {
  const original = 'showReplayStatus(event.name);';
  assert.ok(code.includes(original), 'Mutation must affect the real event update');
  code = code.replace(original, mutation === 'stale' ? "showReplayStatus('0.0');" : '');
}
vm.runInNewContext(code, {document: {getElementById(id) {
  if (id === 'aeb-replay') return {on(name, callback) {handlers[name] = callback;}};
  assert.ok(fields[id], `Unexpected status field ${id}`);
  return fields[id];
}}}, {timeout: 1000});
const values = () => Object.values(fields).map(field => field.textContent);
assert.equal(Object.keys(expected).length, 151);
assert.deepEqual(values(), ['0.0 s · monitor', '10.0 m/s', '無可用數值'], 'initial state');
const listener = handlers.plotly_animatingframe;
assert.equal(typeof listener, 'function', 'native frame event listener');
for (const [name, want] of Object.entries(expected)) {
  listener({name});
  assert.deepEqual(values(), want, `play frame ${name}`);
}
// Plotly's native slider uses animate; feed its targets in nonsequential order.
// This checks our event consumer, not that a browser emits these events.
for (const index of [75, 150, 0, 113, 2]) {
  const name = targets[index];
  listener({name});
  assert.deepEqual(values(), expected[name], `slider frame ${name}`);
}
const before = values();
for (const event of [{name: 'unknown'}, {}]) {
  listener(event);
  assert.deepEqual(values(), before, 'unknown frame preserves last valid display');
}
console.log(JSON.stringify({frames_checked: 151, initial_state: true,
  slider_jumps_checked: 5, unknown_preserves_status: true, rendered: false}));
