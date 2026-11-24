import type { DispatchGroup, RoadPoint } from './types';

export type SearchHit = {
  id: string; type: 'street' | 'address' | 'camera' | 'bus' | 'task' | 'event' | 'page'; name: string;
  page?: string | null;
  route?: string | null;
  coordinates: RoadPoint | null;
  paths?: RoadPoint[][];
};

export function serviceSearchHits(hits: SearchHit[], scenario: boolean): SearchHit[] {
  return scenario
    ? hits.filter(hit => hit.type === 'street' || hit.type === 'address' || hit.type === 'page')
    : [...hits];
}

export function dispatchSearchTarget(hit: SearchHit, dispatch: DispatchGroup[]) {
  const row = dispatch.find(item => item.location_key + '/' + item.kind === hit.id);
  if (row) return { type: 'dispatch', row } as const;

  const reportId = hit.type === 'event'
    ? /^citizen:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$/i.exec(hit.id)?.[1]
    : undefined;
  if (reportId) return { type: 'citizen', id: reportId } as const;

  const page = hit.type === 'task' || hit.id.includes('/') ? 'dispatch' : hit.page;
  return { type: 'page', page } as const;
}
