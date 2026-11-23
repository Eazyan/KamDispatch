import { useEffect, useState } from 'react';
import type { RoadPoint } from './types';

export const trafficColors = { free: '#52cf73', slow: '#f6be48', congested: '#f46c67' };
export type TrafficSegment = {
  corridor_id: string;
  coordinates: RoadPoint[];
  status: keyof typeof trafficColors;
};
type TrafficPublication = {
  published_at: string;
  valid_until: string;
  status: 'complete' | 'partial';
  segments: TrafficSegment[];
};

export function useTraffic(enabled: boolean) {
  const [publication, setPublication] = useState<TrafficPublication | null>();
  const [unavailable, setUnavailable] = useState(false);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let busy = false;
    async function refresh() {
      setNow(Date.now());
      if (busy) return;
      busy = true;
      try {
        const response = await fetch('/api/v1/traffic', { signal: controller.signal });
        if (!response.ok) throw new Error('Traffic feed unavailable');
        const result: TrafficPublication | null = await response.json();
        if (!controller.signal.aborted) {
          setPublication(result);
          setUnavailable(false);
        }
      } catch {
        if (!controller.signal.aborted) setUnavailable(true);
      } finally {
        busy = false;
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [enabled]);
  return {
    publication,
    unavailable,
    stale: Boolean(publication && now >= Date.parse(publication.valid_until)),
  };
}
