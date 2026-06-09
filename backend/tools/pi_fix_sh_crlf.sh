#!/bin/bash
# Убрать Windows CRLF из .sh после scp с ПК. Запуск на Pi:
#   bash ~/drone/backend/tools/pi_fix_sh_crlf.sh
set -eu
for f in ~/drone/backend/tools/*.sh; do
  if [ -f "$f" ]; then
    sed -i 's/\r$//' "$f"
    chmod +x "$f"
    echo "fixed: $f"
  fi
done
echo "OK — перезапусти: bash ~/drone/backend/tools/check_pi_vision.sh"
