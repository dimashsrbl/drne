## Gesture-control (прототип “рука → ориентация → дрончик”)

Мини‑проект рядом с основным `drone/`, чтобы быстро прототипировать управление ориентацией
(IMU на STM32/телефон/симуляция) и визуализировать это в браузере.

### Что внутри
- `pc/ws_imu_bridge.py`: читает `roll,pitch,yaw` из COM (или симуляция) и шлёт по WebSocket
- `pc/requirements.txt`: зависимости Python
- `web/index.html`: 3D “дрончик” (примитивы) + подключение к WebSocket

### Быстрый старт (без железа, симуляция)
В одном терминале (Windows):

```powershell
cd C:\Users\d.sarbalin.WT\Desktop\work\drone\gesture-control\pc
py -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe ws_imu_bridge.py --simulate
```

Во втором терминале:

```powershell
cd C:\Users\d.sarbalin.WT\Desktop\work\drone\gesture-control\web
py -m http.server 5173
```

Открой в браузере:
`http://127.0.0.1:5173`

### Подключение реального IMU (позже)
Прошивка STM32 должна слать строки вида:
`roll,pitch,yaw\n` (в градусах), например:
`-3.2,12.5,180.0`

Тогда запускай:

```powershell
.\.venv\Scripts\python.exe ws_imu_bridge.py --port COM7 --baud 115200
```

