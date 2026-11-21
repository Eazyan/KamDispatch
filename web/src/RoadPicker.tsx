import { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import type { DispatchRoad, RoadPoint } from './types';

const toLatLng = ([lon, lat]: RoadPoint): L.LatLngTuple => [lat, lon];
const toPoint = ({ lng, lat }: L.LatLng): RoadPoint => [+L.Util.wrapNum(lng, [-180, 180]).toFixed(6), +lat.toFixed(6)];
export type RoadSelection = { road: DispatchRoad } | { points: RoadPoint[] };

export function RoadPicker({ selection, onSelect, loadRoads }: {
  selection: RoadSelection | null; onSelect?: (selection: RoadSelection | null) => void;
  loadRoads?: (signal: AbortSignal) => Promise<DispatchRoad[]>;
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map | null>(null);
  const highlighted = useRef<L.LayerGroup | null>(null);
  const roadsLayer = useRef<L.LayerGroup | null>(null);
  const callbacks = useRef({ selection, onSelect });
  const [manual, setManual] = useState(false);
  const [roads, setRoads] = useState<DispatchRoad[]>([]);
  const [roadError, setRoadError] = useState(false);
  const coordinates = selection ? ('road' in selection ? selection.road.coordinates : selection.points) : [];
  const editable = Boolean(onSelect);

  useEffect(() => { callbacks.current = { selection, onSelect }; }, [selection, onSelect]);
  useEffect(() => {
    if (!container.current) return;
    const map = L.map(container.current, { scrollWheelZoom: false, preferCanvas: true }).setView([53.055, 158.645], 12);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', maxZoom: 19,
    }).addTo(map);
    mapRef.current = map;
    roadsLayer.current = L.layerGroup().addTo(map);
    highlighted.current = L.layerGroup().addTo(map);
    const initial = callbacks.current.selection;
    if (!editable && initial) {
      const points = 'road' in initial ? initial.road.coordinates : initial.points;
      map.fitBounds(L.latLngBounds(points.map(toLatLng)), { padding: [24, 24], maxZoom: 16 });
    }
    return () => { map.remove(); mapRef.current = null; };
  }, [editable]);

  useEffect(() => {
    if (!loadRoads) return;
    const controller = new AbortController();
    loadRoads(controller.signal).then(setRoads).catch(() => {
      if (!controller.signal.aborted) {
        setRoadError(true);
        setManual(true);
      }
    });
    return () => controller.abort();
  }, [loadRoads]);

  useEffect(() => {
    const map = mapRef.current;
    const layer = roadsLayer.current;
    if (!map || !layer || !editable) return;
    const click = (event: L.LeafletMouseEvent) => {
      if (!manual) return;
      const previous = callbacks.current.selection;
      const first = previous && 'points' in previous && previous.points.length === 1 ? previous.points[0] : undefined;
      const point = toPoint(event.latlng);
      if (!first || first[0] !== point[0] || first[1] !== point[1]) {
        callbacks.current.onSelect?.({ points: first ? [first, point] : [point] });
      }
    };
    const renderer = L.canvas({ tolerance: 4 }).addTo(map);
    for (const road of roads) {
      L.polyline(road.coordinates.map(toLatLng), {
        renderer, color: '#38696b', weight: 3, opacity: .63, interactive: !manual, bubblingMouseEvents: false,
      }).on('click', () => callbacks.current.onSelect?.({ road })).addTo(layer);
    }
    map.on('click', click);
    return () => { map.off('click', click); layer.clearLayers(); renderer.remove(); };
  }, [roads, manual, editable]);

  useEffect(() => {
    const layer = highlighted.current;
    if (!layer) return;
    layer.clearLayers();
    if (coordinates.length > 1) L.polyline(coordinates.map(toLatLng), {
      color: '#ce581c', weight: 7, interactive: false,
    }).addTo(layer).bringToFront();
    if (coordinates.length && selection && 'points' in selection) {
      for (const point of coordinates) L.circleMarker(toLatLng(point), {
        radius: 5, color: '#743511', fillColor: '#ffad5b', fillOpacity: 1, interactive: false,
      }).addTo(layer).bringToFront();
    }
  }, [coordinates, selection]);

  return <div className="dispatch-map-picker">
    {editable && <>
      <label><input type="checkbox" checked={manual} disabled={roadError} onChange={event => {
        setManual(event.target.checked);
        onSelect?.(null);
      }} /> Указать начало и конец вручную</label>
      <p className="muted">{manual ? ' Нажмите на начало, затем на конец участка.' :
        roads.length ? 'Нажмите на линию дороги на карте.' : 'Загружаются дороги…'}</p>
    </>}
    <div ref={container} className="dispatch-road-map" aria-label="Карта участка дороги" />
    {editable && selection && <p role="status">{'road' in selection ? selection.road.name :
      coordinates.length === 2 ? 'Границы участка отмечены' : 'Теперь отметьте конец участка.'}
      {' '}<button type="button" className="quiet" onClick={() => onSelect?.(null)}>Сбросить</button>
    </p>}
  </div>;
}
