import { $, empty, request } from '../api.js';
import { createMap } from '../map.js?v=twist';
import { raster } from '../raster_motion.js';
import { createSnowVolume } from '../snow_volume.js';
import { createTrafficVolume, trafficColors } from './traffic-volume.js?v=2';

const parentOrigin = new URLSearchParams(location.search).get('parent');
const send = (type, fields) => {
  if (window.parent !== window && /^https?:\/\/[^/]+$/.test(parentOrigin ?? '')) {
    window.parent.postMessage({ type: 'camchatka:' + type, ...fields }, parentOrigin);
  }
};
const time = new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka', day: 'numeric', month: 'short',
  hour: '2-digit', minute: '2-digit' });
const line = coordinates => ({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } });
const coordinate = point => Array.isArray(point) && point.length === 2 &&
  point.every(Number.isFinite) && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 90;
const validLine = row => Array.isArray(row) && row.length >= 2 && row.every(coordinate);

async function start() {
  const metadata = await request('/city/metadata');
  let model;
  let index = 0;
  let state = {};
  let revision;
  let run;
  let pending;
  let retry;
  let generation = 0;
  let timer;
  let markerData;
  let roadData;
  let trafficData;
  let markerObjects = [];
  let addressMarker;
  const view = createMap(metadata, () => {}, async point => {
    if (!model || !state.showSnow) return [];
    const current = model.run_id;
    const query = new URLSearchParams({ lon: point.lng, lat: point.lat, frame: index });
    const row = await request('/operations/city/runs/' + current + '/sample?' + query);
    return current === model?.run_id && row.snow_cm != null ? ['Свежий снег по модели · ' + row.snow_cm.toFixed(1) + ' см'] : [];
  });
  await view.ready;
  const { map } = view;
  underlay(map);
  const volume = createSnowVolume(map);
  const trafficVolume = createTrafficVolume(map, 'dispatch-traffic');
  const layer = $('model-layer');
  const slider = $('model-frame');
  const play = $('play');
  function pause() {
    clearInterval(timer); timer = undefined;
    play.textContent = '▶';
    play.setAttribute('aria-label', 'Воспроизвести расчёт');
    play.setAttribute('aria-pressed', 'false');
  }
  function paint() {
    const mode = layer.value;
    const frame = model?.wind_frames?.[index];
    const isVolume = mode === 'volume';
    const visible = Boolean(model && state.showSnow);
    const image = mode === 'drift_cm' ? model?.overlays.drift_cm : model?.timeline?.[index] || model?.overlays.snow_cm;
    $('model-tools').hidden = !visible;
    $('frame-time').textContent = frame?.valid_at ? time.format(new Date(frame.valid_at)) : '';
    slider.setAttribute('aria-valuetext', $('frame-time').textContent || 'Шаг ' + (index + 1));
    slider.value = index;
    slider.disabled = play.disabled = mode === 'drift_cm' || Number(slider.max) === 0;
    $('model-scale').textContent = !model ? '' : isVolume ? 'Высота снега на экране ×12' :
      'Шкала модели 0–' + model.scales[mode] + ' см';
    map.setLayoutProperty('operations-snow', 'visibility', visible && image && !isVolume ? 'visible' : 'none');
    map.setLayoutProperty('dispatch-roads', 'visibility', state.showSnow && !model ? 'visible' : 'none');
    if (visible && image && !isVolume) raster(map, 'operations-snow', model.bounds, image);
    volume.show(visible && isVolume ? model.run_id : null, index < (model?.timeline?.length ?? 0) ? index : undefined);
    const points = $('show-wind').checked ? frame?.points ?? [] : [];
    map.getSource('weather-arrows').setData({ ...empty(), features: points.map(point => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: point.coordinates },
      properties: { toward: (point.wind_from_deg + 180) % 360, speed: point.wind_speed_ms },
    })) });
  }
  function syncCleanupTime(at) {
    if (!model || !at) return;
    pause();
    const stamp = Date.parse(at);
    const previous = model.wind_frames.findLastIndex(frame => Date.parse(frame.valid_at) <= stamp);
    index = Math.max(0, previous);
    paint();
  }

  async function fetchModel(id, token) {
    if (id !== 'cleanup') return request('/operations/city/runs/' + id);
    const job = await request('/operations/city/runs', { source: 'reanalysis', event_id: 'january-2026' });
    const url = '/operations/city/runs/' + job.run_id;
    while (token === generation) {
      const result = await request(url);
      if (result.status === 'completed') return result;
      if (result.status === 'failed') throw Error('Не удалось рассчитать январский циклон');
      if (result.total_tiles) {
        send('model', { state: 'loading', progress: result.completed_tiles + '/' + result.total_tiles });
      }
      await new Promise(resolve => setTimeout(resolve, 2500));
    }
  }

  function loadRun(id) {
    if ((id && pending === id) || (run === id && !pending)) return;
    const token = ++generation;
    clearTimeout(retry);
    pending = id;
    if (!id) {
      model = undefined; run = id; pause(); paint();
      return;
    }
    send('model', { state: 'loading' });
    fetchModel(id, token).then(result => {
      if (token !== generation) return;
      if (result?.status !== 'completed') throw Error('Расчёт недоступен');
      pause();
      const displayed = model?.wind_frames?.[index]?.valid_at;
      model = result; run = id; pending = undefined;
      const count = Math.max(0, result.wind_frames.length - 1);
      const matching = result.wind_frames.findIndex(frame => frame.valid_at === displayed);
      slider.max = count;
      index = matching < 0 ? count : matching;
      if (id === 'cleanup') syncCleanupTime(state.cleanupAt);
      else paint();
      send('model', { state: 'ready' });
    }).catch(() => {
      if (token !== generation) return;
      model = undefined; run = undefined; pending = undefined;
      pause(); paint(); send('model', { state: 'failed' });
      retry = setTimeout(() => loadRun(id), 15000);
    });
  }
  layer.onchange = () => { pause(); paint(); };
  $('show-wind').onchange = paint;
  slider.oninput = () => { pause(); index = slider.valueAsNumber; paint(); };
  play.onclick = () => {
    if (timer) { pause(); return; }
    if (index >= Number(slider.max)) index = 0;
    paint();
    play.textContent = 'Ⅱ';
    play.setAttribute('aria-label', 'Пауза');
    play.setAttribute('aria-pressed', 'true');
    timer = setInterval(() => {
      index += 1; paint();
      if (index >= Number(slider.max)) pause();
    }, 950);
  };
  window.addEventListener('message', event => {
    if (event.origin !== parentOrigin || event.source !== window.parent || event.data?.type !== 'camchatka:state') return;
    const previousCleanupAt = state.cleanupAt;
    state = event.data;
    const nextRoads = JSON.stringify(state.roads);
    if (roadData !== nextRoads) {
      map.getSource('dispatch-roads').setData({ ...empty(), features: (state.roads ?? []).filter(validLine).map(line) });
      roadData = nextRoads;
    }
    const nextTraffic = JSON.stringify(state.traffic);
    if (trafficData !== nextTraffic) {
      const rows = (state.traffic ?? []).filter(row => row && Object.hasOwn(trafficColors, row.status) && validLine(row.coordinates));
      trafficVolume.setData(rows);
      map.getSource('dispatch-traffic').setData({ ...empty(), features: rows.map(row => ({
        ...line(row.coordinates),
        properties: { color: trafficColors[row.status], estimated: row.estimated === true },
      })) });
      trafficData = nextTraffic;
    }
    trafficVolume.setStale(Boolean(state.trafficStale));
    const opacity = state.trafficStale ? .46 : .95;
    map.setPaintProperty('dispatch-traffic', 'line-opacity', ['case', ['get', 'estimated'], opacity * .68, opacity]);
    const nextMarkers = JSON.stringify(state.showMarkers && state.markers);
    if (markerData !== nextMarkers) {
      markerObjects.forEach(marker => marker.remove());
      markerData = nextMarkers;
      markerObjects = !state.showMarkers ? [] : state.markers.flatMap(mark => {
        const point = mark.coordinates[Math.floor(mark.coordinates.length / 2)];
        if (!coordinate(point)) return [];
        const button = document.createElement('button');
        button.type = 'button'; button.className = mark.kind === 'bus' ? 'city-bus-pin' : 'city-pin';
        button.setAttribute('aria-label', mark.label);
        button.style.setProperty('--pin', mark.color);
        if (mark.kind === 'bus') {
          if (typeof mark.badge !== 'string' || !mark.badge.trim()) return [];
          button.textContent = mark.badge;
          button.title = mark.label;
        }
        button.onclick = event => { event.stopPropagation(); send('select', { id: mark.id }); };
        return new maplibregl.Marker({ element: button, pitchAlignment: 'viewport' }).setLngLat(point).addTo(map);
      });
    }
    if (state.selection?.revision !== revision) {
      revision = state.selection?.revision;
      const points = state.selection?.coordinates?.filter(coordinate) ?? [];
      const paths = state.selection?.paths?.map(path => path.filter(coordinate)) ?? [points];
      const features = state.selection?.fitOnly ? [] : paths.filter(path => path.length > 1).map(line);
      map.getSource('dispatch-focus').setData({ ...empty(), features });
      addressMarker?.remove();
      addressMarker = undefined;
      if (points.length === 1 && !state.selection?.fitOnly && typeof state.selection?.label === 'string') {
        const marker = document.createElement('span');
        marker.className = 'city-pin';
        marker.title = state.selection.label;
        marker.setAttribute('role', 'img');
        marker.setAttribute('aria-label', state.selection.label);
        marker.style.setProperty('--pin', '#fff');
        addressMarker = new maplibregl.Marker({ element: marker }).setLngLat(points[0]).addTo(map);
      }
      if (points.length) {
        const bounds = points.reduce((box, point) => box.extend(point), new maplibregl.LngLatBounds(points[0], points[0]));
        const right = window.innerWidth > 800 ? 340 : 28;
        map.fitBounds(bounds, { maxZoom: 16, bearing: map.getBearing(), padding: { left: 55, top: 90, right, bottom: 145 },
          duration: matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 600 });
      }
    }
    const id = /^[a-f0-9]{64}$/.test(state.runId ?? '') ? state.runId : undefined;
    loadRun(state.cleanupAt ? 'cleanup' : id);
    if (run === 'cleanup' && state.cleanupAt !== previousCleanupAt) syncCleanupTime(state.cleanupAt);
    else paint();
  });
  send('ready', { busBadges: true, traffic: true });
}

function underlay(map) {
  const colors = { background: '#295060', water: '#15384c', shore: '#789ea7', green: '#425c53', streets: '#9db1a9', arterials: '#bfc4ab' };
  for (const [id, color] of Object.entries(colors)) map.setPaintProperty(id, map.getLayer(id).type + '-color', color);
  if (map.getLayer('relief-color')) map.setPaintProperty('relief-color', 'raster-brightness-max', .66);
  for (const [id, color, width] of [['dispatch-roads', '#89d8fc', 3], ['dispatch-traffic', ['get', 'color'], 4], ['dispatch-focus', '#fff', 5]]) {
    map.addSource(id, { type: 'geojson', data: empty() });
    map.addLayer({ id, type: 'line', source: id, paint: { 'line-color': color, 'line-width': width } }, 'structures');
  }

}

start().catch(error => {
  $('scene-status').textContent = error.message;
  send('error');
});
