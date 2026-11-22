import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

async function loadModule(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
  return import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));
}
const [scenarios, ranking] = await Promise.all([loadModule('../src/overviewDemo.ts'), loadModule('../src/overviewWidgets.ts')]);

const getWidgets = data => ranking.overviewWidgets(data, data.snow_forecast?.segments ?? [], Date.parse(data.run_id === 'design-demo' && data.forecasts[0]?.source
  ? scenarios.scenarioClock('response', 'work') : scenarios.demoClock));

test('calm shift stays quiet and snowfall includes one corroborated bus delay', () => {
  assert.deepEqual(getWidgets(scenarios.demoDashboard('calm')), []);
  const widgets = getWidgets(scenarios.demoDashboard('snowfall'));
  assert.equal(widgets[0].row.location_key, 'telman');
  assert.equal(widgets[0].priority, 100);
  assert.equal(widgets[0].segment.id, 'telman');
  assert.equal(widgets.filter(item => item.id === 'model:telman').length, 0);
  assert.equal(widgets.filter(item => item.type === 'camera').length, 0);
  assert.equal(widgets.filter(item => item.type === 'bus').length, 1);
  const bus = widgets.find(item => item.type === 'bus');
  assert.equal(bus.observation.section_id, 'telman');
  assert.equal(bus.observation.vehicle_count, 2);
  assert.equal(ranking.actionRequired(bus), true);
  assert.equal(widgets.filter(ranking.actionRequired).length, 3);
});

test('overdue work and acceptance precede new non-urgent reports and running work', () => {
  const widgets = getWidgets(scenarios.demoDashboard('response')).filter(item => item.type === 'dispatch');
  assert.deepEqual(widgets.map(item => item.row.location_key), ['elizovskaya', 'vladivostok', 'quarry', 'telman']);
  assert.deepEqual(widgets.map(ranking.actionRequired), [true, true, true, false]);
});

test('stale model, expired weather and inactive sources leave the active list', () => {
  const data = scenarios.demoDashboard('snowfall');
  data.snow_forecast.status = 'stale';
  data.warnings[0].payload.end = '2026-11-20T16:00:00+12:00';
  data.dispatch_queue[0].signals[0].active = false;
  data.cameras[0].status = 'stale';
  const widgets = getWidgets(data).filter(item => item.type !== 'bus');
  assert.deepEqual(widgets.map(item => item.row.location_key), ['quarry']);
  assert.equal(widgets[0].segment, undefined);
});

test('a snow observation outside Kamchatka stays in the cameras section', () => {
  const data = scenarios.demoDashboard('snowfall');
  const remoteCamera = { ...data.cameras[0], id: 'remote', coordinates: [33.58, 67.60],
    frame_id: 'remote-frame', analyzed_frame_id: 'remote-frame' };
  data.cameras.push(remoteCamera);
  assert.equal(getWidgets(data).some(item => item.id === 'camera:remote'), false);
  remoteCamera.coordinates = [158.6, 53.06];
  assert.equal(getWidgets(data).some(item => item.id === 'camera:remote'), true);
});

test('an unassigned task appears in the action filter', () => {
  const data = scenarios.demoDashboard('response');
  const row = data.dispatch_queue.find(item => item.location_key === 'telman');
  row.severity = 'watch';
  row.task = { ...row.task, stage: 'draft', assignee: null };
  const widget = getWidgets(data).find(item => item.row?.location_key === 'telman');
  assert.equal(ranking.actionRequired(widget), true);
});

test('cleanup demo uses a single cyclone day and progresses from dispatch to review', () => {
  const before = scenarios.demoDashboard('response', 'assess');
  const work = scenarios.demoDashboard('response', 'work');
  const review = scenarios.demoDashboard('response', 'review');
  assert(before.dispatch_queue.every(row => row.task === null));
  assert.equal(work.dispatch_queue.find(row => row.location_key === 'telman').task.stage, 'in_progress');
  assert.equal(review.dispatch_queue.filter(row => row.task?.stage === 'done').length, 3);
  assert.equal(work.snow_forecast, null);
  assert.match(work.forecasts[0].source, /Open-Meteo/);
  assert.equal(work.forecasts[0].periods.find(period => period.at.includes('T06:00')).snow_cm, 1.82);
  assert(review.dispatch_queue.every(row => row.last_seen.startsWith('2026-01-15')));
  const busRow = data => data.bus_observations.scenarios[0].rows.find(row => row.section_id === 'quarry');
  assert.equal(busRow(before).reason, 'report');
  assert.equal(busRow(work).reason, 'buses');
  assert.equal(busRow(review).status, 'observed');
  assert.equal(getWidgets(review).some(widget => widget.type === 'bus'), false);
});
