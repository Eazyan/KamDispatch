import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from './Icon';
import type { Camera, DispatchGroup, RoadPoint } from './types';

type Bus = { id: string; route: string | null; lon: number; lat: number };
type Result = { id: string; title: string; label?: string; open: () => void };
export type SearchHit = {
  id: string; type: 'street' | 'address' | 'camera' | 'bus' | 'task' | 'event' | 'page'; name: string;
  page?: string | null;
  coordinates: RoadPoint | null;
  paths?: RoadPoint[][];
};
type Props = {
  cameras: Camera[];
  scenario: boolean;
  buses: Bus[];
  dispatch: DispatchGroup[];
  search: (query: string, signal: AbortSignal) => Promise<SearchHit[]>;
  onRoad: (location: { id: string; name: string; coordinates: RoadPoint[]; paths?: RoadPoint[][] }) => void;
  onCamera: (id: string) => void;
  onBus: (id: string, coordinates: RoadPoint) => void;
  onDispatch: (row: DispatchGroup) => void;
  onCitizen: (id: string) => void;
};

const normalize = (value: string) => value.toLocaleLowerCase('ru').replaceAll('ё', 'е')
  .replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

export function MapSearch({ cameras, buses, dispatch, scenario, search, onRoad, onCamera, onBus, onDispatch, onCitizen }: Props) {
  const [query, setQuery] = useState('');
  const [response, setResponse] = useState<{ term: string; hits?: SearchHit[] }>();
  const [active, setActive] = useState(false);
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDialogElement>(null);
  const term = query.trim();

  useEffect(() => {
    if (!active || !term) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      search(term, controller.signal).then(hits => {
        if (!controller.signal.aborted) setResponse({ term, hits });
      }).catch(() => {
        if (!controller.signal.aborted) setResponse({ term });
      });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [active, term, search]);

  const current = response?.term === term ? response : undefined;
  const streets: Result[] = [];
  const addresses: Result[] = [];
  const cameraResults: Result[] = [];
  const busResults: Result[] = [];
  const dispatchResults: Result[] = [];
  const pages: Result[] = [];
  const hits = [...(current?.hits ?? [])];
  if (scenario && term) {
    const words = normalize(term).split(' ').map(word =>
      ({ камеры: 'камера', автобусы: 'автобус', маршруты: 'маршрут', задания: 'задание',
        обращения: 'обращение' } as Record<string, string>)[word] ?? word);
    const matches = (text: string) => {
      const normalized = normalize(text);
      return words.every(word => /\d/.test(word) ? normalized.split(' ').includes(word) : normalized.includes(word));
    };
    const scenarios: SearchHit[] = [
      ...cameras.filter(camera => matches(['Камера', camera.name, camera.area, camera.district].join(' ')))
        .map((camera): SearchHit => ({ id: camera.id, type: 'camera', name: camera.name, coordinates: camera.coordinates ?? null })),
      ...buses.filter(bus => bus.route && matches('Автобус Маршрут ' + bus.route))
        .map((bus): SearchHit => ({ id: bus.id, type: 'bus', name: 'Маршрут ' + bus.route, coordinates: [bus.lon, bus.lat] })),
      ...dispatch.filter(row => matches([row.task ? 'Задание' : 'Обращение', row.address, row.district,
        ...row.reports.map(report => report.description)].join(' ')))
        .map((row): SearchHit => ({ id: row.location_key + '/' + row.kind, type: row.task ? 'task' : 'event',
          name: row.address, coordinates: null })),
    ];
    hits.unshift(...scenarios.filter(row => !hits.some(hit => hit.id === row.id && hit.type === row.type)));
  }
  const point = (coordinates: SearchHit['coordinates']): coordinates is RoadPoint =>
    !!coordinates && coordinates.length === 2 && coordinates.every(Number.isFinite);
  for (const hit of hits) {
    if (hit.type === 'street' && hit.paths?.length) {
      const paths = hit.paths;
      streets.push({ id: hit.id, title: hit.name,
        open: () => onRoad({ id: hit.id, name: hit.name, coordinates: paths.flat(), paths }) });
    } else if (hit.type === 'address' && point(hit.coordinates)) {
      const coordinates = hit.coordinates;
      addresses.push({ id: hit.id, title: hit.name, open: () => onRoad({ id: hit.id, name: hit.name, coordinates: [coordinates] }) });
    } else if (hit.type === 'camera') {
      const camera = cameras.find(item => item.id === hit.id);
      const coordinates = hit.coordinates;
      cameraResults.push({ id: hit.id, title: hit.name, label: camera?.district ?? camera?.area,
        open: () => {
          if (camera || !point(coordinates)) onCamera(hit.id);
          else onRoad({ id: hit.id, name: hit.name, coordinates: [coordinates] });
        } });
    } else if (hit.type === 'bus') {
      const bus = buses.find(item => item.id === hit.id);
      const coordinates = bus ? [bus.lon, bus.lat] as RoadPoint : hit.coordinates;
      if (point(coordinates)) busResults.push({ id: hit.id, title: hit.name, open: () => onBus(hit.id, coordinates) });
    } else if (hit.type === 'task' || hit.type === 'event') {
      const row = dispatch.find(item => item.location_key + '/' + item.kind === hit.id);
      dispatchResults.push({ id: hit.id, title: hit.name, label: row?.district, open: () => {
        if (hit.id.startsWith("citizen:")) onCitizen(hit.id.slice("citizen:".length));
        else if (row) onDispatch(row);
        else if (hit.page) location.hash = hit.page;
      } });
    } else if (hit.type === 'page' && hit.page) {
      const page = hit.page;
      pages.push({ id: hit.id, title: hit.name, open: () => { location.hash = page; } });
    }
  }
  const groups: [string, Result[]][] = [
    ['Адреса', addresses], ['Улицы и участки', streets], ['Камеры', cameraResults], ['Автобусы', busResults],
    ['Обращения и задания', dispatchResults], ['Разделы', pages],
  ];

  function open() {
    panel.current?.showModal();
    setActive(true);
  }

  function close() {
    panel.current?.close();
    trigger.current?.focus();
  }

  return <div className="ops-search">
    <button ref={trigger} className="ops-topbar-button" type="button" aria-label="Поиск по карте"
      title="Поиск по карте" aria-haspopup="dialog" aria-expanded={active} aria-controls={panelId} onClick={open}>
      <Icon name="search" />
    </button>
    <dialog ref={panel} id={panelId} className="ops-search-panel" aria-label="Поиск по карте"
      onClose={() => setActive(false)}
      onKeyDown={event => {
        if (event.key === 'Escape') {
          event.preventDefault();
          close();
        }
      }}
      onClick={event => {
        if (event.target !== event.currentTarget) return;
        const bounds = event.currentTarget.getBoundingClientRect();
        if (event.clientX < bounds.left || event.clientX > bounds.right ||
          event.clientY < bounds.top || event.clientY > bounds.bottom) close();
      }}>
      <div className="ops-search-field">
        <Icon name="search" width="18" height="18" />
        <input autoFocus autoComplete="off" type="search" maxLength={120} aria-label="Адрес, камера, автобус или раздел"
          placeholder="Адрес, камера, автобус, раздел" value={query} onChange={event => setQuery(event.target.value)} />
        <button className="ops-search-close" type="button" aria-label="Закрыть поиск" onClick={close}>
          <Icon name="close" width="17" height="17" />
        </button>
      </div>
      {term && <div className="ops-search-results" aria-label="Результаты поиска">
        {groups.map(([name, items]) => items.length > 0 && <section key={name} aria-label={name}>
          <strong>{name}</strong>
          {items.slice(0, 5).map(item => <button type="button" key={item.id} onClick={() => { close(); item.open(); }}>
            <span>{item.title}{item.label && <small>{item.label}</small>}</span>
            <Icon name="chevron" width="15" height="15" />
          </button>)}
        </section>)}
        {!current && <p role="status">Ищем…</p>}
        {current && !current.hits && <p role="status">Поиск недоступен</p>}
        {current?.hits && !groups.some(([, items]) => items.length) && <p role="status">Ничего не найдено</p>}
      </div>}
    </dialog>
  </div>;
}
