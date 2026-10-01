import test from 'node:test';
import assert from 'node:assert/strict';
import { intervalSeries, seriesCSV, reducePoints, requestGuard } from '../src/results-data.js';

test('inclusive interval and full-resolution CSV with quoted headers and gaps', () => {
  const data = { timestamps: [0, 1, 2, 3], channels: { 'Load,"A"': { unit: 'N', values: [4, null, 6, 7] } } };
  const interval = intervalSeries(data, 1, 2);
  assert.deepEqual(interval.timestamps, [1, 2]);
  assert.equal(seriesCSV(interval), 'Time (s),"Load,""A"" (N)"\r\n1,\r\n2,6\r\n');
  assert.deepEqual(intervalSeries(data, 1.1, 1.9).timestamps, []);
});

test('reduction preserves extrema, order and gaps', () => {
  const times = Array.from({ length: 1000 }, (_, i) => i);
  const values = times.map(() => 0); values[251] = 99; values[252] = -75; values[253] = null;
  const points = reducePoints(times, values, 0, 999, 10);
  assert(points.some(([t, v]) => t === 251 && v === 99));
  assert(points.some(([t, v]) => t === 252 && v === -75));
  assert(points.some(([t, v]) => t === 253 && v === null));
  assert(points.some(([t]) => t === 254));
  assert(points.length < 60);
  assert(points.every((p, i) => i === 0 || points[i - 1][0] < p[0]));
});

test('stale requests are rejected after selection or workspace changes', () => {
  const guard = requestGuard();
  const first = guard.invalidate(); const second = guard.invalidate();
  assert(!guard.current(first)); assert(guard.current(second));
  guard.invalidate(); assert(!guard.current(second));
});

test('channel cache reuses arrays, evicts by size, and clears on run changes', async () => {
  const { channelCache } = await import('../src/results-data.js');
  const cache = channelCache(64);
  const a = { unit: 'rpm', values: [1, 2] }, b = { unit: 'kW', values: [3, 4] };
  cache.put({ timestamps: [0, 1], channels: { A: a, B: b } });
  assert.deepEqual(cache.missing(['A', 'B', 'C']), ['C']);
  assert.equal(cache.select(['A']).channels.A.values, a.values);
  cache.put({ timestamps: [0, 1], channels: { C: { unit: 'm', values: [5, 6] } } });
  assert.deepEqual(cache.missing(['A', 'B', 'C']), ['B']);
  cache.clear();
  assert.deepEqual(cache.missing(['A', 'C']), ['A', 'C']);
  assert.equal(cache.select(['A']).timestamps, null);
});
