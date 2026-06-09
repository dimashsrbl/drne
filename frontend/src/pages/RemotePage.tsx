import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { ASTANA } from '../constants/astana'
import { useGamepad } from '../hooks/useGamepad'
import type { DroneProfile } from '../api/types'

type Vec2 = { x: number; y: number }

function StickPad({
  label,
  value,
  onChange,
  hint,
}: {
  label: string
  value: Vec2
  onChange: (v: Vec2) => void
  hint: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const dragging = useRef(false)

  const norm = useCallback((clientX: number, clientY: number): Vec2 => {
    const el = ref.current
    if (!el) return { x: 0, y: 0 }
    const r = el.getBoundingClientRect()
    const cx = r.left + r.width / 2
    const cy = r.top + r.height / 2
    const x = Math.max(-1, Math.min(1, ((clientX - cx) / (r.width / 2)) * 0.92))
    const y = Math.max(-1, Math.min(1, ((clientY - cy) / (r.height / 2)) * 0.92))
    return { x, y }
  }, [])

  const end = () => {
    dragging.current = false
    onChange({ x: 0, y: 0 })
  }

  return (
    <div>
      <div style={{ fontSize: 12, opacity: 0.85, marginBottom: 6 }}>{label}</div>
      <div
        ref={ref}
        className="stickPad"
        style={{ touchAction: 'none' }}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId)
          dragging.current = true
          onChange(norm(e.clientX, e.clientY))
        }}
        onPointerMove={(e) => {
          if (!dragging.current) return
          onChange(norm(e.clientX, e.clientY))
        }}
        onPointerUp={end}
        onPointerCancel={end}
      >
        <div
          className="stickKnob"
          style={{
            transform: `translate(calc(-50% + ${value.x * 48}px), calc(-50% + ${value.y * 48}px))`,
          }}
        />
      </div>
      <div className="hint" style={{ marginTop: 6, maxWidth: 160 }}>
        {hint}
      </div>
    </div>
  )
}

export function RemotePage() {
  const left = useRef<Vec2>({ x: 0, y: 0 })
  const right = useRef<Vec2>({ x: 0, y: 0 })
  const [, tick] = useState(0)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [profile, setProfile] = useState<DroneProfile | null>(null)

  const gamepad = useGamepad()

  useEffect(() => {
    let dead = false
    void api.profile().then((data) => {
      if (!dead) setProfile(data)
    }).catch(() => {})
    return () => {
      dead = true
    }
  }, [])

  const setLeft = (v: Vec2) => {
    left.current = v
    tick((n) => n + 1)
  }
  const setRight = (v: Vec2) => {
    right.current = v
    tick((n) => n + 1)
  }

  // Синхронизируем виртуальные стики с геймпадом для отображения
  useEffect(() => {
    if (!gamepad.connected) return
    const { roll, pitch, throttle, yaw } = gamepad.axes
    // throttle: 0..1000 → ly: 1 (вниз) .. -1 (вверх)
    const ly = 1 - (throttle / 1000) * 2
    left.current  = { x: yaw / 1000,   y: ly }
    right.current = { x: roll / 1000,  y: -pitch / 1000 }
    tick((n) => n + 1)
  }, [gamepad.axes, gamepad.connected])

  useEffect(() => {
    if (profile && !profile.supports_manual_control) return
    const hz = 14
    const id = window.setInterval(() => {
      let thrust: number, yaw: number, roll: number, pitch: number

      if (gamepad.connected) {
        // Читаем из физического пульта
        thrust = gamepad.axes.throttle
        yaw    = gamepad.axes.yaw
        roll   = gamepad.axes.roll
        pitch  = gamepad.axes.pitch
      } else {
        // Читаем из виртуальных стиков
        const lx = left.current.x
        const ly = left.current.y
        const rx = right.current.x
        const ry = right.current.y
        thrust = Math.round(((1 - ly) / 2) * 1000)
        yaw    = Math.round(lx * 1000)
        roll   = Math.round(rx * 1000)
        pitch  = Math.round(-ry * 1000)
      }

      void fetch('/api/drone/manual-control', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pitch, roll, thrust, yaw }),
      }).catch(() => {})
    }, 1000 / hz)
    return () => window.clearInterval(id)
  }, [gamepad.connected, gamepad.axes, profile])

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    setErr(null)
    try {
      await fn()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="pultWrap">
      <section className="card">
        <div className="cardTitle">Астана и режим</div>
        {err ? (
          <div className="alert" style={{ marginBottom: 12 }}>
            <div className="alertTitle">Ошибка</div>
            <div className="alertBody">{err}</div>
          </div>
        ) : null}
        <p className="hint" style={{ marginBottom: 12 }}>
          <b>Дом (RTL):</b> задать координаты дома на контроллере. В SITL стартовая позиция дрона задаётся симулятором;
          для взлёта «из Астаны» в мире см. README (переменная DRONE_MAVLINK_CONNECTION / SITL сценарий).
        </p>
        {profile && (
          <div className="hint" style={{ marginBottom: 12 }}>
            Профиль: <b>{profile.label}</b>.
            {!profile.supports_manual_control && ' В этом режиме ручные команды с UI отключены, пока bridge не разрешит write-команды.'}
          </div>
        )}
        <div className="kv" style={{ marginBottom: 12 }}>
          <div className="k">широта</div>
          <div className="v">{ASTANA.lat}</div>
          <div className="k">долгота</div>
          <div className="v">{ASTANA.lon}</div>
          <div className="k">AMSL</div>
          <div className="v">{ASTANA.altAmsl} м</div>
        </div>
        <button
          className="btn primary"
          disabled={!!busy}
          onClick={() =>
            void run('home', () => api.setHome(ASTANA.lat, ASTANA.lon, ASTANA.altAmsl))
          }
        >
          Задать дом — Астана
        </button>
        <div className="row" style={{ marginTop: 14, flexWrap: 'wrap' }}>
          <span className="hint" style={{ width: '100%', marginBottom: 6 }}>
            Режим перед пультом (после взлёта удобнее <b>LOITER</b> или <b>POSHOLD</b>):
          </span>
          {(['STABILIZE', 'ALT_HOLD', 'LOITER', 'POSHOLD'] as const).map((m) => (
            <button key={m} className="btn" disabled={!!busy} onClick={() => void run(m, () => api.setFlightMode(m))}>
              {m}
            </button>
          ))}
        </div>
      </section>

      <section className="card pultSticks">
        <div className="cardTitle">Пульт (геймпад / стики)</div>

        {/* Индикатор физического геймпада */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 12,
            padding: '6px 10px',
            borderRadius: 8,
            background: gamepad.connected ? 'rgba(34,197,94,0.15)' : 'rgba(255,255,255,0.06)',
            border: `1px solid ${gamepad.connected ? 'rgba(34,197,94,0.4)' : 'rgba(255,255,255,0.12)'}`,
            fontSize: 13,
          }}
        >
          <span style={{ fontSize: 16 }}>{gamepad.connected ? '🎮' : '⌨️'}</span>
          {gamepad.connected ? (
            <span style={{ color: '#4ade80' }}>
              <b>Пульт подключён</b>
              <span style={{ opacity: 0.7, marginLeft: 6, fontSize: 11 }}>{gamepad.name}</span>
            </span>
          ) : (
            <span style={{ opacity: 0.6 }}>Физический пульт не обнаружен — работают виртуальные стики</span>
          )}
        </div>

        {gamepad.connected && (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(4, 1fr)',
              gap: 6,
              marginBottom: 12,
              fontSize: 12,
              textAlign: 'center',
            }}
          >
            {(['roll', 'pitch', 'throttle', 'yaw'] as const).map((k) => (
              <div key={k} style={{ background: 'rgba(255,255,255,0.05)', borderRadius: 6, padding: '4px 6px' }}>
                <div style={{ opacity: 0.5, textTransform: 'uppercase', fontSize: 10, marginBottom: 2 }}>{k}</div>
                <div style={{ fontFamily: 'monospace', fontWeight: 700 }}>{gamepad.axes[k]}</div>
              </div>
            ))}
          </div>
        )}

        <p className="hint" style={{ marginBottom: 16 }}>
          Левый: <b>газ</b> (вверх — больше тяги), <b>рысканье</b>. Правый: <b>крен / тангаж</b>. Удерживайте стики —
          команды уходят ~14 Гц. На земле газ внизу = минимум.
        </p>
        <div className="pultGrid">
          <StickPad
            label="Левый"
            value={left.current}
            onChange={setLeft}
            hint="вверх — подъём, влево-вправо — поворот"
          />
          <div className="pultMid">
            <div className="row" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
              <button className="btn" disabled={!!busy || (profile != null && !profile.supports_direct_commands)} onClick={() => void run('arm', () => api.arm())}>
                ARM
              </button>
              <button className="btn" disabled={!!busy || (profile != null && !profile.supports_direct_commands)} onClick={() => void run('disarm', () => api.disarm())}>
                DISARM
              </button>
              <button className="btn primary" disabled={!!busy || (profile != null && !profile.supports_missions)} onClick={() => void run('to', () => api.takeoff(10))}>
                Взлёт 10 м
              </button>
              <button className="btn danger" disabled={!!busy || (profile != null && !profile.supports_missions)} onClick={() => void run('ld', () => api.land())}>
                Посадка
              </button>
            </div>
          </div>
          <StickPad
            label="Правый"
            value={right.current}
            onChange={setRight}
            hint="вперёд/назад и влево/вправо"
          />
        </div>
      </section>
    </div>
  )
}
