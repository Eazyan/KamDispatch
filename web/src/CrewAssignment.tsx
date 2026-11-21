import { useEffect, useState } from 'react';
import { RoadPicker } from './RoadPicker';
import { taskStages, TaskProgress, workLabels } from './TaskProgress';
import type { CrewTask, ProgressDraft } from './types';

const dateTime = new Intl.DateTimeFormat('ru', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Asia/Kamchatka' });

async function request<T>(hash: string, draft?: ProgressDraft): Promise<T> {
  const credentials = /^#([0-9a-f-]{36})\.([a-zA-Z0-9_-]{43})$/.exec(hash);
  if (!credentials) throw new Error('Откройте ссылку на задание, которую передал диспетчер.');
  const response = await fetch('/api/v1/crew/tasks/' + credentials[1], {
    method: draft ? 'PUT' : 'GET',
    headers: { Authorization: 'Bearer ' + credentials[2], 'Content-Type': 'application/json' },
    body: draft && JSON.stringify(draft),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === 'string' ? data.detail : 'Не удалось открыть задание.');
  return data;
}

export function CrewAssignment() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const sync = () => setHash(location.hash);
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);
  return <CrewTaskView key={hash} hash={hash} />;
}

function CrewTaskView({ hash }: { hash: string }) {
  const [task, setTask] = useState<CrewTask>();
  const [error, setError] = useState('');
  const [showMap, setShowMap] = useState(false);

  async function refresh() {
    try {
      setTask(await request<CrewTask>(hash));
      setError('');
    } catch (err) {
      setTask(undefined);
      setError((err as Error).message);
    }
  }

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 7000);
    return () => window.clearInterval(timer);
  }, []);

  return <main className="crew-page">
    <h1>Задание на выезд</h1>
    {error ? <p role="alert" className="error">{error}</p> : task ? <section className="panel crew-task">
      <p className={task.severity === 'urgent' ? 'urgent' : 'muted'}>{workLabels[task.work_kind]} · {task.severity === 'urgent' ? 'Срочно' : 'Обычный приоритет'}</p>
      <h2>{task.address}</h2>
      <p className="muted">{task.district}</p>
      <dl className="dispatch-task-facts">
        <div><dt>Исполнитель</dt><dd>{task.assignee}</dd></div>
        <div><dt>Статус</dt><dd>{taskStages[task.stage]}</dd></div>
        {task.due_at && <div><dt>Срок по Камчатке</dt><dd>{dateTime.format(new Date(task.due_at))}</dd></div>}
      </dl>
      {task.instructions && <p><strong>Указания диспетчера</strong> · {task.instructions}</p>}
      {task.map_section && <button type="button" className="quiet" onClick={() => setShowMap(!showMap)}>
        {showMap ? 'Скрыть карту' : 'Показать участок на карте'}
      </button>}
      {task.map_section && showMap && <RoadPicker selection={{ points: task.map_section.coordinates }} />}
      <TaskProgress task={task} dispatcher={false} onProgress={async draft => {
        await request(hash, draft);
        await refresh();
      }} />
    </section> : <p role="status">Загружаю задание…</p>}
  </main>;
}

type InboxItem = Pick<CrewTask, 'id' | 'address' | 'district' | 'work_kind' | 'severity' | 'stage' | 'due_at'> & { crew_token: string };

export function CrewInbox() {
  const [items, setItems] = useState<InboxItem[]>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    async function refresh() {
      try {
        const response = await fetch('/api/v1/dispatch/crew-inbox', { signal: controller.signal });
        const data = await response.json().catch(() => null);
        if (!response.ok) throw new Error(data?.detail ?? 'Не удалось загрузить задания.');
        setItems(data);
        setError('');
      } catch (err) {
        if (!controller.signal.aborted) setError((err as Error).message);
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, 7000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  return <main className="crew-page">
    <h1>Мои задания</h1>
    {error ? <p className="error" role="alert">{error}</p> : items ? (items.length ?
      <ul className="crew-inbox">{items.map(item => <li className="panel crew-task" key={item.id}>
        <h2><a href={'/assignment#' + item.id + '.' + item.crew_token}>{item.address}</a></h2>
        <p>{item.district} · {workLabels[item.work_kind]} · {taskStages[item.stage]}</p>
        {item.severity === 'urgent' && <p className="urgent">Срочно</p>}
        {item.due_at && <p className="muted">Срок по Камчатке · {dateTime.format(new Date(item.due_at))}</p>}
      </li>)}</ul> : <p>Открытых заданий пока нет.</p>) : <p role="status">Загружаю задания…</p>}
  </main>;
}
