import { useCallback, useEffect, useRef, useState } from 'react'
import type { Waypoint } from '../scene/DroneScene'
import { DroneScene } from '../scene/DroneScene'
import { useGamepad } from '../hooks/useGamepad'
import { useTelemetry } from '../telemetry/TelemetryProvider'

// ── Утилита отправки команд дрону ───────────────────────────────
async function cmd(path: string, body?: object): Promise<void> {
  await fetch(`/api/drone/${path}`, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
}

async function setWind(speedMs: number, dirDeg: number): Promise<void> {
  await fetch('/api/drone/sim/wind', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ speed_ms: speedMs, direction_deg: dirDeg }),
  })
}

/** Дождаться когда дрон окажется в радиусе maxDist от точки (опрос HTTP) */
async function waitUntilNear(
  targetLat: number,
  targetLon: number,
  maxDistM: number,
  timeoutS: number,
  abortSignal: { aborted: boolean },
): Promise<boolean> {
  const deadline = Date.now() + timeoutS * 1000
  const DEG_TO_M = 111_000
  while (Date.now() < deadline && !abortSignal.aborted) {
    try {
      const res  = await fetch('/api/telemetry')
      const data = await res.json() as { lat?: number; lon?: number }
      if (data.lat != null && data.lon != null) {
        const dn = (data.lat - targetLat) * DEG_TO_M
        const de = (data.lon - targetLon) * DEG_TO_M * Math.cos(targetLat * Math.PI / 180)
        if (Math.sqrt(dn * dn + de * de) < maxDistM) return true
      }
    } catch { /* игнорируем */ }
    await new Promise((r) => setTimeout(r, 500))
  }
  return false
}

/** Дождаться когда дрон наберёт минимальную высоту */
async function waitUntilAlt(
  minAlt: number,
  timeoutS: number,
  abortSignal: { aborted: boolean },
): Promise<boolean> {
  const deadline = Date.now() + timeoutS * 1000
  while (Date.now() < deadline && !abortSignal.aborted) {
    try {
      const res  = await fetch('/api/telemetry')
      const data = await res.json() as { alt?: number }
      if ((data.alt ?? 0) >= minAlt) return true
    } catch { /* игнорируем */ }
    await new Promise((r) => setTimeout(r, 400))
  }
  return false
}

let _wpCounter = 0

// ── Компонент ───────────────────────────────────────────────────
export function Sim3DPage() {
  const canvasRef  = useRef<HTMLCanvasElement>(null)
  const sceneRef   = useRef<DroneScene | null>(null)

  const { telemetry, wsStatus } = useTelemetry()
  const gamepad = useGamepad()

  // Ветер
  const [windSpeed, setWindSpeed]   = useState(0)
  const [windDir,   setWindDir]     = useState(0)
  const windApplied = useRef({ speed: 0, dir: 0 })

  // Команды
  const [cmdLog, setCmdLog] = useState<string[]>([])
  const [busy, setBusy]     = useState(false)

  // Пульт
  const prevSwa = useRef<number>(0)
  const prevSwd = useRef<number>(0)
  const gamepadRef = useRef(gamepad)
  gamepadRef.current = gamepad

  // ref синхронизирован с state для использования внутри setInterval
  const missionRunningRef = useRef(false)

  // ── Миссия ──────────────────────────────────────────────────
  const [waypoints,    setWaypoints]    = useState<Waypoint[]>([])
  const [placingMode,  setPlacingMode]  = useState(false)
  const [missionRunning, setMissionRunning] = useState(false)
  // обёртка чтобы держать ref в синхроне
  const setMissionRunningSync = (v: boolean) => {
    missionRunningRef.current = v
    setMissionRunning(v)
  }
  const [activeWpIdx,  setActiveWpIdx]  = useState(-1)
  const [missionAlt,   setMissionAlt]   = useState(15)
  const missionAbort = useRef({ aborted: false })

  // Камера
  const [camMode,   setCamMode]   = useState<'third' | 'fpv'>('third')
  const [fpvTilt,   setFpvTilt]   = useState(25)

  const log = useCallback((msg: string) => {
    setCmdLog((prev) => [`${new Date().toLocaleTimeString()} ${msg}`, ...prev].slice(0, 20))
  }, [])

  const run = useCallback(async (label: string, fn: () => Promise<void>) => {
    if (busy) return
    setBusy(true)
    try {
      await fn()
      log(`✓ ${label}`)
    } catch {
      log(`✗ ${label}`)
    } finally {
      setBusy(false)
    }
  }, [busy, log])

  // ── Инициализация Three.js сцены ────────────────────────────
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const scene = new DroneScene(canvas)
    sceneRef.current = scene

    // Обработчик клика по земле
    scene.onGroundClick((lat, lon) => {
      setWaypoints((prev) => {
        const next = [...prev, { id: ++_wpCounter, lat, lon }]
        return next
      })
    })

    return () => {
      scene.dispose()
      sceneRef.current = null
    }
  }, [])

  // ── Синхронизировать режим расстановки со сценой ──────────
  useEffect(() => {
    sceneRef.current?.setPlacingMode(placingMode)
  }, [placingMode])

  // ── Синхронизировать режим камеры со сценой ───────────────
  useEffect(() => {
    sceneRef.current?.setCameraMode(camMode)
  }, [camMode])

  useEffect(() => {
    sceneRef.current?.setFpvTilt(fpvTilt)
  }, [fpvTilt])

  // ── Esc выходит из режима расстановки ────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setPlacingMode(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // ── Синхронизировать маркеры точек со сценой ──────────────
  useEffect(() => {
    sceneRef.current?.setWaypoints(waypoints, activeWpIdx)
  }, [waypoints, activeWpIdx])

  // ── Запустить миссию ──────────────────────────────────────
  const startMission = useCallback(async () => {
    if (waypoints.length === 0) { log('Нет точек маршрута'); return }
    if (missionRunning) return

    missionAbort.current = { aborted: false }
    setMissionRunningSync(true)
    setPlacingMode(false)
    log(`Миссия: ${waypoints.length} точек, высота ${missionAlt} м`)

    try {
      // ARM (если уже armed — переключит в GUIDED)
      log('ARM...')
      await cmd('arm').catch((e) => log(`ARM err: ${String(e)}`))
      await new Promise((r) => setTimeout(r, 400))

      // Взлёт
      log(`Взлёт → ${missionAlt} м...`)
      await cmd('takeoff', { altitude: missionAlt })
      // Ждём набора 75% высоты
      const tookOff = await waitUntilAlt(missionAlt * 0.75, 30, missionAbort.current)
      if (!tookOff) {
        log(`⚠ Взлёт не завершён за 30с — принудительно продолжаем`)
      } else {
        log(`✓ Высота набрана`)
      }

      for (let i = 0; i < waypoints.length; i++) {
        if (missionAbort.current.aborted) break
        const wp = waypoints[i]
        setActiveWpIdx(i)
        await cmd('goto', { lat: wp.lat, lon: wp.lon, alt: missionAlt })
        log(`WP ${i + 1}/${waypoints.length} → (${wp.lat.toFixed(5)}, ${wp.lon.toFixed(5)})`)

        const arrived = await waitUntilNear(wp.lat, wp.lon, 4.0, 60, missionAbort.current)
        if (arrived) {
          log(`✓ WP ${i + 1} достигнута`)
        } else {
          log(`⏱ WP ${i + 1} таймаут 60с — следующая`)
        }
      }

      if (!missionAbort.current.aborted) {
        setActiveWpIdx(-1)
        await cmd('return-home')
        log('Миссия завершена → RTL')
      } else {
        log('Миссия прервана')
      }
    } catch (e) {
      log(`Ошибка миссии: ${String(e)}`)
    } finally {
      setActiveWpIdx(-1)
      setMissionRunningSync(false)
    }
  }, [waypoints, missionRunning, missionAlt, log])

  const stopMission = useCallback(() => {
    missionAbort.current.aborted = true
    void cmd('return-home')
    log('Миссия остановлена → RTL')
  }, [log])

  // ── Передавать телеметрию в сцену ───────────────────────────
  useEffect(() => {
    if (sceneRef.current && telemetry) {
      sceneRef.current.update({
        lat:     telemetry.lat     ?? null,
        lon:     telemetry.lon     ?? null,
        alt:     telemetry.alt     ?? null,
        heading: telemetry.heading ?? null,
        roll:    null,
        pitch:   null,
        speed:   telemetry.speed   ?? null,
        armed:   telemetry.armed   ?? null,
        battery: telemetry.battery ?? null,
        mode:    telemetry.mode    ?? null,
      })
    }
  }, [telemetry])

  // ── Передавать ветер в сцену ─────────────────────────────────
  useEffect(() => {
    if (sceneRef.current) {
      sceneRef.current.setWind(windSpeed, windDir)
    }
  }, [windSpeed, windDir])

  // ── Управление пультом: стики → manual-control ──────────────
  useEffect(() => {
    const hz = 14
    const id = window.setInterval(async () => {
      const gp = gamepadRef.current
      if (!gp.connected) return

      // Во время автомиссии НЕ слать ручные команды — они переключают режим на MANUAL
      if (!missionRunningRef.current) {
        const { roll, pitch, throttle, yaw } = gp.axes
        void fetch('/api/drone/manual-control', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ pitch, roll, thrust: throttle, yaw }),
        }).catch(() => {})
      }

      // Edge-detect переключателей — только вне миссии
      // (во время миссии ARM/DISARM не должны срабатывать от стиков)
      const swa = gp.switches.swa
      const swd = gp.switches.swd

      if (!missionRunningRef.current) {
        if (swa !== prevSwa.current) {
          prevSwa.current = swa
          if (swa === 1) {
            void cmd('arm').then(() => log('SWA↑ → ARM'))
          } else if (swa === -1) {
            void cmd('disarm').then(() => log('SWA↓ → DISARM'))
          }
        }
        if (swd !== prevSwd.current) {
          prevSwd.current = swd
          if (swd === 1) {
            void cmd('return-home').then(() => log('SWD↑ → RTL'))
          }
        }
      } else {
        // Обновляем ref чтобы не было ложного edge при выходе из миссии
        prevSwa.current = swa
        prevSwd.current = swd
      }
    }, 1000 / hz)
    return () => window.clearInterval(id)
  }, [log])

  // ── Синхронизация ветра с бэкендом ──────────────────────────
  const applyWind = useCallback(async () => {
    if (windApplied.current.speed === windSpeed && windApplied.current.dir === windDir) return
    windApplied.current = { speed: windSpeed, dir: windDir }
    await setWind(windSpeed, windDir)
    log(`Ветер: ${windSpeed} м/с с ${windDir}°`)
  }, [windSpeed, windDir, log])

  // ── Стили ─────────────────────────────────────────────────────

  return (
    <div
      className="gcsSimRoot"
      style={{
        position: 'relative',
        width: '100%',
        flex: 1,
        minHeight: 360,
        overflow: 'hidden',
        background: 'var(--gcs-black-pure, #030503)',
      }}
    >
      {/* Three.js Canvas */}
      <canvas
        ref={canvasRef}
        style={{ display: 'block', width: '100%', height: '100%' }}
      />

      {/* Кнопка "Найти дрон" — только в 3rd person */}
      {camMode === 'third' && (
        <button
          onClick={() => sceneRef.current?.resetCamera()}
          title="Вернуть камеру к дрону"
          style={{
            position: 'absolute', top: 12, left: '50%',
            transform: 'translateX(calc(-50% + 175px))',
            background: 'rgba(0,0,0,0.6)', color: '#fff',
            border: '1px solid rgba(255,255,255,0.2)',
            borderRadius: 6, padding: '4px 10px',
            cursor: 'pointer', fontSize: 12,
            backdropFilter: 'blur(4px)',
          }}
        >
          📷 К дрону
        </button>
      )}

      {/* ── HUD: левый верх — телеметрия ── */}
      <div style={hudStyle('top-left')}>
        <div style={hudTitle}>
          <span style={{
            display: 'inline-block',
            width: 10, height: 10, borderRadius: '50%',
            background: telemetry?.armed ? '#4ade80' : '#ef4444',
            marginRight: 6,
          }} />
          {telemetry?.armed ? 'ARMED' : 'DISARMED'}
          {telemetry?.mode ? ` · ${telemetry.mode}` : ''}
        </div>
        <HudRow label="Высота AGL"  value={fmt(telemetry?.alt,     1, 'м')} />
        <HudRow label="Скорость"    value={fmt(telemetry?.speed,   1, 'м/с')} />
        <HudRow label="Курс"        value={fmt(telemetry?.heading, 0, '°')} />
        <HudRow label="Батарея"     value={fmt(telemetry?.battery, 0, '%')} color={battColor(telemetry?.battery)} />
        <HudRow label="WS"          value={wsStatus} />
      </div>

      {/* ── HUD: правый верх — команды ── */}
      <div style={hudStyle('top-right')}>
        <div style={hudTitle}>Команды</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <CmdBtn label="ARM"      color="#16a34a" onClick={() => void run('arm',     () => cmd('arm'))} disabled={busy} />
          <CmdBtn label="DISARM"   color="#dc2626" onClick={() => void run('disarm',  () => cmd('disarm'))} disabled={busy} />
          <CmdBtn label="Взлёт 10м" color="#2563eb" onClick={() => void run('takeoff', () => cmd('takeoff', { altitude: 10 }))} disabled={busy} />
          <CmdBtn label="Посадка"  color="#d97706" onClick={() => void run('land',    () => cmd('land'))} disabled={busy} />
          <CmdBtn label="RTL"      color="#7c3aed" onClick={() => void run('rtl',     () => cmd('return-home'))} disabled={busy} />
        </div>

        {/* Пульт + статус блокировки */}
        <div style={{ marginTop: 10, fontSize: 11 }}>
          {gamepad.connected ? (
            <div>
              <span style={{ color: '#4ade80' }}>🎮 {gamepad.name?.slice(0, 18)}</span>
              {missionRunning && (
                <div style={{
                  marginTop: 4, padding: '3px 6px', borderRadius: 4,
                  background: 'rgba(234,179,8,0.2)', border: '1px solid rgba(234,179,8,0.5)',
                  color: '#fbbf24', fontSize: 10, textAlign: 'center',
                }}>
                  🔒 Джойстик заблокирован (миссия)
                </div>
              )}
            </div>
          ) : (
            <span style={{ color: '#9ca3af', opacity: 0.7 }}>⌨️ Нет пульта</span>
          )}
        </div>
        {gamepad.connected && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4, marginTop: 6, fontSize: 10 }}>
            {(['roll','pitch','throttle','yaw'] as const).map(k => (
              <div key={k} style={{ background: 'rgba(255,255,255,0.07)', padding: '2px 4px', borderRadius: 4, textAlign: 'center' }}>
                <span style={{ opacity: 0.5, textTransform: 'uppercase' }}>{k} </span>
                <b style={{ fontFamily: 'monospace' }}>{gamepad.axes[k]}</b>
              </div>
            ))}
            <div style={{ background: 'rgba(255,255,255,0.07)', padding: '2px 4px', borderRadius: 4, gridColumn: '1 / -1', textAlign: 'center' }}>
              <span style={{ opacity: 0.5 }}>SWA </span>
              <b style={{ color: gamepad.switches.armSwitch ? '#4ade80' : '#9ca3af' }}>
                {gamepad.switches.swa === 1 ? 'ARM' : gamepad.switches.swa === -1 ? 'DISARM' : '—'}
              </b>
              <span style={{ opacity: 0.5, marginLeft: 8 }}>SWD </span>
              <b style={{ color: gamepad.switches.rtlSwitch ? '#c084fc' : '#9ca3af' }}>
                {gamepad.switches.rtlSwitch ? 'RTL' : '—'}
              </b>
            </div>
          </div>
        )}
      </div>

      {/* ── HUD: центр верх — маршрут ── */}
      <div style={{
        ...hudStyle('top-left'),
        top: 12,
        left: '50%',
        transform: 'translateX(-50%)',
        minWidth: 260,
        maxWidth: 320,
      }}>
        <div style={hudTitle}>Маршрутные точки</div>

        {/* Кнопки режима */}
        <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
          <button
            onClick={() => setPlacingMode((v) => !v)}
            disabled={missionRunning}
            style={{
              ...smallBtnStyle,
              background: placingMode ? '#f59e0b' : '#374151',
              flex: 1,
              border: placingMode ? '1px solid #fbbf24' : '1px solid transparent',
            }}
          >
            {placingMode ? '📍 Кликни по земле' : '+ Добавить точку'}
          </button>
          <button
            onClick={() => { setWaypoints([]); setActiveWpIdx(-1) }}
            disabled={missionRunning || waypoints.length === 0}
            style={{ ...smallBtnStyle, background: '#7f1d1d', flex: 0, padding: '4px 8px' }}
          >
            ✕
          </button>
        </div>

        {/* Список точек */}
        {waypoints.length > 0 && (
          <div style={{ maxHeight: 100, overflowY: 'auto', marginBottom: 8 }}>
            {waypoints.map((wp, i) => (
              <div key={wp.id} style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '2px 4px', borderRadius: 4, marginBottom: 2,
                background: i === activeWpIdx ? 'rgba(255,255,255,0.2)' : 'rgba(255,255,255,0.06)',
                fontSize: 11, fontFamily: 'monospace',
              }}>
                <span style={{ color: '#fbbf24', fontWeight: 700, minWidth: 18 }}>{i + 1}</span>
                <span style={{ flex: 1, opacity: 0.8 }}>
                  {wp.lat.toFixed(4)}, {wp.lon.toFixed(4)}
                </span>
                {i === activeWpIdx && <span style={{ color: '#4ade80' }}>→</span>}
                <button
                  onClick={() => setWaypoints((prev) => prev.filter((_, j) => j !== i))}
                  disabled={missionRunning}
                  style={{ background: 'none', border: 'none', color: '#9ca3af', cursor: 'pointer', padding: '0 2px', fontSize: 11 }}
                >✕</button>
              </div>
            ))}
            <div style={{ fontSize: 10, opacity: 0.5, textAlign: 'center', marginTop: 2 }}>
              ↩ Домой (0, 0) после последней точки
            </div>
          </div>
        )}

        {/* Высота полёта */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8, fontSize: 11 }}>
          <span style={{ opacity: 0.7 }}>Высота:</span>
          <input
            type="number" min={3} max={100} step={1} value={missionAlt}
            onChange={(e) => setMissionAlt(parseInt(e.target.value) || 15)}
            disabled={missionRunning}
            style={{
              width: 60, background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.2)',
              color: '#fff', borderRadius: 4, padding: '2px 6px', fontSize: 11,
            }}
          />
          <span style={{ opacity: 0.5 }}>м AGL</span>
        </div>

        {/* Старт/Стоп миссии */}
        {!missionRunning ? (
          <button
            onClick={() => void startMission()}
            disabled={waypoints.length === 0 || busy}
            style={{ ...smallBtnStyle, background: waypoints.length === 0 ? '#374151' : '#16a34a' }}
          >
            Запустить миссию ({waypoints.length} точек)
          </button>
        ) : (
          <button onClick={stopMission} style={{ ...smallBtnStyle, background: '#dc2626' }}>
            Остановить миссию
          </button>
        )}
      </div>

      {/* ── HUD: левый низ — ветер ── */}
      <div style={hudStyle('bottom-left')}>
        <div style={hudTitle}>Ветер</div>
        <label style={{ fontSize: 11, opacity: 0.8 }}>
          Сила: <b>{windSpeed} м/с</b>
        </label>
        <input type="range" min={0} max={20} step={0.5} value={windSpeed}
          onChange={(e) => setWindSpeed(parseFloat(e.target.value))}
          style={{ width: '100%', accentColor: '#38bdf8' }}
        />
        <label style={{ fontSize: 11, opacity: 0.8, marginTop: 4 }}>
          Откуда: <b>{windDir}° ({windDirName(windDir)})</b>
        </label>
        <input type="range" min={0} max={359} step={5} value={windDir}
          onChange={(e) => setWindDir(parseInt(e.target.value))}
          style={{ width: '100%', accentColor: '#38bdf8' }}
        />
        <button onClick={() => void applyWind()} style={smallBtnStyle}>
          Применить
        </button>
      </div>

      {/* ── HUD: правый низ — лог ── */}
      {cmdLog.length > 0 && (
        <div style={hudStyle('bottom-right')}>
          <div style={hudTitle}>Лог</div>
          {cmdLog.slice(0, 8).map((m, i) => (
            <div key={i} style={{ fontSize: 10, opacity: 0.7, fontFamily: 'monospace' }}>{m}</div>
          ))}
        </div>
      )}

      {/* ── Переключатель вида ── */}
      <div style={{
        position: 'absolute', bottom: 14, left: '50%', transform: 'translateX(-50%)',
        display: 'flex', alignItems: 'center', gap: 6,
        background: 'rgba(0,0,0,0.65)', backdropFilter: 'blur(6px)',
        border: '1px solid rgba(255,255,255,0.15)', borderRadius: 10,
        padding: '6px 10px',
      }}>
        {/* Кнопки режима */}
        {([
          { id: 'third', label: '👁 3-е лицо', hint: 'ЛКМ — орбита · колесо — зум' },
          { id: 'fpv',   label: '🎥 FPV',      hint: 'Вид с носовой камеры дрона' },
        ] as const).map(({ id, label, hint }) => (
          <button
            key={id}
            title={hint}
            onClick={() => {
              setCamMode(id)
              if (id === 'third') sceneRef.current?.resetCamera()
            }}
            style={{
              background: camMode === id ? 'rgba(99,102,241,0.8)' : 'rgba(255,255,255,0.08)',
              color: '#fff',
              border: `1px solid ${camMode === id ? '#6366f1' : 'rgba(255,255,255,0.15)'}`,
              borderRadius: 7, padding: '4px 12px',
              cursor: 'pointer', fontSize: 12, fontWeight: camMode === id ? 700 : 400,
              transition: 'all 0.15s',
            }}
          >
            {label}
          </button>
        ))}

        {/* Слайдер угла FPV */}
        {camMode === 'fpv' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 4 }}>
            <span style={{ fontSize: 11, opacity: 0.7, whiteSpace: 'nowrap' }}>Наклон:</span>
            <input
              type="range" min={0} max={55} step={5} value={fpvTilt}
              onChange={(e) => setFpvTilt(parseInt(e.target.value))}
              style={{ width: 80, accentColor: '#6366f1' }}
            />
            <span style={{ fontSize: 11, opacity: 0.7, minWidth: 28 }}>{fpvTilt}°</span>
          </div>
        )}

        {/* Подсказка */}
        <span style={{ fontSize: 10, opacity: 0.4, marginLeft: 4, whiteSpace: 'nowrap' }}>
          {camMode === 'fpv'
            ? 'Вид с носовой камеры'
            : placingMode
              ? '📍 клик = точка · Esc = выход'
              : 'ЛКМ-drag = орбита · колесо = зум'
          }
        </span>
      </div>
    </div>
  )
}

// ── Вспомогательные компоненты ───────────────────────────────────

function HudRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 12, marginBottom: 2 }}>
      <span style={{ opacity: 0.6 }}>{label}</span>
      <span style={{ fontFamily: 'monospace', fontWeight: 700, color: color ?? '#fff' }}>{value}</span>
    </div>
  )
}

function CmdBtn({ label, color, onClick, disabled }: {
  label: string; color: string; onClick: () => void; disabled: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        background: disabled ? '#374151' : color,
        color: '#fff',
        border: 'none',
        borderRadius: 6,
        padding: '5px 10px',
        cursor: disabled ? 'default' : 'pointer',
        fontSize: 12,
        fontWeight: 600,
        opacity: disabled ? 0.5 : 1,
        transition: 'background 0.15s',
      }}
    >
      {label}
    </button>
  )
}

// ── Стили HUD ────────────────────────────────────────────────────

function hudStyle(pos: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'): React.CSSProperties {
  const base: React.CSSProperties = {
    position: 'absolute',
    background: 'var(--gcs-hud-bg)',
    backdropFilter: 'blur(8px)',
    borderRadius: 10,
    padding: '10px 14px',
    color: 'var(--gcs-text)',
    minWidth: 190,
    maxWidth: 230,
    border: '1px solid var(--gcs-border)',
    boxShadow: '0 4px 24px rgba(0,0,0,0.55)',
    userSelect: 'none',
  }
  if (pos === 'top-left')    return { ...base, top: 12, left: 12 }
  if (pos === 'top-right')   return { ...base, top: 12, right: 12 }
  if (pos === 'bottom-left') return { ...base, bottom: 30, left: 12 }
  return { ...base, bottom: 30, right: 12 }
}

const hudTitle: React.CSSProperties = {
  fontSize: 11,
  textTransform: 'uppercase',
  letterSpacing: '0.1em',
  color: 'var(--gcs-muted)',
  marginBottom: 8,
  fontWeight: 700,
}

const smallBtnStyle: React.CSSProperties = {
  marginTop: 8,
  padding: '4px 10px',
  background: '#0ea5e9',
  color: '#fff',
  border: 'none',
  borderRadius: 6,
  cursor: 'pointer',
  fontSize: 11,
  fontWeight: 600,
  width: '100%',
}

// ── Утилиты ──────────────────────────────────────────────────────

function fmt(v: number | null | undefined, dec: number, unit: string): string {
  if (v == null) return '—'
  return `${v.toFixed(dec)} ${unit}`
}

function battColor(b: number | null | undefined): string | undefined {
  if (b == null) return undefined
  if (b > 50) return '#4ade80'
  if (b > 20) return '#facc15'
  return '#ef4444'
}

const WIND_NAMES: [number, string][] = [
  [22.5,  'С'],  [67.5,  'СВ'], [112.5, 'В'],  [157.5, 'ЮВ'],
  [202.5, 'Ю'],  [247.5, 'ЮЗ'], [292.5, 'З'],  [337.5, 'СЗ'],
]
function windDirName(deg: number): string {
  for (const [limit, name] of WIND_NAMES) {
    if (deg < limit) return name
  }
  return 'С'
}
