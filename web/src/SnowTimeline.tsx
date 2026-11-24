import { useId, useState, type CSSProperties } from 'react';
import { Icon } from './Icon';
import type { MapControls } from './OverviewMap';

const dateTime = new Intl.DateTimeFormat('ru', {
  timeZone: 'Asia/Kamchatka', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
});
const date = new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka', day: 'numeric', month: 'short' });
const time = new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka', hour: '2-digit', minute: '2-digit' });

export function SnowTimeline({ snow, archived = false, onMode, onFrame, onPlay }: {
  snow: NonNullable<MapControls['snow']>;
  archived?: boolean;
  onMode: (mode: string) => void;
  onFrame: (frame: number) => void;
  onPlay: () => void;
}) {
  const id = useId();
  const [near, setNear] = useState(false);
  const { frames, index, disabled, playing, minutes } = snow;
  const last = frames.length - 1;
  const hasTimeline = snow.mode !== 'drift_cm' || !disabled;
  const longRange = !!minutes && (minutes.at(-1) ?? 0) > 360;
  const nearEnd = longRange ? minutes.indexOf(180) : last;
  const zoomed = longRange && near && !playing && index <= nearEnd;
  const lastShown = zoomed ? nearEnd : last;
  const offsets = minutes ?? frames.map((_, step) => step);
  const max = offsets[lastShown] ?? 0;
  const current = offsets[index] ?? 0;
  const frameTime = frames[index] ? dateTime.format(new Date(frames[index])) : 'Кадр ' + (index + 1);
  const start = frames[0] ? date.format(new Date(frames[0])) : '';
  const end = frames[lastShown] ? date.format(new Date(frames[lastShown])) : '';
  const frameInterval = frames[0] && frames[1] ? Date.parse(frames[1]) - Date.parse(frames[0]) : 0;
  const hourlyStep = frameInterval > 0 && frameInterval < 3600000 && 3600000 % frameInterval === 0
    ? 3600000 / frameInterval : 0;
  const fullRange = longRange && !zoomed;
  const tickFormat = fullRange || (!hourlyStep && start && end && start !== end) ? date : time;
  const labelStep = !fullRange && hourlyStep ? hourlyStep : Math.max(1, Math.ceil(lastShown / 4));
  const labelInterval = Math.ceil(max / (4*1440)) * 1440;
  const progress = max > 0 ? current / max * 100 : 0;
  return <div className="ops-snow-time">
    <div className="ops-snow-heading">
      <select aria-label="Слой модели снега" value={snow.mode} onChange={e => onMode(e.target.value)}>
        <option value="snow_cm">Свежий снег</option>
        <option value="drift_cm">Наносы</option>
        <option value="volume">Снег в объёме</option>
      </select>
      <output htmlFor={id}>{last >= 0 && hasTimeline ? frameTime : 'Итог расчёта'}</output>
    </div>
    {lastShown > 0 && hasTimeline && <div className="ops-snow-player">
      <button className="ops-topbar-button" type="button" disabled={disabled} aria-pressed={playing}
        aria-label={playing ? 'Пауза' : 'Воспроизвести расчёт'} onClick={() => { setNear(false); onPlay(); }}>
        <Icon name={playing ? 'pause' : 'play'} width="15" height="15" />
      </button>
      <div className="ops-snow-ruler">
        <div className="ops-snow-ticks" aria-hidden="true">
          {frames.slice(0, lastShown+1).map((stamp, step) => {
            const offset = offsets[step];
            const endpoint = step === 0 || step === lastShown;
            if (fullRange && offset > 0 && offset < 180) return null;
            const major = endpoint || (fullRange
              ? offset % labelInterval === 0 && max-offset > labelInterval*.55
              : step % labelStep === 0 && lastShown-step >= labelStep);
            return <span key={step} className={major ? 'is-major' : undefined}
              style={{ left: offset / max * 100 + '%' }}>
              {major && <small>{stamp ? tickFormat.format(new Date(stamp)) : step + 1}</small>}
            </span>;
          })}
        </div>
        <input id={id} type="range" min={0} max={max} value={current} disabled={disabled}
          style={{ '--snow-progress': progress + '%' } as CSSProperties}
          aria-label="Время расчёта на карте" aria-valuetext={frameTime}
          onChange={e => {
            const target = e.target.valueAsNumber;
            const nearest = offsets.slice(0, lastShown+1).reduce((best, offset, step) =>
              Math.abs(offset-target) < Math.abs(offsets[best]-target) ? step : best, 0);
            if (nearest !== index) onFrame(nearest);
          }}
          onKeyDown={event => {
            const step = { ArrowLeft: index-1, ArrowDown: index-1, ArrowRight: index+1,
              ArrowUp: index+1, Home: 0, End: lastShown }[event.key];
            if (step === undefined) return;
            event.preventDefault();
            onFrame(Math.max(0, Math.min(lastShown, step)));
          }} />
      </div>
    </div>}
    <div className="ops-snow-caption">
      <small className="ops-snow-scale">{snow.scale}</small>
      {longRange && hasTimeline && <button type="button" aria-pressed={zoomed} onClick={() => {
        if (!zoomed && index > nearEnd) onFrame(nearEnd);
        setNear(!zoomed);
      }}>{zoomed ? (archived ? 'Весь период' : 'Весь прогноз') : 'Первые 3 ч'}</button>}
    </div>
  </div>;
}
