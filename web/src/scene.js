import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

export function createScene(canvas) {
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, innerWidth / innerHeight, 0.1, 2000);
  camera.position.set(310, 175, 390);
  camera.lookAt(55, 80, 0);
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.enablePan = true;
  controls.screenSpacePanning = true;
  controls.rotateSpeed = 0.65;
  controls.zoomSpeed = 0.85;
  controls.panSpeed = 0.8;
  controls.minPolarAngle = 0.02;
  controls.maxPolarAngle = Math.PI - 0.02;
  controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
  controls.mouseButtons.MIDDLE = THREE.MOUSE.DOLLY;
  controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;

  function resize() {
    const width = Math.max(1, canvas.clientWidth);
    const height = Math.max(1, canvas.clientHeight);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
  }
  resize();
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(canvas.parentElement);

  scene.background = new THREE.Color(0x10141a);
  scene.fog = new THREE.Fog(0x10141a, 260, 980);
  const gridDivisions = 44;
  const grid = new THREE.GridHelper(900, gridDivisions, 0x2e4c6e, 0x1c2733);
  grid.position.y = -42;
  scene.add(grid);

  let turbine = null;
  let baseScale = 1;
  // Basic materials are intentionally unlit: the geometry has solid colors
  // without gradients, reflections, transparency, or lighting-dependent shade.
  const material = new THREE.MeshStandardMaterial({ color: 0x8b95a3, roughness: 0.55, metalness: 0.25 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x394652, roughness: 0.75, metalness: 0.15 });
  const accent = new THREE.MeshStandardMaterial({ color: 0x4a8fe0, roughness: 0.45, metalness: 0.1 });
  const hemisphereLight = new THREE.HemisphereLight(0x8fb3e0, 0x0c0e12, 0.8);
  scene.add(hemisphereLight);
  const keyLight = new THREE.DirectionalLight(0xfff2df, 1.2);
  keyLight.position.set(30, 45, 20);
  scene.add(keyLight);

  function setAccent(color) {
    accent.color.set(color);
  }

  function setBackground(color) {
    const background = color || '#10141a';
    scene.background.set(background);
    scene.fog.color.set(background);
  }

  function setGridColors(centerColor, lineColor) {
    const colors = grid.geometry.getAttribute('color');
    const center = gridDivisions / 2;
    const major = new THREE.Color(centerColor);
    const minor = new THREE.Color(lineColor);
    for (let vertex = 0; vertex < colors.count; vertex++) {
      const line = Math.floor(vertex / 4);
      const color = line === center ? major : minor;
      colors.setXYZ(vertex, color.r, color.g, color.b);
    }
    colors.needsUpdate = true;
  }

  function setTheme(theme) {
    const light = theme === 'light';
    const palette = light ? {
      background: 0xe9eef2, gridCenter: 0x8eabc2, gridLine: 0xcbd6de,
      material: 0x71808c, dark: 0x344552, accent: 0x2f78c8,
      sky: 0xffffff, ground: 0xb8c4cc, key: 0xffffff,
    } : {
      background: 0x10141a, gridCenter: 0x2e4c6e, gridLine: 0x1c2733,
      material: 0x8b95a3, dark: 0x394652, accent: 0x4a8fe0,
      sky: 0x8fb3e0, ground: 0x0c0e12, key: 0xfff2df,
    };
    scene.background.set(palette.background);
    scene.fog.color.set(palette.background);
    material.color.set(palette.material);
    dark.color.set(palette.dark);
    accent.color.set(palette.accent);
    hemisphereLight.color.set(palette.sky);
    hemisphereLight.groundColor.set(palette.ground);
    keyLight.color.set(palette.key);
    setGridColors(palette.gridCenter, palette.gridLine);
  }

  function cylinder(radiusTop, radiusBottom, height, mat = material, segments = 20) {
    return new THREE.Mesh(new THREE.CylinderGeometry(radiusTop, radiusBottom, height, segments), mat);
  }

  function fitCameraToTurbine() {
    if (!turbine) return;
    turbine.updateMatrixWorld(true);
    const bounds = new THREE.Box3().setFromObject(turbine);
    const sphere = bounds.getBoundingSphere(new THREE.Sphere());
    const distance = sphere.radius / Math.tan(THREE.MathUtils.degToRad(camera.fov * .5)) * 1.2;
    const direction = new THREE.Vector3(1, .42, 1).normalize();
    controls.target.copy(sphere.center);
    camera.position.copy(sphere.center).addScaledVector(direction, distance);
    camera.near = Math.max(.1, distance / 1000);
    camera.far = Math.max(2000, distance * 12);
    camera.updateProjectionMatrix();
    controls.minDistance = Math.max(18, sphere.radius * .35);
    controls.maxDistance = Math.max(900, sphere.radius * 8);
    controls.update();
  }

  function rebuild({ hubHeight = 150, bladeLength = 117, platformDraft = 20 } = {}) {
    if (turbine) {
      turbine.traverse(child => child.geometry?.dispose());
      scene.remove(turbine);
    }
    turbine = new THREE.Group();
    baseScale = 230 / Math.max(hubHeight + bladeLength, 180);
    turbine.scale.setScalar(baseScale);

    // A generic VolturnUS-S-inspired semisubmersible is generated because the
    // OpenFAST deck describes physics, not a render mesh.
    const waterline = 0;
    const platform = new THREE.Group();
    [0, 120, 240].forEach((angle) => {
      const a = THREE.MathUtils.degToRad(angle);
      const x = Math.cos(a) * 34, z = Math.sin(a) * 34;
      const column = cylinder(7.5, 10, Math.max(24, platformDraft * 1.3), dark, 16);
      column.position.set(x, -10, z);
      platform.add(column);
      const brace = cylinder(2.2, 2.2, 35, dark, 10);
      brace.rotation.z = Math.PI / 2;
      brace.rotation.y = -a;
      brace.position.set(x * .5, -6, z * .5);
      platform.add(brace);
    });
    platform.add(cylinder(6.5, 7, 25, dark, 18));
    platform.position.y = waterline;
    turbine.add(platform);

    const tower = cylinder(2.4, 7, hubHeight, material, 24);
    tower.position.y = hubHeight / 2 + 8;
    turbine.add(tower);
    const nacelle = new THREE.Mesh(new THREE.BoxGeometry(20, 7, 8), dark);
    nacelle.position.set(5, hubHeight + 8, 0);
    turbine.add(nacelle);
    const hub = new THREE.Mesh(new THREE.SphereGeometry(4.8, 16, 12), accent);
    hub.position.set(-6, hubHeight + 8, 0);
    turbine.add(hub);

    const rotor = new THREE.Group();
    rotor.position.copy(hub.position);
    for (let i = 0; i < 3; i++) {
      const blade = new THREE.Mesh(new THREE.CylinderGeometry(.45, 2.3, bladeLength, 9), material);
      blade.position.y = bladeLength / 2 + 3;
      blade.rotation.z = THREE.MathUtils.degToRad(i * 120);
      blade.position.applyAxisAngle(new THREE.Vector3(0, 0, 1), THREE.MathUtils.degToRad(i * 120));
      rotor.add(blade);
    }
    rotor.rotation.y = Math.PI / 2;
    turbine.add(rotor);
    turbine.position.set(0, -42, 0);
    scene.add(turbine);
    fitCameraToTurbine();
  }

  canvas.addEventListener('contextmenu', event => event.preventDefault());
  canvas.addEventListener('dblclick', fitCameraToTurbine);
  addEventListener('resize', resize);

  rebuild();
  function animate(time) {
    requestAnimationFrame(animate);
    if (turbine) {
      turbine.position.y = -42 + Math.sin(time * .0007) * .35;
    }
    controls.update();
    renderer.render(scene, camera);
  }
  requestAnimationFrame(animate);
  return { rebuild, setAccent, setBackground, setTheme, resetView: fitCameraToTurbine };
}
