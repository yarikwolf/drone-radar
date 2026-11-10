import { PERIODS, STATUS_META, THREAT_FILTERS } from "../lib/format";

function Stat({ status, value }) {
  const meta = STATUS_META[status];
  return (
    <div className="stat" style={{ "--stat-color": meta.color }}>
      <span className="stat__value">{value}</span>
      <span className="stat__label">
        <i className="dot" />
        {meta.short}
      </span>
    </div>
  );
}

export default function ControlPanel({
  filters,
  onChange,
  onReset,
  regions,
  counts,
  notifyState,
  onEnableNotifications
}) {
  const notifyLabel = {
    granted: "Уведомления включены",
    denied: "Уведомления заблокированы",
    unsupported: "Уведомления не поддерживаются",
    default: "Включить уведомления"
  }[notifyState];

  return (
    <aside className="panel controls" aria-label="Фильтры и легенда">
      <div className="controls__head">
        <h2>Фильтры</h2>
        <button type="button" className="link-button" onClick={onReset}>
          Сбросить
        </button>
      </div>

      <label className="field">
        <span className="field__label">Регион</span>
        <div className="select-wrap">
          <select
            value={filters.region}
            onChange={(event) => onChange({ region: event.target.value })}
          >
            <option value="">Все регионы</option>
            {regions.map((region) => (
              <option key={region.code} value={region.code}>
                {region.name}
              </option>
            ))}
          </select>
        </div>
      </label>

      <div className="field">
        <span className="field__label">Тип угрозы</span>
        <div className="segmented" role="group" aria-label="Тип угрозы">
          {THREAT_FILTERS.map((item) => (
            <button
              key={item.value || "all"}
              type="button"
              className={filters.threat === item.value ? "is-active" : ""}
              aria-pressed={filters.threat === item.value}
              onClick={() => onChange({ threat: item.value })}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <span className="field__label">Период</span>
        <div className="segmented segmented--period" role="group" aria-label="Период">
          {PERIODS.map((item) => (
            <button
              key={item.value}
              type="button"
              className={filters.hours === item.value ? "is-active" : ""}
              aria-pressed={filters.hours === item.value}
              onClick={() => onChange({ hours: item.value })}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="stats">
        <Stat status="active" value={counts.active} />
        <Stat status="warning" value={counts.warning} />
        <Stat status="all_clear" value={counts.all_clear} />
      </div>

      <div className="legend">
        <h3>Легенда</h3>
        {Object.entries(STATUS_META).map(([key, meta]) => (
          <div className="legend__row" key={key}>
            <span className="legend__dot" style={{ "--dot-color": meta.color }} />
            <span>{meta.label}</span>
          </div>
        ))}
      </div>

      <button
        type="button"
        className={`notify-button ${notifyState === "granted" ? "is-on" : ""}`}
        onClick={onEnableNotifications}
        disabled={notifyState !== "default"}
      >
        <span className="notify-button__bell" aria-hidden="true">
          ◉
        </span>
        {notifyLabel}
      </button>

      <p className="disclaimer">
        Данные из открытых источников. Не является официальным средством оповещения.
      </p>
    </aside>
  );
}
