import type { BusObservation, Camera, Dashboard, DispatchGroup, DispatchTask, RoadPoint, SnowSegment } from './types';

export const demoScenarios = {
  calm: 'Спокойная смена',
  snowfall: 'Начало снегопада',
  response: 'Уборка в городе',
} as const;
export type DemoScenario = keyof typeof demoScenarios;
export const demoClock = '2026-11-21T06:15:00+12:00';
const stormDay = '2026-01-15';
export const cleanupPhases = {
  assess: { title: 'До выезда', hour: '05:30', description: 'Сверяем снег по камере и обращениям. Участки ждут назначения.' },
  work: { title: 'Уборка', hour: '11:15', description: 'Бригада расчищает подход к переходу. Просрочка поднята наверх.' },
  review: { title: 'Приёмка', hour: '23:15', description: 'Бригады передали результаты. Диспетчер проверяет выполненные работы.' },
} as const;
export type CleanupPhase = keyof typeof cleanupPhases;
export const parseCleanupPhase = (value: string | null): CleanupPhase =>
  value && Object.hasOwn(cleanupPhases, value) ? value as CleanupPhase : 'work';
export const scenarioClock = (scenario: DemoScenario | null, phase: CleanupPhase) =>
  scenario === 'response' ? stormDay + 'T' + cleanupPhases[phase].hour + ':00+12:00' : demoClock;

// Nearest archived Open-Meteo ECMWF IFS cell, [158.77359, 53.04042].
// Hour, fresh snowfall in cm, sustained wind in m/s, temperature in °C.
const stormHours = [
  [0, 0.07, 13.4, -0.1],
  [1, 0.14, 13.22, -0.1],
  [2, 0.21, 14.14, 0.0],
  [3, 0.42, 14.31, -0.2],
  [4, 1.12, 14.66, -0.0],
  [5, 1.47, 15.42, 0.3],
  [6, 1.82, 11.48, 0.7],
  [7, 0.77, 12.19, 0.4],
  [8, 0.77, 13.39, 0.4],
  [9, 0.84, 12.53, 0.5],
  [10, 0.91, 12.96, 0.8],
  [11, 1.12, 10.89, 0.8],
  [12, 2.24, 11.9, -0.5],
  [13, 0.98, 10.23, -0.8],
  [14, 0.56, 6.44, -1.4],
  [15, 0.63, 5.37, -1.2],
  [16, 0.84, 5.23, -0.7],
  [17, 1.61, 7.18, -0.2],
  [18, 2.1, 11.83, 0.2],
  [19, 1.05, 12.55, -0.0],
  [20, 0.49, 13.22, -0.2],
  [21, 0.77, 12.56, -0.3],
  [22, 0.84, 12.78, -0.5],
  [23, 0.77, 12.17, -0.5],
] as const;
const controlFrame = 'demo-snowfall';
export const demoFrameUrl = () => '/overview/snowfall.webp';
export const parseScenario = (value: string | null): DemoScenario | null =>
  value && Object.hasOwn(demoScenarios, value) ? value as DemoScenario : null;

const telman: RoadPoint[] = [[158.64818, 53.04677], [158.64864, 53.04655], [158.65032, 53.04594]];
const quarry: RoadPoint[] = [[158.65196, 53.04062], [158.652, 53.04147], [158.65194, 53.04311]];
const elizovskaya: RoadPoint[] = [[158.65318, 53.04062], [158.65319, 53.04151], [158.65369, 53.04365]];
const vladivostok: RoadPoint[] = [[158.65248, 53.04676], [158.65326, 53.04636], [158.65536, 53.04602]];
const lake: RoadPoint = [158.6412, 53.0328];
const roads: SnowSegment[] = [
  { id: 'telman', name: 'улица Тельмана', coordinates: telman, excess_cm: 4.03, camera_id: 'telman', osm_way: null, status: 'watch' },
  { id: 'quarry', name: 'Карьерная улица', coordinates: quarry, excess_cm: 6.04, camera_id: null, osm_way: null, status: 'watch' },
  { id: 'elizovskaya', name: 'Елизовская улица', coordinates: elizovskaya, excess_cm: 6.99, camera_id: null, osm_way: null, status: 'watch' },
  { id: 'vladivostok', name: 'Владивостокская улица', coordinates: vladivostok, excess_cm: 0.25, camera_id: null, osm_way: null, status: 'no_signal' },
];
const task = (id: string, stage: DispatchTask['stage'], assignee: string, due_at: string, at: (time: string) => string, instructions: string, result = '', overdue = false): DispatchTask => ({
  id, stage, assignee, due_at, overdue, assignment_version: 1, created_at: at('04:40'),
  assigned_at: at('06:50'), accepted_at: at('06:55'), response_seconds: 300,
  work_kind: 'clear', instructions, result: stage === 'done' ? result : '', return_note: null,
});
const report = (id: string, address: string, description: string, coordinates: RoadPoint[], time: string, linked = false) => ({
  id, address, description, map_section: { coordinates }, linked, active: true,
  last_seen: time, reference: id, channel: 'Телефон',
});

function demoBusData(scenario: DemoScenario, phase: CleanupPhase, clock: string): Pick<Dashboard, 'bus_observations' | 'bus_map'> {
  const lastSeen = new Date(Date.parse(clock) - 60_000).toISOString();
  const earlier = scenario === 'response' && phase === 'assess';
  const quiet = scenario === 'calm' || (scenario === 'response' && phase === 'review');
  const telmanCheck = scenario === 'snowfall';
  const quarryCheck = scenario === 'response' && !quiet;
  const rows: BusObservation[] = [
    { section_id: 'telman', name: 'улица Тельмана', direction: 'forward', direction_label: 'На юго-восток',
      coordinates: telman, usual_seconds: 120, observed_seconds: telmanCheck ? 240 : 123, vehicle_count: 2,
      status: telmanCheck ? 'check' : 'observed', reason: telmanCheck ? 'buses' : null, last_seen: lastSeen },
    { section_id: 'quarry', name: 'Карьерная улица', direction: 'forward', direction_label: 'На север',
      coordinates: quarry, usual_seconds: 118, observed_seconds: quiet ? 121 : 240,
      vehicle_count: earlier || telmanCheck ? 1 : 2, status: quarryCheck ? 'check' : telmanCheck ? 'insufficient' : 'observed',
      reason: quarryCheck ? earlier ? 'report' : 'buses' : null, last_seen: lastSeen },
  ];
  return {
    bus_observations: { source: 'simulation', scenarios: [
      { id: scenario + '-' + phase, title: demoScenarios[scenario], as_of: clock, rows },
    ] },
    bus_map: { source: 'simulation', state: 'ready', positions: rows.flatMap((row, index) => {
      const road = index === 0 ? telman : quarry;
      return road.slice(-row.vehicle_count).map(([lon, lat], bus) => ({
        id: row.section_id + '-' + bus, lon, lat, route: 'Д' + (index + 1), measured_at: lastSeen,
      }));
    }) },
  };
}

export function demoDashboard(scenario: DemoScenario, phase: CleanupPhase = 'work'): Dashboard {
  const calm = scenario === 'calm';
  const response = scenario === 'response';
  const clock = scenarioClock(scenario, phase);
  const day = clock.slice(0, 10);
  const at = (time: string) => day + 'T' + time + ':00+12:00';
  const before = response && phase === 'assess';
  const review = response && phase === 'review';
  const reportTime = at(before ? '05:06' : '06:06');
  const camera: Camera = {
    id: 'telman', name: 'Тельмана · дорожная камера', area: 'Петропавловск-Камчатский', district: 'Центр',
    road: true, coordinates: telman[0], section_name: 'улица Тельмана', location_note: 'Контрольный кадр для демосценария.',
    status: calm ? 'waiting' : 'available', last_poll: clock, last_frame: calm ? null : clock,
    source_at: calm ? null : clock,
    frame_id: calm ? null : controlFrame, analyzed_frame_id: calm ? null : controlFrame,
    analyzed_frame_at: calm ? null : clock, analysis_stage: calm ? null : 'done',
    scene: calm ? null : { falling_snow: 'yes', intensity: 'heavy', visibility: 'poor',
      snow_on_ground: 'uncertain', large_snowdrifts: 'uncertain', road_coverage: 'not_visible',
      evidence: 'Плотный снег в воздухе ограничивает видимость.', format_valid: true, model: 'control-label', prompt_version: 'demo' },
  };
  const event = { id: 'demo-snow', kind: 'snowfall', name: camera.name, area: camera.area,
    severity: 'urgent' as const, state: 'new', version: 1, opened_at: at(before ? '04:53' : '06:04'),
    last_seen: clock, ended_at: null, evidence_stale: false, first_frame: controlFrame, latest_frame: controlFrame,
    reason: 'Сильный снегопад · видимость снижена' };
  const queue: DispatchGroup[] = calm ? [] : [{
    location_key: 'telman', address: 'Улица Тельмана', district: 'Центр', kind: 'snowfall', severity: 'urgent',
    last_seen: clock, task: response && !before ? task('t1', review ? 'done' : 'in_progress', 'Бригада № 2', at('13:30'), at,
      'Расчистить подход к пешеходному переходу.', 'Подход к переходу расчищен с обеих сторон.') : null,
    signals: [{ id: event.id, name: camera.name, reason: event.reason, frame_id: controlFrame, active: true, linked: response && !before,
      last_seen: clock, road_priority_reason: 'Подход к пешеходному переходу' }],
    reports: response ? [report('1045', 'Улица Тельмана', 'Снег заносит подход к переходу.', telman, reportTime, !before)] : [],
  }, {
    location_key: 'quarry', address: 'Карьерная улица', district: 'Центр', kind: 'drift', severity: 'watch', task: null,
    last_seen: reportTime, signals: [],
    reports: [report('1047', 'Карьерная улица', 'У выезда образовался сугроб. Машины с трудом разъезжаются.', quarry, reportTime)],
  }];
  if (response) queue.push({
    location_key: 'elizovskaya', address: 'Елизовская улица', district: 'Центр', kind: 'drift', severity: 'urgent',
    last_seen: at(before ? '05:10' : '05:40'), signals: [],
    task: before ? null : task('t2', review ? 'done' : 'accepted', 'Бригада № 4', at('10:00'), at,
      'Убрать нанос у выезда на главную дорогу.', 'Нанос убран, выезд открыт для двух направлений.', !review),
    reports: [report('1042', 'Елизовская улица', 'Сужен выезд на главную дорогу.', elizovskaya, at('05:10'), !before)],
  }, {
    location_key: 'vladivostok', address: 'Владивостокская улица', district: 'Центр', kind: 'coverage', severity: 'watch',
    last_seen: at(before ? '05:15' : '10:51'), signals: [],
    task: before ? null : task('t3', 'done', 'Бригада № 1', at('11:00'), at,
      'Расчистить полосу движения на подъёме.', 'Подъём расчищен, бригада передала результат.'),
    reports: [report('1040', 'Владивостокская улица', 'На подъёме скопился снег.', vladivostok, at(before ? '05:15' : '10:51'), !before)],
  });
  const period = { at: at('09:00'), horizon: 'three_hourly' as const,
    probability: calm ? .09 : .84, snow_mm: calm ? 0 : 4.8, snow: !calm, wind_mps: calm ? 3 : 12, temperature_c: calm ? -6 : -3 };
  const point = 'Петропавловск-Камчатский';
  const archived = stormHours.map(([hour, snow_cm, wind_mps, temperature_c]) => ({
    at: at(String(hour).padStart(2, '0') + ':00'), horizon: 'hourly' as const,
    snow: snow_cm > 0, snow_cm, snow_mm: null, wind_mps, temperature_c, probability: null,
  }));
  const historicalWindow = { ...archived[6], start: at('04:00'), end: at('13:00') };
  const warnings = calm ? [] : [{ point, payload: response ? historicalWindow : { ...period,
    start: at('07:00'), end: at('10:00') } }];
  const periods = [6, 9, 12, 15, 18, 21].map((hour, index) => {
    const snow = !calm && index < 4;
    return { ...period, at: at(String(hour).padStart(2, '0') + ':00'),
      snow, probability: snow ? [.57, .84, .91, .62][index] : .09,
      snow_mm: snow ? [2.1, 4.8, 5.2, 1.8][index] : 0,
      wind_mps: snow ? [8, 12, 14, 9][index] : 3, temperature_c: calm ? -6 + index % 3 : -3 + index % 3 };
  });
  const summary = {
    day, payload: {
      start: at('00:00'), end: clock, events: calm ? [] : [event],
      stale_cameras: response ? ['Култучное озеро'] : [], warnings,
      actions: response && !before ? [{ state: 'acknowledged', note: review ? 'Результат уборки передан диспетчеру.' : 'Бригада выехала на участок.',
        name: camera.name, created_at: at(review ? '23:05' : '07:10') }] : [],
    },
  };
  const deliveries = [{
    id: 1, kind: 'summary', subject: 'Утренняя сводка по городу',
    body: calm ? 'За смену дорожные сигналы не поступали.' : 'Отмечен сильный снегопад на улице Тельмана.',
    stage: 'sent', attempts: 1, error: null, created_at: clock,
  }, ...(response ? [{
    id: 2, kind: 'event', subject: 'Сигнал с улицы Тельмана',
    body: 'Снегопад ограничивает видимость на подходе к переходу.',
    stage: 'failed', attempts: 2, error: 'Демонстрационный сбой почтового сервера.', created_at: event.opened_at,
  }, {
    id: 3, kind: 'forecast', subject: 'Предупреждение о снегопаде',
    body: 'Снег и усиление ветра ожидаются в первой половине дня.',
    stage: 'queued', attempts: 0, error: null, created_at: event.opened_at,
  }] : [])];
  return {
    ...demoBusData(scenario, phase, clock),
    mode: 'demo', run_id: 'design-demo', role: 'viewer', cameras: [camera, {
      ...camera, id: 'lake', name: 'Култучное озеро', coordinates: lake, road: false, section_name: null,
      status: response ? 'stale' : 'waiting', last_frame: null,
      frame_id: null, analyzed_frame_id: null, analyzed_frame_at: null, analysis_stage: null, scene: null,
    }], events: calm ? [] : [event], dispatch_queue: queue, dispatch_locations: queue,
    dispatch_assignees: [], warnings,
    forecasts: response ? [{ point, fetched_at: at('00:00'), source: 'Архив Open-Meteo · ECMWF IFS', periods: archived }]
      : [{ point, periods, fetched_at: clock }, { point: 'Елизово', fetched_at: clock,
      periods: periods.map(item => ({ ...item, temperature_c: item.temperature_c - 2,
        wind_mps: Math.max(0, item.wind_mps - 3) })) }],
    snow_forecast: response ? null : { status: 'ready', scope: 'pilot', calculated_at: clock,
      start: at('06:00'), end: at('09:00'),
      weather_sources: ['Контрольный сценарий'], segments: calm ? [] : roads },
    summaries: [summary], deliveries, checks: [], inspection_queue: [], feedback_counts: response && !before ? { confirmed: 1 } : {}, queue: { pending: 0, oldest: null },
  };
}
