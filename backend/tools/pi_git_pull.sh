#!/usr/bin/env bash
# Обновить код на Pi из GitHub и перезапустить сервисы.
# На Pi:
#   bash ~/drone/backend/tools/pi_git_pull.sh
set -euo pipefail

ROOT="${DRONE_ROOT:-$HOME/drone}"
BRANCH="${DRONE_GIT_BRANCH:-main}"

cd "$ROOT"

if [[ ! -d .git ]]; then
  echo "FAIL: в $ROOT нет .git — сначала: bash ~/drone/backend/tools/pi_git_setup.sh"
  exit 1
fi

echo "=== git pull ($BRANCH) ==="
git fetch origin "$BRANCH"
git reset --hard "origin/$BRANCH"
git log -1 --oneline

echo ""
echo "=== restart services ==="
if command -v systemctl >/dev/null 2>&1; then
  sudo systemctl restart drone-mission
  systemctl is-active drone-mission
  if systemctl list-unit-files vision-tracker.service >/dev/null 2>&1; then
    sudo systemctl restart vision-tracker 2>/dev/null || true
    systemctl is-active vision-tracker 2>/dev/null || true
  fi
fi

echo ""
echo "OK — код обновлён. .env и .venv не трогались."
