# Остановка всех процессов Drone Alert Radar.
# Запускается из «Остановить радар.bat» или вручную.

$ErrorActionPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Stop-Tree([int]$processId, [string]$reason) {
    & taskkill /PID $processId /T /F 2>$null | Out-Null
    Write-Host "  остановлено: PID $processId ($reason)"
}

# 1. Лаунчер и его цикл проверки источников.
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*launcher.ps1*' -and $_.ProcessId -ne $PID } |
    ForEach-Object { Stop-Tree $_.ProcessId "лаунчер" }

Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*scripts\poll_sources.py*' } |
    ForEach-Object { Stop-Tree $_.ProcessId "проверка источников" }

# 2. Постоянный Telegram-воркер (если был запущен вручную).
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*-m app.ingestion.telegram_listener*' } |
    ForEach-Object { Stop-Tree $_.ProcessId "telegram listener" }

# 3. Сервисы по портам (backend/frontend могли быть запущены отдельно).
foreach ($port in 8000, 5173) {
    $owners = Get-NetTCPConnection -State Listen -LocalPort $port |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($owner in $owners) {
        Stop-Tree $owner "порт $port"
    }
}

Write-Host "Drone Alert Radar остановлен." -ForegroundColor Green
