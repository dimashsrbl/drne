# Фаза 0 — установка ArduCopter SITL

## Сделано автоматически

- **QGroundControl** — установщик скачан: `drone/tools/QGroundControl-installer.exe`. Запусти его и установи QGC.
- **Скрипты установки ArduCopter SITL** в WSL:
  - `scripts/wsl-install-ardupilot.sh` — ставит ArduPilot и зависимости в Ubuntu (WSL).
  - `scripts/wsl-run-ardupilot.sh` — запускает симуляцию ArduCopter SITL.

## Что сделать вручную (один раз)

### 1. Установить WSL2 и Ubuntu

В **PowerShell от имени администратора**:

```powershell
wsl --install -d Ubuntu-22.04
```

Перезагрузи ПК. После входа в Windows открой **Ubuntu** из меню Пуск, создай пользователя и пароль.

### 2. Установить ArduCopter SITL внутри WSL

В терминале **Ubuntu (WSL)**:

```bash
cd /mnt/c/Users/Пользователь/Desktop/work/drone/scripts
chmod +x wsl-install-ardupilot.sh
./wsl-install-ardupilot.sh
```

Скрипт клонирует ArduPilot, поставит зависимости и соберёт SITL-бинарник.  
Первая сборка занимает 10–20 минут.

### 3. Запуск симуляции

```bash
cd /mnt/c/Users/Пользователь/Desktop/work/drone/scripts
./wsl-run-ardupilot.sh
```

Будет запущен `sim_vehicle.py` с ArduCopter quad.

Порты:
| Порт | Протокол | Назначение |
|---|---|---|
| 5760 | TCP | MAVLink (наш backend) |
| 14550 | UDP | QGroundControl |
| 14551 | UDP | Доп. вывод |

### 4. QGroundControl (Windows → WSL)

Узнай IP-адрес WSL:
```powershell
wsl hostname -I
```

В QGC: **Q → Application Settings → Comm Links → Add** — тип **UDP**, порт **14550**, хост = IP WSL. Подключись и проверь.

### 5. Готово

После этого Фаза 0 считается выполненной: ArduCopter SITL работает, QGC подключается, backend видит MAVLink через TCP 5760.
