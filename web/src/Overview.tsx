import { trafficColors, useTraffic } from './traffic';
import { MapSearch, type SearchHit } from './MapSearch';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Report } from './CitizenReports';
import { Icon, type IconName } from './Icon';
import { busDuration, busReasons } from './BusChecks';
import { OverviewMap, type MapMarker } from './OverviewMap';
import { cleanupPhases, demoScenarios, scenarioClock, type CleanupPhase, type DemoScenario } from './overviewDemo';
import { actionRequired, overviewWidgets, taskStages, type Widget } from './overviewWidgets';
import type { Dashboard, DispatchGroup, RoadPoint, SnowSegment } from './types';

const emptyRoads: SnowSegment[] = [];
const issueNames = { snowfall: 'Снегопад', coverage: 'Снег на дороге', drift: 'Снежный нанос', other: 'Дорожная проблема' };
const shortTime = (timestamp: string) => new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka',
  hour: '2-digit', minute: '2-digit' }).format(new Date(timestamp));
const fullTime = (value: string) => new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka', day: 'numeric', month: 'short',
  hour: '2-digit', minute: '2-digit' }).format(new Date(value));

type Props = {
  data: Dashboard; error: string; updatedAt: string; scenario: DemoScenario | null;
  onScenario: (scenario: DemoScenario | null) => void; phase: CleanupPhase; onPhase: (phase: CleanupPhase) => void; frameUrl: (id: string) => string;
  onDispatch: (row: DispatchGroup) => void; onSignal: (id: string, trigger: HTMLButtonElement) => void;
  recentCitizens?: ReactNode;
  citizenReports?: Report[];
  onCitizenReport: (id: string) => void;
  onCamera: (id: string) => void; onRefresh: () => void;
  loadRoads: (at: string, signal: AbortSignal) => Promise<SnowSegment[]>;
  search: (query: string, signal: AbortSignal) => Promise<SearchHit[]>;
};

function useRoads(data: Dashboard, load: Props['loadRoads']) {
  const at = data.snow_forecast?.calculated_at;
  const city = data.snow_forecast?.scope === 'city';
  const [snapshot, setSnapshot] = useState<{ at: string; roads: SnowSegment[] }>();
  useEffect(() => {
    if (!at || !city) return;
    const controller = new AbortController();
    let retry: number;
    const update = () => load(at, controller.signal).then(roads => {
      if (!controller.signal.aborted) setSnapshot({ at, roads });
    }).catch(() => {
      if (!controller.signal.aborted) retry = window.setTimeout(update, 15000);
    });
    void update();
    return () => { controller.abort(); clearTimeout(retry); };
  }, [at, city, load]);
  if (city) return snapshot?.at === at ? snapshot?.roads ?? emptyRoads : emptyRoads;
  return data.snow_forecast?.segments ?? emptyRoads;
}

function cardDesign(widget: Widget): { label: string; icon: IconName; mood: string } {
  if (widget.type === 'forecast') return { label: 'Погодное предупреждение', icon: 'forecast', mood: 'blue' };
  if (widget.type === 'model') return { label: 'Расчётный нанос', icon: 'map', mood: 'blue' };
  if (widget.type === 'camera') return { label: 'Снег по камере', icon: 'camera', mood: 'blue' };
  if (widget.type === 'bus') return { label: 'Задержка автобусов', icon: 'bus', mood: 'amber' };
  const { row } = widget;
  if (widget.priority === 100) return { label: 'Срочно проверить', icon: 'bell', mood: 'red' };
  if (row.task?.stage === 'done') return { label: 'Ожидает приёмки', icon: 'check', mood: 'green' };
  if (row.task?.overdue) return { label: 'Срок истёк', icon: 'clock', mood: 'amber' };
  if (row.reports.some(report => report.active && !report.linked)) {
    return { label: 'Обращение жителя', icon: 'reports', mood: 'amber' };
  }
  if (row.task) return { label: taskStages[row.task.stage], icon: 'dispatch', mood: 'green' };
  return { label: issueNames[row.kind], icon: 'camera', mood: 'blue' };
}
const markerColors: Record<string, string> = { red: '#ff9987', amber: '#ffd294', green: '#8fe5bf', blue: '#9edaef' };

function WidgetCard({ widget, selected, onSelect, onDispatch, onSignal, onCamera, frameUrl, scenario }: {
  widget: Widget; selected: boolean; onSelect: () => void;
} & Pick<Props, 'onDispatch' | 'onSignal' | 'onCamera' | 'frameUrl' | 'scenario'>) {
  const { label, icon, mood } = cardDesign(widget);
  const row = widget.type === 'dispatch' ? widget.row : undefined;
  const task = row?.task;
  const signal = row?.signals.find(item => item.active && item.frame_id) ?? row?.signals.find(item => item.active);
  const report = row?.reports.find(item => item.active && !item.linked) ?? row?.reports.find(item => item.active);
  const image = signal?.frame_id ?? (widget.type === 'camera' ? widget.camera.analyzed_frame_id : null);
  const body = row ? task ? task.result || (task.assignee ? task.assignee + (task.due_at ? ' · до ' + shortTime(task.due_at) : '') : 'Исполнитель ещё не назначен')
    : signal?.reason || report?.description || issueNames[row.kind]
    : widget.type === 'model' ? 'Модель указывает, где снег может накопиться сильнее.'
    : widget.type === 'forecast' ? fullTime(widget.warning.payload.start) + ' — ' + shortTime(widget.warning.payload.end)
    : widget.type === 'camera' ? widget.camera.scene?.evidence
    : widget.type === 'bus' ? busDuration(widget.observation.usual_seconds) + ' → ' +
      busDuration(widget.observation.observed_seconds) + ' мин:сек' : '';
  const amount = widget.type === 'model' ? widget.segment.excess_cm : widget.type === 'dispatch' ? widget.segment?.excess_cm : null;
  const shownImage = !!image && (!task || selected);
  const [imageError, setImageError] = useState<string>();

  return <article className={'overview-card mood-' + mood + (selected ? ' card-selected' : '')} data-widget={widget.type}>
    <button className="card-main" type="button" onClick={onSelect} aria-expanded={selected}>
      <span className="card-kicker"><span className="card-kind"><Icon name={icon} width="16" height="16" />{label}</span>
        {widget.at && <time dateTime={widget.at}>{shortTime(widget.at)}</time>}</span>
      <span className="card-title">{widget.title}<Icon name="chevron" width="15" height="15" /></span>
      {body && <span className="card-body">{body}</span>}
    </button>
    {shownImage && imageError !== image && <figure className="widget-frame">
      <img src={frameUrl(image)} alt={scenario ? 'Контрольный кадр сильного снегопада' : 'Последний кадр, связанный с сигналом'}
        loading="lazy" onError={() => setImageError(image)} />
      <figcaption><span className="small-dot" />{scenario ? 'Контрольный кадр' : 'Кадр наблюдения'}</figcaption>
    </figure>}
    {task && <div className="task-live" aria-label={'Статус задания: ' + taskStages[task.stage]}>
      {['sent', 'accepted', 'in_progress', 'done'].map((stage, index) => <span key={stage}
        className={index <= ['sent', 'accepted', 'in_progress', 'done'].indexOf(task.stage) ? 'reached' : ''} />)}
    </div>}
    {(amount != null || row && Number(Boolean(signal)) + Number(Boolean(report)) > 1) &&
      <div className="card-sources">
        {row && signal && <span>Камера</span>}
        {row && report && <span>{row.reports.filter(item => item.active).length} обр.</span>}
        {amount != null && <span className="model-value">Модель +{amount.toLocaleString('ru', { maximumFractionDigits: 1 })} см</span>}
      </div>}
    {selected && <div className="card-details">
      {row && report && (signal || task) && <p><strong>Обращение {report.reference && '№ ' + report.reference}</strong>{report.description}</p>}
      {task?.result && task.assignee && <p>{task.assignee}{task.due_at && ' · Срок ' + fullTime(task.due_at)}</p>}
      {widget.type === 'model' && <p>Избыток свежего снега относительно фонового снегопада. Участок рекомендуется проверить.</p>}
      {widget.type === 'bus' && <p><strong>{widget.observation.direction_label} · {widget.source === 'rnis' ? 'РНИС' : 'Имитация'}</strong>
        {widget.observation.reason && busReasons[widget.observation.reason]}</p>}
      <div className="card-actions">
        {row && <button type="button" onClick={() => onDispatch(row)}>{task ? 'Открыть задание' : 'Открыть в очереди'}</button>}
        {signal && !scenario && <button type="button" onClick={e => onSignal(signal.id, e.currentTarget)}>К сигналу</button>}
        {widget.type === 'camera' && <button type="button" onClick={() => onCamera(widget.camera.id)}>Открыть камеру</button>}
        {widget.type === 'forecast' && <a href="#forecast">Открыть прогноз</a>}
        {widget.type === 'bus' && <a href="#dispatch">Открыть в диспетчерской</a>}
      </div>
    </div>}
  </article>;
}

export function Overview({ data, error, updatedAt, scenario, onScenario, phase, onPhase, frameUrl, onDispatch, onSignal, onCamera, onRefresh, loadRoads, search, recentCitizens, citizenReports, onCitizenReport }: Props) {
  const roads = useRoads(data, loadRoads);
  const cleanup = scenario === 'response';
  const clock = scenarioClock(scenario, phase);
  const snow = data.snow_forecast;
  const validSnow = snow?.status === 'ready' || snow?.status === 'partial';
  const [busClock, setBusClock] = useState(Date.now);
  const widgets = useMemo(() => overviewWidgets(data, roads, scenario ? Date.parse(clock) : Math.max(Date.parse(updatedAt) || 0, busClock)),
    [data, roads, scenario, updatedAt, clock, busClock]);
  const markers = useMemo<MapMarker[]>(() => widgets.flatMap(widget => {
    if (widget.type === 'bus' && widget.source === 'simulation') return [];
    const coordinates = widget.coordinates;
    if (!coordinates?.some(([lon, lat]) => lon >= 158.48 && lon <= 158.80 && lat >= 52.91 && lat <= 53.16)) return [];
    return [{ id: widget.id, label: widget.title, coordinates, priority: widget.priority,
      color: markerColors[cardDesign(widget).mood] }];
  }), [widgets]);
  const [state, setState] = useState<'offline' | 'loading' | 'ready'>('offline');
  const [model, setModel] = useState<'loading' | 'ready' | 'failed'>();
  const [modelProgress, setModelProgress] = useState<string>();
  const [selected, setSelected] = useState<string>();
  const [selection, setSelection] = useState<{ id: string; coordinates: RoadPoint[]; revision: number } | null>(null);
  const [onlyActions, setOnlyActions] = useState(false);
  const [showSnow, setShowSnow] = useState(true);
  const [showTraffic, setShowTraffic] = useState(false);
  const traffic = useTraffic(showTraffic);
  const trafficSegments = showTraffic ? traffic.publication?.segments : undefined;
  const [showMarkers, setShowMarkers] = useState(true);
  const [showBuses, setShowBuses] = useState(true);
  const [selectedBusId, setSelectedBusId] = useState<string>();
  const busMap = data.bus_map;
  useEffect(() => {
    if (busMap?.source !== 'rnis' && data.bus_observations?.source !== 'rnis') return;
    const timer = window.setInterval(() => setBusClock(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, [busMap?.source, data.bus_observations?.source]);
  const positions = useMemo(() => busMap?.positions.filter(bus =>
    busMap.source === 'simulation' || Date.parse(bus.measured_at) > busClock - 90_000), [busMap, busClock]);
  const busMarkers = useMemo<MapMarker[]>(() => (positions ?? []).flatMap(bus => {
    const route = bus.route?.trim();
    if (!route || !/\d/.test(route)) return [];
    return [{ id: 'bus:' + bus.id, label: 'Автобус · маршрут ' + route, kind: 'bus', badge: route,
      coordinates: [[bus.lon, bus.lat]], color: '#74edc5', priority: -1 }];
  }), [positions]);
  const citizenMarkers = useMemo<MapMarker[]>(() => (citizenReports ?? []).map(report => ({
    id: 'citizen:' + report.id, label: 'Обращение жителя · ' + fullTime(report.created_at),
    coordinates: [[report.longitude, report.latitude]], color: '#e5a4ff', priority: 20,
  })), [citizenReports]);
  const displayedMarkers = useMemo(() => [
    ...(showMarkers ? [...markers, ...citizenMarkers] : []), ...(showBuses ? busMarkers : []),
  ], [markers, citizenMarkers, busMarkers, showMarkers, showBuses]);
  const demoCoordinates = !scenario && busMap?.source === 'simulation'
    ? JSON.stringify(busMap.positions.map(bus => [bus.lon, bus.lat])) : '';
  const demoBusFocus = useMemo(() => demoCoordinates ? {
    id: 'demo-buses', revision: 0, fitOnly: true,
    coordinates: JSON.parse(demoCoordinates) as RoadPoint[],
  } : null, [demoCoordinates]);
  const selectedBus = showBuses && positions?.find(bus => bus.id === selectedBusId);
  const busStatus = busMarkers.length ? busMarkers.length.toString() :
    busMap?.state === 'waiting' ? 'ждём GPS' :
      busMap?.state === 'unavailable' ? 'РНИС недоступна' :
        positions?.length ? 'нет номеров маршрутов' : 'нет свежих GPS';
  const [hidden, setHidden] = useState(false);
  const revision = useRef(0);
  const cards = useRef(new Map<string, HTMLElement>());
  const required = widgets.filter(actionRequired).length;
  const visible = onlyActions ? widgets.filter(actionRequired) : widgets;
  useEffect(() => {
    if (selected && !widgets.some(widget => widget.id === selected)) {
      setSelected(undefined); setSelection(null);
    }
  }, [selected, widgets]);


  function select(widget: Widget, fromMap = false) {
    if (selected === widget.id && !fromMap) {
      setSelected(undefined); setSelection(null);
      return;
    }
    setSelected(widget.id);
    const marker = markers.find(item => item.id === widget.id);
    const coordinates = marker?.coordinates ?? (widget.type === 'bus' ? widget.coordinates : undefined);
    setSelection(coordinates ? { id: widget.id, coordinates, revision: ++revision.current } : null);
    if (fromMap) {
      setHidden(false); setOnlyActions(false);
      requestAnimationFrame(() => {
        const target = cards.current.get(widget.id);
        target?.scrollIntoView({ block: 'nearest' });
        target?.querySelector('button')?.focus({ preventScroll: true });
      });
    }
  }

  const cleanupStatus = model === 'ready' ? 'Расчёт циклона готов'
    : model === 'failed' ? 'Архивный расчёт недоступен'
    : data.city_map_url && state === 'offline' ? 'Подключаем архивную карту'
    : !data.city_map_url && (error || updatedAt) ? 'Архивная карта недоступна'
    : !data.city_map_url ? 'Проверяем подключение Neverrie' : 'Рассчитываем исторический снег' + (modelProgress ? ' · ' + modelProgress : '');
  const modelStatus = cleanup ? cleanupStatus : !snow ? 'Расчёт не подключён' : snow.status === 'running' ? 'Рассчитываем снег' : snow.status === 'pending' ? 'Ожидается первый расчёт'
    : snow.status === 'stale' ? 'Модель ждёт обновления' : snow.status === 'unavailable' || snow.status === 'configuration_error'
    ? 'Расчёт недоступен' : state === 'ready' && snow.remote_run_id && model === 'loading' ? 'Загружается поле снега'
    : state === 'ready' && snow.remote_run_id && model === 'failed' ? 'Доступна оценка по дорогам'
    : snow.status === 'partial' ? 'Часть участков рассчитана' : 'Расчёт готов';

  return <section className="ops-overview" aria-label="Дорожная обстановка">
    <OverviewMap cityUrl={scenario && !cleanup ? null : data.city_map_url} cleanupAt={cleanup ? clock : undefined} runId={validSnow ? snow?.remote_run_id : undefined}
      roads={validSnow ? roads : emptyRoads} markers={displayedMarkers} selection={selection ?? demoBusFocus}
      traffic={trafficSegments} trafficStale={traffic.stale || traffic.unavailable}
      showSnow={showSnow} showMarkers={displayedMarkers.length > 0} onClientState={setState} onModelState={(status, progress) => { setModel(status); setModelProgress(progress); }}
      onSelect={id => {
        const citizen = citizenMarkers.find(marker => marker.id === id);
        if (citizen) {
          onCitizenReport(id.slice('citizen:'.length));
          return;
        }
        if (id.startsWith('bus:')) {
          const busId = id.slice(4);
          setSelectedBusId(current => current === busId ? undefined : busId);
          return;
        }
        const widget = widgets.find(item => item.id === id);
        if (widget) select(widget, true);
      }} />
    <div className="ops-map-shade" />
    <header className="ops-heading">
      <div className="ops-location"><span className="small-dot" />Петропавловск-Камчатский</div>
      <h1>Дорожная<br className="ops-break" /> обстановка</h1>
      <p className="ops-status" role="status">
        <Icon name="map" width="15" height="15" />{state === 'ready' ? 'Карта Neverrie' : state === 'loading' ? 'Подключаем карту Neverrie' : 'Обзорная карта'}
        <span aria-hidden="true">·</span>{modelStatus}
      </p>
      {error && <p className="ops-error" role="alert">{error} <button onClick={onRefresh}>Повторить</button></p>}
    </header>
    <div className="ops-topbar" aria-label="Инструменты карты">
      <div className="ops-demo">
        <label htmlFor="ops-demo-select"><Icon name="overview" width="15" height="15" /> Демо</label>
        <select id="ops-demo-select" aria-label="Демосценарий" value={scenario ?? ''} onChange={e => onScenario((e.target.value || null) as DemoScenario | null)}>
          <option value="">Данные сервиса</option>
          {Object.entries(demoScenarios).map(([code, title]) => <option key={code} value={code}>{title}</option>)}
        </select>
      </div>
      <MapSearch cameras={data.cameras} buses={positions ?? []} dispatch={data.dispatch_queue} search={search}
        onCamera={onCamera} onDispatch={onDispatch} onRoad={road => {
          setSelected(undefined);
          setSelection({ id: road.id, coordinates: road.coordinates, revision: ++revision.current });
        }} onBus={(id, point) => {
          setSelected(undefined); setShowBuses(true); setSelectedBusId(id);
          setSelection({ id: 'bus:' + id, coordinates: [point], revision: ++revision.current });
        }} />
      <details className="ops-profile" onBlur={event => {
        if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
      }} onKeyDown={event => {
        if (event.key === 'Escape' && event.currentTarget.open) {
          event.currentTarget.open = false;
          event.currentTarget.querySelector('summary')?.focus();
        }
      }}>
        <summary className="ops-topbar-button ops-profile-avatar" aria-label="Профиль" title="Профиль">
          <Icon name="profile" />
        </summary>
        <div className="ops-profile-panel ops-toolbar-panel">
          <strong>Диспетчер</strong>
          <span>Демонстрационный профиль</span>
        </div>
      </details>
    </div>
    <aside className={'ops-events' + (hidden ? ' ops-events-collapsed' : '')} aria-label="Виджеты обстановки">
      <div className="ops-events-head">
        <div><h2>Внимание</h2><span>{required ? 'Нужно решение · ' + required : 'Нет срочных действий'}</span></div>
        <button type="button" className="ops-icon" onClick={() => setHidden(!hidden)} aria-expanded={!hidden}
          aria-label={hidden ? 'Развернуть виджеты' : 'Свернуть виджеты'}><Icon name={hidden ? 'expand' : 'close'} width="18" height="18" /></button>
      </div>
      {!hidden && <>
        {cleanup && <div className="cleanup-demo-stage">
          <div><strong>15 января 2026 · снежный циклон</strong><small>Open-Meteo · ECMWF IFS</small></div>
          <div className="cleanup-phases" role="group" aria-label="Этап демонстрации уборки">
            {Object.entries(cleanupPhases).map(([id, step]) => <button type="button" key={id}
              aria-pressed={phase === id} onClick={() => onPhase(id as CleanupPhase)}>
              {step.title}<small>{step.hour}</small>
            </button>)}
          </div>
          <p>{cleanupPhases[phase].description}</p>
          <small>Слой показывает расчёт накопления снега. Задания и кадр камеры демонстрационные.</small>
        </div>}
        <div className="ops-filters" aria-label="Фильтр виджетов">
          <button type="button" aria-pressed={!onlyActions} onClick={() => setOnlyActions(false)}>Все <span>{widgets.length}</span></button>
          <button type="button" aria-pressed={onlyActions} onClick={() => setOnlyActions(true)}>К решению <span>{required}</span></button>
        </div>
        <div className="ops-cards">
          {recentCitizens}
          {visible.map(widget => <div key={widget.id} ref={node => { if (node) cards.current.set(widget.id, node); else cards.current.delete(widget.id); }}>
            <WidgetCard widget={widget} selected={selected === widget.id} onSelect={() => select(widget)}
              {...{ onDispatch, onSignal, onCamera, frameUrl, scenario }} />
          </div>)}
          {!visible.length && <div className="ops-empty"><Icon name="check" /><div><strong>{onlyActions ? 'Всё под контролем' : 'Спокойная обстановка'}</strong>
            <p>{onlyActions ? 'Здесь появятся события, по которым нужно принять решение.' : 'Активных сигналов, обращений и заданий нет.'}</p></div></div>}
        </div>
      </>}
    </aside>
    <div className="ops-map-tools" aria-label="Отображение на карте">
      {selectedBus && <div className="ops-bus-detail" role="status">
        <div><strong>{selectedBus.route ? 'Маршрут ' + selectedBus.route : 'Автобус'}</strong>
          <span>{busMap?.source === 'simulation' ? 'Имитация' : 'РНИС'} · {fullTime(selectedBus.measured_at)}</span></div>
        <button type="button" aria-label="Закрыть сведения об автобусе" onClick={() => setSelectedBusId(undefined)}>
          <Icon name="close" width="16" height="16" /></button>
      </div>}
      {showTraffic && <div className="ops-traffic-status" role="status">
        {traffic.publication ? <>
          <span>{traffic.unavailable ? 'Связь с источником потеряна · ' :
            traffic.stale ? 'Данные устарели · ' : ''}{traffic.publication.status === 'partial' ? 'Частичное покрытие · ' : ''}
            {fullTime(traffic.publication.published_at)} · <a href="https://yandex.ru/maps/78/petropavlovsk/probki/"
              target="_blank" rel="noreferrer">Яндекс Карты</a></span>
          {trafficSegments?.length ? <span className="ops-traffic-legend">
            {([['free', 'Свободно'], ['slow', 'Замедление'], ['congested', 'Затор']] as const).map(([key, label]) =>
              <span key={key}><i style={{ background: trafficColors[key] }} />{label}</span>)}
          </span> : <span>Нет наблюдений на дорогах</span>}
        </> : <span>{traffic.unavailable ? 'Пробки недоступны' : traffic.publication === null ? 'Наблюдений пока нет' : 'Загружаем пробки…'}</span>}
      </div>}
      <button type="button" aria-pressed={showTraffic} onClick={() => setShowTraffic(value => !value)}>
        <Icon name="traffic" width="16" height="16" />Пробки
      </button>
      <button type="button" aria-pressed={showSnow} onClick={() => setShowSnow(!showSnow)}><Icon name="forecast" width="16" height="16" />Снег</button>
      <button type="button" aria-pressed={showMarkers} onClick={() => setShowMarkers(!showMarkers)}><Icon name="bell" width="16" height="16" />События</button>
      {busMap ? <button className="ops-bus-toggle" type="button" aria-pressed={showBuses} onClick={() => setShowBuses(!showBuses)}>
        <Icon name="bus" width="16" height="16" />Автобусы · {busStatus}{busMap.source === 'simulation' && ' · имитация'}
      </button> : data.mode !== 'live' && <a href="/demo" title="Открыть автобусы на демонстрационной карте"><Icon name="bus" width="16" height="16" />Демо автобусов</a>}
      {data.city_map_url && !scenario && <a href={data.city_map_url} target="_blank" rel="noopener noreferrer">
        <Icon name="expand" width="15" height="15" />Сценарии модели</a>}
    </div>
    <footer className="ops-foot">
      <span>{scenario || data.mode === 'demo' ? 'Демо' : data.mode === 'replay' ? 'Архив' : 'Камчатка · UTC+12'}{scenario ? ' · ' + fullTime(clock) : ''}</span>
      {snow?.calculated_at && <span>Расчёт · {fullTime(snow.calculated_at)}</span>}
    </footer>
  </section>;
}
