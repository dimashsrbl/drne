import { useEffect, useMemo, useState } from 'react'
import {
  betaflightApi,
  type BetaflightCheckResponse,
  type BetaflightSequenceStatus,
  type BetaflightSequenceStep,
  type BetaflightStepAction,
  type BetaflightVisionCheckResponse,
} from '../api/betaflight'
import { trackerUrl } from '../api/tracker'
import { useTelemetry } from '../telemetry/TelemetryProvider'

function gpsFixLabel(fix: number | null | undefined): string {
  if (fix == null) return '—'
  if (fix >= 2) return '3D'
  if (fix === 1) return '2D'
  return 'нет'
}

const actions: { value: BetaflightStepAction; label: string }[] = [
  { value: 'arm', label: 'ARM' },
  { value: 'neutral', label: 'Neutral' },
  { value: 'throttle', label: 'Throttle (time)' },
  { value: 'takeoff_alt', label: 'Takeoff (baro)' },
  { value: 'hold_alt', label: 'Hold alt (baro)' },
  { value: 'forward', label: 'Forward' },
  { value: 'back', label: 'Back' },
  { value: 'left', label: 'Left' },
  { value: 'right', label: 'Right' },
  { value: 'yaw_left', label: 'Yaw left' },
  { value: 'yaw_right', label: 'Yaw right' },
  { value: 'land', label: 'Land (baro)' },
  { value: 'disarm', label: 'DISARM' },
  { value: 'wait', label: 'Wait' },
]

/** Только взлёт 1 м → пауза → посадка (без манёвров). */
const baro1mMissionPreset: BetaflightSequenceStep[] = [
  { action: 'arm' },
  { action: 'takeoff_alt', seconds: 25, target_alt_m: 1.0, throttle_us: 1410, settle_s: 2.0 },
  { action: 'hold_alt', seconds: 4, throttle_us: 1410 },
  { action: 'land', seconds: 12, throttle_us: 1140 },
  { action: 'disarm' },
]

const baro1mPreset: BetaflightSequenceStep[] = [
  { action: 'arm' },
  { action: 'takeoff_alt', seconds: 25, target_alt_m: 1.0, throttle_us: 1410, settle_s: 2.0 },
  { action: 'hold_alt', seconds: 6, throttle_us: 1410 },
  { action: 'forward', seconds: 0.8, throttle_us: 1410, stick_delta: 50 },
  { action: 'neutral', seconds: 0.6, throttle_us: 1410 },
  { action: 'land', seconds: 12, throttle_us: 1140 },
  { action: 'disarm' },
]

const preset: BetaflightSequenceStep[] = [
  { action: 'arm' },
  { action: 'throttle', seconds: 1.2, throttle_us: 1110 },
  { action: 'neutral', seconds: 1.5, throttle_us: 1060 },
  { action: 'land', seconds: 12, throttle_us: 1140 },
  { action: 'disarm' },
]

const lowTakeoffPreset: BetaflightSequenceStep[] = [
  { action: 'arm' },
  { action: 'throttle', seconds: 1.0, throttle_us: 1110 },
  { action: 'neutral', seconds: 1.5, throttle_us: 1060 },
]

const softLandPreset: BetaflightSequenceStep[] = [
  { action: 'land', seconds: 12, throttle_us: 1140 },
  { action: 'disarm' },
]

const lowHopWithTurnPreset: BetaflightSequenceStep[] = [
  { action: 'arm' },
  { action: 'throttle', seconds: 1.2, throttle_us: 1110 },
  { action: 'neutral', seconds: 1.0, throttle_us: 1060 },
  { action: 'forward', seconds: 0.7, throttle_us: 1060, stick_delta: 60 },
  { action: 'yaw_right', seconds: 0.7, throttle_us: 1060, stick_delta: 60 },
  { action: 'neutral', seconds: 0.8, throttle_us: 1050 },
  { action: 'land', seconds: 12, throttle_us: 1140 },
  { action: 'disarm' },
]

function defaultsForAction(action: BetaflightStepAction): Partial<BetaflightSequenceStep> {
  if (action === 'land') return { seconds: 12, throttle_us: 1140 }
  if (action === 'takeoff_alt') return { seconds: 25, target_alt_m: 1.0, throttle_us: 1410, settle_s: 2.0 }
  if (action === 'hold_alt') return { seconds: 5, throttle_us: 1410 }
  if (action === 'neutral' || action === 'wait') return { seconds: 3, throttle_us: 1410 }
  if (action === 'forward' || action === 'back' || action === 'left' || action === 'right' || action === 'yaw_left' || action === 'yaw_right') {
    return { seconds: 0.8, throttle_us: 1410, stick_delta: 50 }
  }
  return { seconds: 1 }
}

function isInstantAction(action: BetaflightStepAction) {
  return action === 'arm' || action === 'disarm'
}

function isBaroAction(action: BetaflightStepAction) {
  return action === 'takeoff_alt' || action === 'hold_alt' || action === 'land'
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min
  return Math.max(min, Math.min(max, value))
}

async function stopWithRetry(maxAttempts = 6): Promise<BetaflightSequenceStatus> {
  let lastErr: unknown
  for (let i = 0; i < maxAttempts; i++) {
    try {
      return await betaflightApi.stopSequence()
    } catch (e) {
      lastErr = e
      await new Promise((r) => window.setTimeout(r, 250))
    }
  }
  throw lastErr instanceof Error ? lastErr : new Error('STOP не дошёл до Pi — проверь Wi‑Fi')
}

export function BetaflightSequencePage() {
  const { telemetry, wsStatus } = useTelemetry()
  const [port, setPort] = useState('/dev/ttyACM0')
  const [baud, setBaud] = useState(115200)
  const [steps, setSteps] = useState<BetaflightSequenceStep[]>(baro1mMissionPreset)
  const [check, setCheck] = useState<BetaflightCheckResponse | null>(null)
  const [status, setStatus] = useState<BetaflightSequenceStatus | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [vision, setVision] = useState<BetaflightVisionCheckResponse | null>(null)
  const [linkLost, setLinkLost] = useState(false)
  const [missionLimitEnabled, setMissionLimitEnabled] = useState(true)
  const [missionMaxS, setMissionMaxS] = useState(40)

  const json = useMemo(
    () =>
      JSON.stringify(
        {
          port,
          baud,
          steps,
          max_mission_s: missionLimitEnabled ? missionMaxS : 0,
        },
        null,
        2,
      ),
    [port, baud, steps, missionLimitEnabled, missionMaxS],
  )

  const missionMaxPayload = missionLimitEnabled ? missionMaxS : 0

  useEffect(() => {
    if (status?.status !== 'running') {
      setLinkLost(false)
      return
    }
    const statusId = window.setInterval(() => {
      void betaflightApi
        .status()
        .then((s) => {
          setStatus(s)
          setLinkLost(false)
        })
        .catch(() => setLinkLost(true))
    }, 400)
    const hbId = window.setInterval(() => {
      void betaflightApi.heartbeat().catch(() => setLinkLost(true))
    }, 300)
    return () => {
      window.clearInterval(statusId)
      window.clearInterval(hbId)
    }
  }, [status?.status])

  useEffect(() => {
    void betaflightApi.visionCheck().then(setVision).catch(() => {})
  }, [])

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const updateStep = (idx: number, patch: Partial<BetaflightSequenceStep>) => {
    setSteps((prev) => prev.map((step, i) => (i === idx ? { ...step, ...patch } : step)))
  }

  const addStep = () => {
    setSteps((prev) => [...prev, { action: 'neutral', seconds: 1, throttle_us: 1000, stick_delta: 100 }])
  }

  const start = async () => {
    const res = await betaflightApi.startSequence({ port, baud, steps, max_mission_s: missionMaxPayload })
    setStatus(res)
  }

  const stop = async () => {
    const res = await stopWithRetry()
    setStatus(res)
  }

  const emergencyLand = async () => {
    const res = await betaflightApi.emergencyLand({ port, baud, seconds: 30, throttle_us: 1140 })
    setStatus(res)
  }

  const checkVision = async () => {
    const res = await betaflightApi.visionCheck()
    setVision(res)
  }

  const lockTarget = async () => {
    await fetch(trackerUrl('/lock'), { method: 'POST' })
    await checkVision()
  }

  const unlockTarget = async () => {
    await fetch(trackerUrl('/unlock'), { method: 'POST' })
    await checkVision()
  }

  const startTrack = async () => {
    const res = await betaflightApi.trackStart({
      port,
      baud,
      target_alt_m: 1.0,
      throttle_us: 1410,
      wait_lock_s: 90,
      max_mission_s: missionMaxPayload,
    })
    setStatus(res)
  }

  /** Наведи объект в центр кадра → lock → взлёт 1 м → follow */
  const captureAndTrack = async () => {
    await fetch(trackerUrl('/lock'), { method: 'POST' })
    await new Promise((r) => window.setTimeout(r, 500))
    await checkVision()
    const res = await betaflightApi.trackStart({
      port,
      baud,
      target_alt_m: 1.0,
      throttle_us: 1410,
      wait_lock_s: 90,
      max_mission_s: missionMaxPayload,
    })
    setStatus(res)
  }

  const stopTrack = async () => {
    const res = await stopWithRetry()
    setStatus(res)
    await unlockTarget()
  }

  const checkMsp = async () => {
    const res = await betaflightApi.check(port, baud)
    setCheck(res)
  }

  return (
    <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.3fr) minmax(320px, 0.7fr)' }}>
      <section className="card" style={{ gridColumn: '1 / -1' }}>
        <div className="cardTitle">Слежение за целью (камера + дрон)</div>
        <div className="hint" style={{ marginBottom: 12 }}>
          Наведи объект в <b>центр</b> видео → <b>Захват + полёт</b> → дрон ARM, взлёт ~1 м, держит цель в кадре.
          <b> STOP</b> — немедленный DISARM (работает даже после ошибки). <b>LAND</b> — мягкая посадка по барометру.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(280px, 1.2fr) minmax(240px, 0.8fr)', gap: 16 }}>
          <div
            style={{
              background: '#000',
              borderRadius: 12,
              overflow: 'hidden',
              border: '1px solid rgba(255,255,255,0.12)',
              minHeight: 280,
            }}
          >
            <img
              src={trackerUrl('/stream')}
              alt="vision stream"
              style={{ width: '100%', display: 'block', minHeight: 280, objectFit: 'contain' }}
            />
          </div>
          <div>
            {vision ? (
              <div className="kv" style={{ marginBottom: 12, fontSize: 13 }}>
                <div className="k">vision</div>
                <div className="v" style={{ color: vision.ok ? '#4ade80' : '#f87171' }}>
                  {vision.ok ? 'OK' : 'FAIL — проверь vision-tracker на Pi'}
                </div>
                <div className="k">camera</div>
                <div className="v">{vision.camera_status || '—'}</div>
                <div className="k">locked</div>
                <div className="v">{vision.target_locked ? 'да' : 'нет'}</div>
                <div className="k">action</div>
                <div className="v">{status?.current_action ?? '—'}</div>
                <div className="k">alt</div>
                <div className="v">
                  {status?.current_alt_m != null ? `${status.current_alt_m.toFixed(2)} m` : '—'}
                </div>
              </div>
            ) : (
              <div className="hint" style={{ marginBottom: 12 }}>Проверка камеры…</div>
            )}
            <div className="row" style={{ flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
              <button className="btn" disabled={!!busy} onClick={() => void run('vcheck', checkVision)}>
                Check Vision
              </button>
              <button className="btn" disabled={!!busy} onClick={() => void run('unlock', unlockTarget)}>
                Снять захват
              </button>
            </div>
            <div className="row" style={{ flexWrap: 'wrap', gap: 10 }}>
              <button
                className="btn primary"
                style={{ fontWeight: 900, minWidth: 200, fontSize: 15 }}
                disabled={!!busy || status?.status === 'running'}
                onClick={() => void run('capture', captureAndTrack)}
              >
                Захват + полёт
              </button>
              <button
                className="btn danger"
                style={{ fontWeight: 900, minWidth: 200 }}
                disabled={busy === 'stop' || status?.status !== 'running'}
                onClick={() => void run('stop', stopTrack)}
              >
                STOP (посадка)
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="card">
        <div className="cardTitle">Betaflight Sequence Runner</div>

        <div className="alert" style={{ marginBottom: 12, borderColor: 'rgba(34,197,94,0.45)', background: 'rgba(34,197,94,0.08)' }}>
          <div className="alertTitle" style={{ color: '#4ade80' }}>GPS + баро</div>
          <div className="alertBody">
            Высота — барометр FC. Горизонталь в воздухе — <b>GPS POS HOLD</b> (канал 7, Betaflight Modes).
            Нужен 3D fix (≥6 спутников) на улице. Координаты идут на фронт через <code>/telemetry/ws</code>.
            <br />
            Высоту задаёшь в <code>takeoff_alt</code> (<code>target_alt_m</code>). Дальше neutral / forward держат ту же высоту по баро.
            <code>hold_alt</code> — опционально другая цель. Посадка: <code>land</code> 12 с, мягкий сброс газа.
            Если уводит в сторону без GPS — подстрой <code>STICK_CENTER_ROLL/PITCH_US</code> на Pi.
            <br />
            <b>Безопасность:</b> heartbeat ~4 с → баро-посадка ~15 с и DISARM. STOP ×6 retry. Configurator закрыт.
          </div>
        </div>

        {linkLost && status?.status === 'running' ? (
          <div className="alert" style={{ marginBottom: 12, borderColor: 'rgba(239,68,68,0.6)', background: 'rgba(239,68,68,0.12)' }}>
            <div className="alertTitle" style={{ color: '#f87171' }}>Нет связи с Pi</div>
            <div className="alertBody">
              STOP с этого ПК может не дойти. Pi начнёт плавно снижать газ (~15 с) и затем DISARM.
              Если пропеллеры крутятся — выдерни питание или DISARM на пульте.
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="alert" style={{ marginBottom: 12 }}>
            <div className="alertTitle">Ошибка</div>
            <div className="alertBody">{error}</div>
          </div>
        ) : null}

        <div className="row" style={{ alignItems: 'end', marginBottom: 12 }}>
          <label className="field">
            <span>Порт</span>
            <input value={port} onChange={(e) => setPort(e.target.value)} />
          </label>
          <label className="field" style={{ maxWidth: 140 }}>
            <span>Baud</span>
            <input type="number" min={9600} max={1000000} value={baud} onChange={(e) => setBaud(Number(e.target.value))} />
          </label>
          <label className="field" style={{ display: 'flex', alignItems: 'center', gap: 8, maxWidth: 200 }}>
            <input
              type="checkbox"
              checked={missionLimitEnabled}
              onChange={(e) => setMissionLimitEnabled(e.target.checked)}
            />
            <span>Лимит миссии</span>
          </label>
          <label className="field" style={{ maxWidth: 100 }}>
            <span>сек</span>
            <input
              type="number"
              min={1}
              max={600}
              disabled={!missionLimitEnabled}
              value={missionMaxS}
              onChange={(e) => setMissionMaxS(Number(e.target.value))}
            />
          </label>
          <button className="btn" disabled={!!busy} onClick={() => void run('check', checkMsp)}>
            Check MSP
          </button>
          <button className="btn primary" disabled={!!busy} onClick={() => setSteps(baro1mMissionPreset)}>
            Миссия 1 м
          </button>
          <button className="btn" disabled={!!busy} onClick={() => setSteps(baro1mPreset)}>
            Baro 1 m + манёвр
          </button>
          <button className="btn" disabled={!!busy} onClick={() => setSteps(preset)}>
            Low hop
          </button>
          <button className="btn" disabled={!!busy} onClick={() => setSteps(lowTakeoffPreset)}>
            Low takeoff
          </button>
          <button className="btn" disabled={!!busy} onClick={() => setSteps(softLandPreset)}>
            Soft land
          </button>
          <button className="btn" disabled={!!busy} onClick={() => setSteps(lowHopWithTurnPreset)}>
            Hop + turn
          </button>
        </div>

        {check ? (
          <div className="hint" style={{ marginBottom: 12 }}>
            MSP: <b style={{ color: check.ok ? '#4ade80' : '#f87171' }}>{check.ok ? 'OK' : 'FAIL'}</b> • {check.detail}
            {check.variant ? ` • ${check.variant}` : ''}{check.version ? ` ${check.version}` : ''}
          </div>
        ) : null}

        <div style={{ display: 'grid', gap: 10 }}>
          {steps.map((step, idx) => (
            <div key={idx} className="card" style={{ padding: 10 }}>
              <div className="row" style={{ alignItems: 'end' }}>
                <label className="field" style={{ minWidth: 150 }}>
                  <span>Шаг {idx + 1}</span>
                  <select
                    value={step.action}
                    onChange={(e) => {
                      const action = e.target.value as BetaflightStepAction
                      updateStep(idx, { ...defaultsForAction(action), action })
                    }}
                  >
                    {actions.map((a) => (
                      <option key={a.value} value={a.value}>
                        {a.label}
                      </option>
                    ))}
                  </select>
                </label>

                {!isInstantAction(step.action) ? (
                  <label className="field" style={{ maxWidth: 120 }}>
                    <span>{isBaroAction(step.action) ? 'Таймаут, с' : 'Секунды'}</span>
                    <input
                      type="number"
                      min={0.1}
                      max={60}
                      step={0.1}
                      value={step.seconds ?? 1}
                      onChange={(e) => updateStep(idx, { seconds: clampNumber(Number(e.target.value), 0.1, 60) })}
                    />
                  </label>
                ) : (
                  <div className="hint" style={{ minWidth: 120 }}>
                    без секунд
                  </div>
                )}

                {step.action === 'takeoff_alt' ? (
                  <label className="field" style={{ maxWidth: 120 }}>
                    <span>Высота, м</span>
                    <input
                      type="number"
                      min={0.1}
                      max={10}
                      step={0.1}
                      value={step.target_alt_m ?? 1}
                      onChange={(e) => updateStep(idx, { target_alt_m: clampNumber(Number(e.target.value), 0.1, 10) })}
                    />
                  </label>
                ) : null}
                {step.action === 'hold_alt' ? (
                  <label className="field" style={{ maxWidth: 140 }}>
                    <span>Высота, м (опц.)</span>
                    <input
                      type="number"
                      min={0.1}
                      max={10}
                      step={0.1}
                      placeholder="как взлёт"
                      value={step.target_alt_m ?? ''}
                      onChange={(e) => {
                        const raw = e.target.value.trim()
                        updateStep(idx, {
                          target_alt_m: raw === '' ? undefined : clampNumber(Number(raw), 0.1, 10),
                        })
                      }}
                    />
                  </label>
                ) : null}

                {step.action === 'takeoff_alt' ? (
                  <label className="field" style={{ maxWidth: 120 }}>
                    <span>Стаб., с</span>
                    <input
                      type="number"
                      min={0}
                      max={30}
                      step={0.5}
                      value={step.settle_s ?? 3}
                      onChange={(e) => updateStep(idx, { settle_s: clampNumber(Number(e.target.value), 0, 30) })}
                    />
                  </label>
                ) : null}

                <label className="field" style={{ maxWidth: 140 }}>
                  <span>Throttle us</span>
                  <input
                    type="number"
                    min={1000}
                    max={2000}
                    value={
                      step.throttle_us ??
                      (step.action === 'land' ? 1140 : step.action === 'takeoff_alt' || step.action === 'hold_alt' ? 1410 : 1000)
                    }
                    onChange={(e) => updateStep(idx, { throttle_us: clampNumber(Number(e.target.value), 1000, 2000) })}
                  />
                </label>

                <label className="field" style={{ maxWidth: 130 }}>
                  <span>Stick delta</span>
                  <input
                    type="number"
                    min={0}
                    max={500}
                    value={step.stick_delta ?? 100}
                    onChange={(e) => updateStep(idx, { stick_delta: clampNumber(Number(e.target.value), 0, 500) })}
                  />
                </label>

                <button className="btn danger" disabled={steps.length <= 1} onClick={() => setSteps((prev) => prev.filter((_, i) => i !== idx))}>
                  удалить
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" disabled={!!busy} onClick={addStep}>
            + шаг
          </button>
          <button className="btn primary" disabled={!!busy || steps.length === 0 || status?.status === 'running'} onClick={() => void run('start', start)}>
            Start
          </button>
          <button
            className="btn danger"
            style={{ fontWeight: 900, minWidth: 180 }}
            disabled={busy === 'stop'}
            onClick={() => void run('stop', stop)}
          >
            STOP / DISARM (всегда)
          </button>
          <button
            className="btn"
            style={{
              fontWeight: 900,
              minWidth: 180,
              borderColor: 'rgba(251,146,60,0.65)',
              background: 'rgba(251,146,60,0.15)',
              color: '#fdba74',
            }}
            disabled={busy === 'land'}
            onClick={() => void run('land', emergencyLand)}
          >
            LAND (baro)
          </button>
        </div>
      </section>

      <section className="card">
        <div className="cardTitle">GPS телеметрия</div>
        <div className="subtitle" style={{ marginBottom: 10 }}>
          WS: <b>{wsStatus === 'open' ? 'онлайн' : wsStatus === 'connecting' ? 'подключение…' : 'нет связи'}</b>
        </div>
        <div className="kv" style={{ marginBottom: 12 }}>
          <div className="k">фикс</div>
          <div className="v" style={{ color: (telemetry?.gps_fix ?? 0) >= 2 ? '#4ade80' : '#f87171' }}>
            {gpsFixLabel(telemetry?.gps_fix ?? null)}
            {telemetry?.gps_sats != null ? ` · ${telemetry.gps_sats} sat` : ''}
          </div>
          <div className="k">lat</div>
          <div className="v">{telemetry?.lat != null ? telemetry.lat.toFixed(6) : '—'}</div>
          <div className="k">lon</div>
          <div className="v">{telemetry?.lon != null ? telemetry.lon.toFixed(6) : '—'}</div>
          <div className="k">курс</div>
          <div className="v">{telemetry?.heading != null ? `${telemetry.heading.toFixed(0)}°` : '—'}</div>
          <div className="k">скорость</div>
          <div className="v">{telemetry?.speed != null ? `${telemetry.speed.toFixed(1)} м/с` : '—'}</div>
        </div>
        {(telemetry?.gps_fix ?? 0) < 2 ? (
          <div className="hint" style={{ color: '#fbbf24' }}>
            Для POS HOLD выйди на улицу и дождись 3D fix (≥6 спутников). В помещении GPS не работает.
          </div>
        ) : null}
      </section>

      <section className="card">
        <div className="cardTitle">Статус</div>
        {status ? (
          <div className="kv" style={{ marginBottom: 12 }}>
            <div className="k">status</div>
            <div className="v">{status.status}</div>
            <div className="k">step</div>
            <div className="v">
              {status.current_step ?? '-'} / {status.total_steps ?? '-'}
            </div>
            <div className="k">action</div>
            <div className="v">{status.current_action ?? '-'}</div>
            <div className="k">elapsed</div>
            <div className="v">{status.elapsed_s.toFixed(1)} s</div>
            {status.mission_max_s != null ? (
              <>
                <div className="k">mission limit</div>
                <div className="v">
                  {status.mission_remaining_s != null
                    ? `${status.mission_remaining_s.toFixed(1)} / ${status.mission_max_s.toFixed(0)} s`
                    : `${status.mission_max_s.toFixed(0)} s`}
                </div>
              </>
            ) : null}
            <div className="k">alt (baro)</div>
            <div className="v">
              {status.current_alt_m != null ? `${status.current_alt_m.toFixed(2)} m` : '—'}
              {status.target_alt_m != null ? ` → ${status.target_alt_m.toFixed(2)} m` : ''}
            </div>
            <div className="k">port</div>
            <div className="v">{status.port ?? '-'}</div>
          </div>
        ) : (
          <div className="hint" style={{ marginBottom: 12 }}>
            Статус появится после Start или Stop.
          </div>
        )}

        {status?.error ? (
          <div className="alert" style={{ marginBottom: 12 }}>
            <div className="alertTitle">Runner error</div>
            <div className="alertBody">{status.error}</div>
          </div>
        ) : null}

        {status?.current_channels ? (
          <div className="hint" style={{ marginBottom: 12 }}>
            Каналы: <code>{status.current_channels.join(', ')}</code>
          </div>
        ) : null}

        <div className="cardTitle" style={{ marginTop: 14 }}>JSON</div>
        <pre
          style={{
            margin: 0,
            padding: 12,
            background: 'rgba(0,0,0,0.25)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: 12,
            overflow: 'auto',
            maxHeight: 560,
            fontSize: 12,
          }}
        >
          {json}
        </pre>
      </section>
    </div>
  )
}
