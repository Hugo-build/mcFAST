import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';
import { createPlayback, unwrapDegrees, platformTransform } from '../src/playback.js';

const data = { timestamps: [2, 3, 4], channels: {
  Azimuth: { values: [350, 10, 30] }, RotSpeed: { values: [6, 8, 10] },
}};
const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);

test('unwraps forward and reverse crossings without rotating backwards', () => {
  assert.deepEqual(unwrapDegrees([350, 10, 30]), [350, 370, 390]);
  assert.deepEqual(unwrapDegrees([10, 350, 330]), [10, -10, -30]);
});
test('loads paused, interpolates, and clamps seeking to recorded time', () => {
  const player = createPlayback(data);
  assert.equal(player.playing, false);
  assert.equal(player.time, 2);
  player.seek(2.5);
  assert.equal(player.sample().Azimuth, 360);
  assert.equal(player.sample().RotSpeed, 7);
  player.seek(-1); assert.equal(player.time, 2);
  player.seek(100); assert.equal(player.time, 4);
  assert.equal(player.sample().Azimuth, 390);
});
test('pause, resume, speed changes and endpoint replay use one clock', () => {
  const player = createPlayback(data);
  player.play(); player.advance(.5); assert.equal(player.time, 2.5);
  player.pause(); player.advance(1); assert.equal(player.time, 2.5);
  player.setSpeed(2); player.play(); player.advance(.25); assert.equal(player.time, 3);
  player.advance(10); assert.equal(player.time, 4); assert.equal(player.playing, false);
  player.play(); assert.equal(player.time, 2);
  player.setSpeed(.25); player.advance(1); assert.equal(player.time, 2.25);
  player.setSpeed(.5); player.advance(1); assert.equal(player.time, 2.75);
});
test('platform translation and rotations respect reflected coordinates', () => {
  assert.deepEqual(platformTransform({PtfmSurge: 1, PtfmSway: 2, PtfmHeave: 3}).position.toArray(), [1,3,2]);
  const roll = platformTransform({PtfmRoll: 90});
  const pitch = platformTransform({PtfmPitch: 90});
  const yaw = platformTransform({PtfmYaw: 90});
  const rolled = new THREE.Vector3(0,1,0).applyQuaternion(roll.quaternion);
  close(rolled.z, -1);
  const pitched = new THREE.Vector3(0,1,0).applyQuaternion(pitch.quaternion);
  close(pitched.x, 1);
  const yawed = new THREE.Vector3(1,0,0).applyQuaternion(yaw.quaternion);
  close(yawed.z, 1);
  assert.deepEqual(platformTransform({}).position.toArray(), [0,0,0]);
});
test('rotor azimuth rotates blade 1 clockwise in the reflected scene basis', () => {
  const rotor = new THREE.Euler(0, Math.PI / 2, -Math.PI / 2);
  const tip = new THREE.Vector3(0,1,0).applyEuler(rotor);
  close(tip.z, -1);
});
