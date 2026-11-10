// Тонкий клиент API. В dev запросы идут через Vite-прокси,
// в Docker — через nginx, поэтому по умолчанию используем относительные пути.

const RAW_BASE = (import.meta.env.VITE_API_URL || "").replace(/\/$/, "");

function buildUrl(path, params = {}) {
  const url = new URL(`${RAW_BASE}${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, String(value));
    }
  }
  return url;
}

async function request(path, params) {
  const response = await fetch(buildUrl(path, params), {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch {
      // ignore
    }
    throw new Error(detail);
  }
  return response.json();
}

export function fetchAlerts(filters = {}) {
  return request("/api/alerts", {
    region: filters.region,
    threat: filters.threat,
    hours: filters.hours ?? 24,
    limit: 200
  });
}

export function fetchRegions() {
  return request("/api/regions");
}

export function getAlertsSocketUrl() {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;

  if (RAW_BASE) {
    const url = new URL(`${RAW_BASE}/api/ws/alerts`, window.location.origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    return url.toString();
  }

  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/ws/alerts`;
}
