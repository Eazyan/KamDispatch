import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/sectionViews.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { deliveryBucket, upcomingForecast } = await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));

test('weather window keeps the current bucket and does not mix in daily or expired data', () => {
  const period = (at, horizon) => ({ at: '2026-11-21T' + at + ':00+12:00', horizon });
  const periods = [period('12:00', 'three_hourly'), period('00:00', 'daily'),
    period('03:00', 'three_hourly'), period('05:00', 'hourly'), period('06:00', 'three_hourly')];
  assert.deepEqual(upcomingForecast(periods, Date.parse('2026-11-21T06:15:00+12:00')), [periods[4], periods[0]]);
});

test('delivery states distinguish pending or uncertain work from completed records', () => {
  assert.deepEqual(['failed', 'ambiguous', 'queued', 'sending', 'sent', 'logged'].map(deliveryBucket),
    ['attention', 'attention', 'pending', 'pending', 'recorded', 'recorded']);
});
