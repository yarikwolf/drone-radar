import {
  STATUS_META,
  THREAT_META,
  eventHeadline,
  formatDate,
  formatDateTime,
  formatTime,
  relativeTime
} from "../lib/format";

const KIND_LABEL = {
  telegram: "Telegram",
  rss: "RSS",
  official_api: "Официальный API",
  demo: "Демо",
  webhook: "Webhook"
};

function sourceHref(url) {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function EventItem({ alert, onSelect }) {
  const statusKey = STATUS_META[alert.status] ? alert.status : "cleared";
  const status = STATUS_META[statusKey];
  const threat = THREAT_META[alert.threat_type] || THREAT_META.unknown;
  const sources = alert.sources || [];
  const places = (alert.places || []).filter((place) => place && place.name);
  // Первый населённый пункт уже есть в заголовке — остальные показываем чипами.
  const headline = eventHeadline(alert);
  const primaryPlace = places[0]?.name;
  const extraPlaces = places.filter((place) => place.name !== primaryPlace);

  return (
    <article className={`event event--${statusKey}`} style={{ "--event-color": status.color }}>
      <button
        type="button"
        className="event__main"
        onClick={() => onSelect(alert)}
        title="Показать на карте"
      >
        <span className="event__rail">
          <span className="event__dot" />
        </span>
        <span className="event__content">
          <span className="event__top">
            <span className="event__region">
              {alert.region?.name || "Регион не определён"}
            </span>
            <span className="badge">{status.short}</span>
          </span>
          <span className="event__title">{headline}</span>
          {extraPlaces.length > 0 && (
            <span className="event__places">
              {extraPlaces.map((place) => (
                <span className="event__place" key={place.name}>
                  <i aria-hidden="true">◎</i>
                  {place.name}
                </span>
              ))}
            </span>
          )}
          <span className="event__meta">
            <span className="event__threat">
              <i aria-hidden="true">{threat.icon}</i>
              {threat.label}
            </span>
            <span>{formatTime(alert.event_at)}</span>
            <span className="event__muted">{relativeTime(alert.event_at)}</span>
            {alert.is_demo && <span className="demo-tag">DEMO</span>}
          </span>
        </span>
        <span className="event__count" title="Сколько сообщений подтвердили событие">
          {alert.merged_count}
        </span>
      </button>

      <details className="event__details">
        <summary>
          Детали и источники
          <span>{sources.length}</span>
        </summary>

        <div className="event__sources">
          {sources.map((source, index) => {
            const href = sourceHref(source.url);
            return (
              <div className="source" key={`${source.external_message_id}-${index}`}>
                <span className="source__kind">
                  {KIND_LABEL[source.source_kind] || source.source_kind}
                </span>
                <span className="source__name">{source.source_name}</span>
                <span className="source__time">{formatTime(source.posted_at)}</span>
                {href ? (
                  <a href={href} target="_blank" rel="noreferrer noopener">
                    Открыть
                  </a>
                ) : (
                  <span className="source__no-link">без ссылки</span>
                )}
              </div>
            );
          })}
          {sources.length === 0 && <p className="event__muted">Источники не прикреплены</p>}
        </div>

        <div className="event__foot">
          <span>Событие #{alert.id}</span>
          <span>
            {formatDate(alert.event_at)} · обновлено {formatDateTime(alert.last_seen_at)}
          </span>
        </div>
      </details>
    </article>
  );
}

function Skeleton() {
  return (
    <div className="skeleton-list" aria-hidden="true">
      {[0, 1, 2, 3, 4].map((item) => (
        <div className="skeleton" key={item}>
          <span className="skeleton__dot" />
          <span className="skeleton__lines">
            <i />
            <i />
            <i />
          </span>
        </div>
      ))}
    </div>
  );
}

export default function Timeline({ alerts, loading, onSelect, onClose }) {
  return (
    <div className="timeline">
      <div className="timeline__head">
        <div>
          <h2>Лента событий</h2>
          <p>{alerts.length} записей за выбранный период</p>
        </div>
        <div className="timeline__tools">
          <span className="timeline__auto">автообновление</span>
          {onClose && (
            <button
              type="button"
              className="timeline__close"
              onClick={onClose}
              aria-label="Свернуть ленту событий"
              title="Свернуть ленту"
            >
              ×
            </button>
          )}
        </div>
      </div>

      <div className="timeline__scroll">
        {loading && alerts.length === 0 && <Skeleton />}

        {!loading && alerts.length === 0 && (
          <div className="timeline__empty">
            <span>◌</span>
            <strong>Событий не найдено</strong>
            <p>Попробуйте увеличить период или выбрать другой регион.</p>
          </div>
        )}

        <div className="timeline__list">
          {alerts.map((alert) => (
            <EventItem key={alert.id} alert={alert} onSelect={onSelect} />
          ))}
        </div>
      </div>
    </div>
  );
}
