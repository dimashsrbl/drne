# Деплой track + vision с Windows на Pi (testv1@192.168.40.199).
# Запуск в PowerShell из корня репо:
#   .\backend\tools\pi_deploy_track_from_windows.ps1
param(
  [string]$Pi = "testv1@192.168.1.198",
  [string]$RemoteRoot = "~/drone"
)

$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path "$root\backend\app\main.py")) {
  $root = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
}

Write-Host "Repo: $root"
Write-Host "Target: ${Pi}:${RemoteRoot}"

$backendFiles = @(
  "backend/app/services/betaflight_control.py",
  "backend/app/services/betaflight_track.py",
  "backend/app/services/vision_tracker_client.py",
  "backend/app/services/betaflight_port_lock.py",
  "backend/app/services/betaflight_telemetry.py",
  "backend/app/api/routes/betaflight.py",
  "backend/app/schemas/betaflight.py",
  "backend/app/core/config.py",
  "backend/tools/check_pi_vision.sh",
  "backend/tools/pi_install_vision_tracker.sh"
)

foreach ($f in $backendFiles) {
  $local = Join-Path $root $f
  if (-not (Test-Path $local)) { throw "Missing $local" }
  $remoteDir = ($f -replace '/[^/]+$','') -replace '\\','/'
  ssh $Pi "mkdir -p ${RemoteRoot}/${remoteDir}"
  scp $local "${Pi}:${RemoteRoot}/${f}"
  Write-Host "OK $f"
}

# vision-tracker (код + systemd unit)
ssh $Pi "mkdir -p ${RemoteRoot}/vision-tracker/deploy"
scp -r (Join-Path $root "vision-tracker/app") "${Pi}:${RemoteRoot}/vision-tracker/"
scp (Join-Path $root "vision-tracker/requirements.txt") "${Pi}:${RemoteRoot}/vision-tracker/"
scp (Join-Path $root "vision-tracker/deploy/vision-tracker.service") "${Pi}:${RemoteRoot}/vision-tracker/deploy/"

Write-Host ""
Write-Host "На Pi выполни:"
Write-Host "  bash ~/drone/backend/tools/pi_install_vision_tracker.sh"
Write-Host "  sudo systemctl restart drone-mission"
Write-Host "  bash ~/drone/backend/tools/check_pi_vision.sh"
