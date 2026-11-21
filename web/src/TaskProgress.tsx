import { useState, type FormEvent } from 'react';
import type { HistoryItem, ProgressDraft, TaskAction, TaskStage, WorkKind } from './types';

export const taskStages: Record<TaskStage, string> = {
  draft: 'Без исполнителя', sent: 'Направлено', accepted: 'Принято исполнителем',
  in_progress: 'В работе', done: 'Выполнено', returned: 'На доработке', closed: 'Закрыто',
};
export const workLabels: Record<WorkKind, string> = { inspect: 'Проверка', clear: 'Уборка' };
const actions: Record<TaskAction, string> = {
  accept: 'Подтвердить получение', start: 'Начать работу', finish: 'Передать результат',
  return: 'Вернуть на доработку', close: 'Принять и закрыть', followup: 'Создать уборку по итогам проверки',
};
const historyLabels: Record<HistoryItem['action'], string> = {
  ...actions, created: 'Создано', assignment: 'Направлено исполнителю', accept: 'Получение подтверждено',
  start: 'Работа начата', finish: 'Получен результат', return: 'Возвращено на доработку',
  close: 'Принято и закрыто', followup: 'Назначена уборка по итогам проверки',
  link_report: 'Привязано обращение',
};

export function TaskProgress({ task, dispatcher, onProgress }: {
  task: { stage: TaskStage; work_kind: WorkKind; assignment_version: number; result: string; return_note: string | null };
  dispatcher: boolean; onProgress?: (draft: ProgressDraft) => Promise<void>;
}) {
  const [writing, setWriting] = useState<TaskAction>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const available: TaskAction[] = [];
  if (task.stage === 'sent') available.push('accept');
  if (task.stage === 'accepted' || task.stage === 'returned') available.push('start');
  if (task.stage === 'in_progress') available.push('finish');
  if (task.stage === 'done' && dispatcher) {
    available.push('close', 'return');
    if (task.work_kind === 'inspect') available.push('followup');
  }

  async function save(action: TaskAction, note = '') {
    if (!onProgress) return;
    setBusy(true);
    setError('');
    try {
      await onProgress({ action, note, version: task.assignment_version });
      setWriting(undefined);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const note = String(new FormData(event.currentTarget).get('note')).trim();
    if (writing && note) void save(writing, note);
  }

  return <>
    {task.result && <p><strong>Результат работ</strong> · {task.result}</p>}
    {task.return_note && task.stage === 'returned' && <p><strong>Причина доработки</strong> · {task.return_note}</p>}
    {onProgress && writing && available.includes(writing) ? <form className="dispatch-progress-form" onSubmit={submit} key={writing}>
      <label>{writing === 'finish' ? 'Результат работ' : writing === 'return' ? 'Причина доработки' :
        writing === 'followup' ? 'Что нужно убрать' : 'Итог для обращения'}
        <textarea name="note" required maxLength={1500} rows={3} defaultValue={writing === 'close' ? task.result : ''} />
      </label>
      <div className="dispatch-controls">
        <button type="submit" disabled={busy}>{actions[writing]}</button>
        <button type="button" className="quiet" disabled={busy} onClick={() => setWriting(undefined)}>Отмена</button>
      </div>
    </form> : onProgress && available.length > 0 && <div className="dispatch-controls">
      {available.map(action => <button type="button" key={action} disabled={busy} onClick={() => {
        if (action === 'accept' || action === 'start') void save(action);
        else { setError(''); setWriting(action); }
      }}>{actions[action]}</button>)}
    </div>}
    {error && <p className="error" role="alert">{error}</p>}
  </>;
}

export function TaskHistory({ taskId, load, formatTime }: {
  taskId: string; load: (id: string) => Promise<HistoryItem[]>; formatTime: (value: string) => string;
}) {
  const [items, setItems] = useState<HistoryItem[]>();
  const [error, setError] = useState('');
  return <details className="dispatch-history" onToggle={event => {
    if (event.currentTarget.open) {
      setError('');
      void load(taskId).then(setItems).catch(err => setError((err as Error).message));
    }
  }}>
    <summary>История задания</summary>
    {error ? <p className="error">{error}</p> : items ? (items.length ? <ol>{items.map((item, index) =>
      <li key={item.created_at + index}><strong>{workLabels[item.work_kind]} · {historyLabels[item.action]}</strong>
        {' · '}{formatTime(item.created_at)} · {item.actor}
        {item.assignee && <> · {item.assignee}</>}{item.due_at && <> · до {formatTime(item.due_at)}</>}
        {item.note && <p>{item.note}</p>}
      </li>)}</ol> : <p className="muted">История пока пуста.</p>) : <p className="muted">Загружаю историю…</p>}
  </details>;
}
