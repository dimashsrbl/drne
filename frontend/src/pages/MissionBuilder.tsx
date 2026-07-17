import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { missionApi, type MissionAction } from '../api/mission'
import type { DroneProfile, MissionStatus } from '../api/types'

function move<T>(arr: T[], from: number, to: number): T[] {
  const copy = arr.slice()
  const [item] = copy.splice(from, 1)
  copy.splice(to, 0, item)
  return copy
}

export function MissionBuilder() {
  const [actions, setActions] = useState<MissionAction[]>([
    { action: 'arm' },
    { action: 'takeoff', alt: 10 },
    { action: 'wait', seconds: 5 },
    { action: 'land' },
  ])
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<MissionStatus | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [profile, setProfile] = useState<DroneProfile | null>(null)

  useEffect(() => {
    void api.profile().then(setProfile).catch(() => {})
  }, [])

  const json = useMemo(() => JSON.stringify({ mission: actions }, null, 2), [actions])

  const run = async () => {
    setBusy('start')
    setError(null)
    try {
      const res = await missionApi.start({ mission: actions })
      setStatus(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const refresh = async () => {
    setBusy('status')
    setError(null)
    try {
      const res = await missionApi.status()
      setStatus(res)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const add = (a: MissionAction) => setActions((p) => [...p, a])

  return (
    <div className="grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
      <section className="card">
        <div className="cardTitle">Конструктор миссий (MVP)</div>

        {profile && (
          <div className="hint" style={{ marginBottom: 10 }}>
            Профиль: <b>{profile.label}</b>
            {!profile.supports_missions && (
              <span style={{ color: '#f87171', marginLeft: 6 }}>
                — миссии недоступны в текущем профиле
              </span>
            )}
          </div>
        )}

        {profile?.profile === 'ardupilot' && (
          <div className="alert" style={{ marginBottom: 12, borderColor: 'rgba(59,130,246,0.45)', background: 'rgba(59,130,246,0.08)' }}>
            <div className="alertTitle" style={{ color: '#93c5fd' }}>ArduPilot</div>
            <div className="alertBody">
              Миссии: <b>arm → takeoff → goto → land</b> в режиме GUIDED. Маршрут с карты (<code>/nav/route</code>)
              загружает waypoints на FC (AUTO). Нужен GPS 3D. На Pi: <code>DRONE_BACKEND_PROFILE=ardupilot</code>,
              <code> DRONE_SITL_FORCE_ARM=false</code>.
            </div>
          </div>
        )}

        {profile?.profile === 'inav' && (
          <div className="alert" style={{ marginBottom: 12, borderColor: 'rgba(234,179,8,0.5)', background: 'rgba(234,179,8,0.08)' }}>
            <div className="alertTitle" style={{ color: '#fbbf24' }}>INAV: GPS и тест без GPS</div>
            <div className="alertBody">
              Для <b>goto / RTL</b> нужен GPS-фикс. Для короткого теста «взлёт–посадка» можно шаг
              <b> takeoff с no_gps=true</b> (режим ALT_HOLD + барометр, без спутников) — только на свой риск,
              первый раз без пропеллеров.
            </div>
          </div>
        )}

        {error ? (
          <div className="alert" style={{ marginBottom: 12 }}>
            <div className="alertTitle">Ошибка</div>
            <div className="alertBody">{error}</div>
          </div>
        ) : null}

        <div className="row">
          <button className="btn" onClick={() => add({ action: 'arm' })}>
            + включить (arm)
          </button>
          <button className="btn" onClick={() => add({ action: 'takeoff', alt: 10 })}>
            + взлёт (takeoff)
          </button>
          <button
            className="btn"
            title="arm → взлёт 2 м без GPS → пауза → land"
            onClick={() =>
              setActions([
                { action: 'arm' },
                { action: 'takeoff', alt: 2, no_gps: true },
                { action: 'wait', seconds: 3 },
                { action: 'land' },
              ])
            }
          >
            пресет: тест 2м (no_gps)
          </button>
          <button className="btn" onClick={() => add({ action: 'goto', lat: 51.1694, lon: 71.4491, alt: 15 })}>
            + перелёт (goto)
          </button>
          <button className="btn" onClick={() => add({ action: 'wait', seconds: 5 })}>
            + ждать (wait)
          </button>
          <button className="btn" onClick={() => add({ action: 'land' })}>
            + посадка (land)
          </button>
          <button className="btn" onClick={() => add({ action: 'return_home' })}>
            + возврат домой (RTL)
          </button>
        </div>

        <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
          {actions.map((a, idx) => (
            <div key={idx} className="card" style={{ padding: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, alignItems: 'center' }}>
                <div style={{ fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 13 }}>
                  <b>{idx + 1}.</b> {a.action}
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn" disabled={idx === 0} onClick={() => setActions((p) => move(p, idx, idx - 1))}>
                    ↑
                  </button>
                  <button
                    className="btn"
                    disabled={idx === actions.length - 1}
                    onClick={() => setActions((p) => move(p, idx, idx + 1))}
                  >
                    ↓
                  </button>
                  <button className="btn danger" onClick={() => setActions((p) => p.filter((_, i) => i !== idx))}>
                    удалить
                  </button>
                </div>
              </div>

              {a.action === 'takeoff' ? (
                <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <label className="field">
                    <span>высота (м)</span>
                    <input
                      type="number"
                      min={1}
                      max={200}
                      value={a.alt}
                      onChange={(e) =>
                        setActions((p) =>
                          p.map((x, i) =>
                            i === idx && x.action === 'takeoff' ? { ...x, alt: Number(e.target.value) } : x,
                          ),
                        )
                      }
                    />
                  </label>
                  <label className="field" style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <input
                      type="checkbox"
                      checked={a.no_gps === true}
                      onChange={(e) =>
                        setActions((p) =>
                          p.map((x, i) =>
                            i === idx && x.action === 'takeoff' ? { ...x, no_gps: e.target.checked } : x,
                          ),
                        )
                      }
                    />
                    <span>без GPS (INAV: ALT_HOLD + баро)</span>
                  </label>
                </div>
              ) : null}

              {a.action === 'wait' ? (
                <label className="field" style={{ marginTop: 10 }}>
                  <span>секунды</span>
                  <input
                    type="number"
                    min={0.1}
                    max={3600}
                    value={a.seconds}
                    onChange={(e) =>
                      setActions((p) =>
                        p.map((x, i) => (i === idx && x.action === 'wait' ? { ...x, seconds: Number(e.target.value) } : x)),
                      )
                    }
                  />
                </label>
              ) : null}

              {a.action === 'goto' ? (
                <div className="row" style={{ marginTop: 10 }}>
                  <label className="field">
                    <span>широта</span>
                    <input
                      type="number"
                      value={a.lat}
                      onChange={(e) =>
                        setActions((p) =>
                          p.map((x, i) => (i === idx && x.action === 'goto' ? { ...x, lat: Number(e.target.value) } : x)),
                        )
                      }
                    />
                  </label>
                  <label className="field">
                    <span>долгота</span>
                    <input
                      type="number"
                      value={a.lon}
                      onChange={(e) =>
                        setActions((p) =>
                          p.map((x, i) => (i === idx && x.action === 'goto' ? { ...x, lon: Number(e.target.value) } : x)),
                        )
                      }
                    />
                  </label>
                  <label className="field">
                    <span>высота</span>
                    <input
                      type="number"
                      value={a.alt}
                      onChange={(e) =>
                        setActions((p) =>
                          p.map((x, i) => (i === idx && x.action === 'goto' ? { ...x, alt: Number(e.target.value) } : x)),
                        )
                      }
                    />
                  </label>
                </div>
              ) : null}
            </div>
          ))}
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <button
            className="btn primary"
            disabled={!!busy || actions.length === 0 || (profile != null && !profile.supports_missions)}
            onClick={() => void run()}
          >
            Запустить миссию
          </button>
          <button className="btn" disabled={!!busy} onClick={() => void refresh()}>
            Обновить статус миссии
          </button>
        </div>

        {busy ? <div className="hint">Выполняю: <b>{busy}</b></div> : null}
        {status ? (
          <div className="hint" style={{ marginTop: 8 }}>
            статус: <b>{status.status}</b> • шаг: {status.current_step ?? '—'}/{status.total_steps ?? '—'} • действие:{' '}
            {status.current_action ?? '—'} {status.error ? `• ошибка: ${status.error}` : ''}
          </div>
        ) : null}
      </section>

      <section className="card">
        <div className="cardTitle">JSON</div>
        <pre
          style={{
            margin: 0,
            padding: 12,
            background: 'rgba(0,0,0,0.25)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: 12,
            overflow: 'auto',
            maxHeight: 640,
            fontSize: 12,
          }}
        >
          {json}
        </pre>
      </section>
    </div>
  )
}

