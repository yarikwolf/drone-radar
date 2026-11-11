import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ControlPanel from "./components/ControlPanel";
import RadarMap from "./components/RadarMap";
import Timeline from "./components/Timeline";
import { fetchAlerts, fetchRegions, getAlertsSocketUrl } from "./api";
import { THREAT_META, formatTime, latestByRegion } from "./lib/format";

const DEFAULT_FILTERS = { region: "", threat: "", hours: 24 };

function initialNotifyState() {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission;
}

export default function App() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const [alerts, setAlerts] = useState([]);
  const [regions, setRegions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [connected, setConnected] = useState(false);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [focus, setFocus] = useState(null);
  const [notifyState, setNotifyState] = useState(initialNotifyState);

  const filtersRef = useRef(filters);
  const notifyRef = useRef(notifyState);
  const ticketRef = useRef(0);

  const latest = useMemo(() => latestByRegion(alerts), [alerts]);
  const counts = useMemo(
    () =>
      latest.reduce(
        (accumulator, alert) => {
          if (alert.status in accumulator) accumulator[alert.status] += 1;
          return accumulator;
        },
        { active: 0, warning: 0, all_clear: 0 }
      ),
    [latest]
  );

  const load = useCallback(async (silent = false) => {
    const ticket = ++ticketRef.current;
    if (!silent) setLoading(true);
    try {
      const data = await fetchAlerts(filtersRef.current);
      if (ticket !== ticketRef.current) return;
      setAlerts(data.items);
      setError("");
      setLastUpdated(new Date());
    } catch (cause) {
      if (ticket === ticketRef.current) {
        setError(cause?.message || "Не удалось загрузить данные");
      }
    } finally {
      if (ticket === ticketRef.current) setLoading(false);
    }
  }, []);

  // Справочник регионов загружается один раз.
  useEffect(() => {
    let alive = true;
    fetchRegions()
      .then((data) => {
        if (alive) setRegions(data.items);
      })
      .catch((cause) => {
        if (alive) setError(cause?.message || "Не удалось загрузить регионы");
      });
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    filtersRef.current = filters;
  }, [filters]);

  useEffect(() => {
    notifyRef.current = notifyState;
  }, [notifyState]);

  // Загрузка + мягкий polling как fallback для WebSocket.
  useEffect(() => {
    load();
    const timer = window.setInterval(() => load(true), 15000);
    return () => window.clearInterval(timer);
  }, [filters, load]);

  // WebSocket с exponential backoff.
  useEffect(() => {
    // На Vercel WebSocket не нужен: работает опрос API каждые 15 секунд.
    if (import.meta.env.VITE_DISABLE_WS === "1") return undefined;

    let socket = null;
    let stopped = false;
    let retry = 3000;
    let reconnectTimer = null;

    const notifyAboutAlert = (message) => {
      if (notifyRef.current !== "granted") return;
      if (message.status === "cleared") return;
      if (message.created === false) return;
      const current = filtersRef.current;
      if (current.region && message.region_code !== current.region) return;
      if (current.threat && message.threat_type !== current.threat) return;

      try {
        const title = message.status === "all_clear" ? "Отбой тревоги" : "Новая угроза";
        const threat = THREAT_META[message.threat_type]?.label || "Воздушная тревога";
        const notification = new Notification(title, {
          body: `${message.region_name}: ${threat}`,
          tag: `radar-alert-${message.alert_id}`,
          icon: "/radar.svg"
        });
        notification.onclick = () => window.focus();
      } catch {
        // Некоторые браузеры запрещают Notification вне service worker.
      }
    };

    const connect = () => {
      try {
        socket = new WebSocket(getAlertsSocketUrl());
      } catch {
        return;
      }

      socket.onopen = () => {
        if (stopped) {
          socket.close();
          return;
        }
        setConnected(true);
        retry = 3000;
      };

      socket.onmessage = (event) => {
        let message;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message.type === "alert") {
          load(true);
          notifyAboutAlert(message);
        }
      };

      socket.onclose = () => {
        setConnected(false);
        if (!stopped) {
          reconnectTimer = window.setTimeout(connect, retry);
          retry = Math.min(retry * 2, 30000);
        }
      };

      socket.onerror = () => {
        try {
          socket.close();
        } catch {
          // ignore
        }
      };
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
      setConnected(false);
    };
  }, [load]);

  const patchFilters = useCallback((patch) => {
    setFilters((previous) => ({ ...previous, ...patch }));
  }, []);

  const resetFilters = useCallback(() => setFilters({ ...DEFAULT_FILTERS }), []);

  const enableNotifications = useCallback(async () => {
    if (typeof window === "undefined" || !("Notification" in window)) {
      setNotifyState("unsupported");
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      setNotifyState(permission);
    } catch {
      setNotifyState("denied");
    }
  }, []);

  const selectAlert = useCallback((alert) => {
    if (!alert?.region) return;
    const place = (alert.places || []).find(
      (item) => item && item.lat != null && item.lon != null
    );
    setFocus({
      lat: place?.lat ?? alert.lat ?? alert.region.lat,
      lon: place?.lon ?? alert.lon ?? alert.region.lon,
      code: alert.region.code,
      place: place?.name ?? null,
      ts: Date.now()
    });
  }, []);

  // При выборе региона центрируем карту.
  useEffect(() => {
    if (!filters.region) return;
    const region = regions.find((item) => item.code === filters.region);
    if (region) {
      setFocus({
        lat: region.lat,
        lon: region.lon,
        code: region.code,
        ts: Date.now()
      });
    }
  }, [filters.region, regions]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand__radar" aria-hidden="true">
            <span className="brand__sweep" />
            <span className="brand__core" />
          </span>
          <div className="brand__text">
            <h1>Радар беспилотной опасности</h1>
            <p>Мониторинг воздушных тревог по открытым источникам</p>
          </div>
        </div>

        <div className="topbar__right">
          <span className={`live-pill ${connected ? "is-live" : "is-offline"}`} aria-live="polite">
            <span className="live-pill__dot" />
            {connected ? "LIVE" : "ПЕРЕПОДКЛЮЧЕНИЕ"}
          </span>
          <span className="topbar__updated">
            {lastUpdated ? `Обновлено ${formatTime(lastUpdated)}` : "Загрузка…"}
          </span>
        </div>
      </header>

      {error && (
        <div className="error-bar" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => load()}>
            Повторить
          </button>
        </div>
      )}

      <main className="layout">
        <ControlPanel
          filters={filters}
          onChange={patchFilters}
          onReset={resetFilters}
          regions={regions}
          counts={counts}
          notifyState={notifyState}
          onEnableNotifications={enableNotifications}
        />

        <section className="panel map-panel" aria-label="Карта тревог">
          <div className="panel-head">
            <div>
              <h2>Оперативная карта</h2>
              <p>Последний статус по каждому региону</p>
            </div>
            <span className="panel-head__count">
              <b>{latest.length}</b> / {regions.length || "—"}
            </span>
          </div>
          <RadarMap alerts={latest} focus={focus} onSelect={selectAlert} />
        </section>

        <section className="panel timeline-panel" aria-label="Лента событий">
          <Timeline alerts={alerts} loading={loading} onSelect={selectAlert} />
        </section>
      </main>

      <footer className="footer">
        <span>Данные из открытых источников · не официальное оповещение</span>
        <span>{connected ? "WebSocket подключён" : "Polling 15 секунд"}</span>
      </footer>
    </div>
  );
}
