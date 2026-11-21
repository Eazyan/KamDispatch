import type { Delivery, Period } from './types';

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
