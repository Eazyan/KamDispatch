import { useEffect, useMemo, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { trafficColors, type TrafficSegment } from './traffic';
import type { RoadPoint, SnowSegment } from './types';

export type MapMarker = {
  id: string; label: string; coordinates: RoadPoint[]; color: string; priority: number;
} & ({ kind: 'bus'; badge: string } | { kind?: undefined });
type Selection = { id: string; coordinates: RoadPoint[]; revision: number; fitOnly?: boolean };
type Props = {
  cityUrl?: string | null; runId?: string; cleanupAt?: string; roads: SnowSegment[]; markers: MapMarker[];
  traffic?: TrafficSegment[]; trafficStale: boolean;
  selection: Selection | null; showSnow: boolean; showMarkers: boolean;
  onSelect: (id: string) => void; onClientState: (state: 'offline' | 'loading' | 'ready') => void;
  onModelState: (state: 'loading' | 'ready' | 'failed', progress?: string) => void;
};

const cityBounds: L.LatLngBoundsExpression = [[52.91, 158.48], [53.16, 158.80]];
const center: L.LatLngExpression = [53.042, 158.637];
const labels: [L.LatLngExpression, string, string][] = [
  [[53.0254, 158.6301], 'Авачинская губа', 'water'],
  [[53.0354, 158.6421], 'Култучное озеро', 'water'],
  [[53.0455, 158.616], 'Мишенная сопка', ''],
  [[53.062, 158.649], 'Северо-Восток', ''],
  [[53.0192, 158.6518], 'Петровская сопка', ''],
];
const latLng = ([lon, lat]: RoadPoint): L.LatLngTuple => [lat, lon];

function FallbackMap({ roads, traffic, trafficStale, markers, selection, showSnow, showMarkers, onSelect }: Props) {
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<L.Map>();
  const markerData = JSON.stringify(markers);
  const onPick = useRef(onSelect);
  onPick.current = onSelect;

  useEffect(() => {
    if (!element.current) return;
    const map = L.map(element.current, { minZoom: 10, maxZoom: 18, maxBounds: cityBounds,
      zoomControl: false, attributionControl: false, fadeAnimation: false }).setView(center, 13);
    mapRef.current = map;
    L.imageOverlay('/overview/city.webp', cityBounds).addTo(map);
    L.control.zoom({ position: 'bottomleft' }).addTo(map);
    L.control.scale({ position: 'bottomleft', imperial: false }).addTo(map);
    map.attributionControl = L.control.attribution({ position: 'bottomright', prefix: false }).addTo(map)
      .addAttribution('© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · <a href="https://registry.opendata.aws/copernicus-dem/">Copernicus WorldDEM-30</a>');
    for (const [coords, title, style] of labels) {
      L.marker(coords, { interactive: false, keyboard: false, icon: L.divIcon({
        className: 'overview-map-label ' + style, html: title, iconSize: [140, 18], iconAnchor: [70, 9],
      }) }).addTo(map);
    }
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }));
    observer.observe(element.current);
    return () => { observer.disconnect(); map.remove(); mapRef.current = undefined; };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !showSnow) return;
    const canvas = L.canvas({ padding: .25 });
    const layer = L.layerGroup().addTo(map);
    for (const road of roads) {
      if (road.status !== 'watch' || road.coordinates.length < 2) continue;
      const path = road.coordinates.map(latLng);
      L.polyline(path, { renderer: canvas, color: '#68cbff', weight: 12, opacity: .18, interactive: false }).addTo(layer);
      L.polyline(path, { renderer: canvas, color: '#9ee4ff', weight: 3, opacity: .9, interactive: false }).addTo(layer);
    }
    return () => { layer.remove(); canvas.remove(); };
  }, [roads, showSnow]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !traffic?.length) return;
    const pane = map.getPane('traffic') ?? map.createPane('traffic');
    pane.style.zIndex = '420';
    pane.style.pointerEvents = 'none';
    const canvas = L.canvas({ padding: .25, pane: 'traffic' });
    const layer = L.layerGroup().addTo(map);
    const attribution = 'Пробки © Яндекс Карты';
    map.attributionControl.addAttribution(attribution);
    for (const segment of traffic) {
      L.polyline(segment.coordinates.map(latLng), { renderer: canvas, color: trafficColors[segment.status],
        weight: 4, opacity: (trafficStale ? .46 : .95) * (segment.estimated ? .68 : 1), interactive: false }).addTo(layer);
    }
    return () => { layer.remove(); canvas.remove(); map.attributionControl.removeAttribution(attribution); };
  }, [traffic, trafficStale]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !showMarkers) return;
    const layer = L.layerGroup().addTo(map);
    for (const mark of JSON.parse(markerData) as MapMarker[]) {
      const point = mark.coordinates[Math.floor(mark.coordinates.length / 2)];
      if (!point) continue;
      const pill = document.createElement('button');
      pill.className = mark.kind === 'bus' ? 'map-bus-pin' : 'map-pin';
      pill.type = 'button';
      pill.setAttribute('aria-label', mark.label);
      pill.style.setProperty('--pin', mark.color);
      if (mark.kind === 'bus') {
        pill.textContent = mark.badge;
        pill.title = mark.label;
      }
      pill.onclick = event => { L.DomEvent.stopPropagation(event); onPick.current(mark.id); };
      L.marker(latLng(point), { keyboard: false, zIndexOffset: mark.priority * 10, icon: L.divIcon({
        className: 'map-pin-wrap', html: pill, iconSize: [26, 26], iconAnchor: [13, 13],
      }) }).addTo(layer);
    }
    return () => { layer.remove(); };
  }, [markerData, showMarkers]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !selection?.coordinates.length) return;
    const path = selection.coordinates.map(latLng);
    const padding = window.matchMedia('(min-width: 901px)').matches ? 330 : 0;
    map.fitBounds(L.latLngBounds(path), { maxZoom: 15.8, animate: false,
      paddingTopLeft: [55, 105], paddingBottomRight: [padding + 30, 90] });
    if (selection.fitOnly) return;
    const mark = path.length > 1 ? L.polyline(path, { color: '#fff', weight: 4, interactive: false })
      : L.circleMarker(path[0], { color: '#fff', radius: 14, fill: false, interactive: false });
    mark.addTo(map);
    return () => { mark.remove(); };
  }, [selection]);

  return <div ref={element} className="overview-map-canvas" aria-label="Обзорная карта Петропавловска-Камчатского" />;
}

function embeddedUrl(cityUrl: string) {
  try {
    const base = new URL(cityUrl);
    if (!['https:', 'http:'].includes(base.protocol) || base.username || base.password) return null;
    base.pathname = base.pathname.replace(/\/?$/, '/');
    const url = new URL('static/ops/index.html', base);
    url.searchParams.set('parent', location.origin);
    url.searchParams.set('bus-markers', '2');
    url.searchParams.set('traffic', '1');
    return url;
  } catch { return null; }
}

export function OverviewMap(props: Props) {
  const { cityUrl, runId, cleanupAt, roads, traffic, trafficStale, markers, selection, showSnow, showMarkers, onSelect, onClientState, onModelState } = props;
  const url = useMemo(() => cityUrl ? embeddedUrl(cityUrl) : null, [cityUrl]);
  const [bridge, setBridge] = useState<{ url: string; state: 'ready' | 'offline'; busBadges?: boolean; traffic?: boolean }>();
  const frame = useRef<HTMLIFrameElement>(null);
  const callbacks = useRef({ onSelect, onModelState });
  callbacks.current = { onSelect, onModelState };
  const mapState = !url ? 'offline' : bridge?.url === url.href ? bridge.state : 'loading';
  const needsBusBadges = showMarkers && markers.some(mark => mark.kind === 'bus');
  const missingLayer = (needsBusBadges && !bridge?.busBadges) || (traffic?.length && !bridge?.traffic);
  const state = mapState === 'ready' && missingLayer ? 'offline' : mapState;
  useEffect(() => { onClientState(state); }, [state, onClientState]);

  useEffect(() => {
    if (!url) return;
    const timer = window.setTimeout(() => setBridge({ url: url.href, state: 'offline' }), 15000);
    function receive(event: MessageEvent) {
      if (event.origin !== url?.origin || event.source !== frame.current?.contentWindow) return;
      const message = event.data;
      if (message?.type === 'camchatka:ready' || message?.type === 'camchatka:error') {
        clearTimeout(timer);
        setBridge({ url: url.href, state: message.type === 'camchatka:ready' ? 'ready' : 'offline',
          busBadges: message.busBadges === true, traffic: message.traffic === true });
      }
      if (message?.type === 'camchatka:select' && typeof message.id === 'string') callbacks.current.onSelect(message.id);
      if (message?.type === 'camchatka:model' && ['loading', 'ready', 'failed'].includes(message.state)) {
        callbacks.current.onModelState(message.state, typeof message.progress === 'string' ? message.progress : undefined);
      }
    }
    window.addEventListener('message', receive);
    return () => { clearTimeout(timer); window.removeEventListener('message', receive); };
  }, [url]);

  useEffect(() => {
    if (state !== 'ready' || !url) return;
    frame.current?.contentWindow?.postMessage({ type: 'camchatka:state', runId, cleanupAt, showSnow, showMarkers, markers, traffic, trafficStale,
      roads: roads.filter(row => row.status === 'watch').map(row => row.coordinates), selection }, url.origin);
  }, [state, url, runId, cleanupAt, roads, traffic, trafficStale, markers, selection, showSnow, showMarkers]);

  return <div className="overview-map">
    {state !== 'ready' && <FallbackMap {...props} />}
    {url && <iframe key={url.href} ref={frame} src={url.href}
      title="Neverrie · городская 3D-карта и модель снега" className={state === 'ready' ? 'city-frame is-ready' : 'city-frame'}
      tabIndex={state === 'ready' ? 0 : -1} aria-hidden={state !== 'ready'} />}
  </div>;
}
