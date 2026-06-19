## Unity симулятор — геймпад → Unity (без браузера)

**Геймпад USB → Unity → физика → камера.** Backend и браузер **не нужны** для полёта.

### Запуск (только Unity)

1. Сцена: **Plane** + **Drone** (`Rigidbody` + `DroneSimBootstrap`)
2. Пульт по **USB**
3. **Play ▶**
4. **Start** = ARM → газ → летишь
5. **C** = камера FPV/chase

В левом верхнем углу Game — HUD с именем пульта.

### Bootstrap настройки (Inspector)

| Поле | По умолчанию | Зачем |
|------|--------------|-------|
| **Use Local Gamepad** | ✅ | Прямой геймпад |
| **Use Backend Udp** | ❌ | Только если нужен backend |

### Backend (опционально)

Нужен **только** если хочешь тот же путь, что прод (`React → backend → Unity`).  
Для «просто полетать» — **не запускай**.

### Скрипты

```
Assets/Scripts/
├── Input/LocalGamepadInput.cs   ← геймпад
├── Physics/  Flight/  Sim/  Camera/
└── Bridge/                      ← только если useBackendUdp
```

План: [ROADMAP.md](./ROADMAP.md)
