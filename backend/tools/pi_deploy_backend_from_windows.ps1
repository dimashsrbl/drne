# Деплой backend (баро-взлёт, track, config) на Pi.
# PowerShell из корня репо:
#   .\backend\tools\pi_deploy_backend_from_windows.ps1
param(
  [string]$Pi = "testv1@192.168.1.198",
  [string]$RemoteRoot = "~/drone"
)

$ErrorActionPreference = "Stop"
$root = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path "$root\backend\app\main.py")) {
  $root = (Split-Path $PSScriptRoot -Parent) | Split-Path -Parent
}

Write-Host "Repo: $root"
Write-Host "Target: ${Pi}:${RemoteRoot}"
Write-Host ""

$files = @(
  "backend/app/services/betaflight_control.py",
  "backend/app/services/betaflight_track.py",
  "backend/app/services/vision_tracker_client.py",
  "backend/app/services/betaflight_port_lock.py",
  "backend/app/services/betaflight_telemetry.py",
  "backend/app/api/routes/betaflight.py",
  "backend/app/schemas/betaflight.py",
  "backend/app/core/config.py",
  "backend/tools/test_mission_start.json"
)

foreach ($f in $files) {
  $local = Join-Path $root $f
  if (-not (Test-Path $local)) { throw "Missing $local" }
  $remoteDir = ($f -replace '/[^/]+$', '') -replace '\\', '/'
  ssh $Pi "mkdir -p ${RemoteRoot}/${remoteDir}"
  scp $local "${Pi}:${RemoteRoot}/${f}"
  Write-Host "OK $f"
}

Write-Host ""
Write-Host "=== На Pi (SSH) ==="
Write-Host "  sudo systemctl restart drone-mission"
Write-Host "  systemctl is-active drone-mission"
Write-Host "  curl -s http://127.0.0.1:8000/health"
Write-Host ""
Write-Host "Опционально в ~/drone/backend/.env:"
Write-Host "  DRONE_BACKEND_PROFILE=bob57_bridge"
Write-Host "  DRONE_BETAFLIGHT_PORT=/dev/serial0"
Write-Host "  DRONE_BETAFLIGHT_ALT_HOVER_US=1410"
Write-Host "  DRONE_BETAFLIGHT_ALT_P_GAIN=70"
Write-Host "  DRONE_BETAFLIGHT_ALT_MAX_CLIMB_US=1440"
Write-Host "  DRONE_BETAFLIGHT_LAND_THROTTLE_US=1080"
