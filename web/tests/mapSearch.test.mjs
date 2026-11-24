import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import ts from 'typescript';

const source = await readFile(new URL('../src/mapSearchResults.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext } });
const { dispatchSearchTarget, matchesSearch, serviceSearchHits } =
  await import('data:text/javascript;base64,' + Buffer.from(outputText).toString('base64'));

const citizenId = '0921bde5-e0a1-42cf-a37f-a5b4c1c02276';
const row = { location_key: 'citizen:' + citizenId, kind: 'coverage' };

test('search opens a citizen dispatch task or queue event in dispatch', () => {
  for (const type of ['task', 'event']) {
    const hit = { id: row.location_key + '/' + row.kind, type, page: type === 'task' ? 'dispatch' : 'citizens' };
    assert.deepEqual(dispatchSearchTarget(hit, [row]), { type: 'dispatch', row });
    assert.deepEqual(dispatchSearchTarget(hit, []), { type: 'page', page: 'dispatch' });
  }
});

test('search passes only the UUID when it opens the original citizen report', () => {
  const report = { id: row.location_key, type: 'event', page: 'citizens' };
  assert.deepEqual(dispatchSearchTarget(report, [row]), { type: 'citizen', id: citizenId });
});

test('a demo scenario keeps only shared map and navigation results from the service', () => {
  const hits = ['street', 'camera', 'address', 'poi', 'bus', 'task', 'event', 'page']
    .map(type => ({ id: type, type }));
  assert.deepEqual(serviceSearchHits(hits, true).map(hit => hit.type), ['street', 'address', 'poi', 'page']);
  assert.deepEqual(serviceSearchHits(hits, false), hits);
  assert.equal(hits.length, 8);
});

test('camera filtering accepts keyboard punctuation and matches numbers exactly', () => {
  const fields = ['Подъём № 20', 'Нагорный район'];
  assert.ok(matchesSearch('нагорный подъём, №20', fields));
  assert.ok(matchesSearch('подъем', fields));
  assert.ok(!matchesSearch('подъём 2', fields));
});
