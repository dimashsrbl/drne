import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DroneProfile } from '../api/types'
import { ArduPilotSequencePage } from './ArduPilotSequencePage'
import { BetaflightSequencePage } from './BetaflightSequencePage'

export function FlightSequencePage() {
  const [profile, setProfile] = useState<DroneProfile | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    void api.profile().then(setProfile).catch((e) => setError(e instanceof Error ? e.message : String(e)))
  }, [])

  if (error) {
    return (
      <div className="card">
        <div className="cardTitle">Нет связи с backend</div>
        <div className="alert">{error}</div>
      </div>
    )
  }

  if (!profile) {
    return <div className="card"><div className="hint">Определяем профиль полётного контроллера…</div></div>
  }

  if (profile.profile === 'ardupilot') {
    return <ArduPilotSequencePage />
  }

  return <BetaflightSequencePage />
}
