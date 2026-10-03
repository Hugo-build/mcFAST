import test from 'node:test';
import assert from 'node:assert/strict';
import { createTowerGeometry } from '../src/tower-geometry.js';

test('tower mesh uses all radii at their source elevations', () => {
  const stations = [
    { elevation: 15, radius: 5 },
    { elevation: 80, radius: 4.5 },
    { elevation: 144.386, radius: 3.25 },
  ];
  const geometry = createTowerGeometry({ stations }, 144.386);
  geometry.computeBoundingBox();
  assert.ok(Math.abs(geometry.boundingBox.min.y - 15) < 1e-4);
  assert.ok(Math.abs(geometry.boundingBox.max.y - 144.386) < 1e-4);
  const positions = geometry.getAttribute('position');
  for (const { elevation, radius } of stations) {
    let found = false;
    for (let i = 0; i < positions.count; i++) {
      if (Math.abs(positions.getY(i) - elevation) < 1e-4 &&
          Math.abs(Math.hypot(positions.getX(i), positions.getZ(i)) - radius) < 1e-4) found = true;
    }
    assert.ok(found, `missing tower station at ${elevation}`);
  }
  geometry.dispose();
});

test('missing profile preserves the legacy schematic tower', () => {
  const geometry = createTowerGeometry(null, 150);
  geometry.computeBoundingBox();
  assert.equal(geometry.boundingBox.min.y, 8);
  assert.equal(geometry.boundingBox.max.y, 158);
  assert.equal(geometry.parameters.radiusTop, 2.4);
  assert.equal(geometry.parameters.radiusBottom, 7);
  geometry.dispose();
});
