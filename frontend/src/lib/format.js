export const STATUS_META = {
  active: { label: "Активная угроза", short: "Тревога", color: "#ff4d6d" },
  warning: { label: "Предупреждение", short: "Внимание", color: "#ffc23d" },
  all_clear: { label: "Отбой", short: "Отбой", color: "#2fe08a" },
  cleared: { label: "Завершено", short: "Завершено", color: "#7c8798" }
};

export const THREAT_META = {
  drone: { label: "БПЛА", icon: "◈" },
  missile: { label: "Ракета", icon: "▲" },
  mixed: { label: "Смешанная", icon: "✦" },
  unknown: { label: "Тревога", icon: "●" }
};

export const THREAT_FILTERS = [
  { value: "", label: "Все" },
  { value: "drone", label: "БПЛА" },
  { value: "missile", label: "Ракеты" },
  { value: "mixed", label: "Смешанная" }
];

export const PERIODS = [
  { value: 1, label: "1 ч" },
  { value: 6, label: "6 ч" },
  { value: 24, label: "24 ч" },
  { value: 72, label: "3 дня" }
];

const timeFmt = new Intl.DateTimeFormat("ru-RU", {
  hour: "2-digit",
  minute: "2-digit"
});

const dateFmt = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit"
});

const fullFmt = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit"
});

export function formatTime(value) {
  if (!value) return "—";
  return timeFmt.format(new Date(value));
}

export function formatDate(value) {
  if (!value) return "—";
  return dateFmt.format(new Date(value));
}

export function formatDateTime(value) {
  if (!value) return "—";
  return fullFmt.format(new Date(value));
}

export function relativeTime(value) {
  if (!value) return "—";
  const diff = Math.max(0, Date.now() - new Date(value).getTime());
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "только что";
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ч назад`;
  const days = Math.floor(hours / 24);
  return `${days} дн назад`;
}

export function latestByRegion(alerts) {
  const map = new Map();
  for (const alert of alerts) {
    if (!alert.region) continue;
    const current = map.get(alert.region.code);
    if (!current || new Date(alert.event_at) > new Date(current.event_at)) {
      map.set(alert.region.code, alert);
    }
  }
  return Array.from(map.values());
}
