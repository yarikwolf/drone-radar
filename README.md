# Радар беспилотной опасности (Drone Alert Radar)

Сервис мониторинга воздушных тревог, атак БПЛА и ракетной опасности по открытым источникам.
Справочник охватывает регионы России (85 субъектов, а также Херсонскую и Запорожскую
области, ДНР и ЛНР). Монорепозиторий: Python-бэкенд
(FastAPI + PostgreSQL), ingestion-воркеры (Telethon, RSS), фоновый notifier (APScheduler)
и React-интерфейс с картой Leaflet.

Текущий публичный деплой: **<https://drone-radar-one.vercel.app>**
(API — <https://drone-radar-api.vercel.app>, база — Supabase `eu-west-1`).

> **Важно.** Проект агрегирует только публично доступные сообщения. Он не является официальным
> средством оповещения, данные могут быть неполными или непроверенными. Не используйте сервис как
> единственный источник информации в ситуациях, связанных с безопасностью жизни.

---

## 1. Архитектура

```
                    ОТКРЫТЫЕ ИСТОЧНИКИ
        ┌─────────────────┬──────────────────┬─────────────────┐
        │ Telegram        │ RSS / новости    │ Официальные API  │
        │ публичные каналы│ (feedparser)     │ (webhook/poller) │
        └────────┬────────┴────────┬─────────┴────────┬────────┘
                 │                 │                  │
                 ▼                 ▼                  ▼
        ┌────────────────────────────────────────────────────┐
        │              INGESTION LAYER (Python)              │
        │  Telethon listener / RSS poller / webhook-приёмник │
        │  • reconnect + exponential backoff                 │
        │  • FloodWait / HTTP retry                          │
        │  • нормализация текста                             │
        └───────────────────────┬────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │              NLP & GEO (regex + словарь)           │
        │  • ключевые слова: БПЛА, ракета, тревога, отбой    │
        │  • тип угрозы: drone / missile / mixed / unknown   │
        │  • регион: алиасы (RU/EN) → координаты             │
        └───────────────────────┬────────────────────────────┘
                                ▼
        ┌────────────────────────────────────────────────────┐
        │         ДЕДУПЛИКАЦИЯ + ХРАНЕНИЕ (PostgreSQL)       │
        │  alerts · sources · regions · alert_sources        │
        │  exact hash + временное окно + fuzzy Jaccard       │
        └───────┬────────────────────────────────┬───────────┘
                │                                │
                ▼                                ▼
        ┌──────────────────┐             ┌──────────────────┐
        │ FastAPI          │             │ Notifier         │
        │ REST + WebSocket │             │ APScheduler      │
        └────────┬─────────┘             └───────┬──────────┘
                 │                               │
                 ▼                               ▼
        ┌──────────────────┐             Discord webhook /
        │ React + Leaflet  │             Telegram Bot API
        │ карта, таймлайн  │
        └──────────────────┘
```

### Слои и ответственность

| Слой | Модули | Задача |
|---|---|---|
| Ingestion | `app/ingestion/*` | чтение открытых источников, ретраи, нормализация |
| NLP & Geo | `keywords.py`, `geo.py`, `classifier.py` | тип угрозы, регион, координаты, статус |
| Dedup & Store | `services/alerts.py`, `models.py` | схема БД, склейка дублей, жизненный цикл |
| API | `app/api/*` | REST-фильтры, WebSocket-пуш, health |
| Frontend | `frontend/src/*` | карта, заливка регионов по статусу, маркеры, таймлайн |
| Notifier | `app/workers/notifier.py` | Discord/Telegram, retry, окно событий |

### Поток обработки сообщения

1. Воркер получает текст, имя канала, ссылку и время публикации.
2. `normalize_text()` убирает ссылки, упоминания, разметку и приводит текст к нижнему регистру.
3. `detect_keywords()` проверяет словарь угроз. Нерелевантные сообщения отбрасываются.
4. `match_regions()` ищет самый длинный алиас региона, чтобы «Московская область» не превратилась в «Москву».
   Затем `match_places()` по найденным регионам достаёт конкретные города и районы
   («Шебекино», «Суджанский район») из `data/places.json`.
5. `classify()` выставляет `threat_type` (`drone`/`missile`/`mixed`/`unknown`) и
   `status` (`active`/`warning`/`all_clear`).
6. `services.alerts.upsert_alert()` ищет дубль:
   - **exact** — SHA-256 от нормализованного текста + регион + тип + статус, окно 2 часа,
     уникальный индекс `(content_hash, dedup_bucket)`;
   - **fuzzy** — тот же регион/тип/статус в окне 20 минут и Jaccard-сходство токенов ≥ 0.55.
   При совпадении сообщение прикрепляется к существующему алерту как новый источник.
7. «Отбой» создаёт зелёный алерт и переводит активные алерты региона в `cleared`.
8. После commit FastAPI WebSocket рассылает событие, UI перезапрашивает данные.
9. Notifier раз в 20 секунд забирает неотправленные алерты и шлёт уведомления с retry.

### Модель статусов

| Статус | Цвет | Смысл |
|---|---|---|
| `active` | красный | активная угроза (БПЛА/ракета/смешанная) |
| `warning` | жёлтый | тревога/предупреждение без уточнения типа |
| `all_clear` | зелёный | отбой |
| `cleared` | серый | историческая запись, закрытая отбоем |

Тип угрозы и статус хранятся отдельно: «Ракетная опасность» — это `missile` + `active`,
а «Воздушная тревога» — `unknown` + `warning`.

### Слой регионов и точки назначения

Контуры субъектов РФ лежат в `frontend/public/data/russia-regions.geojson`
(Natural Earth 10m для 85 субъектов, geoBoundaries ADM1 для Херсонской и
Запорожской областей, ДНР и ЛНР; упрощение RDP с допуском 0.012°).
Полигон закрашивается цветом последнего статуса региона.

Если в сообщении назван конкретный город или район, на карте появляется
**точка назначения**: маркер с подписью и пунктирная зона вокруг него.
Координаты берутся из `backend/app/data/places.json` — это ~645 населённых
пунктов для приграничных и южных регионов. Названия мест также видны в
ленте событий и во всплывающей карточке.

При клике по региону с тревогой открывается карточка события, по региону без
тревоги — карта приближается к его границам.

---

## 2. Структура репозитория

```
drone-radar/
├─ backend/
│  ├─ app/
│  │  ├─ main.py                 # FastAPI + lifespan + WS
│  │  ├─ config.py               # настройки из .env
│  │  ├─ db.py                   # async engine/session
│  │  ├─ models.py               # SQLAlchemy-схема
│  │  ├─ schemas.py              # Pydantic-ответы
│  │  ├─ seed.py                 # регионы из data/regions.json
│  │  ├─ data/regions.json       # словарь регионов и координат
│  │  ├─ data/places.json        # города и райцентры для точек назначения
│  │  ├─ ingestion/
│  │  │  ├─ keywords.py          # нормализация и ключевые слова
│  │  │  ├─ geo.py               # извлечение региона и населённого пункта
│  │  │  ├─ classifier.py        # классификация угрозы
│  │  │  ├─ pipeline.py          # единая точка приёма сообщения
│  │  │  ├─ telegram_listener.py # Telethon: live-режим и разовый опрос
│  │  │  ├─ rss_listener.py      # RSS-поллер
│  │  │  └─ demo.py              # генератор демо-событий
│  │  ├─ services/
│  │  │  ├─ alerts.py            # дедупликация и upsert
│  │  │  ├─ events.py            # in-process WebSocket broadcaster
│  │  │  └─ dedup.py             # hash/Jaccard/bucket
│  │  ├─ api/
│  │  │  ├─ alerts.py            # /api/alerts, /api/alerts/active
│  │  │  ├─ regions.py           # /api/regions
│  │  │  ├─ system.py            # health, stats, webhook, WS
│  │  │  └─ __init__.py
│  │  └─ workers/notifier.py     # APScheduler + Discord/Telegram
│  ├─ tests/                     # pytest: NLP, geo, dedup, отбой
│  ├─ sql/schema.sql             # справочный DDL для PostgreSQL
│  ├─ requirements.txt
│  └─ Dockerfile
├─ frontend/
│  ├─ src/
│  │  ├─ App.jsx
│  │  ├─ api.js
│  │  ├─ components/RadarMap.jsx
│  │  ├─ components/Timeline.jsx
│  │  ├─ components/ControlPanel.jsx
│  │  ├─ lib/format.js
│  │  └─ styles.css
│  ├─ public/data/russia-regions.geojson  # границы 89 регионов
│  ├─ vite.config.js
│  ├─ nginx.conf
│  └─ Dockerfile
├─ backend/scripts/poll_sources.py  # разовая проверка источников по расписанию
├─ launcher.ps1                     # запуск сайта + опрос раз в 5 минут
├─ stop.ps1                         # остановка сервисов
├─ Запустить радар.bat              # двойной щелчок — и всё работает
├─ Остановить радар.bat
├─ docker-compose.yml
├─ .env.example
└─ README.md
```

---

## 3. Быстрый запуск

### Windows: один файл (локальный запуск)

Дважды щёлкните **`Запустить радар.bat`** в корне проекта. Лаунчер:

1. поднимает backend и frontend в свёрнутых окнах;
2. ждёт готовности и открывает <http://localhost:5173>;
3. каждые 5 минут запускает проверку Telegram-источников
   (`backend/scripts/poll_sources.py`);
4. пишет логи в `logs/backend.log`, `logs/frontend.log`, `logs/sources.log`.

Остановка — **`Остановить радар.bat`** или `Ctrl+C` в окне лаунчера.
Интервал проверки можно изменить перед запуском:

```bat
set RADAR_POLL_SECONDS=600
Запустить радар.bat
```

Для Telegram должен быть включён VPN (Happ) или задан `TELEGRAM_PROXY`.
Если сайт уже запущен, лаунчер переиспользует его и не поднимает дубли.

### Docker (рекомендуется)

```bash
cp .env.example .env
docker compose up --build
```

- UI: <http://localhost:8080>
- API и Swagger: <http://localhost:8000/docs>
- PostgreSQL: `localhost:5432`, база `radar`

Наполнить карту демо-событиями (без Telegram-ключей):

```bash
docker compose --profile demo run --rm demo
```

При старте `docker compose up` поднимаются `postgres`, `api`, `frontend`
и `poller` — он раз в 5 минут проверяет Telegram-каналы. Ничего дополнительно
включать не нужно, если `.env` заполнен.

Дополнительные воркеры включаются профилями:

```bash
# Постоянный live-listener вместо разового опроса (альтернатива poller)
docker compose --profile telegram up -d telegram

# RSS-ленты (RSS_FEEDS в .env)
docker compose --profile rss up -d rss

# Webhook-уведомления (DISCORD_WEBHOOK_URL или TELEGRAM_BOT_TOKEN/CHAT_ID)
docker compose --profile notify up -d notifier
```

Публикация на сервере, домен и HTTPS: см. [`deploy/DEPLOY.md`](deploy/DEPLOY.md).
Бесплатный вариант на Vercel (frontend + FastAPI + Supabase + внешний cron):
см. [`deploy/VERCEL.md`](deploy/VERCEL.md).

### Локальная разработка

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

# Для быстрого старта без PostgreSQL:
$env:DATABASE_URL="sqlite+aiosqlite:///./radar.db"
uvicorn app.main:app --reload

# в отдельном терминале
python -m app.ingestion.demo --once
```

```powershell
cd frontend
npm install
npm run dev
```

UI: <http://localhost:5173> (Vite проксирует `/api` на `localhost:8000`).

---

## 4. Настройка источников

### Telegram

1. Получите `api_id` и `api_hash` на <https://my.telegram.org>.
2. Укажите в `.env`:
   ```env
   TELEGRAM_API_ID=123456
   TELEGRAM_API_HASH=...
   TELEGRAM_CHANNELS=monitoring_channel,another_channel
   ```
3. Для локального запуска достаточно один раз авторизоваться:
   ```bash
   python -m app.ingestion.telegram_listener
   ```
   Появится запрос телефона/кода, создастся `backend/data/radar.session`.
   После успешного входа остановите воркер (`Ctrl+C`): лаунчер сам опрашивает
   каналы каждые 5 минут.
4. Проверить источники вручную (один цикл, без ожидания):
   ```bash
   cd backend
   python scripts/poll_sources.py
   ```
5. Для Docker сгенерируйте строковую сессию:
   ```bash
   docker compose run --rm telegram python scripts/telegram_login.py
   ```
   и положите вывод в `TELEGRAM_SESSION_STRING`.

Слушатель читает **только публичные** каналы, которые может открыть аккаунт. Не добавляйте
закрытые чаты и соблюдайте правила Telegram и законодательство вашей юрисдикции.

#### Доступ к Telegram из России

Напрямую Telegram с российских провайдеров часто недоступен (TCP-соединения к DC и `t.me`
блокируются). Укажите рабочий прокси в `TELEGRAM_PROXY` — воркер сам выберет нужный тип
соединения:

```env
# SOCKS5 или HTTP-прокси
TELEGRAM_PROXY=socks5://127.0.0.1:7891
# TELEGRAM_PROXY=http://user:pass@127.0.0.1:8080

# MTProto-прокси (например, tg ws proxy): mtproxy://<secret>@host:port
# TELEGRAM_PROXY=mtproxy://0123456789abcdef0123456789abcdef@127.0.0.1:1443
```

Пустое значение = подключение напрямую. Для SOCKS/HTTP нужен пакет `PySocks`
(он уже в `requirements.txt`).

#### Текущий набор каналов

```env
TELEGRAM_CHANNELS=radar_krasdar,lpr1_treugolnik,kubtrevoga93,russiamonitoring_radar_bpla,trevogi_rus,Detector_Aero
```

`Detector_Aero` — официальный канал сервиса <https://detector-aero.ru/>. Публичного RSS у сайта
нет, а его API `/api/pub/v1/*` защищён PoW-сессией, поэтому данные берутся из канала, а не
обходом защиты сайта.

### RSS

```env
RSS_FEEDS=https://example.org/alerts.rss,https://example.net/feed.xml
RSS_POLL_SECONDS=90
```

### Официальные API

Для pull-интеграции добавьте адаптер в `app/ingestion/` по образцу `rss_listener.py` и вызывайте
`ingest_message()`. Для push-интеграции используйте:

```bash
curl -X POST http://localhost:8000/api/ingest/webhook \
  -H "Content-Type: application/json" \
  -H "X-Ingest-Token: $INGEST_TOKEN" \
  -d '{"text":"Ракетная опасность в Курской области","source":"region-api","source_name":"Официальный API"}'
```

Webhook закрыт токеном `INGEST_TOKEN`; если токен не задан, эндпоинт отключён (503).

---

## 5. API

| Метод | Путь | Описание |
|---|---|---|
| GET | `/api/alerts` | лента: `region`, `threat`, `status`, `since`, `until`, `hours`, `limit`, `offset` |
| GET | `/api/alerts/active` | последний статус каждого региона (active/warning до 4 ч, отбой до 90 мин) |
| GET | `/api/alerts/{id}` | детали алерта |
| GET | `/api/regions` | справочник регионов |
| GET | `/api/stats/summary` | счётчики текущих статусов регионов за 24 часа |
| GET | `/api/health` | health + наличие БД |
| WS | `/api/ws/alerts` | push новых событий |
| POST | `/api/ingest/webhook` | приём текста из внешнего источника |
| POST | `/api/internal/poll` | один цикл опроса по расписанию (нужен `X-Poll-Token`) |

Примеры:

```bash
curl "http://localhost:8000/api/alerts?region=RU-BELGOROD&hours=24"
curl "http://localhost:8000/api/alerts?threat=drone,missile&status=active&limit=50"
curl "http://localhost:8000/api/alerts?since=2026-11-01T00:00:00Z&until=2026-11-02T00:00:00Z"
```

`region` принимает код региона (`RU-BELGOROD`, `RU-KURSK`, ...). `since`/`until` — ISO 8601.
Значения `threat` и `status` можно перечислять через запятую.

---

## 6. Дедупликация: детали

- `content_hash = sha256(region|threat|status|normalized_text)`.
- `dedup_bucket = floor(event_at / DEDUP_WINDOW_MINUTES)`, уникальный ключ
  `(content_hash, dedup_bucket)`. Проверяются текущий и предыдущий bucket, поэтому событие
  не «размножается» на границе окна.
- Если текст перепечатан с другим заголовком/подписью канала, срабатывает fuzzy-проверка:
  регион + тип + статус + окно `DEDUP_FUZZY_WINDOW_MINUTES` + Jaccard по токенам.
- Дубли не удаляются: каждый источник остаётся в `alert_sources`, а `alerts.merged_count`
  показывает, сколько сообщений подтвердили событие. Это важно для доверия к данным.
- `INSERT` защищён unique-индексом: при гонке двух воркеров второй получает `IntegrityError`
  и прикрепляется к уже созданному алерту.

---

## 7. Ограничения и что улучшить в production

- WebSocket-broadcaster работает в памяти процесса. При нескольких репликах API нужен Redis
  Pub/Sub (или PostgreSQL `LISTEN/NOTIFY`).
- `create_all()` удобен для старта; для production подключите Alembic и миграции.
- Словарь регионов статичен (`regions.json`). Для динамики — периодическая перезагрузка
  справочника и админка.
- Notifier отправляет уведомления последовательно. При больших объёмах — очередь (Celery/RQ)
  и идемпотентность по `(alert_id, channel, chat_id)`.
- Нет авторизации в API для чтения; для публичного развёртывания добавьте rate limit и CORS-политику.
- Точность геолокации ограничена словарём: сообщения без региона отбрасываются, а не
  привязываются к центру страны.
- Классификатор основан на regex и легко ошибается на иронии/цитатах. Для повышения точности
  можно добавить лёгкую NLP-модель (ruBERT/ONNX) как второй фильтр, но regex остаётся
  объяснимым baseline.
- Обязательно соблюдайте ToS источников, лимиты запросов и не публикуйте персональные данные.

---

## 8. Тесты

```bash
cd backend
pytest -q
```

Покрыты: классификация угроз, извлечение региона (включая «Москву» vs «Московскую область»),
дедупликация exact/fuzzy, сценарий «тревога → отбой → cleared».
