import 'leaflet/dist/leaflet.css'

import { useEffect, useMemo, useRef, useState } from 'react'
import type { LatLngLiteral, LeafletMouseEvent } from 'leaflet'
import { MapContainer, Marker, Polyline, TileLayer, useMapEvents } from 'react-leaflet'
import type { TelemetryResponse, Waypoint } from '../../api/types'
import { astanaCenter } from '../../constants/astana'
import { DroneScene } from '../../scene/DroneScene'
import type { ViewportMode } from '../store'

type VisionStatus = {
  running: boolean
  fps: number
  error: string | null
  obstacle_detected: boolean
  obstacle_primary_class: string
  obstacle_track_id: number | null
  backend: string
}

type TrackerTarget = {
  track_id: number | null
  confidence: number
  target_locked: boolean
  lock_note: string
  cls_name: string | null
  lost: boolean
}

const TRACKER_BASE = import.meta.env.VITE_VISION_TRACKER_PATH ?? '/tracker'

function ClickToAdd({
  enabled,
  onAdd,
}: {
  enabled: boolean
  onAdd: (point: LatLngLiteral) => void
}) {
  useMapEvents({
    click(e: LeafletMouseEvent) {
      if (enabled) onAdd(e.latlng)
    },
  })
  return null
}

function MapViewport({
  telemetry,
  waypoints,
  addMode,
  onAddWaypoint,
}: {
  telemetry: TelemetryResponse | null
  waypoints: Waypoint[]
  addMode: boolean
  onAddWaypoint: (lat: number, lon: number) => void
}) {
  const dronePos = useMemo<LatLngLiteral | null>(
    () =>
      telemetry?.lat != null && telemetry?.lon != null
        ? { lat: telemetry.lat, lng: telemetry.lon }
        : null,
    [telemetry],
  )
  const center = useMemo<LatLngLiteral>(() => dronePos ?? astanaCenter, [dronePos])
  const polyline = useMemo(() => waypoints.map((p) => ({ lat: p.lat, lng: p.lon })), [waypoints])

  return (
    <div className="missionViewportSurface">
      <MapContainer center={center} zoom={15} style={{ height: '100%', width: '100%' }}>
        <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <ClickToAdd enabled={addMode} onAdd={(p) => onAddWaypoint(p.lat, p.lng)} />
        {dronePos ? <Marker position={dronePos} /> : null}
        {polyline.length >= 2 ? <Polyline positions={polyline} /> : null}
        {polyline.length === 1 ? <Marker position={polyline[0]} /> : null}
      </MapContainer>
    </div>
  )
}

function SimViewport({
  telemetry,
  waypoints,
  addMode,
  onAddWaypoint,
}: {
  telemetry: TelemetryResponse | null
  waypoints: Waypoint[]
  addMode: boolean
  onAddWaypoint: (lat: number, lon: number) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const sceneRef = useRef<DroneScene | null>(null)
  const simWp = useMemo(() => waypoints.map((w, idx) => ({ id: idx + 1, lat: w.lat, lon: w.lon })), [waypoints])

  useEffect(() => {
    if (!canvasRef.current) return
    const scene = new DroneScene(canvasRef.current)
    sceneRef.current = scene
    scene.onGroundClick((lat, lon) => {
      onAddWaypoint(lat, lon)
    })
    return () => {
      scene.dispose()
      sceneRef.current = null
    }
  }, [onAddWaypoint])

  useEffect(() => {
    sceneRef.current?.setPlacingMode(addMode)
  }, [addMode])

  useEffect(() => {
    sceneRef.current?.setWaypoints(simWp)
  }, [simWp])

  useEffect(() => {
    if (!telemetry || !sceneRef.current) return
    sceneRef.current.update({
      lat: telemetry.lat ?? null,
      lon: telemetry.lon ?? null,
      alt: telemetry.alt ?? null,
      heading: telemetry.heading ?? null,
      roll: null,
      pitch: null,
      speed: telemetry.speed ?? null,
      armed: telemetry.armed ?? null,
      battery: telemetry.battery ?? null,
      mode: telemetry.mode ?? null,
    })
  }, [telemetry])

  return (
    <div className="missionViewportSurface missionSimSurface">
      <canvas ref={canvasRef} className="missionSimCanvas" />
      <div className="missionSimHint">ЛКМ: орбита. В режиме «Добавлять точки» клик по земле добавляет waypoint.</div>
    </div>
  )
}

function VisionViewport({ onTrackerHealth }: { onTrackerHealth: (healthy: boolean) => void }) {
  const [visionStatus, setVisionStatus] = useState<VisionStatus | null>(null)
  const [trackerTarget, setTrackerTarget] = useState<TrackerTarget | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const [visionRes, trackerRes] = await Promise.all([
          fetch('/api/vision/status'),
          fetch(`${TRACKER_BASE}/target`),
        ])
        if (!alive) return
        if (visionRes.ok) {
          setVisionStatus((await visionRes.json()) as VisionStatus)
        }
        if (trackerRes.ok) {
          setTrackerTarget((await trackerRes.json()) as TrackerTarget)
          onTrackerHealth(true)
        } else {
          onTrackerHealth(false)
        }
        setErr(null)
      } catch (e) {
        if (alive) {
          setErr(e instanceof Error ? e.message : String(e))
          onTrackerHealth(false)
        }
      }
    }
    void tick()
    const id = window.setInterval(() => void tick(), 700)
    return () => {
      alive = false
      window.clearInterval(id)
    }
  }, [onTrackerHealth])

  const runTrackerCommand = async (path: '/lock' | '/unlock') => {
    setErr(null)
    try {
      const res = await fetch(`${TRACKER_BASE}${path}`, { method: 'POST' })
      if (!res.ok) throw new Error(`Tracker ${path} failed`)
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  return (
    <div className="missionViewportSurface missionVisionSurface">
      <img src={`${TRACKER_BASE}/stream`} alt="Vision tracker stream" className="missionVisionStream" />
      <div className="missionVisionOverlay">
        <button className="btn" onClick={() => void runTrackerCommand('/lock')}>
          Lock
        </button>
        <button className="btn danger" onClick={() => void runTrackerCommand('/unlock')}>
          Unlock
        </button>
      </div>
      <div className="missionVisionStats">
        <div>backend: {visionStatus?.backend ?? '—'}</div>
        <div>obstacle: {visionStatus?.obstacle_detected ? 'yes' : 'no'}</div>
        <div>track: {trackerTarget?.track_id ?? '—'}</div>
        <div>lock: {trackerTarget?.target_locked ? `on (${trackerTarget.lock_note || 'ok'})` : 'off'}</div>
        <div>target: {trackerTarget?.cls_name ?? '—'}</div>
      </div>
      {err ? <div className="alert missionInlineAlert"><div className="alertBody">{err}</div></div> : null}
    </div>
  )
}

export function MissionViewport({
  mode,
  telemetry,
  waypoints,
  addMode,
  onSetMode,
  onAddWaypoint,
  onTrackerHealth,
}: {
  mode: ViewportMode
  telemetry: TelemetryResponse | null
  waypoints: Waypoint[]
  addMode: boolean
  onSetMode: (mode: ViewportMode) => void
  onAddWaypoint: (lat: number, lon: number) => void
  onTrackerHealth: (healthy: boolean) => void
}) {
  return (
    <section className="card missionViewportCard">
      <div className="missionViewportHeader">
        <div className="cardTitle" style={{ marginBottom: 0 }}>
          Окно выполнения
        </div>
        <div className="missionModeSwitch">
          <button className={`btn ${mode === 'map' ? 'primary' : ''}`} onClick={() => onSetMode('map')}>
            Карта
          </button>
          <button className={`btn ${mode === 'sim' ? 'primary' : ''}`} onClick={() => onSetMode('sim')}>
            Симуляция
          </button>
          <button className={`btn ${mode === 'vision' ? 'primary' : ''}`} onClick={() => onSetMode('vision')}>
            Vision
          </button>
        </div>
      </div>

      {mode === 'map' ? <MapViewport telemetry={telemetry} waypoints={waypoints} addMode={addMode} onAddWaypoint={onAddWaypoint} /> : null}
      {mode === 'sim' ? <SimViewport telemetry={telemetry} waypoints={waypoints} addMode={addMode} onAddWaypoint={onAddWaypoint} /> : null}
      {mode === 'vision' ? <VisionViewport onTrackerHealth={onTrackerHealth} /> : null}
    </section>
  )
}
