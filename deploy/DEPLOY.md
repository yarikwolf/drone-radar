# Публикация сайта

Проект состоит из трёх частей, которым нужен запущенный сервер:

| Часть | Что делает | Куда деплоить |
|---|---|---|
| `backend` | FastAPI, база, приём сообщений | сервер с Python/Docker |
| `poller` | раз в 5 минут читает Telegram | рядом с backend |
| `frontend` | статическая сборка React | nginx или статический хостинг |

GitHub хранит код и запускает тесты, но не выполняет Python-бэкенд.
Для сайта нужен VPS или PaaS (Render, Railway, Fly.io).

---

## 1. Секреты и что нельзя коммитить

В репозиторий **не попадают**:

- `.env` — там API-ключи Telegram и токены;
- `backend/data/radar.session` — авторизация Telegram-аккаунта;
- `backend/radar.db` — рабочая база;
- `logs/` — журналы.

Они перечислены в `.gitignore`. Перед первым пушем проверьте:

```bash
git status --short
```

В списке не должно быть `.env`, `*.session`, `*.db`, `logs/`.

---

## 2. Вариант A: VPS + Docker Compose (рекомендуется)

Нужен сервер с Ubuntu 22.04/24.04, 1–2 ГБ RAM и Docker. Подойдёт
Hetzner, Netcup, Timeweb Cloud, Aeza и т. п. Зарубежный сервер удобнее:
Telegram открывается напрямую, без VPN.

### 2.1. Установка Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
# перезайти в сессию, чтобы применилась группа
```

### 2.2. Код

```bash
git clone https://github.com/ВАШ_ЛОГИН/drone-radar.git
cd drone-radar
```

### 2.3. Настройки

```bash
cp .env.example .env
nano .env
```

Минимум, что нужно заполнить:

```dotenv
ENVIRONMENT=production
DATABASE_URL=postgresql+asyncpg://radar:radar@postgres:5432/radar
CORS_ORIGINS=https://ваш-домен.ru
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=...
TELEGRAM_CHANNELS=https://t.me/radar_krasdar,https://t.me/lpr1_treugolnik,https://t.me/kubtrevoga93,https://t.me/russiamonitoring_radar_bpla,https://t.me/trevogi_rus
TELEGRAM_SESSION=/app/data/radar
NOTIFY_SCHEDULER_ENABLED=false
```

`TELEGRAM_SESSION` — путь **внутри контейнера**, он смонтирован из
`./backend/data`.

### 2.4. Авторизация Telegram

Два способа.

**Способ 1 — перенести сессию с домашнего компьютера.** На сервере:

```bash
mkdir -p backend/data
# с локальной машины:
scp backend/data/radar.session user@SERVER:/path/to/drone-radar/backend/data/
chmod 600 backend/data/radar.session
```

**Способ 2 — войти прямо на сервере.** Нужен доступ к телефону:

```bash
docker compose run --rm poller python scripts/telegram_login.py
```

Скрипт спросит телефон и код, создаст `backend/data/radar.session`.
Строковую сессию из вывода можно сохранить в `TELEGRAM_SESSION_STRING`
и не хранить файл — тогда удалите `TELEGRAM_SESSION` из `.env`
или оставьте файл как резервную копию.

### 2.5. Запуск

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f poller
```

Сайт: `http://IP_СЕРВЕРА:8080`
API: `http://IP_СЕРВЕРА:8000/api/health` (порт 8000 можно закрыть файрволом).

Первый опрос выполняется сразу при старте `poller`, дальше — каждые 5 минут.
Проверка в логах:

```bash
docker compose logs --tail=30 poller
```

### 2.6. Обновление

```bash
git pull
docker compose up -d --build
```

База PostgreSQL и сессия Telegram хранятся в томах/каталоге `backend/data`
и при обновлении не теряются.

### 2.7. Домен и HTTPS

Проще всего через Caddy. Добавьте в `docker-compose.yml` сервис:

```yaml
  caddy:
    image: caddy:2-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      - frontend
    restart: unless-stopped
```

В `volumes` внизу файла добавьте `caddy_data:` и `caddy_config:`.
В `deploy/Caddyfile` замените домен:

```
radar.example.ru {
    encode gzip
    reverse_proxy frontend:80
}
```

```bash
docker compose up -d
```

Caddy сам получит сертификат Let's Encrypt. После этого порт `8080`
можно закрыть файрволом и оставить только 80/443.

### 2.8. Безопасность

- Не публикуйте порты `5432` (PostgreSQL) и `8000` наружу.
- Файл сессии Telegram даёт доступ к аккаунту — храните как пароль.
- Раз в несколько месяцев проверяйте `git status`, чтобы секреты не попали в коммит.

---

## 3. Вариант B: Render / Railway

Подходит, если не хочется администрировать сервер. Нужен аккаунт и карта.

1. Создайте managed PostgreSQL и скопируйте `DATABASE_URL`.
2. Задеплойте `backend` как Docker-сервис с командой
   `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
3. Добавьте worker с командой
   `sh -c 'while true; do python scripts/poll_sources.py; sleep 300; done'`.
4. Задеплойте `frontend` как статический сайт (`npm run build`, каталог `dist`).
5. В переменных окружения задайте `TELEGRAM_SESSION_STRING` (вывод
   `telegram_login.py`), а не файл сессии.
6. Во фронтенде укажите адрес API: `VITE_API_URL=https://api.example.ru`
   или настройте прокси `/api` на стороне хостинга.

Важно: на бесплатных тарифах сервисы засыпают, и опрос Telegram
останавливается. Для непрерывной работы нужен платный инстанс или
внешний cron, который раз в 5 минут дергает `/api/health`.

---

## 4. GitHub: первый пуш

```bash
git init
git add .
git commit -m "Drone Alert Radar: карта регионов и точки назначения"
git branch -M main
git remote add origin https://github.com/ВАШ_ЛОГИН/drone-radar.git
git push -u origin main
```

При первом пуше Git Credential Manager откроет браузер для входа.
После пуша GitHub Actions выполнит тесты бэкенда и сборку фронтенда
(`.github/workflows/ci.yml`).

---

## 5. Если что-то не работает

| Симптом | Причина и решение |
|---|---|
| `Connection to Telegram failed` | сервер не видит Telegram. Проверьте `curl -I https://t.me`; при блокировке задайте `TELEGRAM_PROXY` или выберите зарубежный хостинг |
| `Session not authorized` | сессия не перенесена или отозвана. Повторите `telegram_login.py` |
| Карта пустая, API отвечает | проверьте `VITE_API_URL`/прокси `/api` и `CORS_ORIGINS` |
| Данные не обновляются | `docker compose logs poller`; проверьте, что контейнер `poller` запущен |
| Нет регионов на карте | не загрузился `frontend/public/data/russia-regions.geojson`; проверьте сеть в браузере |
