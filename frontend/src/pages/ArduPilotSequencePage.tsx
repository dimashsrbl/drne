import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { missionApi, type MissionAction } from '../api/mission'
import type { MissionStatus } from '../api/types'
import { useTelemetry } from '../telemetry/TelemetryProvider'

type ActionName = MissionAction['action']

const actionOptions: { value: ActionName; label: string }[] = [
  { value: 'arm', label: 'ARM' },
  { value: 'takeoff', label: 'Взлёт' },
  { value: 'wait', label: 'Ожидание' },
  { value: 'nudge', label: 'Вперёд / назад (баро)' },
  { value: 'goto', label: 'Перелёт к точке (GPS)' },
  { value: 'land', label: 'Посадка' },
  { value: 'return_home', label: 'RTL — домой' },
  { value: 'disarm', label: 'DISARM' },
]

const flightPreset: MissionAction[] = [
  { action: 'arm' },
  { action: 'takeoff', alt: 1 },
  { action: 'wait', seconds: 5 },
  { action: 'land' },
]

const baroPreset: MissionAction[] = [
  { action: 'arm', force: true },
  { action: 'takeoff', alt: 1, no_gps: true },
  { action: 'wait', seconds: 2 },
  { action: 'nudge', direction: 'forward', seconds: 2 },
  { action: 'wait', seconds: 1 },
  { action: 'land' },
]

const benchPreset: MissionAction[] = [
  { action: 'arm', force: true },
  { action: 'wait', seconds: 3 },
  { action: 'disarm' },
]

function defaultAction(action: ActionName): MissionAction {
  if (action === 'takeoff') return { action, alt: 1, no_gps: true }
  if (action === 'wait') return { action, seconds: 3 }
  if (action === 'goto') return { action, lat: 0, lon: 0, alt: 3 }
  if (action === 'nudge') return { action, direction: 'forward', seconds: 2 }
  return { action }
}

function requiresGps(actions: MissionAction[]) {
  return actions.some((a) => {
    if (a.action === 'goto' || a.action === 'return_home') return true
    if (a.action === 'takeoff' && !a.no_gps) return true
    return false
  })
}

export function ArduPilotSequencePage() {
  const { telemetry, wsStatus } = useTelemetry()
  const [steps, setSteps] = useState<MissionAction[]>(baroPreset)
  const [status, setStatus] = useState<MissionStatus | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const gpsReady = (telemetry?.gps_fix ?? 0) >= 3 && (telemetry?.gps_sats ?? 0) >= 6
  const missionNeedsGps = requiresGps(steps)
  const json = useMemo(() => JSON.stringify({ mission: steps }, null, 2), [steps])

  useEffect(() => {
    let dead = false
    const poll = () => {
      void missionApi
        .status()
        .then((next) => {
          if (!dead) setStatus(next)
        })
        .catch(() => {})
    }
    poll()
    const id = window.setInterval(poll, status?.status === 'running' ? 500 : 1500)
    return () => {
      dead = true
      window.clearInterval(id)
    }
  }, [status?.status])

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

  const updateStep = (index: number, value: MissionAction) => {
    setSteps((previous) => previous.map((step, i) => (i === index ? value : step)))
  }

  const start = async () => {
    const next = await missionApi.start({ mission: steps })
    setStatus(next)
  }

  const stop = async (action: 'land' | 'rtl' | 'disarm') => {
    const next = await missionApi.stop(action)
    setStatus(next)
  }

  return (
    <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 1.35fr) minmax(300px, 0.65fr)' }}>
      <section className="card" style={{ gridColumn: '1 / -1' }}>
        <div className="cardTitle">Pixhawk / ArduPilot Mission Runner</div>
        <div className="alert" style={{ borderColor: 'rgba(59,130,246,0.5)', background: 'rgba(59,130,246,0.08)' }}>
          <div className="alertTitle" style={{ color: '#93c5fd' }}>MAVLink вместо MSP</div>
          <div className="alertBody">
            Команды идут: браузер → Pi backend → <code>/dev/serial0</code> → Pixhawk. Порт и throttle_us больше не задаются:
            высоту, стабилизацию и моторы контролирует ArduPilot. GOTO/RTL — только с GPS.
            В помещении: пресет «Баро 1 м → вперёд» (ALT_HOLD + барометр, без GPS). Пропеллеры только когда готов.
          </div>
        </div>
      </section>

      <section className="card">
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div className="cardTitle" style={{ margin: 0 }}>Последовательность</div>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <button className="btn primary" disabled={!!busy} onClick={() => setSteps(baroPreset)}>Баро 1 м → вперёд</button>
            <button className="btn" disabled={!!busy} onClick={() => setSteps(flightPreset)}>GPS взлёт 1 м → LAND</button>
            <button className="btn" disabled={!!busy} onClick={() => setSteps(benchPreset)}>Bench ARM → DISARM</button>
          </div>
        </div>

        {missionNeedsGps && !gpsReady ? (
          <div className="alert" style={{ marginBottom: 12, borderColor: 'rgba(251,191,36,0.55)' }}>
            <div className="alertTitle" style={{ color: '#fbbf24' }}>Нет GPS 3D</div>
            <div className="alertBody">
              Для GPS-миссии нужно ≥6 спутников и fix 3D. Без GPS бери «Баро 1 м → вперёд» или «Bench ARM → DISARM».
            </div>
          </div>
        ) : null}

        {error ? (
          <div className="alert" style={{ marginBottom: 12 }}>
            <div className="alertTitle">Ошибка</div>
            <div className="alertBody">{error}</div>
          </div>
        ) : null}

        <div style={{ display: 'grid', gap: 10 }}>
          {steps.map((step, index) => (
            <div key={`${index}-${step.action}`} className="card" style={{ padding: 10 }}>
              <div className="row" style={{ alignItems: 'end', flexWrap: 'wrap' }}>
                <label className="field" style={{ minWidth: 180 }}>
                  <span>Шаг {index + 1}</span>
                  <select
                    value={step.action}
                    onChange={(e) => updateStep(index, defaultAction(e.target.value as ActionName))}
                  >
                    {actionOptions.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>

                {step.action === 'takeoff' ? (
                  <>
                    <label className="field" style={{ maxWidth: 140 }}>
                      <span>Высота AGL, м</span>
                      <input
                        type="number"
                        min={0.5}
                        max={120}
                        step={0.5}
                        value={step.alt}
                        onChange={(e) => updateStep(index, { ...step, alt: Number(e.target.value) })}
                      />
                    </label>
                    <label className="field" style={{ maxWidth: 160 }}>
                      <span>Без GPS (баро)</span>
                      <select
                        value={step.no_gps ? '1' : '0'}
                        onChange={(e) => updateStep(index, { ...step, no_gps: e.target.value === '1' })}
                      >
                        <option value="1">да — ALT_HOLD</option>
                        <option value="0">нет — GUIDED/GPS</option>
                      </select>
                    </label>
                  </>
                ) : null}

                {step.action === 'nudge' ? (
                  <>
                    <label className="field" style={{ maxWidth: 160 }}>
                      <span>Направление</span>
                      <select
                        value={step.direction}
                        onChange={(e) => updateStep(index, { ...step, direction: e.target.value as 'forward' | 'back' })}
                      >
                        <option value="forward">вперёд</option>
                        <option value="back">назад</option>
                      </select>
                    </label>
                    <label className="field" style={{ maxWidth: 140 }}>
                      <span>Секунды</span>
                      <input
                        type="number"
                        min={0.2}
                        max={8}
                        step={0.5}
                        value={step.seconds}
                        onChange={(e) => updateStep(index, { ...step, seconds: Number(e.target.value) })}
                      />
                    </label>
                  </>
                ) : null}

                {step.action === 'wait' ? (
                  <label className="field" style={{ maxWidth: 140 }}>
                    <span>Секунды</span>
                    <input
                      type="number"
                      min={0.1}
                      max={3600}
                      step={0.5}
                      value={step.seconds}
                      onChange={(e) => updateStep(index, { ...step, seconds: Number(e.target.value) })}
                    />
                  </label>
                ) : null}

                {step.action === 'goto' ? (
                  <>
                    <label className="field" style={{ maxWidth: 170 }}>
                      <span>Latitude</span>
                      <input type="number" step={0.000001} value={step.lat} onChange={(e) => updateStep(index, { ...step, lat: Number(e.target.value) })} />
                    </label>
                    <label className="field" style={{ maxWidth: 170 }}>
                      <span>Longitude</span>
                      <input type="number" step={0.000001} value={step.lon} onChange={(e) => updateStep(index, { ...step, lon: Number(e.target.value) })} />
                    </label>
                    <label className="field" style={{ maxWidth: 120 }}>
                      <span>AGL, м</span>
                      <input type="number" min={1} max={120} step={0.5} value={step.alt} onChange={(e) => updateStep(index, { ...step, alt: Number(e.target.value) })} />
                    </label>
                  </>
                ) : null}

                <button
                  className="btn danger"
                  disabled={steps.length <= 1 || status?.status === 'running'}
                  onClick={() => setSteps((previous) => previous.filter((_, i) => i !== index))}
                >
                  удалить
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="row" style={{ marginTop: 12, flexWrap: 'wrap' }}>
          <button className="btn" disabled={!!busy || status?.status === 'running'} onClick={() => setSteps((p) => [...p, { action: 'wait', seconds: 3 }])}>
            + шаг
          </button>
          <button
            className="btn primary"
            disabled={!!busy || steps.length === 0 || status?.status === 'running' || (missionNeedsGps && !gpsReady)}
            onClick={() => void run('start', start)}
          >
            START MISSION
          </button>
          <button className="btn danger" disabled={!!busy} onClick={() => void run('land', () => stop('land'))}>
            STOP → LAND
          </button>
          <button className="btn" disabled={!!busy || !gpsReady} onClick={() => void run('rtl', () => stop('rtl'))}>
            STOP → RTL
          </button>
          <button
            className="btn danger"
            title="Только на стенде или после посадки"
            disabled={!!busy}
            onClick={() => void run('disarm', () => stop('disarm'))}
          >
            DISARM
          </button>
        </div>
      </section>

      <div style={{ display: 'grid', gap: 16, alignContent: 'start' }}>
        <section className="card">
          <div className="cardTitle">Pixhawk</div>
          <div className="kv">
            <div className="k">LINK</div><div className="v">{wsStatus === 'open' ? 'ONLINE' : wsStatus}</div>
            <div className="k">ARM</div><div className="v">{telemetry?.armed == null ? '—' : telemetry.armed ? 'ARMED' : 'DISARMED'}</div>
            <div className="k">MODE</div><div className="v">{telemetry?.mode ?? '—'}</div>
            <div className="k">ALT</div><div className="v">{telemetry?.alt == null ? '—' : `${telemetry.alt.toFixed(2)} м AGL`}</div>
            <div className="k">BARO</div><div className="v">{telemetry?.baro_alt_m == null ? '—' : `${telemetry.baro_alt_m.toFixed(1)} м`}</div>
            <div className="k">HDG</div><div className="v">{telemetry?.heading == null ? '—' : `${telemetry.heading.toFixed(0)}°`}</div>
            <div className="k">GPS</div>
            <div className="v" style={{ color: gpsReady ? '#4ade80' : '#fbbf24' }}>
              {telemetry?.gps_fix ?? 0} fix · {telemetry?.gps_sats ?? 0} sat
            </div>
            <div className="k">BAT</div><div className="v">{telemetry?.battery == null ? '—' : `${telemetry.battery.toFixed(0)}%`}</div>
          </div>
        </section>

        <section className="card">
          <div className="cardTitle">Статус миссии</div>
          <div className="kv">
            <div className="k">status</div><div className="v">{status?.status ?? 'idle'}</div>
            <div className="k">step</div><div className="v">{status?.current_step ?? '—'} / {status?.total_steps ?? '—'}</div>
            <div className="k">action</div><div className="v">{status?.current_action ?? '—'}</div>
          </div>
          {status?.error ? <div className="alert" style={{ marginTop: 12 }}>{status.error}</div> : null}
        </section>

        <section className="card">
          <div className="cardTitle">JSON → /mission</div>
          <pre style={{ margin: 0, maxHeight: 360, overflow: 'auto', fontSize: 12 }}>{json}</pre>
        </section>

        <section className="card">
          <div className="cardTitle">Быстрые команды</div>
          <div className="row" style={{ flexWrap: 'wrap' }}>
            <button
              className="btn"
              disabled={!!busy}
              title={gpsReady ? 'Обычный ARM' : 'Без GPS — force-arm (только без пропеллеров)'}
              onClick={() => void run('arm', () => api.arm(!gpsReady))}
            >
              {gpsReady ? 'ARM' : 'Force ARM'}
            </button>
            <button className="btn" disabled={!!busy} onClick={() => void run('land-direct', api.land)}>LAND</button>
            <button className="btn" disabled={!!busy || !gpsReady} onClick={() => void run('rtl-direct', api.returnHome)}>RTL</button>
          </div>
        </section>
      </div>
    </div>
  )
}
