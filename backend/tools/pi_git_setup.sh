#!/usr/bin/env bash
# Однократная привязка ~/drone к GitHub (сохраняет .env и .venv).
# На Pi:
#   bash ~/drone/backend/tools/pi_git_setup.sh
set -euo pipefail

REPO_URL="${DRONE_GIT_URL:-git@github.com:dimashsrbl/drne.git}"
ROOT="${DRONE_ROOT:-$HOME/drone}"

if [[ ! -d "$ROOT/backend" ]]; then
  echo "FAIL: нет $ROOT/backend — ожидается ~/drone"
  exit 1
fi

cd "$ROOT"

if [[ -d .git ]]; then
  echo "Git уже настроен в $ROOT"
  git remote -v
  git status -sb
  exit 0
fi

echo "=== git init в $ROOT ==="
git init -b main
git remote add origin "$REPO_URL"
git fetch origin main
git reset --hard origin/main
git branch --set-upstream-to=origin/main main

echo ""
echo "OK: ~/drone привязан к $REPO_URL (ветка main)"
echo "Дальше обновления: bash ~/drone/backend/tools/pi_git_pull.sh"
echo ""
echo "Сохранены локально (не в git): backend/.env, vision-tracker/.env, .venv/"
