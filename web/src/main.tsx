import { useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { CameraMap } from './Map';
import { Overview } from './Overview';
import { CitizenReports, RecentCitizenReports, useRecentCitizenReports } from './CitizenReports';
import { cameraRoadAssessment, deliveryBucket, upcomingForecast } from './sectionViews';
import { demoDashboard, demoFrameUrl, demoScenarios, parseCleanupPhase, parseScenario, scenarioClock, type CleanupPhase, type DemoScenario } from './overviewDemo';
import { cameraShowsSnow, currentCameraScene, dispatchKey } from './overviewWidgets';
import { DispatchQueue } from './DispatchQueue';
import { CrewAssignment, CrewInbox } from './CrewAssignment';
import { Icon, type IconName } from './Icon';
import type { ActionItem, ArchivedTask, AssignmentDraft, Camera, Dashboard, DispatchGroup, DispatchRoad, EventItem, FeedbackVerdict, HistoryItem, ObservedFrame, ProgressDraft, ReportDraft, Scene, SignalReview, SnowRaster, SnowSegment, TaskDraft } from './types';
import './styles.css';
import './overview.css';
import './sections.css';

const stateLabels: Record<string, string> = {
  new: 'Ожидает проверки', acknowledged: 'Принято', deferred: 'Отложено',
  en_route: 'Техника в пути', resolved: 'Закрыто',
};
const cameraLabels: Record<string, string> = {
  waiting: 'Ожидает кадр', available: 'Доступна', unchanged: 'Кадр без изменений',
  stale: 'Кадр устарел', error: 'Источник недоступен',
};
const deliveryLabels: Record<string, string> = {
  logged: 'В журнале', queued: 'В очереди', sending: 'Отправляется',
  sent: 'Принято почтовым сервером', ambiguous: 'Приём письма неизвестен', failed: 'Ошибка доставки',
};
const checkLabels: Record<string, string> = {
  ready: 'Работает', three_hourly: 'Прогноз на 5 суток · шаг 3 часа', mail_test: 'Тестовый приёмник Mailpit', unconfigured: 'Требует настройки', unavailable: 'Недоступен',
  invalid: 'Ответ вне схемы', fixtures: 'Контрольные данные',
  snow_only: 'Доступен признак падающего снега', v2_partial: 'Падающий снег проверен резервным анализом', local_log: 'Сообщения в локальном журнале',
};
const kindLabels: Record<string, string> = { snowfall: 'Снегопад', coverage: 'Снег на дороге', drift: 'Снежный вал' };
const transitions: Record<string, string[]> = {
  new: ['acknowledged', 'deferred', 'en_route', 'resolved'],
  acknowledged: ['deferred', 'en_route', 'resolved'],
  deferred: ['acknowledged', 'en_route', 'resolved'], en_route: ['deferred', 'resolved'], resolved: [],
};
const sections = [
  { id: 'overview', title: 'Обстановка', icon: 'overview' },
  { id: 'dispatch', title: 'Диспетчерская', icon: 'dispatch' },
  { id: 'citizens', title: 'Обращения жителей', icon: 'bell' },
  { id: 'cameras', title: 'Камеры', icon: 'camera' },
  { id: 'forecast', title: 'Прогноз', icon: 'forecast' },
  { id: 'reports', title: 'Сводки и доставка', icon: 'reports' },
] as const satisfies ReadonlyArray<{ id: string; title: string; icon: IconName }>;
type Tab = typeof sections[number]['id'];
const currentTab = (): Tab => sections.find(section => '#' + section.id === location.hash)?.id ?? 'overview';
const params = new URLSearchParams(location.search);
const run = location.pathname === '/demo' ? 'demo' : params.get('run');
const suffix = run ? '?' + new URLSearchParams({ run }) : '';
const time = (value: string | null, seconds = false) => value ? new Intl.DateTimeFormat('ru', {
  dateStyle: 'short', timeStyle: seconds ? 'medium' : 'short', timeZone: 'Asia/Kamchatka',
}).format(new Date(value)) : '—';
const frameUrl = (frameId: string) => frameId === 'demo-snowfall' ? demoFrameUrl() : '/api/v1/frames/' + frameId + suffix;

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const query = suffix && path.includes('?') ? '&' + suffix.slice(1) : suffix;
  const response = await fetch('/api/v1/' + path + query, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : 'Запрос завершился с ошибкой (' + response.status + ')');
  }
  return response.json() as Promise<T>;
}

function loadSnowRaster(id: string, calculatedAt: string, signal: AbortSignal) {
  const query = new URLSearchParams({ calculated_at: calculatedAt });
  return api<SnowRaster[]>('snow-raster/' + encodeURIComponent(id) + '?' + query, { signal });
}

const noDemoRaster = async () => [];

function loadSnowRoads(calculatedAt: string, signal: AbortSignal) {
  return api<SnowSegment[]>('snow-roads?' + new URLSearchParams({ calculated_at: calculatedAt }), { signal });
}

function loadDispatchRoads(signal: AbortSignal) {
  return api<{segments: DispatchRoad[]}>('dispatch/roads', { signal }).then(data => data.segments);
}

function SceneFacts({ scene, road, missing }: { scene: Scene | null; road: boolean; missing: string }) {
  const values: Record<string, string> = {
    yes: 'Да', no: 'Нет', uncertain: 'Не определено', none: 'Нет', light: 'Слабая',
    moderate: 'Умеренная', heavy: 'Сильная', good: 'Хорошая', reduced: 'Снижена',
    poor: 'Плохая', clear: 'Чисто', partial: 'Частично', mostly_covered: 'Большая часть',
    not_visible: 'Дорога вне кадра', limited: 'Обзор ограничен', unusable: 'Обзор недоступен', major: 'Сильное',
  };
  const valid = scene?.format_valid && scene.view_quality !== 'unusable' ? scene : null;
  const detail = valid?.details;
  const value = (field?: string) => field ? values[field] : 'Нет оценки';
  const visibility = valid?.visibility === 'uncertain' && detail
    ? detail.air_view === 'good' ? 'Хороший обзор' : value(detail.air_view)
    : value(valid?.visibility);
  const conclusion = !scene ? missing
    : !scene.format_valid ? 'Анализ кадра не прошёл проверку.'
    : !valid ? 'По этому кадру нельзя оценить обстановку.'
    : /\p{Script=Latin}/u.test(valid.evidence)
      ? { yes: 'В кадре виден снегопад.', no: 'Падающего снега не видно.',
          uncertain: 'По кадру не удалось определить, идёт ли снег.' }[valid.falling_snow]
      : valid.evidence;
  return <><dl className="facts">
    <div><dt>Идёт снег</dt><dd>{value(valid?.falling_snow)}</dd></div>
    <div><dt>Интенсивность</dt><dd>{value(valid?.intensity)}</dd></div>
    <div><dt>Видимость по кадру</dt><dd>{visibility}</dd></div>
    <div><dt>Снег на дороге</dt><dd>{road ? value(valid?.road_coverage) : 'Вне дорожного мониторинга'}</dd></div>
    <div><dt>Снег лежит на земле</dt><dd>{value(valid?.snow_on_ground)}</dd></div>
    <div><dt>Крупные валы</dt><dd>{value(valid?.large_snowdrifts)}</dd></div>
  </dl>
    <p className="evidence">{conclusion}</p>
    {valid?.snow_source === 'snow' && !detail && <p className="muted">Резервный анализ оценил только падающий снег.</p>}
    {detail && <details>
      <summary>Дополнительные признаки</summary>
      <dl className="facts">
        <div><dt>Позёмка</dt><dd>{value(detail.blowing_snow)}</dd></div>
        {road && <>
          <div><dt>Вал на проезжей части</dt><dd>{value(detail.snowdrift_on_road)}</dd></div>
          <div><dt>Снежная каша</dt><dd>{value(detail.slush_on_road)}</dd></div>
          <div><dt>Препятствие на дороге</dt><dd>{value(detail.road_obstruction)}</dd></div>
        </>}
      </dl>
    </details>}
  </>;
}

function CameraDetail({ camera, real, onOpenCleanup }: { camera: Camera; real: boolean; onOpenCleanup?: () => void }) {
  const scene = currentCameraScene(camera);
  const previous = camera.archived_analysis ?? (
    !scene && camera.scene && camera.analyzed_frame_id && camera.analyzed_frame_at
      ? { scene: camera.scene, frame_id: camera.analyzed_frame_id, at: camera.analyzed_frame_at } : null
  );
  const { passage, action } = cameraRoadAssessment(camera, scene);
  const analysisState = camera.analysis_stage === 'pending' ? 'Последний кадр в очереди на анализ.'
    : camera.analysis_stage === 'running' ? 'Последний кадр анализируется.'
    : camera.analysis_stage === 'skipped' ? 'Анализ последнего кадра пропущен.'
    : camera.analysis_stage === 'failed' ? 'Анализ последнего кадра завершился ошибкой.'
    : 'Для последнего кадра нет актуальной оценки.';
  const missing = camera.status === 'error' ? 'Источник камеры недоступен.'
    : camera.status === 'needs_feed' ? 'Адрес видеопотока не задан.'
    : !camera.frame_id ? 'Кадр ещё не получен.'
    : camera.status === 'stale' ? 'Последний кадр устарел.'
    : analysisState;
  return <section className="panel camera-detail" aria-label={'Оценка камеры: ' + camera.name}>
    <div className="camera-detail-media">
      {camera.frame_id ? <a className="full-frame" href={frameUrl(camera.frame_id)} target="_blank" rel="noreferrer"
        aria-label={'Открыть полный кадр: ' + camera.name}>
        <img src={frameUrl(camera.frame_id)} alt={camera.name} />
        <span><Icon name="expand" /> Полный кадр</span>
      </a> : <div className="image-empty"><Icon name="camera" /> Нет кадра</div>}
    </div>
    <div className="card-content">
      <CameraStatus camera={camera} />
      <h2>{camera.name}</h2>
      <p className="muted">{camera.road ? 'Наблюдение за дорогой' : 'Погодное наблюдение'}
        {camera.district && <><br />{camera.district}</>}
        {camera.section_name && <><br />{camera.section_name}</>}
        {camera.location_note && <><br />{camera.location_note}</>}
      </p>
      <dl className="camera-times">
        <div><dt>{real ? 'Кадр получен' : 'Время в демо'}</dt><dd>{time(camera.last_frame, true)}</dd></div>
        {real && <div><dt>Последняя проверка</dt><dd>{time(camera.last_poll, true)}</dd></div>}
        {real && camera.source_at && <div><dt>Обновление у источника</dt><dd>{time(camera.source_at, true)}</dd></div>}
      </dl>
      <dl className="facts camera-road-assessment">
        <div><dt>Состояние проезда</dt><dd>{passage}</dd></div>
        {onOpenCleanup ? <div><dt>Решение диспетчера</dt><dd>
          <button type="button" className="quiet" onClick={onOpenCleanup}>Требуется очистка · открыть задание</button>
        </dd></div> : action && <div><dt>Рекомендация</dt><dd>{action}</dd></div>}
      </dl>
      <SceneFacts scene={scene} road={camera.road} missing={missing} />
      {previous && <details className="camera-archive">
        <summary>Оценка прошлого кадра · {time(previous.at, true)}</summary>
        <a href={frameUrl(previous.frame_id)} target="_blank" rel="noreferrer">Открыть кадр от {time(previous.at, true)}</a>
        <SceneFacts scene={previous.scene} road={camera.road} missing="" />
      </details>}
    </div>
  </section>;
}

const verdictLabels: Record<FeedbackVerdict, string> = {
  confirmed: 'Подтвердилось', false_alarm: 'Ложный сигнал', already_working: 'Уже в работе',
};

function EvidenceFrame({ frame, label }: { frame: ObservedFrame; label: string }) {
  return <figure>
    <a href={frameUrl(frame.id)} target="_blank" rel="noreferrer"><img src={frameUrl(frame.id)} alt={label} /></a>
    <figcaption><strong>{label}</strong><br />{time(frame.observed_at, true)}</figcaption>
  </figure>;
}

function EventDetail({ event, role, close, refresh }: {
  event: EventItem; role: Dashboard['role']; close: () => void; refresh: () => Promise<void>;
}) {
  const [actions, setActions] = useState<ActionItem[]>([]);
  const [review, setReview] = useState<SignalReview | null>(null);
  const [reviewError, setReviewError] = useState('');
  const [reviewBusy, setReviewBusy] = useState(false);
  const eventPath = 'events/' + event.id;
  const feedback = review?.feedback;
  const afterWork = review?.after;
  const [state, setState] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus(); }, []);
  useEffect(() => {
    let active = true;
    api<ActionItem[]>('events/' + event.id + '/actions').then(rows => { if (active) setActions(rows); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [event.id, event.version]);

  useEffect(() => {
    const controller = new AbortController();
    const refreshReview = () => api<SignalReview>(eventPath + '/review', { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setReview(value); })
      .catch(e => { if (!controller.signal.aborted) setReviewError(e.message); });
    void refreshReview();
    const timer = ['en_route', 'resolved'].includes(event.state) && role === 'operator' && !reviewBusy
      ? window.setInterval(refreshReview, 12000) : undefined;
    return () => { controller.abort(); if (timer !== undefined) window.clearInterval(timer); };
  }, [eventPath, event.version, event.latest_frame, event.state, role, reviewBusy]);

  async function updateReview(path: string, body: object) {
    setReviewBusy(true);
    setReviewError('');
    try {
      await api(eventPath + path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      setReview(await api<SignalReview>(eventPath + '/review'));
      await refresh();
    } catch (e) {
      setReviewError((e as Error).message);
      try { setReview(await api<SignalReview>(eventPath + '/review')); } catch { /* Keep the original error. */ }
    } finally {
      setReviewBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('events/' + event.id + '/actions', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: crypto.randomUUID(), expected_version: event.version, state, note }),
      });
      setState('');
      setNote('');
      await refresh();
    } catch (e) {
      setError((e as Error).message);
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  return <section className="panel detail" aria-label="Карточка сигнала">
    <div className="detail-head">
      <div><EventSeverity event={event} />
      <h2 ref={heading} tabIndex={-1}>{event.name}</h2><p className="muted">{kindLabels[event.kind]} · {time(event.opened_at)}</p></div>
      <button className="quiet icon-button" onClick={close} aria-label="Закрыть карточку"><Icon name="close" /></button>
    </div>
    <div className="detail-body">
      <div className="evidence-images">
        <h3>Сравнение кадров</h3>
        <div className="frame-pair">
          {review?.before ? <EvidenceFrame frame={review.before} label="До ухудшения" />
            : <p className="frame-unavailable muted">{review ? 'Кадр до ухудшения не найден.' : 'Загружаю сравнение…'}</p>}
          <EvidenceFrame frame={review?.current ?? { id: event.latest_frame, observed_at: event.last_seen }}
            label="Последний подтверждающий кадр" />
        </div>
        {afterWork && <EvidenceFrame frame={afterWork} label="После работ · отмечено оператором" />}
        {role === 'operator' && review?.candidate && review.candidate.id !== afterWork?.id && <div className="after-work">
          <a href={frameUrl(review.candidate.id)} target="_blank" rel="noreferrer">
            Проверить кадр от {time(review.candidate.observed_at, true)}
          </a>
          <button className="quiet" disabled={reviewBusy}
            onClick={() => void updateReview('/after-work', { frame_id: review.candidate?.id })}>Закрепить как кадр после работ</button>
        </div>}
        {role === 'operator' && review && !review.candidate && !afterWork && ['en_route', 'resolved'].includes(event.state) &&
          <p className="muted">Ожидается новый кадр с этого ракурса для отметки после работ.</p>}
      </div>
      <div className="detail-controls">
        <p>{event.reason}</p>
        <p className="muted">{event.ended_at ? 'Признак завершился ' + time(event.ended_at) : (event.evidence_stale ? 'Нет свежего подтверждения · последний сигнал ' : 'Последнее подтверждение ') + time(event.last_seen)}</p>
        <h3>Решение · {stateLabels[event.state]}</h3>
        {role === 'operator' && transitions[event.state]?.length > 0 && <form onSubmit={submit} className="action-form">
          <label>Действие<select value={state} onChange={e => setState(e.target.value)} required>
            <option value="">Выбрать действие</option>
            {transitions[event.state].map(option => <option key={option} value={option}>{stateLabels[option]}</option>)}
          </select></label>
          <label>Комментарий<input value={note} maxLength={500} onChange={e => setNote(e.target.value)}
            required={state === 'deferred' || state === 'resolved'} /></label>
          <button disabled={busy || !state}>{busy ? 'Сохраняю…' : 'Сохранить'}</button>
        </form>}
        {error && <p role="alert" className="error">{error}</p>}
        <div className="feedback-panel">
          <h3>Оценка сигнала</h3>
          {role === 'operator' && <div className="feedback-buttons">
            {(Object.keys(verdictLabels) as FeedbackVerdict[]).map(verdict =>
              <button className="quiet" key={verdict} disabled={reviewBusy || !review}
                aria-pressed={feedback?.verdict === verdict}
                onClick={() => void updateReview('/feedback', { verdict })}>{verdictLabels[verdict]}</button>)}
          </div>}
          {feedback && <p className="muted" role="status">Записано: {verdictLabels[feedback.verdict]}</p>}
          {reviewError && <p className="error" role="alert">{reviewError}</p>}
        </div>
        {actions.length > 0 && <ul className="timeline">{actions.map(action => <li key={action.request_id}>
          <strong>{stateLabels[action.state]}</strong> · {time(action.created_at)} · {action.actor}
          {action.note && <p>{action.note}</p>}
        </li>)}</ul>}
      </div>
    </div>
  </section>;
}

function cameraHealthy(camera: Camera) {
  return camera.frame_id && (camera.status === 'available' || camera.status === 'unchanged');
}

function CameraStatus({ camera }: { camera: Camera }) {
  const healthy = cameraHealthy(camera);
  const degraded = camera.status === 'error' || camera.status === 'stale';
  return <span className={'status ' + (healthy ? 'ok' : degraded ? 'watch' : 'neutral')}>
    <span className="indicator" />{cameraLabels[camera.status] ?? camera.status}
  </span>;
}

function cameraName(camera: Camera) {
  if (camera.id === 'moroznaya') return 'Гора Морозная';
  if (camera.id === 'ran-avachinsky') return 'Мишенная сопка';
  return camera.name.replace(/^(Петропавловск-Камчатский|Елизово), /, '');
}

function CameraList({ cameras, selected, onSelect, real }: {
  cameras: Camera[]; selected?: string; onSelect: (id: string) => void; real: boolean;
}) {
  return cameras.length ? <ul className="camera-list">{cameras.map(camera => <li key={camera.id}>
    <button className="camera-choice" type="button" onClick={() => onSelect(camera.id)}
      title={camera.name} aria-pressed={selected === camera.id}>
      <span className="camera-thumb">{camera.frame_id ? <img src={frameUrl(camera.frame_id)} alt="" loading="lazy" />
        : <Icon name="camera" width="18" height="18" />}</span>
      <span className="camera-choice-text"><strong>{cameraName(camera)}</strong><small>{camera.area}</small>
        <span className="camera-choice-foot"><CameraStatus camera={camera} />
          {camera.last_frame && <time dateTime={camera.last_frame}>{real ? timeOfDay(camera.last_frame) : time(camera.last_frame)}</time>}
        </span>
      </span>
    </button>
  </li>)}</ul> : <Empty icon="camera">Камеры по этому фильтру не найдены</Empty>;
}

function EventSeverity({ event }: { event: EventItem }) {
  return event.ended_at || event.evidence_stale
    ? <span className="status neutral">{event.ended_at ? 'Завершён' : 'Нет свежего подтверждения'}</span>
    : <span className={'status ' + event.severity}><span className="indicator" />
        {event.severity === 'urgent' ? 'Срочно' : 'Наблюдение'}
      </span>;
}

function Empty({ icon, children, hint }: { icon: IconName; children: ReactNode; hint?: string }) {
  return <div className="empty"><Icon name={icon} /><strong>{children}</strong>{hint && <p>{hint}</p>}</div>;
}

const kamchatkaClock = new Intl.DateTimeFormat('ru', {
  timeZone: 'Asia/Kamchatka', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});
const timeOfDay = (value: string) => kamchatkaClock.format(new Date(value));
const modeLabels = { demo: 'Демо', replay: 'Воспроизведение', preview: 'Реальные данные', live: 'Рабочий режим' };
const deliveryKinds: Record<string, string> = { event: 'Сигнал', forecast: 'Прогноз', summary: 'Сводка' };

function forecastChecks(data: Dashboard) {
  const points = data.checks.filter(check => check.name.startsWith('forecast:'));
  return points.length ? points : data.checks.filter(check => check.name === 'forecast');
}

const periodLabel = { daily: 'сутки', hourly: 'час', three_hourly: '3 часа' };
const forecastDate = new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka', day: 'numeric', month: 'short',
  hour: '2-digit', minute: '2-digit' });
const amount = (value: number | null, unit: string) => value == null ? '—' : value.toLocaleString('ru') + ' ' + unit;
const percent = (value: number | null) => value == null ? '—' : Math.round(value * 100) + '%';

function ForecastPage({ data, real, updatedAt }: { data: Dashboard; real: boolean; updatedAt: string }) {
  const [point, setPoint] = useState('');
  const checks = forecastChecks(data);
  const points = [...new Set([...(data.forecast_points ?? []), ...data.forecasts.map(item => item.point)])];
  const selected = points.includes(point) ? point : points[0];
  const forecast = data.forecasts.find(item => item.point === selected);
  const reference = real || forecast?.source ? updatedAt : forecast?.fetched_at;
  const from = Date.parse(reference ?? '') || Date.now();
  const periods = forecast ? upcomingForecast(forecast.periods, from) : [];
  const warning = forecast && data.warnings.find(item => item.point === forecast.point && Date.parse(item.payload.end) > from);
  const nearestSnow = periods.find(period => period.snow);
  return <div className="page-stack">
    {points.length > 0 && <div className="panel panel-head">
      <label>Город или район <select value={selected} onChange={event => setPoint(event.currentTarget.value)}>
        {points.map(name => <option key={name}>{name}</option>)}
      </select></label>
    </div>}
    {forecast ? <section className="panel forecast">
      <div className="panel-head forecast-head">
        <div><h2>Погодное окно</h2><p>{forecast.source ?? (real ? 'OpenWeather' : 'Контрольный прогноз')} · {time(forecast.fetched_at)}</p></div>
      </div>
      {warning && <div className="forecast-warning">
        <Icon name="bell" /><div><strong>{forecast.source ? 'Снежный эпизод в сценарии' : 'Погодное предупреждение'}</strong>
          <span>{forecastDate.format(new Date(warning.payload.start))} — {forecastDate.format(new Date(warning.payload.end))}</span></div>
      </div>}
      <div className="forecast-window">
        <h3>Ближайшие периоды</h3>
        {periods.length ? <>
          <p className="muted">{nearestSnow
            ? (Date.parse(nearestSnow.at) <= from ? 'В текущем периоде есть снег' : 'Снег ожидается с ' + forecastDate.format(new Date(nearestSnow.at)))
            : 'В показанные периоды снега нет'}</p>
          <ol className="weather-window" aria-label="Ближайшие периоды прогноза">
            {periods.map(period => <li key={period.at + period.horizon} className={period.snow ? 'has-snow' : ''}>
              <time dateTime={period.at}>{forecastDate.format(new Date(period.at))}</time>
              <strong>{amount(period.temperature_c, '°C')}</strong>
              <span><Icon name="forecast" width="15" height="15" />{period.snow ? (period.snow_cm == null ? amount(period.snow_mm, 'мм снега') : amount(period.snow_cm, 'см снега')) : 'Без снега'}</span>
              <span>Ветер · {amount(period.wind_mps, 'м/с')}</span>
              <span className="weather-probability">Осадки · {percent(period.probability)}</span>
              {period.probability != null && <meter min={0} max={1} value={period.probability}
                aria-label="Вероятность осадков" />}
            </li>)}
          </ol>
        </> : <p className="muted">Почасовых данных для ближайших периодов пока нет.</p>}
      </div>
      <details className="forecast-details">
        <summary>Все периоды прогноза · {forecast.periods.length}</summary>
        {forecast.periods.length ? <div className="table-scroll" tabIndex={0} aria-label={'Таблица прогноза: ' + forecast.point}>
          <table><thead><tr><th>Период</th><th>Вероятность осадков</th><th>{forecast.source ? 'Снег, см' : 'Снег, мм'}</th>
            <th>Воздух, °C</th><th>Ветер, м/с</th></tr></thead>
            <tbody>{forecast.periods.map(period =>
              <tr key={period.at + period.horizon}><td>{time(period.at)} · {periodLabel[period.horizon]}</td>
                <td>{percent(period.probability)}</td><td>{period.snow_cm ?? period.snow_mm ?? '—'}</td>
                <td>{period.temperature_c ?? '—'}</td><td>{period.wind_mps ?? '—'}</td></tr>)}</tbody>
          </table>
        </div> : <Empty icon="forecast">Периоды прогноза отсутствуют</Empty>}
      </details>
    </section> : <section className="panel"><Empty icon="forecast" hint="Здесь появятся ожидаемые осадки, температура и ветер.">
      {selected ? 'Для выбранной точки прогноза пока нет' : 'Прогноз ещё не получен'}
    </Empty></section>}
    {checks.length > 0 && <section className="panel">
      <div className="panel-head"><h2>Источник прогноза</h2></div>
      <dl className="health-list">{checks.map(check =>
        <div key={check.name}><dt>{check.name.split(':')[1] ?? 'OpenWeather'}</dt>
          <dd>{checkLabels[check.status] ?? check.status}</dd><dd className="muted">{time(check.observed_at)}</dd>
        </div>)}</dl>
    </section>}
  </div>;
}

function ReportsPage({ data }: { data: Dashboard }) {
  const [filter, setFilter] = useState<'all' | 'attention' | 'pending'>('all');
  const deliveries = data.deliveries.filter(item => filter === 'all' || deliveryBucket(item.stage) === filter);
  const attention = data.deliveries.filter(item => deliveryBucket(item.stage) === 'attention').length;
  const pending = data.deliveries.filter(item => deliveryBucket(item.stage) === 'pending').length;
  const last = data.summaries[0];
  return <div className="page-stack">
    <dl className="section-metrics" aria-label="Состояние сводок и доставки">
      <div><dt><Icon name="reports" />Последняя сводка</dt><dd>{last ? forecastDate.format(new Date(last.payload.end)) : 'Ещё нет'}</dd></div>
      <div><dt><Icon name="bell" />Требуют внимания</dt><dd className={attention ? 'hot' : ''}>{attention}</dd></div>
      <div><dt><Icon name="clock" />Ожидают отправки</dt><dd>{pending}</dd></div>
    </dl>
    <div className="reports-grid">
    <section className="panel">
      <div className="panel-head"><div><h2>{data.mode === 'demo' || data.mode === 'replay' ? 'Сводки прогона' : 'Утренние сводки'}</h2>
        <p>Обстановка и действия за период</p></div></div>
      <dl className="feedback-totals">{(Object.keys(verdictLabels) as FeedbackVerdict[]).map(verdict =>
        <div key={verdict}><dt>{verdictLabels[verdict]}</dt><dd>{data.feedback_counts[verdict] ?? 0}</dd></div>)}</dl>
      {data.summaries.length ? data.summaries.map(summary => <details className="journal-entry" key={summary.day}>
        <summary><span>{time(summary.payload.end)}<small>Сигналов: {summary.payload.events.length}</small></span></summary>
        <div className="journal-body">
          <p className="muted">{time(summary.payload.start)} — {time(summary.payload.end)}</p>
          {summary.payload.events.map(item => <p key={item.id}>{item.name} · {stateLabels[item.state]} · {item.reason}</p>)}
          {summary.payload.warnings?.length > 0 && <ul>{summary.payload.warnings.map(warning =>
            <li key={warning.point}>{warning.point} · {time(warning.payload.start)} — {time(warning.payload.end)}</li>)}</ul>}
          {summary.payload.stale_cameras.length > 0 && <p>Нет свежих кадров: {summary.payload.stale_cameras.join(', ')}</p>}
          {summary.payload.actions?.length > 0 && <ul>{summary.payload.actions.map((action, index) =>
            <li key={action.name + action.created_at + index}>{action.name} · {stateLabels[action.state]} · {time(action.created_at)}
              {action.note && <span> · {action.note}</span>}</li>)}</ul>}
        </div>
      </details>) : <Empty icon="reports">Сводок пока нет</Empty>}
    </section>
    <section className="panel">
      <div className="panel-head"><div><h2>Журнал доставки</h2><p>Оповещения и состояние отправки</p></div></div>
      <div className="section-filters delivery-filters" aria-label="Фильтр доставки">
        {([['all', 'Все', data.deliveries.length], ['attention', 'К проверке', attention], ['pending', 'В очереди', pending]] as const)
          .map(([id, label, count]) => <button type="button" key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>
            {label} <small>{count}</small></button>)}
      </div>
      {deliveries.length ? deliveries.map(delivery => <details className="journal-entry" key={delivery.id}>
        <summary><span>{delivery.subject}<small>{deliveryKinds[delivery.kind] ?? delivery.kind} · {time(delivery.created_at)}</small></span>
          <span className={'status ' + (deliveryBucket(delivery.stage) === 'attention' ? 'urgent' : delivery.stage === 'sent' ? 'ok' : 'neutral')}>{deliveryLabels[delivery.stage] ?? delivery.stage}</span></summary>
        <div className="journal-body">
          <p className="muted">Попыток: {delivery.attempts}</p>
          <p className="message">{delivery.body}</p>
          {delivery.error && <p className="error">{delivery.error}</p>}
        </div>
      </details>) : <Empty icon="bell">{filter === 'all' ? 'Сообщений пока нет' : 'Сообщений по этому фильтру нет'}</Empty>}
    </section>
    </div>
  </div>;
}

function SignalDialog({ event, close, children }: { event: EventItem; close: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return <dialog ref={dialog} className="overview-signal" onCancel={e => { e.preventDefault(); close(); }}
    aria-label={event.name}>{children}</dialog>;
}

function App() {
  const [serviceData, setData] = useState<Dashboard | null>(null);
  const [scenario, setScenario] = useState(() => parseScenario(params.get('scenario')));
  const [phase, setPhase] = useState(() => parseCleanupPhase(params.get('phase')));
  const data = useMemo(() => {
    if (!scenario) return serviceData;
    const demo = demoDashboard(scenario, phase);
    return scenario === 'response' ? { ...demo, city_map_url: serviceData?.city_map_url } : demo;
  }, [scenario, phase, serviceData]);
  const [dispatchTarget, setDispatchTarget] = useState<{ key: string }>();
  const [roadDetails, setRoadDetails] = useState(false);
  const [onlyActiveSignals, setOnlyActiveSignals] = useState(true);
  const [cameraSearch, setCameraSearch] = useState('');
  const [cameraFilter, setCameraFilter] = useState<'all' | 'snow' | 'attention'>('all');
  const [tab, setTab] = useState<Tab>(currentTab);
  const [citizenToken, setCitizenToken] = useState('');
  const [citizenReport, setCitizenReport] = useState<string>();
  const [cameraId, setCameraId] = useState<string>();
  const [eventId, setEventId] = useState(params.get('event'));
  const [error, setError] = useState('');
  const [refreshAt, setRefreshAt] = useState('');
  const [linkedEvent, setLinkedEvent] = useState<EventItem | null>(null);
  const [linkError, setLinkError] = useState('');
  const requestInFlight = useRef<Promise<void> | null>(null);
  const eventTrigger = useRef<HTMLButtonElement | null>(null);
  const cameraPanel = useRef<HTMLDivElement>(null);
  const refresh = () => {
    if (requestInFlight.current) return requestInFlight.current;
    requestInFlight.current = api<Dashboard>('dashboard').then(result => {
      setData(result); setError(''); setRefreshAt(new Date().toISOString());
    }).catch(e => { setError(e.message); }).finally(() => { requestInFlight.current = null; });
    return requestInFlight.current;
  };

  async function saveDispatch(path: string, draft: ReportDraft | TaskDraft | AssignmentDraft | ProgressDraft | { task_id: string }, method = 'POST') {
    await api('dispatch/' + path, {
      method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft),
    });
    await requestInFlight.current;
    await refresh();
  }

  useEffect(() => {
    const sync = () => setTab(currentTab());
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);
  useEffect(() => {
    if (scenario && scenario !== 'response') return;
    void refresh();
    if (scenario) return;
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, [scenario]);
  useEffect(() => {
    if (tab === 'cameras' && cameraPanel.current && cameraId) cameraPanel.current.focus({ preventScroll: true });
  }, [tab]);

  const real = data?.mode === 'preview' || data?.mode === 'live';
  const listedEvent = data?.events.find(item => item.id === eventId);
  useEffect(() => {
    setLinkError('');
    if (!eventId || listedEvent || scenario) return;
    let active = true;
    api<EventItem>('events/' + encodeURIComponent(eventId)).then(item => { if (active) setLinkedEvent(item); })
      .catch(e => { if (active) setLinkError(e.message); });
    return () => { active = false; };
  }, [eventId, Boolean(listedEvent), scenario]);
  const event = listedEvent ?? (linkedEvent?.id === eventId ? linkedEvent : null);
  const cameras = [...(data?.cameras ?? [])].sort((first, second) => Number(second.road) - Number(first.road));
  const cameraAttention = (camera: Camera) => !cameraHealthy(camera);
  const search = cameraSearch.trim().toLocaleLowerCase('ru');
  const visibleCameras = cameras.filter(camera => (!search || [camera.name, camera.area, camera.district, camera.section_name]
    .some(value => value?.toLocaleLowerCase('ru').includes(search))) &&
    (cameraFilter === 'all' || (cameraFilter === 'snow' ? cameraShowsSnow(camera) : cameraAttention(camera))));
  const selectedCamera = visibleCameras.find(camera => camera.id === cameraId) ?? visibleCameras[0];
  const cameraCleanup = selectedCamera?.road ? data?.dispatch_queue.find(row => row.task?.work_kind === 'clear' &&
    row.signals.some(signal => signal.camera_id === selectedCamera.id && signal.linked && signal.active)) : undefined;
  const page = {
    overview: ['Дорожная обстановка', 'Текущие сигналы и состояние камер'],
    dispatch: ['Диспетчерская', 'Сигналы камер, обращения жителей и задания на проверку или уборку'],
    citizens: ['Обращения жителей', 'Сообщения с сайта, из Telegram и MAX'],
    cameras: ['Камеры', 'Последние кадры и оценки по каждому ракурсу'],
    forecast: ['Прогноз', 'Ожидаемые осадки, температура и ветер'],
    reports: ['Сводки и доставка', 'Журнал погодных предупреждений и действий'],
  }[tab];

  function openCamera(id: string) {
    setCameraId(id);
    if (tab !== 'cameras') {
      setCameraFilter('all'); setCameraSearch('');
      location.hash = 'cameras';
      window.scrollTo(0, 0);
    }
  }

  function closeEvent() {
    setEventId(null);
    eventTrigger.current?.focus();
  }

  function changeScenario(next: DemoScenario | null) {
    const url = new URL(location.href);
    if (next) url.searchParams.set('scenario', next);
    else url.searchParams.delete('scenario');
    url.searchParams.delete('event');
    history.replaceState(null, '', url);
    setScenario(next);
    setEventId(null); setDispatchTarget(undefined); setCameraId(undefined); setCameraSearch(''); setCameraFilter('all');
  }

  function changeCleanupPhase(next: CleanupPhase) {
    const url = new URL(location.href);
    url.searchParams.set('phase', next);
    history.replaceState(null, '', url);
    setPhase(next);
  }

  function openDispatch(row: DispatchGroup) {
    setDispatchTarget({ key: dispatchKey(row) });
    location.hash = 'dispatch';
  }

  function openCitizen(id?: string) {
    setCitizenReport(id);
    location.hash = 'citizens';
    window.scrollTo(0, 0);
  }

  const showOverview = tab === 'overview';
  const showCitizens = tab === 'citizens';
  const showRecentCitizens = showOverview && !scenario && run !== 'demo';
  const recentReports = useRecentCitizenReports(citizenToken, showRecentCitizens);
  const recentCitizens = showRecentCitizens
    ? <RecentCitizenReports result={recentReports} onOpen={openCitizen} /> : null;
  const serviceMode = serviceData?.mode ?? data?.mode;
  const showBusDemoLink = tab === 'dispatch' && (serviceMode === 'preview' || serviceMode === 'demo') &&
    data?.bus_observations?.source !== 'simulation';
  const signals = data?.events.filter(item => !onlyActiveSignals || item.state !== 'resolved') ?? [];
  return <div className="app-shell app-monitor">
    <a className="skip-link" href="#main-content" onClick={e => {
      e.preventDefault();
      document.getElementById('main-content')?.focus();
    }}>К основному содержанию</a>
    <aside className="sidebar">
      <a className="brand" href="#overview" aria-label="Алгоритмы Камчатки, обстановка">
        <span className="brand-mark"><Icon name="mark" width="23" height="23" /></span>
        <span><strong>Алгоритмы<br />{' '}Камчатки</strong><small>Зимний мониторинг</small></span>
      </a>
      <nav aria-label="Разделы">{sections.map(section =>
        <a key={section.id} title={section.title} aria-label={section.title} href={'#' + section.id} aria-current={section.id === tab ? 'page' : undefined}
          onClick={() => { setDispatchTarget(undefined); if (section.id === 'citizens') setCitizenReport(undefined); if (section.id !== tab) window.scrollTo(0, 0); }}>
          <Icon name={section.icon} /><span>{section.title}</span></a>
      )}</nav>
      <div className="sidebar-bottom">
        {data && <span className={'mode ' + (real ? 'mode-real' : 'mode-demo')}><span className="indicator" />{modeLabels[data.mode]}</span>}
        {data?.mode === 'preview' && <a className="mode-link" href="/demo">Открыть демо <Icon name="chevron" width="16" height="16" /></a>}
        {data?.mode === 'demo' && location.pathname === '/demo' &&
          <a className="mode-link" href="/">Реальные данные <Icon name="chevron" width="16" height="16" /></a>}
      </div>
    </aside>
    <main className={showOverview ? "workspace overview-workspace" : "workspace section-workspace"} id="main-content" tabIndex={-1}>
      {!showOverview && <header className="workspace-head">
        <div><div className="section-place"><span className="small-dot" />Алгоритмы Камчатки</div><h1>{page[0]}</h1><p>{page[1]}</p></div>
        {!showCitizens && <div className="page-tools">
          {showBusDemoLink && <a className="bus-demo-link" href="/demo#dispatch">
            <Icon name="bus" width="17" height="17" />Демо задержек автобусов
          </a>}
          {scenario || data?.mode === 'demo' ? <label className="section-demo-select"><span>Демосценарий</span>
            <select value={scenario ?? ''} onChange={e => changeScenario(parseScenario(e.currentTarget.value))}>
              <option value="">Данные сервиса</option>
              {Object.entries(demoScenarios).map(([id, title]) => <option key={id} value={id}>{title}</option>)}
            </select>
          </label> : <>
            <div className="refresh-time"><span>Камчатка · UTC+12</span>
              <strong>{refreshAt ? 'Обновлено ' + timeOfDay(refreshAt) : 'Ожидается обновление'}</strong>
            </div>
            <button className="quiet icon-button" onClick={refresh} title="Обновить данные" aria-label="Обновить данные"><Icon name="refresh" /></button>
          </>}
        </div>}
      </header>}
      {!showOverview && !showCitizens && error && !scenario && <p className="error" role="alert">{error}</p>}
      {!showOverview && !showCitizens && !data && !error && <p className="muted" role="status">Загружаем данные мониторинга…</p>}
      {data && !real && !showOverview && !showCitizens && !scenario && <p className="demo-note">{data.mode === 'demo' ? 'Демонстрационный сценарий. Кадры и события взяты из контрольного прогона.'
        : 'Воспроизведение сохранённых наблюдений.'}</p>}
      {linkError && <p className={showOverview ? 'overview-link-error' : 'error'} role="alert">{linkError}</p>}
      {data && !scenario && event && showOverview && <SignalDialog key={event.id} event={event} close={closeEvent}>
        <EventDetail event={event} role={data.role} close={closeEvent} refresh={refresh} />
      </SignalDialog>}
      {data && !scenario && event && (tab === 'dispatch' || tab === 'cameras') &&
        <EventDetail key={event.id} event={event} role={data.role} close={closeEvent} refresh={refresh} />}
      {data && tab === 'dispatch' && <DispatchQueue rows={data.dispatch_queue} loadRoads={loadDispatchRoads}
        photoUrl={id => '/api/v1/dispatch/result-photos/' + id + suffix} onCitizen={openCitizen}
        busObservations={data.bus_observations}
        assignees={data.dispatch_assignees}
        cameraDistricts={data.cameras.map(camera => camera.district || camera.area)}
        target={dispatchTarget} operator={data.role === 'operator'} onRegister={draft => saveDispatch('reports', draft)}
        onSaveTask={draft => saveDispatch('tasks', draft)}
        onAssign={(id, draft) => saveDispatch('tasks/' + id + '/assignment', draft, 'PUT')}
        onProgress={(id, draft) => saveDispatch('tasks/' + id + '/progress', draft, 'PUT')}
        onLink={id => api<{ token: string }>('dispatch/tasks/' + id + '/link', { method: 'POST' }).then(value => value.token)}
        onLinkReport={(id, taskId) => saveDispatch('reports/' + id + '/task', { task_id: taskId }, 'PUT')}
        loadHistory={id => scenario ? Promise.resolve([]) : api<HistoryItem[]>('dispatch/tasks/' + id + '/history')}
        loadArchive={() => scenario ? Promise.resolve([]) : api<ArchivedTask[]>('dispatch/archive')}
        onOpenSignal={scenario ? undefined : (id, trigger) => { eventTrigger.current = trigger; setEventId(id); }}
        frameUrl={frameUrl} formatTime={time} />}
      {showOverview && (data ? <Overview key={scenario ?? data.run_id} data={data} error={scenario && scenario !== 'response' ? '' : error} updatedAt={refreshAt}
        scenario={scenario} onScenario={changeScenario} phase={phase} onPhase={changeCleanupPhase} frameUrl={frameUrl} onDispatch={openDispatch}
        onSignal={(id, trigger) => { eventTrigger.current = trigger; setEventId(id); }}
        onCamera={openCamera} onRefresh={refresh} loadRoads={loadSnowRoads} loadSearchRoads={loadDispatchRoads} recentCitizens={recentCitizens}
        citizenReports={showRecentCitizens ? recentReports.page?.items : undefined} onCitizenReport={openCitizen} />
        : <section className="overview-start">
          <h1>Дорожная обстановка</h1>
          <p role={error ? 'alert' : 'status'}>{error || 'Подключаем данные мониторинга…'}</p>
          <div><button type="button" onClick={() => changeScenario('snowfall')}>Открыть демо</button>
            {error && <button type="button" onClick={refresh}>Повторить подключение</button>}</div>
          {recentCitizens}
        </section>)}
      {showCitizens && <CitizenReports key={citizenReport} token={citizenToken} onToken={setCitizenToken} selected={citizenReport} />}
      {data && tab === 'cameras' && <div className="page-stack">
        <div className="camera-console">
          <section className="panel camera-index" aria-labelledby="camera-index-heading">
            <div className="panel-head"><div><h2 id="camera-index-heading">Список камер</h2>
              <p>{cameras.filter(cameraHealthy).length} из {cameras.length} со свежим кадром</p></div>
              <Icon name="camera" /></div>
            <div className="camera-index-tools">
              <input type="search" value={cameraSearch} onChange={e => setCameraSearch(e.currentTarget.value)}
                placeholder="Название или район" aria-label="Поиск камер" />
              <div className="section-filters" aria-label="Фильтр камер">
                {([['all', 'Все', cameras.length], ['snow', 'Идёт снег', cameras.filter(cameraShowsSnow).length],
                  ['attention', 'Нет свежих кадров', cameras.filter(cameraAttention).length]] as const).map(([id, label, count]) =>
                  <button type="button" key={id} aria-pressed={cameraFilter === id} onClick={() => setCameraFilter(id)}>
                    {label} <small>{count}</small>
                  </button>)}
              </div>
            </div>
            <CameraList cameras={visibleCameras} selected={selectedCamera?.id} real={real} onSelect={id => {
              openCamera(id);
              if (matchMedia('(max-width: 750px)').matches) requestAnimationFrame(() => {
                cameraPanel.current?.focus({ preventScroll: true });
                cameraPanel.current?.scrollIntoView({ block: 'start' });
              });
            }} />
          </section>
          <div ref={cameraPanel} tabIndex={-1} className="camera-selection">
            {selectedCamera ? <CameraDetail camera={selectedCamera} real={real}
              onOpenCleanup={cameraCleanup ? () => openDispatch(cameraCleanup) : undefined} /> : <section className="panel">
              <Empty icon="camera">Выберите камеру</Empty>
            </section>}
          </div>
        </div>
        <div className="camera-secondary">
        <section className="panel">
          <div className="panel-head"><h2>Журнал сигналов камер</h2>
            <label className="toggle"><input type="checkbox" checked={onlyActiveSignals}
              onChange={e => setOnlyActiveSignals(e.currentTarget.checked)} /> Только открытые</label></div>
          {signals.length ? <div className="event-list">{signals.map(item =>
            <button className="event-row" key={item.id} type="button" disabled={Boolean(scenario)}
              aria-expanded={eventId === item.id} onClick={e => { eventTrigger.current = e.currentTarget; setEventId(item.id); }}>
              <span className="event-meta"><EventSeverity event={item} /><small>{time(item.last_seen)}</small></span>
              <strong>{item.name}</strong><span>{item.reason}</span>
              <small className="event-state">{stateLabels[item.state]}</small>
            </button>)}</div> : <Empty icon="check">{onlyActiveSignals ? 'Открытых сигналов нет' : 'Событий пока нет'}</Empty>}
        </section>
        <section className="panel">
          <div className="panel-head"><div><h2>Обработка кадров</h2><p>В очереди: {data.queue.pending}
            {data.queue.oldest && ' · старейший кадр ' + time(data.queue.oldest)}</p></div></div>
          <dl className="health-list">{data.checks.filter(check => !check.name.startsWith('forecast')).map(check =>
            <div key={check.name}><dt>{{ model: 'Модель', worker: 'Обработчик', mail: 'Почта' }[check.name] ?? check.name}</dt>
              <dd>{checkLabels[check.status] ?? check.status}</dd><dd className="muted">{time(check.observed_at)}</dd></div>
          )}</dl>
        </section>
        </div>
      </div>}
      {data && tab === 'forecast' && <>
        <ForecastPage data={data} real={real} updatedAt={scenario ? scenarioClock(scenario, phase) : refreshAt} />
        {(data.snow_forecast || cameras.some(item => item.coordinates)) && <details className="panel forecast-map"
          onToggle={e => setRoadDetails(e.currentTarget.open)}>
          <summary>Карта камер и дорожных отрезков</summary>
          {roadDetails && <CameraMap cameras={cameras} onSelect={openCamera} snow={data.snow_forecast}
            loadRaster={scenario ? noDemoRaster : loadSnowRaster} loadRoads={loadSnowRoads} />}
        </details>}
      </>}
      {data && tab === 'reports' && <ReportsPage data={data} />}
    </main>
  </div>;
}

const entry = location.pathname === '/assignment' ? <CrewAssignment /> : location.pathname === '/crew' ? <CrewInbox /> : <App />;
createRoot(document.getElementById('root')!).render(entry);
