-- Справочный DDL для PostgreSQL.
-- В приложении таблицы создаются автоматически через SQLAlchemy (AUTO_CREATE_TABLES=true).
-- Для production используйте Alembic; этот файл удобен для code review и ручной инициализации.
--
-- Время хранится как naive UTC (TIMESTAMP WITHOUT TIME ZONE), API отдаёт ISO 8601 с UTC.

CREATE TABLE IF NOT EXISTS regions (
    id            SERIAL PRIMARY KEY,
    code          VARCHAR(32)  NOT NULL UNIQUE,
    name          VARCHAR(160) NOT NULL,
    region_type   VARCHAR(24)  NOT NULL DEFAULT 'region',
    country       VARCHAR(8)   NOT NULL DEFAULT 'UA',
    lat           DOUBLE PRECISION NOT NULL,
    lon           DOUBLE PRECISION NOT NULL,
    aliases       JSON         NOT NULL DEFAULT '[]'::json,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP    NOT NULL DEFAULT (now() AT TIME ZONE 'utc')
);

CREATE TABLE IF NOT EXISTS sources (
    id            SERIAL PRIMARY KEY,
    kind          VARCHAR(24)  NOT NULL,
    external_id   VARCHAR(255) NOT NULL,
    name          VARCHAR(255) NOT NULL,
    url           VARCHAR(1024),
    trust         DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    last_error    TEXT,
    created_at    TIMESTAMP    NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),
    last_seen_at  TIMESTAMP,
    CONSTRAINT uq_sources_kind_external UNIQUE (kind, external_id)
);
CREATE INDEX IF NOT EXISTS ix_sources_kind ON sources (kind);

CREATE TABLE IF NOT EXISTS alerts (
    id             SERIAL PRIMARY KEY,
    region_id      INTEGER REFERENCES regions(id) ON DELETE SET NULL,
    threat_type    VARCHAR(16)  NOT NULL,
    status         VARCHAR(16)  NOT NULL,
    title          VARCHAR(200) NOT NULL,
    message        TEXT         NOT NULL,
    norm_text      TEXT         NOT NULL,
    content_hash   VARCHAR(64)  NOT NULL,
    dedup_bucket   INTEGER      NOT NULL,
    priority       SMALLINT     NOT NULL DEFAULT 1,
    confidence     DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    lat            DOUBLE PRECISION,
    lon            DOUBLE PRECISION,
    event_at       TIMESTAMP    NOT NULL,
    first_seen_at  TIMESTAMP    NOT NULL,
    last_seen_at   TIMESTAMP    NOT NULL,
    cleared_at     TIMESTAMP,
    notified_at    TIMESTAMP,
    merged_count   INTEGER      NOT NULL DEFAULT 1,
    is_demo        BOOLEAN      NOT NULL DEFAULT FALSE,
    CONSTRAINT uq_alerts_content_bucket UNIQUE (content_hash, dedup_bucket)
);
CREATE INDEX IF NOT EXISTS ix_alerts_region_id ON alerts (region_id);
CREATE INDEX IF NOT EXISTS ix_alerts_threat_type ON alerts (threat_type);
CREATE INDEX IF NOT EXISTS ix_alerts_status ON alerts (status);
CREATE INDEX IF NOT EXISTS ix_alerts_content_hash ON alerts (content_hash);
CREATE INDEX IF NOT EXISTS ix_alerts_dedup_bucket ON alerts (dedup_bucket);
CREATE INDEX IF NOT EXISTS ix_alerts_event_at ON alerts (event_at);
CREATE INDEX IF NOT EXISTS ix_alerts_notified_at ON alerts (notified_at);
CREATE INDEX IF NOT EXISTS ix_alerts_region_event ON alerts (region_id, event_at);
CREATE INDEX IF NOT EXISTS ix_alerts_status_event ON alerts (status, event_at);

CREATE TABLE IF NOT EXISTS alert_sources (
    id                   SERIAL PRIMARY KEY,
    alert_id             INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    source_id            INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_message_id  VARCHAR(128) NOT NULL,
    message_url          VARCHAR(1024),
    raw_text             TEXT NOT NULL,
    posted_at            TIMESTAMP NOT NULL,
    CONSTRAINT uq_alert_source_message UNIQUE (alert_id, source_id, external_message_id)
);
CREATE INDEX IF NOT EXISTS ix_alert_sources_alert_id ON alert_sources (alert_id);
CREATE INDEX IF NOT EXISTS ix_alert_sources_source_id ON alert_sources (source_id);
