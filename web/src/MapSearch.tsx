import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from './Icon';
import type { Camera, DispatchGroup, DispatchRoad, RoadPoint } from './types';

type Bus = { id: string; route: string | null; lon: number; lat: number };
type Result = { id: string; title: string; label?: string; open: () => void };
export type SearchHit = {
  id: string; type: 'street' | 'camera' | 'bus' | 'task' | 'event'; name: string;
  coordinates: RoadPoint[] | RoadPoint | null;
};
type Props = {
  cameras: Camera[];
  buses: Bus[];
  dispatch: DispatchGroup[];
  search: (query: string, signal: AbortSignal) => Promise<SearchHit[]>;
  onRoad: (road: DispatchRoad) => void;
  onCamera: (id: string) => void;
  onBus: (id: string, coordinates: RoadPoint) => void;
  onDispatch: (row: DispatchGroup) => void;
};

export function MapSearch({ cameras, buses, dispatch, search, onRoad, onCamera, onBus, onDispatch }: Props) {
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
  const cameraResults: Result[] = [];
  const busResults: Result[] = [];
  const dispatchResults: Result[] = [];
  for (const hit of current?.hits ?? []) {
    if (hit.type === 'street' && Array.isArray(hit.coordinates?.[0])) {
      const coordinates = hit.coordinates as RoadPoint[];
      streets.push({ id: hit.id, title: hit.name, open: () => onRoad({ id: hit.id, name: hit.name, coordinates }) });
    } else if (hit.type === 'camera') {
      const camera = cameras.find(item => item.id === hit.id);
      if (camera) cameraResults.push({ id: hit.id, title: hit.name, label: camera.district ?? camera.area,
        open: () => onCamera(hit.id) });
    } else if (hit.type === 'bus') {
      const bus = buses.find(item => item.id === hit.id);
      if (bus) busResults.push({ id: hit.id, title: hit.name, open: () => onBus(hit.id, [bus.lon, bus.lat]) });
    } else if (hit.type === 'task' || hit.type === 'event') {
      const row = dispatch.find(item => item.location_key + '/' + item.kind === hit.id);
      if (row) dispatchResults.push({ id: hit.id, title: hit.name,
        label: row.task ? 'Задание · ' + row.district : row.district, open: () => onDispatch(row) });
    }
  }
  const groups: [string, Result[]][] = [
    ['Улицы и участки', streets], ['Камеры', cameraResults], ['Автобусы', busResults],
    ['Обращения и задания', dispatchResults],
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
        <input autoFocus autoComplete="off" type="search" maxLength={120} aria-label="Улица, камера, автобус или задание"
          placeholder="Улица, камера, автобус, задание" value={query} onChange={event => setQuery(event.target.value)} />
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
