import { useEffect, useId, useRef, useState } from 'react';
import { Icon } from './Icon';
import type { Camera, DispatchGroup, DispatchRoad, RoadPoint } from './types';

type Bus = { id: string; route: string | null; lon: number; lat: number };
type Result = { id: string; title: string; label?: string; open: () => void };
type Props = {
  cameras: Camera[];
  buses: Bus[];
  dispatch: DispatchGroup[];
  loadRoads: (signal: AbortSignal) => Promise<DispatchRoad[]>;
  onRoad: (road: DispatchRoad) => void;
  onCamera: (id: string) => void;
  onBus: (id: string, coordinates: RoadPoint) => void;
  onDispatch: (row: DispatchGroup) => void;
};

const normalize = (text: string) => text.toLocaleLowerCase('ru').replaceAll('ё', 'е')
  .replace(/[^\p{L}\p{N}]+/gu, ' ').trim();

export function MapSearch({ cameras, buses, dispatch, loadRoads, onRoad, onCamera, onBus, onDispatch }: Props) {
  const [query, setQuery] = useState('');
  const [roads, setRoads] = useState<DispatchRoad[]>();
  const [error, setError] = useState(false);
  const [active, setActive] = useState(false);
  const panelId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (!active || roads) return;
    const controller = new AbortController();
    setError(false);
    loadRoads(controller.signal).then(setRoads).catch(() => {
      if (!controller.signal.aborted) setError(true);
    });
    return () => controller.abort();
  }, [active, roads, loadRoads]);

  const term = normalize(query);
  const tokens = term.split(' ');
  const matches = (label: string) => {
    const normalized = normalize(label);
    return tokens.every(token => normalized.includes(token));
  };
  const streets = new Map<string, DispatchRoad>();
  if (term) {
    for (const road of roads ?? []) {
      const name = normalize(road.name);
      if (!streets.has(name) && matches(name)) streets.set(name, road);
      if (streets.size === 5) break;
    }
  }
  const groups: [string, Result[]][] = term ? [
    ['Улицы и участки', [...streets.values()].map(road => ({ id: road.id, title: road.name, open: () => onRoad(road) }))],
    ['Камеры', cameras.filter(camera => matches('Камера ' + [camera.name, camera.area, camera.district, camera.section_name].join(' '))).slice(0, 5)
      .map(camera => ({ id: camera.id, title: camera.name, label: camera.district ?? camera.area, open: () => onCamera(camera.id) }))],
    ['Автобусы', buses.filter(bus => bus.route && matches('Автобус Маршрут ' + bus.route)).slice(0, 5)
      .map(bus => ({ id: bus.id, title: 'Маршрут ' + bus.route, open: () => onBus(bus.id, [bus.lon, bus.lat]) }))],
    ['Обращения и задания', dispatch.filter(row => matches([
      row.task ? 'Задание' : 'Обращение', row.address, row.district, ...row.reports.flatMap(r => [r.description, r.reference]),
    ].join(' '))).slice(0, 5)
      .map(row => ({ id: row.location_key + '/' + row.kind, title: row.address,
        label: row.task ? 'Задание · ' + row.district : row.district, open: () => onDispatch(row) }))],
  ] : [];

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
        <input autoFocus autoComplete="off" type="search" aria-label="Улица, камера, автобус или задание"
          placeholder="Улица, камера, автобус, задание" value={query} onChange={event => setQuery(event.target.value)} />
        <button className="ops-search-close" type="button" aria-label="Закрыть поиск" onClick={close}>
          <Icon name="close" width="17" height="17" />
        </button>
      </div>
      {term && <div className="ops-search-results" aria-label="Результаты поиска">
        {groups.map(([name, items]) => items.length > 0 && <section key={name} aria-label={name}>
          <strong>{name}</strong>
          {items.map(item => <button type="button" key={item.id} onClick={() => { close(); item.open(); }}>
            <span>{item.title}{item.label && <small>{item.label}</small>}</span>
            <Icon name="chevron" width="15" height="15" />
          </button>)}
        </section>)}
        {error ? <p role="status">Справочник улиц недоступен</p> : !roads && <p role="status">Загружаем улицы…</p>}
        {roads && !groups.some(([, items]) => items.length) && <p role="status">Ничего не найдено</p>}
      </div>}
    </dialog>
  </div>;
}
