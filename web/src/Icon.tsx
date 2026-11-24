import type { SVGProps } from 'react';

const paths = {
  mark: 'M3 18 10 6l4 6 3-4 4 10M7 18h14M9 11l1 2 2-1',
  overview: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z',
  dispatch: 'M8 6h13M8 12h13M8 18h13M3 6h1M3 12h1M3 18h1',
  camera: 'M3 6h13v12H3zM16 10l5-3v10l-5-3',
  forecast: 'M6 16a4 4 0 0 1-.4-8A6 6 0 0 1 17 9h.5a3.5 3.5 0 0 1 0 7h-1M10 17v5m-2.2-3.8 4.4 2.6m0-2.6-4.4 2.6',
  reports: 'M5 3h10l4 4v14H5zM15 3v5h4M8 12h8m-8 4h8',
  refresh: 'M20 7v5h-5M4 17v-5h5m-3-4a7 7 0 0 1 12-2l2 3M4 15l2 3a7 7 0 0 0 12-2',
  check: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm-4-9 3 3 5-6',
  bell: 'M5 17h14l-2-3V9a5 5 0 0 0-10 0v5l-2 3Zm5 3h4',
  expand: 'M14 4h6v6m0-6-7 7M10 20H4v-6m0 6 7-7',
  search: 'M10.5 17.5a7 7 0 1 1 0-14 7 7 0 0 1 0 14Zm5-2 4.8 4.8',
  profile: 'M12 13a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm-7 7a7 7 0 0 1 14 0',
  close: 'm6 6 12 12M18 6 6 18',
  chevron: 'm9 18 6-6-6-6',
  focus: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-19v5m0 10v5M2 12h5m10 0h5',
  layers: 'm3 8 9-5 9 5-9 5zm0 4 9 5 9-5M3 16l9 5 9-5',
  play: 'm8 4 12 8-12 8z',
  pause: 'M9 5v14m6-14v14',
  map: 'm3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3zM9 3v15m6-12v15',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-14v5l3 2',
  traffic: 'M9 2h6a2 2 0 0 1 2 2v13a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2Zm3 17v3m0-16h.01m0 4.5h.01m0 4.5h.01',
  bus: 'M5 16V6a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v10M5 16h14v3H5zM7 19v2m10-2v2M5 11h14M8 16h.1m7.9 0h.1',
} satisfies Record<string, string>;

export type IconName = keyof typeof paths;

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...props}><path d={paths[name]} /></svg>;
}
