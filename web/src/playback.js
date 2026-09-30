import * as THREE from 'three';

export function unwrapDegrees(values) {
  const result = [values[0]];
  for (let i = 1; i < values.length; i++) {
    const delta = ((values[i] - values[i - 1] + 180) % 360 + 360) % 360 - 180;
    result.push(result[i - 1] + delta);
  }
  return result;
}

export function createPlayback(data) {
  const times = data.timestamps;
  const channels = Object.fromEntries(Object.entries(data.channels).map(([name, channel]) =>
    [name, name === 'Azimuth' ? unwrapDegrees(channel.values) : channel.values]));
  let time = times[0];
  let playing = false;
  let speed = 1;
  function seek(value) { time = Math.max(times[0], Math.min(times.at(-1), value)); }
  function sample() {
    let lo = 0, hi = times.length - 1;
    while (hi - lo > 1) {
      const mid = Math.floor((lo + hi) / 2);
      if (times[mid] <= time) lo = mid; else hi = mid;
    }
    const fraction = (time - times[lo]) / (times[hi] - times[lo]);
    return Object.fromEntries(Object.entries(channels).map(([name, values]) =>
      [name, values[lo] + fraction * (values[hi] - values[lo])]));
  }
  return {
    get time() { return time; }, get playing() { return playing; },
    seek, sample,
    play() { if (time >= times.at(-1)) seek(times[0]); playing = true; },
    pause() { playing = false; },
    setSpeed(value) { if ([.25, .5, 1, 2].includes(value)) speed = value; },
    advance(seconds) {
      if (!playing) return;
      seek(time + Math.max(0, seconds) * speed);
      if (time >= times.at(-1)) playing = false;
    },
  };
}

// Existing geometry maps OpenFAST (x,y,z) to Three.js (x,z,y).
// This basis is reflected, so rotations must also be conjugated by it.
const basis = new THREE.Matrix4().set(1,0,0,0, 0,0,1,0, 0,1,0,0, 0,0,0,1);
export function platformTransform(frame) {
  const rad = THREE.MathUtils.degToRad;
  const rotation = new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(
    rad(frame.PtfmRoll ?? 0), rad(frame.PtfmPitch ?? 0), rad(frame.PtfmYaw ?? 0), 'ZYX'));
  rotation.premultiply(basis).multiply(basis);
  return {
    position: new THREE.Vector3(frame.PtfmSurge ?? 0, frame.PtfmHeave ?? 0, frame.PtfmSway ?? 0),
    quaternion: new THREE.Quaternion().setFromRotationMatrix(rotation),
  };
}
