import * as THREE from 'three';

export function createTowerGeometry(towerProfile, hubHeight) {
  const stations = towerProfile?.stations;
  if (stations?.length >= 2) {
    // Radius/elevation points give a linear taper between AeroDyn stations.
    // Axis points at both ends close the top and bottom of the tower.
    const points = [new THREE.Vector2(0, stations[0].elevation)];
    for (const { radius, elevation } of stations) points.push(new THREE.Vector2(radius, elevation));
    points.push(new THREE.Vector2(0, stations.at(-1).elevation));
    return new THREE.LatheGeometry(points, 24);
  }
  // Preserve the schematic tower for models and old runs without a profile.
  const geometry = new THREE.CylinderGeometry(2.4, 7, hubHeight, 24);
  geometry.translate(0, hubHeight / 2 + 8, 0);
  return geometry;
}
