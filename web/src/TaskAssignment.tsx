import { useId, useState, type FormEvent } from 'react';
import { TaskHistory, TaskProgress, taskStages } from './TaskProgress';
import type { AssignmentDraft, DispatchTask, HistoryItem, ProgressDraft } from './types';

const kamchatkaInput = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Kamchatka', dateStyle: 'short', timeStyle: 'short',
});

export function TaskAssignment({ task, operator, assignees, onAssign, onProgress, onLink, loadHistory, formatTime }: {
  task: DispatchTask; operator: boolean; onAssign: (id: string, draft: AssignmentDraft) => Promise<void>;
  assignees: string[];
  onProgress: (id: string, draft: ProgressDraft) => Promise<void>;
  onLink: (id: string) => Promise<string>; loadHistory: (id: string) => Promise<HistoryItem[]>;
  formatTime: (value: string) => string;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [link, setLink] = useState<{ version: number; url: string }>();
  const assigneeList = useId();

  async function update(action: () => Promise<void>) {
    setBusy(true);
    setError('');
    try {
      await action();
      setEditing(false);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    const assignee = String(fields.get('assignee')).trim();
    const deadline = String(fields.get('due'));
    if (!assignee || !deadline) return;
    void update(() => onAssign(task.id, { version: task.assignment_version, assignee, due_at: deadline + ':00+12:00',
      instructions: String(fields.get('instructions')).trim() }));
  }

  return <div className="dispatch-assignment">
    {task.assignee && <dl className="dispatch-task-facts">
      <div><dt>Исполнитель</dt><dd>{task.assignee}</dd></div>
      {task.claimed_by && <div><dt>Принял</dt><dd>{task.claimed_by}</dd></div>}
      {task.due_at && <div><dt>Срок</dt><dd>{formatTime(task.due_at)}{task.overdue && <strong className="urgent"> · Просрочено</strong>}</dd></div>}
      <div><dt>Получение</dt><dd>{task.accepted_at ? 'Подтверждено · ' + formatTime(task.accepted_at) : 'Ждёт подтверждения'}</dd></div>
      {task.response_seconds !== null && <div><dt>От сигнала до назначения</dt><dd>{Math.floor(task.response_seconds / 60)} мин {task.response_seconds % 60} сек</dd></div>}
    </dl>}
    {task.instructions && <p><strong>Указания</strong> · {task.instructions}</p>}
    <p><strong>Статус</strong> · {taskStages[task.stage]}</p>
    {task.work_in_bot && task.stage !== 'done' && <p>Бригада передаёт результат в боте.</p>}
    {operator && task.stage !== 'done' && (editing ? <form className="dispatch-progress-form" onSubmit={submit} key={task.assignment_version}>
      <fieldset disabled={busy}>
        <legend>Назначение исполнителя</legend>
        <div className="dispatch-fields">
          <label>Исполнитель<input name="assignee" required maxLength={150} defaultValue={task.assignee ?? ''}
            list={assignees.length ? assigneeList : undefined} /></label>
          {assignees.length > 0 && <datalist id={assigneeList}>{assignees.map(name => <option key={name} value={name} />)}</datalist>}
          <label>Срок по Камчатке<input name="due" type="datetime-local" required
            defaultValue={task.due_at ? kamchatkaInput.format(new Date(task.due_at)).replace(' ', 'T') : ''} /></label>
          <label className="dispatch-wide">Указания исполнителю<textarea name="instructions" rows={3} maxLength={1500}
            defaultValue={task.instructions} /></label>
        </div>
        <div className="dispatch-controls">
          <button type="submit">{task.assignee ? 'Сохранить назначение' : 'Выдать задание'}</button>
          <button type="button" className="quiet" onClick={() => setEditing(false)}>Отмена</button>
        </div>
      </fieldset>
    </form> : <div className="dispatch-controls">
      <button type="button" className={task.assignee ? 'quiet' : undefined} disabled={busy}
        onClick={() => { setError(''); setEditing(true); }}>{task.assignee ? 'Изменить назначение' : 'Назначить исполнителя'}</button>
      {task.assignee && !task.work_in_bot && <button type="button" className="quiet" disabled={busy} onClick={() => void update(async () => {
        const token = await onLink(task.id);
        const url = new URL('/assignment#' + task.id + '.' + token, location.origin).href;
        setLink({ version: task.assignment_version, url });
        await navigator.clipboard?.writeText(url).catch(() => {});
      })}>Ссылка для исполнителя</button>}
    </div>)}
    {link?.version === task.assignment_version && <label className="dispatch-share">Ссылка на задание
      <input readOnly value={link.url} onFocus={event => event.target.select()} />
    </label>}
    <TaskProgress task={task} dispatcher onProgress={operator && !editing ? draft => onProgress(task.id, draft) : undefined} />
    <TaskHistory taskId={task.id} load={loadHistory} formatTime={formatTime} />
    {error && <p className="error" role="alert">{error}</p>}
  </div>;
}
