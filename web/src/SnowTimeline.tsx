import { useId, type CSSProperties } from 'react';
import { Icon } from './Icon';
import type { MapControls } from './OverviewMap';

const dateTime = new Intl.DateTimeFormat('ru', {
  timeZone: 'Asia/Kamchatka', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
});
const date = new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka', day: 'numeric', month: 'short' });
const time = new Intl.DateTimeFormat('ru', { timeZone: 'Asia/Kamchatka', hour: '2-digit', minute: '2-digit' });

export function SnowTimeline({ snow, onMode, onFrame, onPlay }: {
  snow: NonNullable<MapControls['snow']>;
  onMode: (mode: string) => void;
  onFrame: (frame: number) => void;
  onPlay: () => void;
}) {
  const id = useId();
  const { frames, index, disabled, playing } = snow;
  const last = frames.length - 1;
  const frameTime = frames[index] ? dateTime.format(new Date(frames[index])) : 'Кадр ' + (index + 1);
  const start = frames[0] ? date.format(new Date(frames[0])) : '';
  const end = frames[last] ? date.format(new Date(frames[last])) : '';
  const tickFormat = start && end && start !== end ? date : time;
  const labelStep = Math.max(1, Math.ceil(last / 4));
  const progress = last > 0 ? index / last * 100 : 0;
  return <div className="ops-snow-time">
    <div className="ops-snow-heading">
      <select aria-label="Слой модели снега" value={snow.mode} onChange={e => onMode(e.target.value)}>
        <option value="snow_cm">Свежий снег</option>
        <option value="drift_cm">Наносы</option>
        <option value="volume">Снег в объёме</option>
      </select>
      <output htmlFor={id}>{last >= 0 && snow.mode !== 'drift_cm' ? frameTime : 'Итог расчёта'}</output>
    </div>
    {last > 0 && snow.mode !== 'drift_cm' && <div className="ops-snow-player">
      <button className="ops-topbar-button" type="button" disabled={disabled} aria-pressed={playing}
        aria-label={playing ? 'Пауза' : 'Воспроизвести расчёт'} onClick={onPlay}>
        <Icon name={playing ? 'pause' : 'play'} width="15" height="15" />
      </button>
      <div className="ops-snow-ruler">
        <div className="ops-snow-ticks" aria-hidden="true">
          {frames.map((stamp, step) => {
            const endpoint = step === 0 || step === last;
            const major = endpoint || (step % labelStep === 0 && last - step >= labelStep);
            return <span key={step} className={major ? 'is-major' : undefined}
              style={{ left: step / last * 100 + '%' }}>
              {major && <small>{stamp ? tickFormat.format(new Date(stamp)) : step + 1}</small>}
            </span>;
          })}
        </div>
        <input id={id} type="range" min={0} max={last} value={index} disabled={disabled}
          style={{ '--snow-progress': progress + '%' } as CSSProperties}
          aria-label="Время расчёта на карте" aria-valuetext={frameTime} onChange={e => onFrame(e.target.valueAsNumber)} />
      </div>
    </div>}
    <small className="ops-snow-scale">{snow.scale}</small>
  </div>;
}
