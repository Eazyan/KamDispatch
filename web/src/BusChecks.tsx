import { useState } from 'react';
import type { BusObservation, BusObservations } from './types';

const reasons: Record<NonNullable<BusObservation['reason']>, string> = {
  buses: 'Две или более машин замедлились',
  camera: 'Замедление подтверждено камерой',
  report: 'Замедление подтверждено обращением',
};

function duration(seconds: number | null) {
  if (seconds == null) return '—';
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, '0')}`;
}

export function BusChecks({ data, formatTime }: { data: BusObservations; formatTime: (at: string) => string }) {
  const [selected, setSelected] = useState('');
  const scenarios = data.source === 'simulation' ? data.scenarios : [];
  const scenario = scenarios.find(item => item.id === selected) ?? scenarios[0];
  const asOf = data.source === 'rnis' ? data.as_of : scenario?.as_of;
  const rows = [...(data.source === 'rnis' ? data.rows : scenario?.rows ?? [])]
    .sort((a, b) => Number(b.status === 'check') - Number(a.status === 'check'));
  const source = data.source === 'simulation' ? 'Имитация' : {
    ready: 'РНИС', insufficient: 'РНИС · Нет свежих GPS-точек', unavailable: 'РНИС · Источник недоступен',
  }[data.state];
  if (!rows.length) return null;

  return <section className="panel bus-checks" aria-labelledby="bus-checks-heading">
    <div className="panel-head bus-checks-heading">
      <div><h2 id="bus-checks-heading">Участки для проверки</h2>
        <p>Автобусные проезды · {source}{asOf && ' · ' + formatTime(asOf)}</p></div>
      {scenario && <label>Сценарий <select value={scenario.id} onChange={event => setSelected(event.target.value)}>
        {scenarios.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select></label>}
    </div>
    <ul className="bus-checks-list">
      {rows.map(row => <li key={row.section_id + '/' + row.direction}>
        <div><strong>{row.name}</strong><span className="muted">{row.direction_label}</span></div>
        <div><strong className={row.status === 'check' ? 'bus-check-active' : undefined}>
          {row.status === 'check' ? 'Проверить участок' : row.status === 'observed' ? 'Нет подтверждения' : 'Недостаточно данных'}
        </strong>{row.reason && <span className="muted">{reasons[row.reason]}</span>}</div>
        <div><strong>{duration(row.usual_seconds)} → {duration(row.observed_seconds)}</strong>
          <span className="muted">Обычно → наблюдение, мин:сек</span></div>
        <div><strong>Машин: {row.vehicle_count}</strong>
          <span className="muted">{row.last_seen ? formatTime(row.last_seen) : 'Свежих проездов нет'}</span></div>
      </li>)}
    </ul>
  </section>;
}
