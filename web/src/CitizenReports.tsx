import { useEffect, useState, type FormEvent } from 'react';
import { Icon } from './Icon';
import './citizens.css';

export type Report = {
  id: string;
  description: string;
  district: string | null;
  address: string;
  latitude: number;
  longitude: number;
  created_at: string;
  tags: string[];
  suggested_tags: string[];
  photo_url: string;
};
type Page = { items: Report[]; next_offset: number | null };
type District = { id: string; name: string | null; parent_id?: string };
type Result = { page?: Page; error?: string; signIn?: boolean };
const dateFormat = new Intl.DateTimeFormat('ru', {
  dateStyle: 'short', timeStyle: 'short', timeZone: 'Asia/Kamchatka',
});
const reportsPath = '/api/v1/citizens/reports';
const headers = (token: string) => token ? { Authorization: 'Bearer ' + token } : undefined;
const localMidnight = (day: string) => day + 'T00:00:00+12:00';

async function fetchReports(query: string, token: string, signal: AbortSignal): Promise<Result> {
  try {
    const response = await fetch(reportsPath + '?' + query, { headers: headers(token), signal });
    if (response.status === 401) return { signIn: true, error: 'Требуется вход сотрудника.' };
    if (response.status === 403) return { signIn: true, error: 'Нет доступа к обращениям.' };
    if (response.status === 422) return { error: 'Проверьте даты выбранного периода.' };
    if (!response.ok) return { error: 'Не удалось загрузить обращения.' };
    return { page: await response.json() as Page };
  } catch {
    return { error: 'Не удалось загрузить обращения.' };
  }
}

function useReports(query: string, token: string, refresh: number, poll = false, enabled = true) {
  const [result, setResult] = useState<Result>({});
  useEffect(() => {
    setResult({});
    if (!enabled) return;
    const controller = new AbortController();
    let timer: number;
    async function load() {
      const next = await fetchReports(query, token, controller.signal);
      if (controller.signal.aborted) return;
      setResult(previous => next.error && !next.signIn && previous.page
        ? { ...next, page: previous.page } : next);
      if (poll && !next.signIn) timer = window.setTimeout(load, 10000);
    }
    void load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query, token, refresh, poll, enabled]);
  return result;
}

function ReportTime({ value }: { value: string }) {
  return <time dateTime={value}>{dateFormat.format(new Date(value))}</time>;
}

export function useRecentCitizenReports(token: string, enabled: boolean) {
  return useReports('limit=3', token, 0, true, enabled);
}

export function RecentCitizenReports({ result, onOpen }: { result: Result; onOpen: (reportId?: string) => void }) {
  const { page, error } = result;
  return <section className="overview-card mood-amber citizen-recent" aria-labelledby="citizen-recent-title">
    <h3 id="citizen-recent-title" className="card-title">
      <span className="card-kind"><Icon name="reports" width="16" height="16" />Новые обращения жителей</span>
    </h3>
    {error && <p className="card-body citizen-recent-status" role="status">{error}</p>}
    {!error && !page && <p className="card-body citizen-recent-status" role="status">Загружаем обращения…</p>}
    {page?.items.length === 0 && <p className="card-body citizen-recent-status">Обращений пока нет.</p>}
    {page?.items.length ? <ul>{page.items.map(report => <li key={report.id}>
      <a className="card-main" href="#citizens" onClick={() => onOpen(report.id)}>
        <span className="card-kicker">
          <span>{report.district ?? 'Район не определён'}</span><ReportTime value={report.created_at} />
        </span>
        <span className="citizen-recent-entry">
          <span className="card-body citizen-recent-description">{report.description}</span>
          <Icon name="chevron" width="15" height="15" />
        </span>
      </a>
    </li>)}</ul> : null}
    <div className="card-actions"><a href="#citizens" onClick={() => onOpen()}>Все обращения</a></div>
  </section>;
}

function ReportPhoto({ report, token }: { report: Report; token: string }) {
  const [photo, setPhoto] = useState('');
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = '';
    setPhoto('');
    setFailed(false);
    fetch(report.photo_url, { headers: headers(token), signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error();
        return response.blob();
      }).then(blob => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setPhoto(objectUrl);
      }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [report.photo_url, token]);
  return photo ? <a href={photo} target="_blank" rel="noopener noreferrer">
    <img className="citizen-photo" src={photo} alt="Фотография к обращению" />
  </a> : <p role="status">{failed ? 'Фото недоступно.' : 'Загружаем фото…'}</p>;
}

function ReportCard({ report, token, initiallyOpen }: { report: Report; token: string; initiallyOpen: boolean }) {
  const [showPhoto, setShowPhoto] = useState(initiallyOpen);
  const { latitude, longitude } = report;
  const coordinates = latitude.toFixed(6) + ', ' + longitude.toFixed(6);
  return <article className="panel citizen-card">
    <div className="citizen-meta">
      <strong>{report.district ?? 'Район не определён'}</strong><ReportTime value={report.created_at} />
    </div>
    <p className="citizen-description">{report.description}</p>
    <p className="citizen-address">{report.address && <>{report.address} · </>}
      <a href={`https://www.openstreetmap.org/?mlat=${latitude}&mlon=${longitude}#map=17/${latitude}/${longitude}`}
        target="_blank" rel="noopener noreferrer">{coordinates}</a>
    </p>
    {report.tags.length > 0 && <p className="citizen-tags">Теги {report.tags.map(tag => '#' + tag).join(' ')}</p>}
    {report.suggested_tags.length > 0 && <p className="citizen-tags">Автоматические теги {report.suggested_tags.map(tag => '#' + tag).join(' ')}</p>}
    <button type="button" className="quiet" aria-expanded={showPhoto} onClick={() => setShowPhoto(!showPhoto)}>
      {showPhoto ? 'Скрыть фото' : 'Показать фото'}
    </button>
    {showPhoto && <ReportPhoto report={report} token={token} />}
  </article>;
}

export function CitizenReports({ token, onToken, selected }: {
  token: string; onToken: (value: string) => void; selected?: string;
}) {
  const [districts, setDistricts] = useState<District[]>([]);
  const [districtError, setDistrictError] = useState(false);
  const [filters, setFilters] = useState({ district: '', start: '', end: '' });
  const [activeReport, setActiveReport] = useState(selected);
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const limit = 20;
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (filters.district) query.set('district', filters.district);
  if (filters.start) query.set('start', localMidnight(filters.start));
  if (filters.end) query.set('end', new Date(Date.parse(localMidnight(filters.end)) + 86400000).toISOString());
  if (activeReport) query.set('id', activeReport);
  const { page, error, signIn } = useReports(query.toString(), token, refresh);
  useEffect(() => {
    const controller = new AbortController();
    setDistrictError(false);
    fetch('/api/v1/citizens/districts', { headers: headers(token), signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error();
        return response.json() as Promise<District[]>;
      }).then(items => { if (!controller.signal.aborted) setDistricts(items); })
      .catch(() => { if (!controller.signal.aborted) setDistrictError(true); });
    return () => controller.abort();
  }, [token, refresh]);

  function filter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setOffset(0);
    setActiveReport(undefined);
    setFilters({ district: String(data.get('district') ?? ''), start: String(data.get('start') ?? ''),
      end: String(data.get('end') ?? '') });
  }

  return <div className="page-stack citizen-page">
    {signIn ? <form className="panel citizen-filters" onSubmit={event => {
      event.preventDefault();
      const value = String(new FormData(event.currentTarget).get('token') ?? '').trim();
      if (!value) return;
      event.currentTarget.reset();
      onToken(value);
      setRefresh(n => n + 1);
    }}>
      <label>Токен OIDC сотрудника<input name="token" type="password" autoComplete="off" required /></label>
      <button>Войти</button>
    </form> : <form className="panel citizen-filters" onSubmit={filter}>
      <label>Район<select name="district" defaultValue={filters.district}>
        <option value="">Все районы</option>
        {districts.filter(district => !district.parent_id).map(district => {
          const children = districts.filter(item => item.parent_id === district.id);
          const name = district.name ?? 'Район не определён';
          return children.length ? <optgroup key={district.id} label={name}>
            <option value={district.id}>Весь городской округ</option>
            {children.map(child => <option key={child.id} value={child.id}>{child.name}</option>)}
          </optgroup> : <option key={district.id} value={district.id}>{name}</option>;
        })}
      </select></label>
      <label>С даты<input name="start" type="date" defaultValue={filters.start} /></label>
      <label>По дату<input name="end" type="date" defaultValue={filters.end} /></label>
      <button>Применить</button>
      <button type="button" className="quiet" onClick={() => { setOffset(0); setRefresh(n => n + 1); }}>
        <Icon name="refresh" width="16" height="16" /> Обновить
      </button>
    </form>}
    {activeReport && !signIn && <div><button type="button" className="quiet" onClick={() => setActiveReport(undefined)}>Все обращения</button></div>}
    {error && <p className="error" role="alert">{error}</p>}
    {!signIn && !error && districtError && <p role="status">Список районов недоступен.</p>}
    {!error && !page && <p role="status">Загружаем обращения…</p>}
    {page?.items.length === 0 && <p>По выбранным условиям обращений нет.</p>}
    {page?.items.map(report => <ReportCard key={report.id} report={report} token={token} initiallyOpen={activeReport === report.id} />)}
    {(offset > 0 || page?.next_offset != null) && <nav className="citizen-pagination" aria-label="Страницы обращений">
      <button type="button" className="quiet" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - limit))}>Назад</button>
      <button type="button" className="quiet" disabled={page?.next_offset == null} onClick={() => {
        if (page?.next_offset != null) setOffset(page.next_offset);
      }}>Далее</button>
    </nav>}
  </div>;
}
