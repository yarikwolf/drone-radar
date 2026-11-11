# Деплой на Vercel (бесплатный тариф Hobby)

Схема:

```
GitHub (код) ──► Vercel: frontend  ──►  https://drone-radar.vercel.app
                        │  /api
                        ▼
                 Vercel: backend (FastAPI)  ──►  Neon PostgreSQL
                        ▲
                        │ POST /api/internal/poll (X-Poll-Token)
                 GitHub Actions, каждые 5 минут
```

Что важно знать заранее:

- **cron на Hobby работает только раз в сутки**, поэтому опрос Telegram
  запускает GitHub Actions (бесплатно для публичного репозитория);
- **базы данных в Vercel нет**, нужен внешний PostgreSQL — используем Neon (free);
- **WebSocket** на Vercel в бете и нам не нужен: фронтенд опрашивает API каждые 15 секунд;
- **Hobby — для некоммерческих проектов**; публичный бесплатный радар подходит;
- у функций лимит **300 секунд**, холодный старт 2–10 секунд, логи хранятся 1 час.

Понадобятся аккаунты: Vercel (вход через GitHub), Neon. Всё остальное уже есть
в репозитории.

---

## 1. База данных: Neon

1. Зарегистрируйтесь на <https://neon.com> через GitHub.
2. **Create project**:
   - имя: `drone-radar`;
   - регион: **Frankfurt (eu-central-1)** — ближе к Vercel `fra1` и к Telegram;
   - версия PostgreSQL — по умолчанию.
3. На странице проекта нажмите **Connect** и скопируйте строку подключения
   с пометкой **Pooled connection** (в имени хоста есть `-pooler`).
4. Приведите строку к виду, понятному SQLAlchemy:

```dotenv
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?ssl=require
```

Замены:

| Neon отдаёт | Нужно в проекте |
|---|---|
| `postgresql://` | `postgresql+asyncpg://` |
| `?sslmode=require` | `?ssl=require` |
| `channel_binding=require` | удалить параметр |

Проверить строку можно локально:

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL="postgresql+asyncpg://..."
python -c "import asyncio; from app.db import init_db; asyncio.run(init_db()); print('ok')"
```

> Бесплатный Neon засыпает после ~5 минут простоя. Первый запрос после паузы
> будет на 1–2 секунды дольше — это нормально.

---

## 2. Строка сессии Telegram

На Vercel нельзя хранить файл `radar.session`, зато можно передать строку.
Она уже есть в вашей локальной сессии — экспортируйте её (локальный радар
можно не выключать):

```powershell
cd "C:\Users\Valya\Documents\Проект по умолчанию\drone-radar\backend"
.\.venv\Scripts\Activate.ps1
python scripts/session_to_string.py
```

Скрипт напечатает:

```
TELEGRAM_SESSION_STRING:
1BVtsOK4...
```

Скопируйте **всю строку**. Это фактически пароль от аккаунта: не коммитьте её и
не вставляйте в переписку. Если сессия когда-нибудь отзовётся — повторите
`python scripts/telegram_login.py`, затем экспорт ещё раз.

---

## 3. Проект API на Vercel

1. Откройте <https://vercel.com/new> и войдите через GitHub.
2. **Import Git Repository** → выберите `yarikwolf/drone-radar`.
3. Настройки проекта:
   - **Project Name**: `drone-radar-api`;
   - **Framework Preset**: `FastAPI` (если не определился — оставьте `Other`);
   - **Root Directory**: нажмите `Edit` и выберите **`backend`**;
   - Build & Output Settings не меняйте.
4. **Environment Variables** — добавьте для Production (и Preview, если нужно):

| Переменная | Значение |
|---|---|
| `DATABASE_URL` | строка из шага 1 |
| `SERVERLESS` | `true` |
| `POLL_TOKEN` | длинный случайный токен (см. ниже) |
| `POLL_MAX_SECONDS` | `240` |
| `TELEGRAM_API_ID` | из локального `.env` |
| `TELEGRAM_API_HASH` | из локального `.env` |
| `TELEGRAM_SESSION_STRING` | строка из шага 2 |
| `TELEGRAM_CHANNELS` | `https://t.me/radar_krasdar,https://t.me/lpr1_treugolnik,https://t.me/kubtrevoga93,https://t.me/russiamonitoring_radar_bpla,https://t.me/trevogi_rus` |
| `TELEGRAM_BACKFILL_LIMIT` | `20` |
| `WS_ENABLED` | `false` |
| `NOTIFY_SCHEDULER_ENABLED` | `false` |
| `AUTO_CREATE_TABLES` | `true` |
| `ENVIRONMENT` | `production` |
| `CORS_ORIGINS` | пока `http://localhost:5173`, обновим после шага 4 |

Сгенерировать `POLL_TOKEN`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

5. **Deploy**. Первая сборка ставит Python-зависимости, это 2–5 минут.
6. Проверьте:

```powershell
curl https://drone-radar-api.vercel.app/api/health
# {"status":"ok","database":"ok","version":"1.0.0"}
```

Если `database":"error"` — проверьте `DATABASE_URL` (частая ошибка: остался
`postgresql://` вместо `postgresql+asyncpg://` или `sslmode` вместо `ssl`).

7. Ручной запуск опроса (подставьте свой токен):

```powershell
curl -X POST "https://drone-radar-api.vercel.app/api/internal/poll" `
  -H "X-Poll-Token: ВАШ_ТОКЕН"
# {"ok":true,"stored":12,"skipped":false,"elapsed_seconds":18.4}
```

> Первый вызов после деплоя может занять 20–40 секунд: создаются таблицы,
> засеивается справочник регионов и поднимается соединение с Telegram.

---

## 4. Проект фронтенда на Vercel

1. Снова <https://vercel.com/new> → тот же репозиторий.
2. Настройки:
   - **Project Name**: `drone-radar`;
   - **Framework Preset**: `Vite` (определится сам);
   - **Root Directory**: **`frontend`**;
   - Build Command: `npm run build`, Output Directory: `dist` (по умолчанию).
3. **Environment Variables** (Production):

| Переменная | Значение |
|---|---|
| `VITE_API_URL` | `https://drone-radar-api.vercel.app` |
| `VITE_DISABLE_WS` | `1` |

4. **Deploy**. Получите адрес вида `https://drone-radar.vercel.app`.
5. Вернитесь в проект **drone-radar-api** → Settings → Environment Variables →
   измените `CORS_ORIGINS`:

```dotenv
CORS_ORIGINS=https://drone-radar.vercel.app
```

Сохраните и сделайте **Redeploy** (Deployments → ⋯ → Redeploy), чтобы новый
CORS применился.

6. Откройте сайт. Должны появиться карта, регионы и лента событий.

> Домены Preview (`drone-radar-xxx.vercel.app`) в CORS не входят — проверяйте
> продовый адрес. Если нужны превью, добавьте их через запятую.

---

## 5. Планировщик: опрос каждые 5 минут

### Вариант A: GitHub Actions (уже в репозитории)

Файл `.github/workflows/poll.yml` запускается по расписанию.

1. GitHub → репозиторий → **Settings → Secrets and variables → Actions**.
2. Вкладка **Variables** → **New repository variable**:
   - `API_BASE_URL` = `https://drone-radar-api.vercel.app`
3. Вкладка **Secrets** → **New repository secret**:
   - `POLL_TOKEN` = тот же токен, что в Vercel.
4. **Actions → Poll sources → Run workflow** — проверить вручную.
5. Через 10 минут в Actions появится запуск по расписанию.

Ограничения GitHub: расписание может задерживаться на 5–15 минут, а после
60 дней без активности репозитория GitHub отключает cron (достаточно зайти и
включить снова или сделать любой коммит).

### Вариант B: cron-job.org

Бесплатный внешний планировщик с точным интервалом:

1. <https://cron-job.org> → регистрация → **Create cronjob**.
2. URL: `https://drone-radar-api.vercel.app/api/internal/poll`
3. Schedule: **every 5 minutes**.
4. Advanced → Request method: **POST**; Headers:
   `X-Poll-Token: ВАШ_ТОКЕН`.
5. Включите уведомления о падениях на почту.

Достаточно одного планировщика: если работают оба, второй запуск просто
пропускается благодаря блокировке (`"skipped": true`).

---

## 6. Проверка после деплоя

| Что проверить | Как | Ожидание |
|---|---|---|
| API жив | `GET /api/health` | `status: ok`, `database: ok` |
| Регионы загрузились | `GET /api/regions` | `total: 89` |
| Опрос работает | `POST /api/internal/poll` с токеном | `stored > 0` или `skipped` |
| Данные появились | `GET /api/alerts?hours=24` | непустой `items` |
| Сайт видит API | открыть сайт, консоль браузера | нет ошибок CORS/404 |
| Расписание | Actions или cron-job.org | запуски каждые 5 минут |

Типичные ошибки:

| Симптом | Причина |
|---|---|
| `database: error` | `DATABASE_URL` без `+asyncpg` или без `?ssl=require` |
| `401 Неверный X-Poll-Token` | токен в планировщике не совпадает с Vercel |
| `503 ... не задан POLL_TOKEN` | переменная не добавлена в проект API |
| Сайт пустой, в консоли CORS | `CORS_ORIGINS` не совпадает с адресом фронтенда |
| `Session not authorized` | строка сессии повреждена или отозвана |
| Опрос `stored: 0` | каналы молчат либо `TELEGRAM_CHANNELS` пуст |

---

## 7. Обновление сайта

```powershell
cd "C:\Users\Valya\Documents\Проект по умолчанию\drone-radar"
git add -A
git commit -m "что изменилось"
git push
```

Vercel пересоберёт оба проекта автоматически, GitHub Actions продолжит опрос.
Если менялись переменные окружения — нужен Redeploy.

---

## 8. Ограничения бесплатного Vercel

- 300 секунд на функцию, 2 ГБ RAM, 1 vCPU;
- cron только раз в сутки (поэтому внешний планировщик);
- логи — 1 час;
- нет постоянного диска: всё состояние в Neon;
- холодный старт: первый запрос после простоя дольше обычного;
- Hobby запрещает коммерческое использование;
- при превышении бесплатных квот Vercel может приостановить проект до конца месяца.

Если проект вырастет, те же файлы разворачиваются на VPS через
[`deploy/DEPLOY.md`](DEPLOY.md) без изменений кода.
