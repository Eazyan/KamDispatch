import { Icon } from './Icon';
import { BusChecks } from './BusChecks';
import { dispatchKey } from './overviewWidgets';
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { RoadPicker, type RoadSelection } from './RoadPicker';
import { TaskAssignment } from './TaskAssignment';
import { ResultPhotos, TaskHistory, taskStages, workLabels } from './TaskProgress';
import { dispatchStage, dispatchStages } from './sectionViews';
import type { ArchivedTask, AssignmentDraft, BusObservations, DispatchGroup, DispatchRoad, HistoryItem, IssueKind, MapSection, ProgressDraft, ReportDraft, TaskDraft, WorkKind } from './types';

const issues: Record<IssueKind, string> = {
  snowfall: 'Снегопад', coverage: 'Снег на дороге', drift: 'Снежный вал', other: 'Другая проблема',
};
const channels: Record<string, string> = {
  phone: 'Телефон', online: 'Онлайн-приёмная', in_person: 'Лично', other: 'Другой канал',
};
const work: Record<WorkKind, string> = { inspect: 'Проверка', clear: 'Уборка' };

const manualDistrict = '__manual__';

type LoadRoads = (signal: AbortSignal) => Promise<DispatchRoad[]>;
type AssignmentActions = {
  assignees: string[];
  onAssign: (id: string, draft: AssignmentDraft) => Promise<void>;
  onProgress: (id: string, draft: ProgressDraft) => Promise<void>;
  onLink: (id: string) => Promise<string>;
  onLinkReport: (reportId: string, taskId: string) => Promise<void>;
  loadHistory: (id: string) => Promise<HistoryItem[]>;
};
const rowKey = dispatchKey;
const rowId = (row: DispatchGroup) => 'dispatch-' + encodeURIComponent(rowKey(row));
const urgentToAssign = (row: DispatchGroup) => row.severity === 'urgent' && !row.task?.assignee;
const taskFilters = {
  all: 'Все статусы', unassigned: 'Без исполнителя', pending: 'Ждут подтверждения',
  confirmed: 'Подтверждены', overdue: 'Просрочены', review: 'Ждут приёмки',
} as const;
type TaskFilter = keyof typeof taskFilters;

function hasStatus(row: DispatchGroup, filter: TaskFilter) {
  switch (filter) {
    case 'unassigned': return !row.task?.assignee;
    case 'pending': return Boolean(row.task?.assignee && !row.task.accepted_at);
    case 'confirmed': return Boolean(row.task?.accepted_at);
    case 'overdue': return Boolean(row.task?.overdue);
    case 'review': return row.task?.stage === 'done';
    default: return true;
  }
}

function ReportForm({ districts, initialDistrict, loadRoads, onSave, onCancel }: {
  districts: string[]; initialDistrict: string; loadRoads: LoadRoads;
  onSave: (draft: ReportDraft) => Promise<void>; onCancel: () => void;
}) {
  const [mode, setMode] = useState<'address' | 'map'>('address');
  const [selection, setSelection] = useState<RoadSelection | null>(null);
  const [area, setArea] = useState(initialDistrict || (districts.length ? '' : manualDistrict));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const district = area === manualDistrict ? String(fields.get('district') ?? '').trim() : area;
    const street = String(fields.get('street') ?? '').trim();
    const house = String(fields.get('house') ?? '').trim();
    const description = String(fields.get('description') ?? '').trim();
    let place: Pick<ReportDraft, 'address' | 'road_id' | 'endpoints'>;
    if (mode === 'map') {
      if (selection && 'road' in selection) {
        place = { road_id: selection.road.id };
      } else if (selection?.points.length === 2) {
        place = { endpoints: [selection.points[0], selection.points[1]] };
      } else {
        setError('Выберите дорогу или отметьте начало и конец участка.');
        return;
      }
      if (street) place.address = [street, house].filter(Boolean).join(', ');
    } else {
      place = { address: [street, house].filter(Boolean).join(', ') };
    }
    if (!description || !district || (mode === 'address' && !street)) {
      setError('Укажите район, место и описание обращения.');
      return;
    }
    requestId.current ??= crypto.randomUUID();
    setBusy(true);
    setError('');
    try {
      await onSave({
        id: requestId.current, district, ...place,
        kind: fields.get('kind') as IssueKind, description, channel: String(fields.get('channel')),
        reference: String(fields.get('reference')), severity: String(fields.get('severity')),
        passage_obstructed: fields.has('passageObstructed'),
      });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <form className="dispatch-form" onSubmit={submit}>
    <fieldset disabled={busy}>
      <legend>Регистрация обращения</legend>
      <div className="dispatch-fields">
        <div className="dispatch-wide dispatch-place-options" role="group" aria-label="Как указать участок">
          <label><input type="radio" name="placeMode" checked={mode === 'address'} onChange={() => setMode('address')} /> По адресу</label>
          <label><input type="radio" name="placeMode" checked={mode === 'map'} onChange={() => setMode('map')} /> На карте</label>
        </div>
          {districts.length > 0 && <label>Район обращения
            <select required value={area} onChange={event => setArea(event.target.value)}>
              <option value="" disabled>Выберите район</option>
              {districts.map(district => <option key={district}>{district}</option>)}
              <option value={manualDistrict}>Ввести новый район</option>
            </select>
          </label>}
          {area === manualDistrict && <label>Название района<input name="district" required maxLength={255} /></label>}
        <div className="dispatch-wide dispatch-fields">
          <label>Улица<input name="street" required={mode === 'address'} maxLength={180} /></label>
          <label>Ближайший дом<input name="house" maxLength={60} /></label>
        </div>
        {mode === 'map' && <div className="dispatch-wide">
          <RoadPicker selection={selection} onSelect={setSelection} loadRoads={loadRoads} />
        </div>}
        <label>Проблема<select name="kind" required defaultValue="">
          <option value="" disabled>Выбрать проблему</option>
          {Object.entries(issues).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>Приоритет<select name="severity" defaultValue="watch">
          <option value="watch">Обычный</option><option value="urgent">Срочно</option>
        </select></label>
        <label>Канал<select name="channel">
          {Object.entries(channels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>Номер исходной заявки<input name="reference" maxLength={100} /></label>
        <label className="dispatch-wide">Описание<textarea name="description" required maxLength={1500} rows={3} /></label>
        <div className="dispatch-wide dispatch-place-options">
          <label><input type="checkbox" name="passageObstructed" /> В обращении указано, что проезд затруднён</label>
        </div>
      </div>
      <div className="dispatch-controls">
        <button type="submit">{busy ? 'Сохраняю…' : 'Зарегистрировать'}</button>
        <button type="button" className="quiet" onClick={onCancel}>Отмена</button>
      </div>
      {error && <p className="error" role="alert">{error}</p>}
    </fieldset>
  </form>;
}

function ReportTaskLink({ tasks, onSave }: {
  tasks: DispatchGroup[]; onSave: (id: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return open ? <form className="dispatch-report-link" onSubmit={async event => {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError('');
    try {
      await onSave(selected);
      setOpen(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }}>
    <label>Открытое задание
      <select required value={selected} disabled={busy} onChange={event => setSelected(event.target.value)}>
        <option value="" disabled>Выберите задание</option>
        {tasks.map(({ task, address }) => task && <option key={task.id} value={task.id}>
          {address} · {workLabels[task.work_kind]} · {taskStages[task.stage]}
        </option>)}
      </select>
    </label>
    <div className="dispatch-controls">
      <button type="submit" disabled={busy}>Привязать обращение</button>
      <button type="button" className="quiet" disabled={busy} onClick={() => setOpen(false)}>Отмена</button>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
  </form> : <button type="button" className="quiet" onClick={() => setOpen(true)}>Связать с заданием</button>;
}

function QueueItem({ row, taskChoices, operator, assignees, onOpenSignal, onSave, onAssign, onProgress, onLink, onLinkReport,
  loadHistory, frameUrl, formatTime, photoUrl, onCitizen }: AssignmentActions & {
  row: DispatchGroup; operator: boolean; onOpenSignal?: (id: string, trigger: HTMLButtonElement) => void;
  taskChoices: DispatchGroup[]; photoUrl: (id: string) => string; onCitizen?: (id: string) => void;
  onSave: (draft: TaskDraft) => Promise<void>; frameUrl: (id: string) => string; formatTime: (at: string) => string;
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');
  const [shownSection, setShownSection] = useState<MapSection | null>(null);
  const unlinked = [...row.signals, ...row.reports].some(item => !item.linked);
  const reason = row.signals.find(item => item.active)?.reason ??
    row.reports.find(item => item.active)?.description;
  const openLabel = !operator ? 'Подробнее' : !row.task ? 'Разобрать сигнал'
    : row.task.stage === 'draft' ? 'Назначить исполнителя'
    : row.task.stage === 'done' ? 'Проверить результат' : 'Открыть задание';

  async function save(workKind?: WorkKind) {
    setBusy(true);
    setNotice('');
    setError('');
    try {
      await onSave({ location_key: row.location_key, kind: row.kind, work_kind: workKind });
      setNotice(workKind ? 'Задание создано.' : 'Источники привязаны к заданию.');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return <li><details id={rowId(row)}>
    <summary className="dispatch-summary">
      <span className="dispatch-place">
        <strong>{row.address}</strong><small>{row.district}</small>
      </span>
      <span className="dispatch-reason">
        <strong>{issues[row.kind]}</strong>{reason && <small>{reason}</small>}
      </span>
      <span className={'status ' + row.severity}>{row.severity === 'urgent' ? 'Срочно' : 'Обычный'}</span>
      <span className="dispatch-task-cell">{row.task ? <>
        {work[row.task.work_kind]}
        <small>{taskStages[row.task.stage]}</small>
        {row.task.due_at && <small>До {formatTime(row.task.due_at)}</small>}
        {row.task.overdue && <small className="urgent">Просрочено</small>}
      </> : 'Без задания'}</span>
      <span className="dispatch-expand"><span>{openLabel}</span><span>Свернуть</span></span>
    </summary>
    <div className="dispatch-detail">
      {row.task && <TaskAssignment key={row.task.id} task={row.task} operator={operator} assignees={assignees} onAssign={onAssign}
        onProgress={onProgress} onLink={onLink} loadHistory={loadHistory} formatTime={formatTime} photoUrl={photoUrl} />}
      {operator && !row.task && <div className="dispatch-controls">
        <button disabled={busy} onClick={() => void save('inspect')}>Создать проверку</button>
        <button className="quiet" disabled={busy} onClick={() => void save('clear')}>Создать уборку</button>
      </div>}
      {row.signals.length > 0 && <>
        <h3>Сигналы камер</h3>
        <ul>{row.signals.map(signal => <li key={signal.id}>
          {onOpenSignal ? <button type="button" className="quiet" onClick={event => onOpenSignal(signal.id, event.currentTarget)}>{signal.name}</button> : <strong>{signal.name}</strong>}
          <span>{signal.reason} · {formatTime(signal.last_seen)}</span>
          {signal.road_priority_reason && <small className="muted">{signal.road_priority_reason}</small>}
          {signal.frame_id && <a href={frameUrl(signal.frame_id)} target="_blank" rel="noreferrer">Открыть кадр</a>}
          {row.task && <small className="muted">{signal.linked ? 'В задании' : 'Новый источник'}</small>}
          {!signal.active && <small className="muted">Архивный сигнал</small>}
        </li>)}</ul>
      </>}
      {row.reports.length > 0 && <>
        <h3>Обращения жителей</h3>
        <ul>{row.reports.map(report => <li key={report.id}>
          <strong>{channels[report.channel]}{report.reference && ' · № ' + report.reference}</strong>
          <span>{report.description}</span>
          {report.address !== row.address && <span className="muted">{report.address}</span>}
          <small className="muted">{formatTime(report.last_seen)}{row.task && (report.linked ? ' · В задании' : ' · Новый источник')}</small>
          {report.citizen_report_id && onCitizen && <button type="button" className="quiet"
            onClick={() => onCitizen(report.citizen_report_id!)}>Открыть обращение жителя</button>}
          {report.map_section && <button type="button" className="quiet"
            onClick={() => setShownSection(report.map_section)}>Показать участок на карте</button>}
          {operator && taskChoices.length > 0 && !report.linked && report.active &&
            <ReportTaskLink tasks={taskChoices} onSave={id => onLinkReport(report.id, id)} />}
        </li>)}</ul>
      </>}
      {shownSection && <div>
        <RoadPicker key={JSON.stringify(shownSection.coordinates)} selection={{ points: shownSection.coordinates }} />
        <button type="button" className="quiet" onClick={() => setShownSection(null)}>Скрыть карту</button>
      </div>}
      {operator && row.task && unlinked && <div className="dispatch-controls">
        <button className="quiet" disabled={busy} onClick={() => void save()}>Привязать новые источники</button>
      </div>}
      {notice && <p className="muted" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  </details></li>;
}

export function DispatchQueue({ rows, busObservations, cameraDistricts, loadRoads, operator, assignees, onRegister, onSaveTask, onAssign, onProgress, onLink, onLinkReport,
  loadHistory, loadArchive, onOpenSignal, frameUrl, formatTime, target, photoUrl, onCitizen }: AssignmentActions & {
  target?: { key: string };
  busObservations?: BusObservations | null;
  rows: DispatchGroup[]; cameraDistricts: string[]; loadRoads: LoadRoads; operator: boolean;
  onRegister: (draft: ReportDraft) => Promise<void>; onSaveTask: (draft: TaskDraft) => Promise<void>;
  onOpenSignal?: (id: string, trigger: HTMLButtonElement) => void;
  loadArchive: () => Promise<ArchivedTask[]>; photoUrl: (id: string) => string; onCitizen?: (id: string) => void;
  frameUrl: (id: string) => string; formatTime: (at: string) => string;
}) {
  const [showForm, setShowForm] = useState(false);
  const [notice, setNotice] = useState('');
  const [district, setDistrict] = useState('');
  const [priority, setPriority] = useState('');
  const [status, setStatus] = useState<TaskFilter>('all');
  const [jump, setJump] = useState<{ id: string }>();
  const [archive, setArchive] = useState<ArchivedTask[]>();
  const [archiveError, setArchiveError] = useState('');
  const archiveRef = useRef<HTMLDetailsElement>(null);
  const archiveRequest = useRef(0);
  const districts = [...new Set([...cameraDistricts, ...rows.map(item => item.district)])].sort((a, b) => a.localeCompare(b, 'ru'));
  const selected = districts.includes(district) ? district : '';
  const visible = rows.filter(row => (!selected || row.district === selected) &&
    (!priority || row.severity === priority) && hasStatus(row, status));
  const urgentElsewhere = rows.filter(row => row.severity === 'urgent' && (
    (selected && row.district !== selected) || priority === 'watch' || !hasStatus(row, status)
  ));
  const counts = new Map<string, { urgent: number; overdue: number }>();
  for (const row of rows) {
    const count = counts.get(row.district) ?? { urgent: 0, overdue: 0 };
    if (urgentToAssign(row)) count.urgent++;
    if (row.task?.overdue) count.overdue++;
    counts.set(row.district, count);
  }

  useEffect(() => {
    if (!target) return;
    setDistrict(''); setPriority(''); setStatus('all');
    setJump({ id: 'dispatch-' + encodeURIComponent(target.key) });
  }, [target]);

  useEffect(() => {
    if (!jump) return;
    const details = document.getElementById(jump.id);
    if (details instanceof HTMLDetailsElement) {
      details.open = true;
      details.querySelector('summary')?.focus();
      details.scrollIntoView({ block: 'start' });
    }
  }, [jump]);

  function focusUrgent(row: DispatchGroup) {
    setDistrict(row.district);
    setPriority('urgent');
    setStatus('all');
    setJump({ id: rowId(row) });
  }

  async function refreshArchive() {
    const request = ++archiveRequest.current;
    setArchiveError('');
    try {
      const tasks = await loadArchive();
      if (request === archiveRequest.current) setArchive(tasks);
    } catch (err) {
      if (request === archiveRequest.current) setArchiveError((err as Error).message);
    }
  }
  const counters = [
    { id: 'unassigned', icon: 'dispatch', label: 'Без исполнителя', count: rows.filter(row => hasStatus(row, 'unassigned')).length },
    { id: 'overdue', icon: 'clock', label: 'Просрочено', count: rows.filter(row => hasStatus(row, 'overdue')).length },
    { id: 'review', icon: 'check', label: 'Ожидают приёмки', count: rows.filter(row => hasStatus(row, 'review')).length },
  ] as const;

  return <div className="page-stack">
    <div className="section-metrics dispatch-metrics" aria-label="Быстрые фильтры диспетчерской">
      {counters.map(({ id, label, count, icon }) => <button type="button" key={id}
        aria-pressed={!selected && !priority && status === id} onClick={() => {
          const active = !selected && !priority && status === id;
          setDistrict(''); setPriority(''); setStatus(active ? 'all' : id);
        }}><span><Icon name={icon} />{label}</span><strong className={count && id === 'overdue' ? 'hot' : ''}>{count}</strong></button>)}
    </div>
    <section className="panel dispatch-panel" aria-labelledby="dispatch-heading">
    <div className="panel-head dispatch-heading">
      <h2 id="dispatch-heading">Очередь диспетчера</h2>
      {operator && <button type="button" aria-expanded={showForm} onClick={() => {
        setNotice('');
        setShowForm(!showForm);
      }}>Добавить обращение</button>}
    </div>
    {showForm && operator && <ReportForm districts={districts} initialDistrict={selected} loadRoads={loadRoads}
      onCancel={() => setShowForm(false)} onSave={async draft => {
      await onRegister(draft);
      const addedDistrict = draft.district;
      if (selected && addedDistrict) setDistrict(addedDistrict);
      setNotice('Обращение зарегистрировано.');
      setShowForm(false);
    }} />}
    <details className="dispatch-district-overview">
      <summary>Сводка по районам · {districts.length}</summary>
      <ul>
        {districts.map(name => <li key={name}><button type="button" aria-pressed={name === selected}
          onClick={() => setDistrict(name === selected ? '' : name)}>
          <strong>{name}</strong><span className={counts.get(name)?.urgent ? 'urgent' : 'muted'}>Срочно к назначению · {counts.get(name)?.urgent ?? 0}</span>
          <span className={counts.get(name)?.overdue ? 'urgent' : 'muted'}>Просрочено · {counts.get(name)?.overdue ?? 0}</span>
        </button></li>)}
      </ul>
    </details>
    <div className="dispatch-other-urgent" aria-live="polite">
      {urgentElsewhere.length > 0 && <>
        <strong>{urgentElsewhere.every(row => selected && row.district !== selected)
          ? 'Срочно в других районах' : 'Срочные вне списка'} · {urgentElsewhere.length}</strong>
        <ul>{urgentElsewhere.map(row => <li key={rowKey(row)}><button type="button" className="quiet"
          onClick={() => focusUrgent(row)}>{row.district} · {row.address} · {issues[row.kind]}</button></li>)}</ul>
      </>}
    </div>
    <div className="dispatch-filter">
      <label htmlFor="dispatch-district">Район</label>
      <select id="dispatch-district" value={selected} onChange={event => setDistrict(event.target.value)}>
        <option value="">Все районы</option>
        {districts.map(option => <option key={option}>{option}</option>)}
      </select>
      <label htmlFor="dispatch-priority">Приоритет</label>
      <select id="dispatch-priority" value={priority} onChange={event => setPriority(event.target.value)}>
        <option value="">Все приоритеты</option><option value="urgent">Срочные</option><option value="watch">Обычные</option>
      </select>
      <label htmlFor="dispatch-status">Статус задания</label>
      <select id="dispatch-status" value={status} onChange={event => setStatus(event.target.value as TaskFilter)}>
        {Object.entries(taskFilters).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
      </select>
      <span className="muted">В списке: {visible.length}</span>
    </div>
    {notice && <p className="dispatch-empty muted" role="status">{notice}</p>}
    {Object.entries(dispatchStages).map(([stage, label]) => {
      const stageRows = visible.filter(row => dispatchStage(row.task?.stage) === stage);
      return <section className="dispatch-stage" key={stage} aria-labelledby={'dispatch-stage-' + stage}>
        <h3 id={'dispatch-stage-' + stage}>{label} <span>{stageRows.length}</span></h3>
        {stageRows.length > 0 && <ul className="dispatch-list">{stageRows.map(row =>
          <QueueItem key={rowKey(row)} row={row} operator={operator} assignees={assignees}
            onAssign={async (id, draft) => {
              await onAssign(id, draft);
              if (row.task?.stage === 'draft') setJump({ id: rowId(row) });
            }}
            onProgress={async (id, draft) => {
              await onProgress(id, draft);
              if (draft.action === 'close' || draft.action === 'followup') {
                if (archiveRef.current?.open) void refreshArchive();
                if (draft.action === 'close') {
                  setNotice('Задание закрыто.');
                  archiveRef.current?.querySelector('summary')?.focus();
                  return;
                }
              }
              if (draft.action === 'finish' || draft.action === 'return' || draft.action === 'followup') {
                setJump({ id: rowId(row) });
              }
            }}
            taskChoices={rows.filter(item => item.task && item.district.toLocaleLowerCase('ru') === row.district.toLocaleLowerCase('ru'))}
            onLink={onLink} onLinkReport={onLinkReport} loadHistory={loadHistory}
            onSave={onSaveTask} onOpenSignal={onOpenSignal} frameUrl={frameUrl} formatTime={formatTime}
            photoUrl={photoUrl} onCitizen={onCitizen} />
        )}</ul>}
      </section>;
    })}
    {!visible.length && <p className="dispatch-empty muted">{selected || priority || status !== 'all'
      ? 'По выбранным фильтрам ничего нет.' : 'Очередь пуста.'}</p>}
    <details ref={archiveRef} className="dispatch-archive" onToggle={event => {
      if (event.currentTarget.open) void refreshArchive();
    }}>
      <summary>Закрыто · архив заданий и обращений</summary>
      {archiveError ? <p className="error">{archiveError}</p> : archive ? (archive.length ? <ul>{archive.map(task =>
        <li key={task.id}>
          <strong>{task.address} · {workLabels[task.work_kind]}</strong> · {task.district} · {formatTime(task.closed_at)}
          {task.claimed_by && <p>Принял · {task.claimed_by}</p>}
          {task.result && <p>{task.result}</p>}
          <ResultPhotos ids={task.result_photo_ids} photoUrl={photoUrl} />
          {task.reports.map((report, index) => <p key={index}>
            {report.reference && <>Обращение № {report.reference} · </>}{report.description}
            {report.closed_at ? <> · Закрыто. {report.resolution}</> : <> · На исполнении</>}
          </p>)}
          <TaskHistory taskId={task.id} load={loadHistory} formatTime={formatTime} />
        </li>)}</ul> : <p className="muted">Закрытых заданий пока нет.</p>) : <p className="muted">Загружаю архив…</p>}
    </details>
    </section>
    {busObservations && <BusChecks data={busObservations} formatTime={formatTime} />}
  </div>;
}
