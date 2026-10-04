import test from 'node:test';
import assert from 'node:assert/strict';
import { workerSlots, terminal } from '../src/simulation-panel.js';
const targets = [{ target_id: 'local', name: 'Local', ready: true, mode: 'local', cpu_capacity: 4 }];
test('slots enforce local capacity and integer counts', () => {
  assert.deepEqual(workerSlots(targets, { local: 2 }), { local: 2 });
  assert.throws(() => workerSlots(targets, { local: 5 }), /at most 4/);
  assert.throws(() => workerSlots([{ ...targets[0], ready: false }], { local: 1 }), /unavailable/);
  assert.throws(() => workerSlots(targets, { local: 1.5 }), /integers/);
  assert.throws(() => workerSlots(targets, {}), /at least one/);
});
test('active and terminal case states', () => {
  assert.equal(terminal('running'), false);
  assert.equal(terminal('queued'), false);
  for (const state of ['failed', 'completed', 'cancelled', 'interrupted']) assert.equal(terminal(state), true);
});
