import { useState } from 'react'
import { api } from '../../api/client'
import type { DroneProfile } from '../../api/types'

export function FlightActionsPanel({
  profile,
  onError,
}: {
  profile: DroneProfile | null
  onError: (message: string | null) => void
}) {
  const [takeoffAlt, setTakeoffAlt] = useState(10)
  const [busy, setBusy] = useState<string | null>(null)

  const allowCoreCommands = profile?.supports_direct_commands ?? true
  const allowMissionCommands = profile == null ? true : profile.profile === 'ardupilot' || profile.supports_missions

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    onError(null)
    try {
      await fn()
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card">
      <div className="cardTitle">Быстрые команды</div>
      <div className="row">
        <button className="btn" disabled={!!busy || !allowCoreCommands} onClick={() => void run('arm', () => api.arm())}>
          ARM
        </button>
        <button className="btn" disabled={!!busy || !allowCoreCommands} onClick={() => void run('disarm', () => api.disarm())}>
          DISARM
        </button>
        <button className="btn" disabled={!!busy || !allowMissionCommands} onClick={() => void run('rtl', () => api.returnHome())}>
          RTL
        </button>
      </div>
      <div className="row">
        <label className="field" style={{ minWidth: 120 }}>
          <span>Взлёт (м)</span>
          <input type="number" min={1} max={200} value={takeoffAlt} onChange={(e) => setTakeoffAlt(Number(e.target.value))} />
        </label>
        <button
          className="btn primary"
          disabled={!!busy || !allowMissionCommands}
          onClick={() => void run('takeoff', () => api.takeoff(takeoffAlt))}
        >
          Взлёт
        </button>
        <button className="btn danger" disabled={!!busy || !allowMissionCommands} onClick={() => void run('land', () => api.land())}>
          Посадка
        </button>
      </div>
      <div className="hint">{busy ? `Выполняю: ${busy}` : 'Готово к командам'}</div>
    </section>
  )
}
