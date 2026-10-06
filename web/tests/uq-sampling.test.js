import test from 'node:test';
import assert from 'node:assert/strict';
import { samplingCountError, samplingHint } from '../src/uq-sampling.js';

test('Sobol count validation accepts powers of two within the study limit', () => {
  for (const count of [1, 2, 64, 128, 65536]) assert.equal(samplingCountError('sobol', count), '');
  for (const count of [0, -1, 1.5, 100, 100000, 131072]) assert.match(samplingCountError('sobol', count), /power-of-two/);
});

test('other samplers allow arbitrary case counts and explain their behavior', () => {
  for (const method of ['monte_carlo', 'lhs', 'halton']) {
    assert.equal(samplingCountError(method, 100), '');
    assert.ok(samplingHint(method).length > 0);
  }
  assert.match(samplingHint('sobol'), /65,536/);
});
