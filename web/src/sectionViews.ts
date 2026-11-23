import type { Camera, Delivery, Period, Scene, TaskStage } from './types';

export const dispatchStages = {
  incoming: 'Поступило', working: 'В работе', review: 'На приёмке',
} as const;

export function dispatchStage(stage?: TaskStage) {
  if (!stage || stage === 'draft') return 'incoming';
  if (stage === 'done') return 'review';
  if (stage === 'closed') return 'closed';
  return 'working';
}

export function cameraRoadAssessment(camera: Camera, scene: Scene | null) {
  if (!camera.road) return { passage: 'Вне дорожного мониторинга', action: null };
  if (!scene?.format_valid || scene.view_quality === 'unusable') {
    return { passage: 'Нет актуальной оценки', action: 'Недостаточно данных' };
  }
  if (scene.road_coverage === 'not_visible' || scene.details?.road_view === 'unusable') {
    return { passage: 'Дорога не видна', action: 'Недостаточно данных' };
  }
  const obstruction = scene.details?.road_obstruction;
  const obstacle = obstruction === 'partial' || obstruction === 'major';
  const snowHazard = scene.road_coverage === 'mostly_covered' || scene.details?.snowdrift_on_road === 'yes';
  const passage = obstruction === 'major' ? 'Крупное препятствие в кадре'
    : obstruction === 'partial' ? 'Проезд может быть затруднён'
    : obstruction === 'none' ? 'Видимых препятствий нет'
    : 'Проезд не оценён';
  return {
    passage,
    action: obstacle || snowHazard ? 'Проверить участок'
      : obstruction === 'none' && scene.details?.road_view === 'good' &&
        scene.road_coverage === 'clear' ? 'Проверка по кадру не требуется'
      : 'Недостаточно данных',
  };
}

export function upcomingForecast(periods: Period[], from: number) {
  const duration = { hourly: 1, three_hourly: 3, daily: 24 };
  return periods.filter(period => period.horizon !== 'daily' &&
    Date.parse(period.at) + duration[period.horizon] * 3_600_000 > from)
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at)).slice(0, 12);
}

export function deliveryBucket(stage: Delivery['stage']) {
  if (stage === 'failed' || stage === 'ambiguous') return 'attention';
  if (stage === 'queued' || stage === 'sending') return 'pending';
  return 'recorded';
}
