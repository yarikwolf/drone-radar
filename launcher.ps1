# Единый запуск Drone Alert Radar.
#
# 1. Поднимает backend (FastAPI) и frontend (Vite).
# 2. Открывает сайт в браузере.
# 3. Каждые 5 минут проверяет Telegram-источники карты.
#
# Остановка: Ctrl+C в этом окне или «Остановить радар.bat».
# Интервал можно переопределить переменной окружения RADAR_POLL_SECONDS.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$logs = Join-Path $root "logs"
$sourcesLog = Join-Path $logs "sources.log"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
try { $Host.UI.RawUI.WindowTitle = "Drone Alert Radar — лаунчер" } catch { }

$pollSeconds = 300
if ($env:RADAR_POLL_SECONDS -match '^\d+$') {
    $pollSeconds = [int]$env:RADAR_POLL_SECONDS
}

$backendProc = $null
$frontendProc = $null
$failed = $false

function Write-Step([string]$message) {
    Write-Host ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $message) -ForegroundColor Cyan
}

function Write-Warn([string]$message) {
    Write-Host ("          {0}" -f $message) -ForegroundColor Yellow
}

function Add-SourceLog([string]$message) {
    $line = "[{0}] {1}{2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message, [Environment]::NewLine
    [System.IO.File]::AppendAllText($sourcesLog, $line, [System.Text.UTF8Encoding]::new($false))
}

function Test-Port([int]$port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Stop-ProcessTree($process) {
    if ($null -ne $process -and -not $process.HasExited) {
        & taskkill /PID $process.Id /T /F 2>$null | Out-Null
    }
}

function Wait-Http([string]$url, [int]$seconds, [string]$name) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try {
            $response = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
            # сервис ещё поднимается
        }
        Start-Sleep -Seconds 1
    }
    Write-Warn "$name не ответил за $seconds секунд"
    return $false
}

function Stop-StaleListeners() {
    # Постоянный слушатель Telegram больше не нужен: источники опрашиваются
    # по расписанию. Гасим его, чтобы не было двух подключений к аккаунту.
    $stale = Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -like '*-m app.ingestion.telegram_listener*' }
    foreach ($item in $stale) {
        Write-Step "Останавливаю старый Telegram-воркер (PID $($item.ProcessId))"
        & taskkill /PID $item.ProcessId /T /F 2>$null | Out-Null
    }
}

# Защита от второго лаунчера: иначе два цикла будут опрашивать Telegram.
$existing = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*launcher.ps1*' -and $_.ProcessId -ne $PID }
if ($existing) {
    Write-Host ""
    Write-Host "Лаунчер уже запущен (PID $($existing[0].ProcessId))." -ForegroundColor Yellow
    Write-Host "Остановите его через «Остановить радар.bat» или Ctrl+C в его окне."
    Start-Sleep -Seconds 4
    exit 0
}

try {
    try { Clear-Host } catch { }
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════╗" -ForegroundColor DarkCyan
    Write-Host "  ║   РАДАР БЕСПИЛОТНОЙ ОПАСНОСТИ — ЗАПУСК      ║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════╝" -ForegroundColor DarkCyan
    Write-Host ""

    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "Python не найден в PATH. Установите Python 3.11+ и повторите запуск."
    }

    Stop-StaleListeners

    # --- Backend ---
    if (Test-Port 8000) {
        Write-Step "Backend уже работает на http://127.0.0.1:8000 — использую его"
    } else {
        Write-Step "Запускаю backend (http://127.0.0.1:8000)"
        $backendCmd = "title Radar Backend && cd /d `"$root\backend`" && python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> `"$logs\backend.log`" 2>&1"
        $backendProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $backendCmd -WindowStyle Hidden -PassThru
    }

    # --- Frontend ---
    if (Test-Port 5173) {
        Write-Step "Сайт уже работает на http://localhost:5173 — использую его"
    } else {
        if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
            throw "Node.js/npm не найдены в PATH. Установите Node.js 18+ и повторите запуск."
        }
        if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
            Write-Step "Устанавливаю зависимости фронтенда (npm install) — это один раз..."
            & cmd.exe /c "cd /d `"$root\frontend`" && npm install >> `"$logs\frontend-install.log`" 2>&1"
            if ($LASTEXITCODE -ne 0) {
                throw "npm install завершился с ошибкой. Смотрите logs\frontend-install.log"
            }
        }
        Write-Step "Запускаю сайт (http://localhost:5173)"
        $frontendCmd = "title Radar Frontend && cd /d `"$root\frontend`" && npm run dev >> `"$logs\frontend.log`" 2>&1"
        $frontendProc = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $frontendCmd -WindowStyle Hidden -PassThru
    }

    # --- Ожидание готовности ---
    Write-Step "Жду готовности API..."
    if (-not (Wait-Http "http://127.0.0.1:8000/api/health" 60 "Backend")) {
        throw "Backend не запустился. Откройте logs\backend.log"
    }

    Write-Step "Жду готовности сайта..."
    if (-not (Wait-Http "http://127.0.0.1:5173/" 90 "Frontend")) {
        throw "Сайт не запустился. Откройте logs\frontend.log"
    }

    Start-Process "http://localhost:5173/"
    Write-Step "Сайт открыт в браузере: http://localhost:5173"
    Write-Host ""
    Write-Host "  Источники проверяются каждые $([math]::Round($pollSeconds / 60, 1)) мин." -ForegroundColor Green
    Write-Host "  Логи: logs\backend.log, logs\frontend.log, logs\sources.log" -ForegroundColor DarkGray
    Write-Host "  Остановка: Ctrl+C или «Остановить радар.bat»." -ForegroundColor DarkGray
    Write-Host ""

    # --- Периодическая проверка источников ---
    while ($true) {
        Add-SourceLog "=== проверка источников ==="
        Write-Step "Проверяю источники карты..."
        $startedAt = Get-Date

        $pollCmd = "cd /d `"$root\backend`" && python scripts\poll_sources.py >> `"$sourcesLog`" 2>&1"
        $poll = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $pollCmd -WindowStyle Hidden -PassThru

        Wait-Process -Id $poll.Id -Timeout 240 -ErrorAction SilentlyContinue

        if (-not $poll.HasExited) {
            Write-Warn "Проверка зависла дольше 4 минут — останавливаю."
            Add-SourceLog "таймаут проверки, процесс остановлен"
            Stop-ProcessTree $poll
        } elseif ($poll.ExitCode -eq 0) {
            $elapsed = [int]((Get-Date) - $startedAt).TotalSeconds
            Write-Host "          Источники проверены за $elapsed c." -ForegroundColor Green
            Add-SourceLog "проверка завершена за $elapsed c"
        } else {
            Write-Warn "Проверка не удалась (код $($poll.ExitCode)). Подробности: logs\sources.log"
            Write-Warn "Если Telegram недоступен — включите VPN (Happ) и дождитесь следующей проверки."
            Add-SourceLog "проверка завершилась с кодом $($poll.ExitCode)"
        }

        Write-Step "Следующая проверка через $([math]::Round($pollSeconds / 60, 1)) мин. Ctrl+C — остановить."
        Start-Sleep -Seconds $pollSeconds
    }
}
catch {
    Write-Host ""
    Write-Host "ОШИБКА: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Логи: $logs" -ForegroundColor Yellow
    $failed = $true
}
finally {
    Write-Host ""
    Write-Step "Останавливаю запущенные сервисы..."
    Stop-ProcessTree $backendProc
    Stop-ProcessTree $frontendProc
    Write-Step "Готово. Окно можно закрыть."
}

if ($failed) {
    exit 1
}
