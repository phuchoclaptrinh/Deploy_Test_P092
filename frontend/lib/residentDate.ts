/** Date and time presentation for the Resident screens.
 *  Notifications and the report list both group by day, so the grouping and the
 *  labels live here rather than being written twice. */

const VI_MONTH_DAY = new Intl.DateTimeFormat("vi-VN", { day: "numeric", month: "long" });
const TIME = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit", hour12: false });
const DAY_MONTH = new Intl.DateTimeFormat("vi-VN", { day: "2-digit", month: "2-digit" });

const startOfDay = (value: Date) => new Date(value.getFullYear(), value.getMonth(), value.getDate());
const daysBetween = (from: Date, to: Date) => Math.round((startOfDay(to).getTime() - startOfDay(from).getTime()) / 86_400_000);

/** "08:07" */
export function formatTime(iso: string) {
  return TIME.format(new Date(iso));
}

/** "21:53 · 20/08" — the compact stamp used wherever a full date is needed. */
export function formatShortDateTime(iso: string) {
  const value = new Date(iso);
  return `${TIME.format(value)} · ${DAY_MONTH.format(value)}`;
}

const SCHEDULE_MOMENT = new Intl.DateTimeFormat("vi-VN", {
  timeZone: "Asia/Ho_Chi_Minh",
  day: "numeric",
  month: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** "09:23 · 27 Th8" — a concrete operational deadline in Vietnam time. */
export function formatScheduleMoment(iso: string) {
  const value = new Date(iso);
  const parts = Object.fromEntries(SCHEDULE_MOMENT.formatToParts(value).map((part) => [part.type, part.value]));
  return `${parts.hour}:${parts.minute} · ${parts.day} Th${parts.month}`;
}

/** "Trước 19:51 hôm nay", "Trước 19:51 ngày mai", then "Trước 19:51 20 Th8".
 *  A date a resident can plan around, rather than a duration they have to add up. */
export function formatDeadline(iso: string, now = new Date()) {
  const value = new Date(iso);
  const time = TIME.format(value);
  const distance = daysBetween(now, value);
  if (distance === 0) return `Trước ${time} hôm nay`;
  if (distance === 1) return `Trước ${time} ngày mai`;
  return `Trước ${time} ${value.getDate()} Th${value.getMonth() + 1}`;
}

/** "Hôm nay", "Hôm qua", then "20 tháng 8". */
export function dateGroupLabel(iso: string, now = new Date()) {
  const value = new Date(iso);
  const distance = daysBetween(value, now);
  if (distance === 0) return "Hôm nay";
  if (distance === 1) return "Hôm qua";
  return VI_MONTH_DAY.format(value);
}

export type DateGroup<T> = { key: string; label: string; items: T[] };

/** Group newest-first by calendar day, preserving the order items arrive in.
 *  The caller sorts; this only buckets, so a backend-sorted list keeps its order. */
export function groupByDate<T>(items: T[], getIso: (item: T) => string, now = new Date()): Array<DateGroup<T>> {
  const groups = new Map<string, DateGroup<T>>();
  for (const item of items) {
    const iso = getIso(item);
    const value = new Date(iso);
    const key = `${value.getFullYear()}-${value.getMonth()}-${value.getDate()}`;
    const group = groups.get(key);
    if (group) group.items.push(item);
    else groups.set(key, { key, label: dateGroupLabel(iso, now), items: [item] });
  }
  return [...groups.values()];
}
