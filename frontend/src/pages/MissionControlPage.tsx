import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DroneProfile } from '../api/types'
import { useTelemetry } from '../telemetry/TelemetryProvider'
import { CompassGpsPanel } from '../mission-control/components/CompassGpsPanel'
import { FlightActionsPanel } from '../mission-control/components/FlightActionsPanel'
import { MissionPanel } from '../mission-control/components/MissionPanel'
import { MissionViewport } from '../mission-control/components/MissionViewport'
import { TelemetryPanel } from '../mission-control/components/TelemetryPanel'
import { MissionControlProvider, useMissionControlStore } from '../mission-control/store'

function MissionControlBody() {
  const { telemetry, wsStatus } = useTelemetry()
  const { state, setTelemetry, setViewportMode, setWaypointAlt, toggleAddPointsMode, addWaypoint, deleteWaypoint, clearWaypoints, setMissionStatus, setVisionTrackerHealthy } =
    useMissionControlStore()
  const [profile, setProfile] = useState<DroneProfile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    setTelemetry(telemetry, wsStatus)
  }, [telemetry, wsStatus, setTelemetry])

  useEffect(() => {
    let dead = false
    void api
      .profile()
      .then((data) => {
        if (!dead) setProfile(data)
      })
      .catch(() => {})
    return () => {
      dead = true
    }
  }, [])

  return (
    <div className="missionControlLayout">
      <div className="missionTopLeft">
        <TelemetryPanel telemetry={state.telemetry} profile={profile} wsStatus={state.wsStatus} />
        <CompassGpsPanel telemetry={state.telemetry} />
      </div>

      <div className="missionCenter">
        <MissionViewport
          mode={state.viewportMode}
          telemetry={state.telemetry}
          waypoints={state.waypoints}
          addMode={state.addPointsMode}
          onSetMode={setViewportMode}
          onTrackerHealth={setVisionTrackerHealthy}
          onAddWaypoint={(lat, lon) => addWaypoint({ lat, lon, alt: state.waypointAlt })}
        />
      </div>

      <div className="missionRight">
        <FlightActionsPanel profile={profile} onError={setError} />
        <section className="card">
          <div className="cardTitle">Сервисы</div>
          <div className="hint">Vision tracker: {state.visionTrackerHealthy ? <b style={{ color: 'var(--gcs-accent-bright)' }}>online</b> : <b style={{ color: 'var(--gcs-danger)' }}>offline</b>}</div>
          <div className="hint">Текущий режим окна: <b>{state.viewportMode}</b></div>
          <div className="hint">Точек в миссии: <b>{state.waypoints.length}</b></div>
          {busy ? <div className="hint">Выполняю: {busy}</div> : null}
        </section>
        {error ? (
          <div className="alert">
            <div className="alertTitle">Ошибка</div>
            <div className="alertBody">{error}</div>
          </div>
        ) : null}
      </div>

      <div className="missionBottom">
        <MissionPanel
          waypoints={state.waypoints}
          waypointAlt={state.waypointAlt}
          addPointsMode={state.addPointsMode}
          busy={busy}
          missionStatus={state.missionStatus}
          onSetAlt={setWaypointAlt}
          onToggleAddMode={toggleAddPointsMode}
          onDeleteWaypoint={deleteWaypoint}
          onClearWaypoints={clearWaypoints}
          onRunStart={setBusy}
          onRunFinish={() => setBusy(null)}
          onSetStatus={setMissionStatus}
          onError={setError}
        />
      </div>
    </div>
  )
}

export function MissionControlPage() {
  return (
    <MissionControlProvider>
      <MissionControlBody />
    </MissionControlProvider>
  )
}
