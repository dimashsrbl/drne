# Фаза 0: подготовка окружения на Windows для ArduCopter SITL
# Запуск: PowerShell от имени администратора, затем:
#   Set-ExecutionPolicy Bypass -Scope Process -Force; .\scripts\phase0-windows.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

Write-Host "=== Фаза 0: ArduCopter SITL + QGroundControl ===" -ForegroundColor Cyan

# 1. WSL
$wslList = wsl -l 2>&1
if ($LASTEXITCODE -ne 0 -or $wslList -match "Установка|Install") {
    Write-Host "`n[1] Устанавливаю WSL2 и Ubuntu 22.04 (потребуется перезагрузка)..." -ForegroundColor Yellow
    wsl --install -d Ubuntu-22.04
    Write-Host "После перезагрузки откройте Ubuntu, задайте логин/пароль. Затем снова запустите этот скрипт." -ForegroundColor Green
    exit 0
}
Write-Host "`n[1] WSL уже установлен." -ForegroundColor Green

# 2. QGroundControl — скачивание установщика
$qgcDir = Join-Path $ProjectRoot "tools"
$qgcExe = Join-Path $qgcDir "QGroundControl-installer.exe"
if (-not (Test-Path $qgcDir)) { New-Item -ItemType Directory -Path $qgcDir -Force | Out-Null }
if (-not (Test-Path $qgcExe)) {
    Write-Host "`n[2] Скачиваю QGroundControl для Windows..." -ForegroundColor Yellow
    $qgcUrl = "https://d176tv9ibo4jno.cloudfront.net/latest/QGroundControl-installer.exe"
    try {
        Invoke-WebRequest -Uri $qgcUrl -OutFile $qgcExe -UseBasicParsing
        Write-Host "Скачано: $qgcExe . Запустите установщик вручную." -ForegroundColor Green
    } catch {
        Write-Host "Скачать не удалось. Установите вручную: https://docs.qgroundcontrol.com/master/en/qgc-user-guide/getting_started/download_and_install.html" -ForegroundColor Red
    }
} else {
    Write-Host "`n[2] QGroundControl установщик уже есть: $qgcExe" -ForegroundColor Green
}

# 3. Инструкция по установке ArduPilot в WSL
$drive = (Get-Item $ProjectRoot).Root.TrimEnd('\').Replace(':', '').ToLower()
$wslPath = "/mnt/$drive" + $ProjectRoot.Substring(2).Replace('\', '/')

Write-Host "`n[3] Установка ArduCopter SITL выполняется внутри WSL." -ForegroundColor Cyan
Write-Host "Выполните в PowerShell:" -ForegroundColor White
Write-Host "  wsl -d Ubuntu-22.04" -ForegroundColor Gray
Write-Host "Затем внутри WSL:" -ForegroundColor White
Write-Host "  cd $wslPath/scripts" -ForegroundColor Gray
Write-Host "  chmod +x wsl-install-ardupilot.sh && ./wsl-install-ardupilot.sh" -ForegroundColor Gray

Write-Host "`n[4] После установки запуск симуляции:" -ForegroundColor Cyan
Write-Host "  cd $wslPath/scripts" -ForegroundColor Gray
Write-Host "  chmod +x wsl-run-ardupilot.sh && ./wsl-run-ardupilot.sh" -ForegroundColor Gray

Write-Host "`n[5] QGroundControl подключение (Windows → WSL):" -ForegroundColor Cyan
Write-Host "  IP адрес WSL: wsl hostname -I" -ForegroundColor Gray
Write-Host "  В QGC добавьте: UDP, порт 14550, хост = IP WSL" -ForegroundColor Gray

Write-Host "`n[6] Бэкенд подключается к ArduPilot через TCP 5760 (localhost)." -ForegroundColor Cyan
Write-Host "  Убедитесь, что WSL-симуляция запущена перед стартом backend." -ForegroundColor Gray
