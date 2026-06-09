## Unity симулятор (MVP) — управление через наш Backend

Цель: **Tango 2 / геймпад → Frontend → Backend → Unity** (ручное управление + миссии).

Unity играет роль "виртуального полётника": принимает команды по UDP и отправляет телеметрию по UDP.

### Требования
- Windows + Unity **2022 LTS** (или любая LTS)
- Наш backend запущен с профилем `unity_sim`

### Сеть/порты (по умолчанию)
- Unity слушает команды: `udp://127.0.0.1:15000`
- Backend слушает телеметрию: `udp://0.0.0.0:15001`

Настраивается через `.env`:

```env
DRONE_BACKEND_PROFILE=unity_sim
DRONE_UNITY_CMD_HOST=127.0.0.1
DRONE_UNITY_CMD_PORT=15000
DRONE_UNITY_TELEM_PORT=15001
```

### Шаги в Unity (самый простой старт)
1) Создай новый 3D проект.
2) Создай объект `Drone` (Empty) и добавь к нему:
   - `Rigidbody`
   - `UnitySimDroneController`
   - `UnityUdpBridge`
3) В `Drone` добавь примитивы для вида (Cube для корпуса, 4 цилиндра для моторов — как хочешь).
4) Создай `Plane` как землю.
5) Запусти Play.

### Скрипты
Скрипты лежат в `sim-unity/Assets/Scripts/`.
Скопируй папку `Assets` в свой Unity проект (или просто эти `.cs` в `Assets/Scripts/`).

### Что должно работать
- `manual-control` из нашего UI будет наклонять/ускорять "дрон" (Rigidbody).
- `/api/telemetry/ws` начнёт показывать `lat/lon/alt/heading` из Unity.
- Миссии (`/api/mission`) будут вызывать `arm/takeoff/goto/rtl/land` на Unity стороне.

### Примечание (важно)
Это MVP: физика упрощённая. Мы делаем “приятную” управляемость, а не аэродинамическую модель.

