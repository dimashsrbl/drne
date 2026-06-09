import { missionApi } from '../../api/mission'
import { navApi } from '../../api/nav'
import type { MissionStatus, Waypoint } from '../../api/types'

export function MissionPanel({
  waypoints,
  waypointAlt,
  addPointsMode,
  busy,
  missionStatus,
  onSetAlt,
  onToggleAddMode,
  onDeleteWaypoint,
  onClearWaypoints,
  onRunStart,
  onRunFinish,
  onSetStatus,
  onError,
}: {
  waypoints: Waypoint[]
  waypointAlt: number
  addPointsMode: boolean
  busy: string | null
  missionStatus: MissionStatus | null
  onSetAlt: (alt: number) => void
  onToggleAddMode: () => void
  onDeleteWaypoint: (index: number) => void
  onClearWaypoints: () => void
  onRunStart: (label: string) => void
  onRunFinish: () => void
  onSetStatus: (status: MissionStatus | null) => void
  onError: (message: string | null) => void
}) {
  const runRoute = async () => {
    if (waypoints.length === 0) return
    onRunStart('route')
    onError(null)
    try {
      const status = await navApi.route({ waypoints, arm: true, takeoff_alt: 10, land_at_end: false })
      onSetStatus(status)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      onRunFinish()
    }
  }

  const refreshStatus = async () => {
    onRunStart('mission-status')
    onError(null)
    try {
      const status = await missionApi.status()
      onSetStatus(status)
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e))
    } finally {
      onRunFinish()
    }
  }

  return (
    <section className="card missionBottomPanel">
      <div className="cardTitle">Миссия и точки</div>
      <div className="row">
        <button className="btn" onClick={onToggleAddMode}>
          {addPointsMode ? 'Стоп добавление' : 'Добавлять точки'}
        </button>
        <label className="field" style={{ minWidth: 140 }}>
          <span>Высота точки (м)</span>
          <input type="number" min={1} max={200} value={waypointAlt} onChange={(e) => onSetAlt(Number(e.target.value))} />
        </label>
        <button className="btn" disabled={waypoints.length === 0} onClick={onClearWaypoints}>
          Очистить
        </button>
      </div>

      <div className="missionWaypointList">
        {waypoints.length === 0 ? (
          <div className="hint">Точек пока нет. Можно ставить на карте или в режиме симуляции.</div>
        ) : (
          waypoints.map((wp, idx) => (
            <div key={`${wp.lat}-${wp.lon}-${idx}`} className="missionWaypointRow">
              <span>#{idx + 1}</span>
              <span>{wp.lat.toFixed(5)}</span>
              <span>{wp.lon.toFixed(5)}</span>
              <span>{wp.alt.toFixed(0)}м</span>
              <button className="btn danger" onClick={() => onDeleteWaypoint(idx)}>
                удалить
              </button>
            </div>
          ))
        )}
      </div>

      <div className="row" style={{ marginTop: 10 }}>
        <button className="btn primary" disabled={!!busy || waypoints.length === 0} onClick={() => void runRoute()}>
          Запустить маршрут
        </button>
        <button className="btn" disabled={!!busy} onClick={() => void refreshStatus()}>
          Обновить статус миссии
        </button>
      </div>

      {missionStatus ? (
        <div className="hint">
          статус: <b>{missionStatus.status}</b> • шаг: {missionStatus.current_step ?? '—'}/{missionStatus.total_steps ?? '—'} • действие:{' '}
          {missionStatus.current_action ?? '—'} {missionStatus.error ? `• ошибка: ${missionStatus.error}` : ''}
        </div>
      ) : null}
    </section>
  )
}
