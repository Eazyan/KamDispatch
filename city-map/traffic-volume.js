import * as THREE from '../vendor/three.module.min.js';

const styles = {
  free: { color: '#52cf73', radius: 14 },
  slow: { color: '#f6be48', radius: 17 },
  congested: { color: '#f46c67', radius: 20 },
};

export const trafficColors = Object.fromEntries(Object.entries(styles).map(([status, { color }]) => [status, color]));

const sides = 10;
const profile = Array.from({ length: sides }, (_, i) => {
  const angle = 2 * Math.PI * i / sides;
  return { across: Math.cos(angle), up: Math.sin(angle) };
});

function samplePath(coordinates, map, origin, scale) {
  const points = [];
  const terrain = map.getTerrain();
  function sample(coordinate) {
    const ground = terrain ? map.queryTerrainElevation(coordinate) ?? 0 : 0;
    const point = maplibregl.MercatorCoordinate.fromLngLat(coordinate, ground);
    points.push(new THREE.Vector3((point.x - origin.x) / scale, (point.y - origin.y) / scale, point.z / scale));
  }

  sample(coordinates[0]);
  for (let i = 1; i < coordinates.length; i++) {
    const [lon, lat] = coordinates[i - 1];
    const [nextLon, nextLat] = coordinates[i];
    if (lon === nextLon && lat === nextLat) continue;
    const distance = new maplibregl.LngLat(lon, lat).distanceTo(new maplibregl.LngLat(nextLon, nextLat));
    const steps = terrain ? Math.min(128, Math.max(1, Math.ceil(distance / 24))) : 1;
    for (let step = 1; step <= steps; step++) {
      const t = step / steps;
      sample([lon + (nextLon - lon) * t, lat + (nextLat - lat) * t]);
    }
  }
  return points;
}

function appendTube(batch, points, radius, color) {
  if (points.length < 2) return;
  const { positions, colors, indices } = batch;
  let previousRing;

  function ring(point, direction, radialScale = 1, offset = 0) {
    const start = positions.length / 3;
    for (const { across, up } of profile) {
      const side = across * radialScale;
      const height = up * radialScale;
      positions.push(
        point.x + radius * (direction.x * offset - direction.y * side),
        point.y + radius * (direction.y * offset + direction.x * side),
        point.z + radius * (1.45 + 1.35 * height),
      );
      const light = .51 + .39 * height - .19 * side;
      const highlight = .29 * Math.max(0, height * .92 - side * .39) ** 4;
      colors.push(color.r * light + highlight, color.g * light + highlight, color.b * light + highlight);
    }
    if (previousRing !== undefined) {
      for (let i = 0; i < sides; i++) {
        const next = (i + 1) % sides;
        indices.push(previousRing + i, previousRing + next, start + i, previousRing + next, start + next, start + i);
      }
    }
    previousRing = start;
  }

  for (let i = 0; i < points.length; i++) {
    const before = points[Math.max(0, i - 1)];
    const after = points[Math.min(points.length - 1, i + 1)];
    const direction = new THREE.Vector2(after.x - before.x, after.y - before.y);
    if (!direction.lengthSq()) direction.set(points[i].x - before.x, points[i].y - before.y);
    direction.normalize();
    if (i === 0) {
      ring(points[i], direction, 0, -1);
      ring(points[i], direction, Math.SQRT1_2, -Math.SQRT1_2);
    }
    ring(points[i], direction);
    if (i === points.length - 1) {
      ring(points[i], direction, Math.SQRT1_2, Math.SQRT1_2);
      ring(points[i], direction, 0, 1);
    }
  }
}

export function createTrafficVolume(map, flatLayer) {
  const origin = maplibregl.MercatorCoordinate.fromLngLat(map.getCenter());
  const scale = origin.meterInMercatorCoordinateUnits();
  const transform = new THREE.Matrix4().makeTranslation(origin.x, origin.y, 0).scale(new THREE.Vector3(scale, scale, scale));
  const scene = new THREE.Scene();
  const camera = new THREE.Camera();
  const meshes = [];
  let renderer;
  let rows = [];
  let stale = false;
  let dirty = false;
  let visible;

  function clear() {
    for (const mesh of meshes) {
      scene.remove(mesh);
      mesh.geometry.dispose();
      mesh.material.dispose();
    }
    meshes.length = 0;
  }

  function draw() {
    if (!visible || !dirty) return;
    dirty = false;
    clear();
    const batches = [false, true].map(estimated => ({ estimated, positions: [], colors: [], indices: [] }));
    for (const { coordinates, status, estimated } of rows) {
      const { color, radius } = styles[status];
      appendTube(batches[Number(estimated === true)], samplePath(coordinates, map, origin, scale), radius, new THREE.Color(color));
    }
    for (const { estimated, positions, colors, indices } of batches) {
      if (!indices.length) continue;
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      geometry.setIndex(indices);
      const material = new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide, transparent: true, forceSinglePass: true });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.userData.estimated = estimated;
      mesh.frustumCulled = false;
      meshes.push(mesh);
      scene.add(mesh);
    }
    updateOpacity();
    map.triggerRepaint();
  }

  function updateOpacity() {
    for (const { material, userData } of meshes) {
      material.opacity = (stale ? .46 : .95) * (userData.estimated ? .68 : 1);
    }
  }

  function updatePitch() {
    const pitched = map.getPitch() > 1;
    if (visible === pitched) return;
    visible = pitched;
    map.setLayoutProperty(flatLayer, 'visibility', visible ? 'none' : 'visible');
    draw();
  }

  function refreshTerrain() {
    dirty = true;
  }

  map.addLayer({
    id: 'dispatch-traffic-volume',
    type: 'custom',
    renderingMode: '3d',
    onAdd(map, gl) {
      renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl });
      renderer.autoClear = false;
    },
    render(gl, frame) {
      if (!visible || !meshes.length) return;
      camera.projectionMatrix.fromArray(frame.defaultProjectionData.mainMatrix).multiply(transform);
      renderer.resetState();
      renderer.render(scene, camera);
    },
    onRemove() {
      map.off('pitch', updatePitch);
      map.off('idle', draw);
      map.off('terrain', refreshTerrain);
      map.off('sourcedata', onSourceData);
      clear();
      renderer.dispose();
    },
  }, 'dispatch-focus');

  function onSourceData(event) {
    if (event.sourceId === map.getTerrain()?.source && event.sourceDataType === 'content') refreshTerrain();
  }

  map.on('pitch', updatePitch);
  map.on('idle', draw);
  map.on('terrain', refreshTerrain);
  map.on('sourcedata', onSourceData);
  updatePitch();

  return {
    setData(nextRows) {
      rows = nextRows;
      dirty = true;
      draw();
    },
    setStale(value) {
      if (stale === value) return;
      stale = value;
      updateOpacity();
      map.triggerRepaint();
    },
  };
}
