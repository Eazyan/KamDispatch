import type { BusObservation, Camera, Dashboard, DispatchGroup, DispatchTask, RoadPoint, SnowSegment, Warning } from './types';

export const taskStages: Record<DispatchTask['stage'], string> = {
  draft: 'Ожидает назначения', sent: 'Выдано', accepted: 'Принято бригадой',
  in_progress: 'В работе', done: 'Ожидает приёмки', returned: 'На доработке', closed: 'Закрыто',
};
export const dispatchKey = (row: DispatchGroup) => JSON.stringify([row.location_key, row.kind]);

type WidgetBase = { id: string; priority: number; at: string; coordinates?: RoadPoint[] };
export type Widget =
  | WidgetBase & { type: 'dispatch'; row: DispatchGroup; segment?: SnowSegment; title: string }
  | WidgetBase & { type: 'model'; segment: SnowSegment; title: string }
  | WidgetBase & { type: 'forecast'; warning: Warning; title: string }
  | WidgetBase & { type: 'camera'; camera: Camera; title: string }
  | WidgetBase & { type: 'bus'; observation: BusObservation; title: string };

const score = { urgent: 100, overdue: 90, review: 80, assign: 70, report: 65, bus: 64, camera: 60, work: 45, forecast: 35, model: 25 };
const normalized = (value: string) => value.toLowerCase().replace(/ё/g, 'е').trim();
const distance = (a: RoadPoint, b: RoadPoint) => Math.hypot((a[0] - b[0]) * 67_000, (a[1] - b[1]) * 111_000);
function near(a: RoadPoint[], b: RoadPoint[], limit: number) {
  return a.some(point => b.some(other => distance(point, other) < limit));
}

function localCamera(camera: Camera) {
  const point = camera.coordinates;
  if (point) return point[0] >= 156 && point[0] <= 164 && point[1] >= 50 && point[1] <= 61;
  return /камчат|елизов|морозная/i.test(camera.area + ' ' + camera.name);
}

export function dispatchPriority(row: DispatchGroup) {
  const task = row.task;
  if (row.severity === 'urgent' && (!task || task.stage === 'draft')) return score.urgent;
  if (task?.overdue && task.stage !== 'done') return score.overdue;
  if (task?.stage === 'done') return score.review;
  if (task?.stage === 'draft') return score.assign;
  if (row.reports.some(item => item.active && !item.linked)) return score.report;
  if (task) return score.work;
  return score.camera;
}

export function overviewWidgets(data: Dashboard, segments: SnowSegment[], now: number): Widget[] {
  const watched = data.snow_forecast?.status === 'ready' || data.snow_forecast?.status === 'partial'
    ? segments.filter(row => row.status === 'watch').sort((a, b) =>
      (b.excess_cm ?? 0) - (a.excess_cm ?? 0) || a.id.localeCompare(b.id)) : [];
  const claimed = new Set<string>();
  const widgets: Widget[] = [];
  const busDemo = data.bus_observations?.source === 'simulation' ? data.bus_observations.scenarios[0] : undefined;
  for (const observation of busDemo?.rows ?? []) {
    if (observation.status !== 'check') continue;
    widgets.push({ type: 'bus', id: 'bus-delay:' + observation.section_id + ':' + observation.direction,
      priority: score.bus, title: observation.name, at: observation.last_seen ?? busDemo?.as_of ?? '',
      coordinates: observation.coordinates, observation });
  }
  const featuredFrames = new Set<string>();
  for (const row of data.dispatch_queue) {
    if (!row.task && !row.reports.some(item => item.active) && !row.signals.some(item => item.active)) continue;
    const frameIds = row.signals.filter(item => item.active).map(item => item.frame_id);
    for (const frameId of frameIds) if (frameId) featuredFrames.add(frameId);
    const road = row.reports.find(item => item.active && item.map_section)?.map_section?.coordinates
      ?? row.reports.find(item => item.map_section)?.map_section?.coordinates;
    const camera = data.cameras.find(item => item.coordinates &&
      (item.frame_id && frameIds.includes(item.frame_id) ||
        item.analyzed_frame_id && frameIds.includes(item.analyzed_frame_id) ||
        row.signals.some(signal => signal.name === item.name)));
    const coordinates = road ?? (camera?.coordinates ? [camera.coordinates] : undefined);
    const segment = coordinates && ['drift', 'coverage', 'snowfall'].includes(row.kind)
      ? watched.find(item => !claimed.has(item.id) && near(coordinates, item.coordinates, road ? 35 : 65) &&
        (Boolean(road) || normalized(row.address).includes(normalized(item.name)) || item.camera_id === camera?.id))
      : undefined;
    if (segment) claimed.add(segment.id);
    widgets.push({ type: 'dispatch', id: dispatchKey(row), priority: dispatchPriority(row),
      at: row.last_seen, row, title: row.address, coordinates: road ?? segment?.coordinates ?? coordinates, segment });
  }
  for (const camera of data.cameras) {
    const goodFrame = ['available', 'unchanged'].includes(camera.status);
    const frameId = camera.analyzed_frame_id;
    if (!localCamera(camera) || !goodFrame || !frameId || featuredFrames.has(frameId) || featuredFrames.has(camera.frame_id ?? '') ||
      camera.scene?.format_valid !== true || camera.scene.falling_snow !== 'yes') continue;
    widgets.push({ type: 'camera', id: 'camera:' + camera.id, title: camera.name, priority: score.camera - 5,
      at: camera.analyzed_frame_at ?? camera.last_frame ?? '', camera, coordinates: camera.coordinates ? [camera.coordinates] : undefined });
  }
  // Limit redundant road cards while keeping the model layer complete on the map.
  const modelCards: SnowSegment[] = [];
  for (const segment of watched) {
    if (claimed.has(segment.id) || modelCards.some(other =>
      normalized(segment.name) === normalized(other.name) && near(segment.coordinates, other.coordinates, 250))) continue;
    modelCards.push(segment);
    widgets.push({ type: 'model', id: 'model:' + segment.id, title: segment.name,
      priority: score.model, at: data.snow_forecast?.calculated_at ?? '', segment, coordinates: segment.coordinates });
    if (modelCards.length === 3) break;
  }
  for (const warning of data.warnings) {
    if (Date.parse(warning.payload.end) <= now) continue;
    widgets.push({ type: 'forecast', id: 'forecast:' + warning.point, title: warning.point,
      priority: score.forecast, at: warning.payload.start, warning });
  }
  return widgets.sort((a, b) => b.priority - a.priority || b.at.localeCompare(a.at) || a.id.localeCompare(b.id));
}

export function actionRequired(widget: Widget) {
  return widget.type === 'bus' || widget.type === 'dispatch' && (widget.priority >= score.report ||
    (!widget.row.task && widget.row.signals.some(signal => signal.active && !signal.linked)));
}
