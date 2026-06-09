#!/bin/bash
# Фаза 0: установка ArduCopter SITL в WSL (Ubuntu 22.04)
# Запуск из WSL: ./wsl-install-ardupilot.sh

set -e
echo "=== Установка ArduCopter SITL (Фаза 0) ==="

cd ~

# 1. Системные зависимости
echo "[1/4] Установка зависимостей..."
sudo apt-get update -qq
sudo apt-get install -y git python3 python3-pip python3-venv \
    python3-dev build-essential ccache g++ gawk wget

# 2. Клонирование ArduPilot
if [ ! -d "ardupilot" ]; then
  echo "[2/4] Клонирование ardupilot..."
  git clone https://github.com/ArduPilot/ardupilot.git --recurse-submodules
else
  echo "[2/4] ardupilot уже есть, обновляю..."
  cd ardupilot && git pull && git submodule update --recursive && cd ~
fi

# 3. Установка зависимостей ArduPilot
echo "[3/4] Установка зависимостей ArduPilot (install-prereqs-ubuntu.sh)..."
cd ~/ardupilot
Tools/environment_install/install-prereqs-ubuntu.sh -y
. ~/.profile

# 4. Первая сборка ArduCopter SITL
echo "[4/4] Первая сборка ArduCopter SITL..."
./waf configure --board sitl
./waf copter

echo ""
echo "Готово! Для запуска симуляции:"
echo "  ./wsl-run-ardupilot.sh"
echo ""
echo "Порты по умолчанию:"
echo "  TCP 5760  — основной MAVLink (подключается наш бэкенд)"
echo "  UDP 14550 — GCS (QGroundControl)"
echo "  UDP 14551 — дополнительный вывод"
