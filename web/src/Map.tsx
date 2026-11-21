import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { Camera, SnowForecast, SnowRaster, SnowSegment } from './types';

type Point = Pick<Camera, 'id' | 'name' | 'coordinates' | 'location_note' | 'road'>;

const segmentLabels: Record<SnowSegment['status'], string> = {
  watch: 'Кандидат на проверку', no_signal: 'Без сигнала модели', unavailable: 'Нет оценки',
};
const segmentColors: Record<SnowSegment['status'], string> = {
  watch: '#b34d0a', no_signal: '#447487', unavailable: '#6b7376',
};
const forecastNotes: Partial<Record<SnowForecast['status'], string>> = {
  partial: 'Для части участков расчёт недоступен.', unavailable: 'Расчёт сейчас недоступен.',
  pending: 'Ожидается первый расчёт.', running: 'Рассчитываем снег.', stale: 'Расчёт устарел. Ожидается обновление.',
  configuration_error: 'Проверьте конфигурацию дорожных отрезков.',
};
const emptySegments: SnowSegment[] = [];
const kamchatkaCenter = L.latLng(53.11, 158.54);

function segmentStatus(segment: SnowSegment, stale: boolean) {
  return stale ? 'unavailable' : segment.status;
}

export function CameraMap({ cameras, onSelect, snow, loadRaster, loadRoads }: {
  cameras: Camera[]; onSelect: (id: string) => void; snow?: SnowForecast | null;
  loadRaster: (id: string, calculatedAt: string, signal: AbortSignal) => Promise<SnowRaster[]>;
  loadRoads: (calculatedAt: string, signal: AbortSignal) => Promise<SnowSegment[]>;
}) {
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const forecastLayer = useRef<L.FeatureGroup | null>(null);
  const lines = useRef(new Map<string, L.Polyline>());
  const showedRoads = useRef(false);
  const select = useRef(onSelect);
  const [selected, setSelected] = useState<string>();
  const [tilesUnavailable, setTilesUnavailable] = useState(false);
  const [showRaster, setShowRaster] = useState(false);
  const [rasterStatus, setRasterStatus] = useState<'loading' | 'ready' | 'unavailable'>('loading');
  const [cityRoads, setCityRoads] = useState<{at: string; segments: SnowSegment[]} | null>(null);
  const [cityError, setCityError] = useState('');
  useEffect(() => { select.current = onSelect; }, [onSelect]);
  const points: Point[] = cameras.filter(camera => camera.coordinates)
    .map(({ id, name, coordinates, location_note, road }) => ({ id, name, coordinates, location_note, road }));
  const locations = JSON.stringify(points);
  const stale = snow?.status === 'stale';
  const calculatedAt = snow?.calculated_at;
  const city = snow?.scope === 'city';
  const citySegments = city && cityRoads?.at === calculatedAt ? cityRoads?.segments : undefined;
  const segments = city ? citySegments ?? emptySegments : snow?.segments ?? emptySegments;
  const pilotData = city ? '[]' : JSON.stringify(segments);
  const hasMap = points.length > 0 || segments.length > 0;
  const selectedSegment = segments.find(segment => segment.id === selected);
  const canShowRaster = Boolean(calculatedAt && selectedSegment && segmentStatus(selectedSegment, stale) !== 'unavailable');
  useEffect(() => {
    if (!city || !calculatedAt) return;
    const controller = new AbortController();
    setCityError('');
    async function refreshRoads() {
      if (!calculatedAt) return;
      try {
        const rows = await loadRoads(calculatedAt, controller.signal);
        if (!controller.signal.aborted) {
          setCityRoads({at: calculatedAt, segments: rows});
          setCityError('');
          window.clearInterval(retry);
        }
      } catch {
        if (!controller.signal.aborted) setCityError('Не удалось загрузить дорожный слой. Повторяем запрос.');
      }
    }
    const retry = window.setInterval(refreshRoads, 15_000);
    void refreshRoads();
    return () => { controller.abort(); window.clearInterval(retry); };
  }, [city, calculatedAt, loadRoads]);

  useEffect(() => {
    if (!element.current) return;
    const map = L.map(element.current, { scrollWheelZoom: false }).setView(kamchatkaCenter, 10);
    mapRef.current = map;
    const heatmapPane = map.createPane('snow-heatmap');
    heatmapPane.style.zIndex = '350';
    heatmapPane.style.pointerEvents = 'none';
    let loaded = false;
    let failed = false;
    const baseLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      referrerPolicy: 'strict-origin-when-cross-origin',
    });
    baseLayer.on('loading', () => { loaded = false; failed = false; });
    baseLayer.on('tileload', () => { loaded = true; setTilesUnavailable(false); });
    baseLayer.on('tileerror', () => { failed = true; });
    baseLayer.on('load', () => setTilesUnavailable(failed && !loaded));
    baseLayer.addTo(map);
    const bounds = L.latLngBounds([]);
    const kamchatkaBounds = L.latLngBounds([]);
    for (const point of JSON.parse(locations) as Point[]) {
      if (!point.coordinates) continue;
      const [lon, lat] = point.coordinates;
      const popup = document.createElement('div');
      const name = document.createElement('strong');
      name.textContent = point.name;
      popup.append(name);
      if (point.location_note) {
        const description = document.createElement('p');
        description.textContent = point.location_note;
        popup.append(description);
      }
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'map-open';
      open.textContent = 'Открыть камеру';
      open.onclick = () => select.current(point.id);
      popup.append(open);
      L.marker([lat, lon], {
        alt: point.name,
        title: point.name,
        icon: L.divIcon({ className: 'map-point ' + (point.road ? 'legend-road' : 'legend-weather'), iconSize: [17, 17] }),
      }).bindPopup(popup).addTo(map).getElement()?.setAttribute('aria-label', point.name);
      bounds.extend([lat, lon]);
      if (kamchatkaCenter.distanceTo([lat, lon]) <= 100_000) kamchatkaBounds.extend([lat, lon]);
    }
    const initialBounds = kamchatkaBounds.isValid() ? kamchatkaBounds : bounds;
    if (initialBounds.isValid()) map.fitBounds(initialBounds, { padding: [28, 28], maxZoom: 14, animate: false });
    return () => { mapRef.current = null; map.remove(); };
  }, [locations, hasMap]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const group = L.featureGroup().addTo(map);
    const renderer = city ? L.canvas({ tolerance: 4 }) : undefined;
    forecastLayer.current = group;
    const nextLines = new Map<string, L.Polyline>();
    const roads = citySegments ? [...citySegments].sort((a, b) => Number(a.status === 'watch') - Number(b.status === 'watch'))
      : JSON.parse(pilotData) as SnowSegment[];
    for (const segment of roads) {
      const status = segmentStatus(segment, stale);
      const coordinates: L.LatLngTuple[] = segment.coordinates.map(([lon, lat]) => [lat, lon]);
      const line = L.polyline(coordinates, {
        renderer, color: segmentColors[status], weight: city ? (status === 'watch' ? 4 : 2.5) : 6,
        opacity: .92, dashArray: status === 'unavailable' ? '4 6' : undefined,
      }).addTo(group);
      line.bindPopup(() => {
        const popup = document.createElement('div');
        const name = document.createElement('strong');
        name.textContent = segment.name;
        const label = document.createElement('p');
        label.textContent = segmentLabels[status];
        popup.append(name, label);
        return popup;
      }).on('click', () => setSelected(segment.id));
      nextLines.set(segment.id, line);
    }
    lines.current = nextLines;
    if (nextLines.size && !showedRoads.current) {
      map.fitBounds(city ? [[53.008, 158.585], [53.108, 158.679]] : group.getBounds(),
        { padding: [26, 26], maxZoom: 16, animate: false });
      showedRoads.current = true;
    }
    return () => { forecastLayer.current = null; group.remove(); renderer?.remove(); };
  }, [pilotData, citySegments, city, stale, locations, hasMap]);

  useEffect(() => {
    const map = mapRef.current;
    const line = selected ? lines.current.get(selected) : undefined;
    if (map && line) {
      map.fitBounds(line.getBounds(), { padding: [32, 32], maxZoom: 17, animate: false });
      line.openPopup();
    }
  }, [selected]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !showRaster || !canShowRaster || !selected || !calculatedAt) return;
    const controller = new AbortController();
    const layer = L.layerGroup().addTo(map);
    setRasterStatus('loading');
    loadRaster(selected, calculatedAt, controller.signal).then(tiles => {
      if (controller.signal.aborted) return;
      for (const { bbox_wgs84: [west, south, east, north], png } of tiles) {
        L.imageOverlay('data:image/png;base64,' + png, [[south, west], [north, east]], { pane: 'snow-heatmap' }).addTo(layer);
      }
      setRasterStatus(tiles.length ? 'ready' : 'unavailable');
    }).catch(() => { if (!controller.signal.aborted) setRasterStatus('unavailable'); });
    return () => { controller.abort(); layer.remove(); };
  }, [selected, calculatedAt, showRaster, canShowRaster, locations, hasMap, loadRaster]);

  const ordered = (city ? segments.filter(item => !stale && item.status === 'watch') : [...segments]).sort((a, b) => {
    if (stale) return a.name.localeCompare(b.name, 'ru');
    return (b.excess_cm ?? -Infinity) - (a.excess_cm ?? -Infinity) || a.name.localeCompare(b.name, 'ru');
  });
  const listed = city ? ordered.slice(0, 12) : ordered;
  if (city && selectedSegment && !listed.some(segment => segment.id === selectedSegment.id)) listed.unshift(selectedSegment);
  const hour = (value?: string) => value && new Intl.DateTimeFormat('ru', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kamchatka',
  }).format(new Date(value));
  const note = snow && forecastNotes[snow.status];

  return <>
    {points.length > 0 && <div className="snow-map-controls" aria-label="Масштаб карты">
      {segments.length > 0 && <button type="button" onClick={() => {
        const bounds = forecastLayer.current?.getBounds();
        if (bounds?.isValid()) mapRef.current?.fitBounds(bounds, { padding: [26, 26], maxZoom: 16, animate: false });
      }}>{city ? 'Вся сеть' : 'К участкам'}</button>}
      <button type="button" onClick={() => {
        const coordinates: L.LatLngTuple[] = points.flatMap(point => point.coordinates ? [[point.coordinates[1], point.coordinates[0]]] : []);
        if (coordinates.length) mapRef.current?.fitBounds(coordinates, { padding: [28, 28], maxZoom: 14, animate: false });
      }}>Все камеры</button>
    </div>}
    {hasMap ? <div className="map" ref={element} aria-label="Карта камер и дорожных отрезков" />
      : <p className="empty">Карта появится после подтверждения координат.</p>}
    {hasMap && tilesUnavailable && <p className="map-tile-note muted" role="status">Подложка OpenStreetMap недоступна.</p>}
    {points.length > 0 && <div className="map-legend">
      <span><i className="legend-road" />Дорожные камеры</span>
      <span><i className="legend-weather" />Погодные камеры</span>
    </div>}
    {snow && <section className="snow-layer" aria-label="Прогноз накопления снега">
      <h3>Где проверить дорогу</h3>
      {city && snow.counts && <div className="snow-counts">
        {(['watch', 'no_signal', 'unavailable'] as const).map(status => <span key={status}>
          <i className={'snow-dot snow-' + status} />{segmentLabels[status]} · {stale
            ? status === 'unavailable' ? segments.length : 0 : snow.counts?.[status]}
        </span>)}
      </div>}
      {snow.start && snow.end && <p className="muted snow-meta">
        {hour(snow.start)} — {hour(snow.end)} · Камчатское время
        {snow.weather_sources?.length ? ' · ' + snow.weather_sources.join(', ') : ''}
      </p>}
      {snow.calculated_at && <p className="muted snow-meta">Расчёт от {hour(snow.calculated_at)}</p>}
      {note && <p className="muted snow-meta" role="status">{note}</p>}
      {city && calculatedAt && !citySegments && <p className="muted snow-meta" role="status">{cityError || 'Загружаем дорожный слой…'}</p>}
      {city && citySegments && listed.length === 0 && !stale && snow.counts?.no_signal !== 0 &&
        <p className="muted snow-meta">Модель не выделила участки для проверки. Нажмите на дорогу на карте, чтобы открыть её расчёт.</p>}
      {city && ordered.length > 12 && <p className="muted snow-meta">Первые 12 участков по расчётному избытку. Все участки видны на карте.</p>}
      {listed.length > 0 && <ul className="snow-segments">{listed.map(segment => {
        const status = segmentStatus(segment, stale);
        const linkedCamera = cameras.find(camera => camera.road && camera.id === segment.camera_id);
        const active = segment.id === selected;
        return <li key={segment.id}>
          <button type="button" className="snow-segment" aria-expanded={active} onClick={() => {
            if (active) {
              const line = lines.current.get(segment.id);
              if (line && !line.isPopupOpen()) line.openPopup();
              else { line?.closePopup(); setSelected(undefined); }
            } else setSelected(segment.id);
          }}>
            <span><i className={'snow-dot snow-' + status} />{segment.name}</span>
            <small>{segmentLabels[status]}</small>
          </button>
          {active && <div className="snow-detail muted">
            <p>{status === 'watch' && segment.excess_cm !== null
              ? 'Расчётный избыток свежего снега около ' + Math.round(segment.excess_cm) + ' см относительно фонового снегопада.'
              : status === 'no_signal' ? 'Модель не выделила локального накопления выше порога.' : 'Для участка нет актуальной оценки.'}</p>
            {status !== 'unavailable' && segment.calculation_source && <p>{segment.calculation_source === 'remote'
              ? 'Расчёт 3D-модели' : 'Расчёт резервной модели'}</p>}
            {(canShowRaster || linkedCamera) && <div className="snow-detail-actions">
              {canShowRaster && <button type="button" aria-pressed={showRaster} onClick={() => {
                setShowRaster(!showRaster);
                if (!showRaster) element.current?.scrollIntoView({ block: 'nearest' });
              }}>Тепловой слой</button>}
              {linkedCamera && <button type="button" onClick={() => onSelect(linkedCamera.id)}>Открыть камеру</button>}
            </div>}
            {canShowRaster && showRaster && <p role="status">{rasterStatus === 'ready'
              ? 'Чем темнее оранжевый, тем больше расчётный избыток снега.'
              : rasterStatus === 'loading' ? 'Загружается тепловой слой…' : 'Тепловой слой недоступен.'}</p>}
          </div>}
        </li>;
      })}</ul>}
      {segments.length > 0 && <p className="muted snow-meta">{city ? 'Экспериментальный прогноз по дорогам Петропавловска.' : 'Экспериментальный прогноз по выбранным участкам.'}</p>}
    </section>}
  </>;
}
