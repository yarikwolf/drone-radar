# Деплой на Vercel (бесплатный тариф Hobby)

Схема:

```
GitHub (код) ──► Vercel: frontend  ──►  https://drone-radar-one.vercel.app
                        │  /api
                        ▼
                 Vercel: backend (FastAPI)  ──►  Supabase PostgreSQL
                        ▲                        (eu-west-1, pooler)
                        │ POST /api/internal/poll (X-Poll-Token)
                 GitHub Actions, каждые 5 минут
```

Текущий рабочий деплой:

| Что | Адрес |
|---|---|
| Сайт | <https://drone-radar-one.vercel.app> |
| API | <https://drone-radar-api.vercel.app> (`/api/health`, `/docs`) |
| База | Supabase `drone-radar`, проект `dckoxkmeykjokmvuitlc`, регион `eu-west-1` |

Что важно знать заранее:

- **cron на Hobby работает только раз в сутки**, поэтому опрос Telegram
  запускает GitHub Actions (бесплатно для публичного репозитория);
- **базы данных в Vercel нет**, нужен внешний PostgreSQL — используем **Supabase**
  (Neon недоступен из РФ: отдаёт `403 Access Blocked in Your Region`);
- **WebSocket** на Vercel нам не нужен: фронтенд опрашивает API каждые 15 секунд
  (`WS_ENABLED=false`, `VITE_DISABLE_WS=1`);
- **Hobby — для некоммерческих проектов**; публичный бесплатный радар подходит;
- у функций лимит **300 секунд**, холодный старт 2–10 секунд, логи хранятся 1 час;
- **`git push` сам по себе сайт не обновляет**: Git-интеграция Vercel не
  подключена, деплой идёт скриптом [`vercel_deploy.py`](vercel_deploy.py)
  (см. раздел 8).

Понадобятся аккаунты: Vercel и Supabase. Всё остальное уже есть в репозитории.

---

## 1. База данных: Supabase

1. <https://supabase.com/dashboard> → **New project**:
   - имя: `drone-radar`;
   - регион: **West EU (Ireland) `eu-west-1`** (Frankfurt на free-тарифе
     недоступен, а Ирландия ближе к серверам Vercel и к Telegram);
   - пароль базы: сгенерировать и сохранить;
   - план: **Free**.
2. Строка подключения берётся в **Project Settings → Database → Connection
   pooling → Connection string**. Для serverless нужен **transaction pooler**:

```dotenv
DATABASE_URL=postgresql+asyncpg://postgres.<REF>:<ПАРОЛЬ>@aws-0-eu-west-1.pooler.supabase.com:6543/postgres?ssl=require
```

Замены:

| Supabase отдаёт | Нужно в проекте |
|---|---|
| `postgresql://` | `postgresql+asyncpg://` |
| пользователь `postgres` | `postgres.<REF>` (REF — идентификатор проекта) |
| `?sslmode=require` | `?ssl=require` |

Если при первом запросе в логах `tenant/user ... not found`, значит у проекта
другой пулер — замените `aws-0` на `aws-1` в имени хоста (оба варианта
существуют, верный определяется экспериментом).

> Прямое подключение `db.<REF>.supabase.co` доступно только по IPv6 — не
> используйте его на Vercel.

Проверить строку можно локально (нужен доступ к порту 6543):

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
$env:DATABASE_URL="postgresql+asyncpg://..."
python -c "import asyncio; from app.db import init_db; asyncio.run(init_db()); print('ok')"
```

> В РФ соединение с Supabase часто режется VPN/DPI (TCP открыт, а PostgreSQL
> молчит). В этом случае проверяйте уже после деплоя — через `/api/health`.

Таблицы создаются сами при первом запросе (`AUTO_CREATE_TABLES=true`), там же
засеиваются 89 регионов.

---

## 2. Строка сессии Telegram

На Vercel нельзя хранить файл `radar.session`, зато можно передать строку.

```powershell
cd "C:\Users\Valya\Documents\Проект по умолчанию\drone-radar\backend"
.\.venv\Scripts\Activate.ps1
python scripts/session_to_string.py
```

Скрипт напечатает строку `1BVtsOK4...`. Это фактически пароль от аккаунта:
не коммитьте её и не вставляйте в переписку. Если сессия отзовётся — повторите
`python scripts/telegram_login.py`, затем экспорт ещё раз.

---

## 3. Токен Vercel и проекты

Токен нужен, если деплой идёт через API (раздел 8):

1. <https://vercel.com/account/settings/tokens> → **Create Token**:
   - имя: `drone-radar-deploy`;
   - scope: **Full Account**;
   - срок: 1 год.
2. Токен показывается один раз. Сохраните его вне репозитория:

```powershell
Set-Content "$env:USERPROFILE\.vercel-token" "vcp_..." -Encoding ASCII -NoNewline
```

Проекты:

```powershell
python deploy/vercel_deploy.py create-project drone-radar-api fastapi
python deploy/vercel_deploy.py create-project drone-radar
```

---

## 4. Переменные API

Файл `api.env.json` (в репозиторий не кладём!) и загрузка:

```powershell
python deploy/vercel_deploy.py env drone-radar-api api.env.json
```

| Переменная | Значение |
|---|---|
| `DATABASE_URL` | строка из шага 1 |
| `ENVIRONMENT` | `production` |
| `SERVERLESS` | `true` |
| `WS_ENABLED` | `false` |
| `AUTO_CREATE_TABLES` | `true` |
| `CORS_ORIGINS` | `https://drone-radar-one.vercel.app,http://localhost:5173` |
| `POLL_TOKEN` | длинный случайный токен (см. ниже) |
| `POLL_MAX_SECONDS` | `240` |
| `NOTIFY_ENABLED` | `false` |
| `NOTIFY_SCHEDULER_ENABLED` | `false` (на serverless планировщика нет) |
| `TELEGRAM_API_ID` | из локального `.env` |
| `TELEGRAM_API_HASH` | из локального `.env` |
| `TELEGRAM_SESSION_STRING` | строка из шага 2 |
| `TELEGRAM_CHANNELS` | `https://t.me/radar_krasdar,https://t.me/lpr1_treugolnik,https://t.me/kubtrevoga93,https://t.me/russiamonitoring_radar_bpla,https://t.me/trevogi_rus` |
| `TELEGRAM_BACKFILL_LIMIT` | `20` |

Сгенерировать `POLL_TOKEN`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## 5. Деплой API

```powershell
python deploy/vercel_deploy.py deploy drone-radar-api backend
```

Vercel сам определяет FastAPI по `app/main.py`, ставит зависимости из
`requirements.txt`, а `backend/vercel.json` задаёт лимит 300 секунд.

Проверка:

```powershell
curl https://drone-radar-api.vercel.app/api/health
# {"status":"ok","database":"ok","version":"1.0.0"}

curl https://drone-radar-api.vercel.app/api/regions
# {"total":89,...}
```

Если `database: error` — смотрите `DATABASE_URL` (частые ошибки: остался
`postgresql://` вместо `postgresql+asyncpg://`, нет `?ssl=require`, неверный
хост пулера).

Ручной запуск опроса:

```powershell
curl -X POST "https://drone-radar-api.vercel.app/api/internal/poll" `
  -H "X-Poll-Token: ВАШ_ТОКЕН"
# {"ok":true,"stored":50,"skipped":false,"elapsed_seconds":114.97}
```

> Первый вызов после деплоя может занять 20–40 секунд: создаются таблицы,
> засеивается справочник регионов и поднимается соединение с Telegram.

---

## 6. Деплой фронтенда

Адрес API «запекается» в бандл на этапе сборки, поэтому сначала собираем
локально с нужными переменными:

```powershell
cd frontend
$env:VITE_API_URL="https://drone-radar-api.vercel.app"
$env:VITE_DISABLE_WS="1"
npm run build
cd ..
python deploy/vercel_deploy.py deploy drone-radar frontend/dist --all
```

Флаг `--all` обязателен: без него каталог `dist/data` (GeoJSON с границами
регионов) будет пропущен как служебный.

Затем пропишите `VITE_API_URL` и `VITE_DISABLE_WS=1` в переменные проекта
`drone-radar` — они пригодятся, если проект когда-нибудь будет собираться на
стороне Vercel:

```powershell
python deploy/vercel_deploy.py env drone-radar web.env.json
```

> Домены Preview (`drone-radar-xxx.vercel.app`) в CORS не входят — проверяйте
> продовый адрес. Если нужны превью, добавьте их через запятую.

---

## 7. Планировщик: опрос каждые 5 минут

### Вариант A: GitHub Actions (уже в репозитории)

Файл `.github/workflows/poll.yml` запускается по расписанию.

1. GitHub → репозиторий → **Settings → Secrets and variables → Actions**.
2. Вкладка **Variables** → **New repository variable**:
   - `API_BASE_URL` = `https://drone-radar-api.vercel.app`
3. Вкладка **Secrets** → **New repository secret**:
   - `POLL_TOKEN` = тот же токен, что в Vercel.
4. **Actions → Poll sources → Run workflow** — проверить вручную.

Ограничения GitHub: расписание может задерживаться на 5–15 минут, а после
60 дней без активности репозитория GitHub отключает cron (достаточно зайти и
включить снова или сделать любой коммит).

### Вариант B: cron-job.org

1. <https://cron-job.org> → регистрация → **Create cronjob**.
2. URL: `https://drone-radar-api.vercel.app/api/internal/poll`
3. Schedule: **every 5 minutes**.
4. Advanced → Request method: **POST**; Headers:
   `X-Poll-Token: ВАШ_ТОКЕН`.

Достаточно одного планировщика: если работают оба, второй запуск просто
пропускается благодаря блокировке (`"skipped": true`).

---

## 8. Обновление сайта

Git-интеграция Vercel не подключена (GitHub App не установлен), поэтому
`git push` обновляет только код. Чтобы выложить изменения:

```powershell
cd "C:\Users\Valya\Documents\Проект по умолчанию\drone-radar"
git add -A
git commit -m "что изменилось"
git push

# бэкенд
python deploy/vercel_deploy.py deploy drone-radar-api backend

# фронтенд (после npm run build с VITE_API_URL и VITE_DISABLE_WS=1)
python deploy/vercel_deploy.py deploy drone-radar frontend/dist --all
```

Меняли переменные окружения — после `env` нужен новый деплой, иначе значения
не применятся.

Если позже подключите Git-интеграцию (Vercel → **Add Git Repository**, root
`backend` и `frontend`), деплой станет автоматическим: Vercel будет собирать
проекты на каждый пуш, а `vercel.json` уже лежит в репозитории.

---

## 9. Проверка после деплоя

| Что проверить | Как | Ожидание |
|---|---|---|
| API жив | `GET /api/health` | `status: ok`, `database: ok` |
| Регионы загрузились | `GET /api/regions` | `total: 89` |
| Опрос работает | `POST /api/internal/poll` с токеном | `stored > 0` или `skipped` |
| Данные появились | `GET /api/alerts?hours=24` | непустой `items` |
| Сайт видит API | открыть сайт, консоль браузера | нет ошибок CORS/404 |
| Границы регионов | карта | заливка субъектов + маркеры |
| Расписание | Actions или cron-job.org | запуски каждые 5 минут |

Типичные ошибки:

| Симптом | Причина |
|---|---|
| `FUNCTION_INVOCATION_FAILED` | нет файлов `app/data/*.json` в деплое |
| `database: error` | `DATABASE_URL` без `+asyncpg`, без `?ssl=require` или неверный пулер |
| `tenant/user not found` | замените `aws-0` на `aws-1` в хосте пулера |
| `401 Неверный X-Poll-Token` | токен в планировщике не совпадает с Vercel |
| `503 ... не задан POLL_TOKEN` | переменная не добавлена в проект API |
| Сайт пустой, в консоли CORS | `CORS_ORIGINS` не совпадает с адресом фронтенда |
| Нет заливки регионов | в деплой не попал `data/russia-regions.geojson` (нужен `--all`) |
| `Session not authorized` | строка сессии повреждена или отозвана |
| Опрос `stored: 0` | каналы молчат либо `TELEGRAM_CHANNELS` пуст |

---

## 10. Ограничения бесплатного Vercel

- 300 секунд на функцию, 2 ГБ RAM, 1 vCPU;
- cron только раз в сутки (поэтому внешний планировщик);
- логи — 1 час;
- нет постоянного диска: всё состояние в Supabase;
- холодный старт: первый запрос после простоя дольше обычного;
- Hobby запрещает коммерческое использование;
- при превышении бесплатных квот Vercel может приостановить проект до конца месяца.

Если проект вырастет, те же файлы разворачиваются на VPS через
[`deploy/DEPLOY.md`](DEPLOY.md) без изменений кода.
