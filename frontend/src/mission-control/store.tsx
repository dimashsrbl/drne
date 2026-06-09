/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useMemo, useReducer } from 'react'
import type { ReactNode } from 'react'
import type { MissionStatus, TelemetryResponse, Waypoint } from '../api/types'

export type ViewportMode = 'map' | 'sim' | 'vision'

type MissionControlState = {
  telemetry: TelemetryResponse | null
  wsStatus: 'connecting' | 'open' | 'closed'
  waypoints: Waypoint[]
  waypointAlt: number
  addPointsMode: boolean
  viewportMode: ViewportMode
  missionStatus: MissionStatus | null
  visionTrackerHealthy: boolean
}

type MissionControlAction =
  | { type: 'setTelemetry'; payload: { telemetry: TelemetryResponse | null; wsStatus: MissionControlState['wsStatus'] } }
  | { type: 'setViewport'; payload: ViewportMode }
  | { type: 'setWaypointAlt'; payload: number }
  | { type: 'setAddPointsMode'; payload: boolean }
  | { type: 'toggleAddPointsMode' }
  | { type: 'addWaypoint'; payload: Waypoint }
  | { type: 'deleteWaypoint'; payload: number }
  | { type: 'clearWaypoints' }
  | { type: 'setMissionStatus'; payload: MissionStatus | null }
  | { type: 'setVisionTrackerHealthy'; payload: boolean }

const initialState: MissionControlState = {
  telemetry: null,
  wsStatus: 'connecting',
  waypoints: [],
  waypointAlt: 15,
  addPointsMode: false,
  viewportMode: 'map',
  missionStatus: null,
  visionTrackerHealthy: false,
}

function reducer(state: MissionControlState, action: MissionControlAction): MissionControlState {
  switch (action.type) {
    case 'setTelemetry':
      return { ...state, telemetry: action.payload.telemetry, wsStatus: action.payload.wsStatus }
    case 'setViewport':
      return { ...state, viewportMode: action.payload }
    case 'setWaypointAlt':
      return { ...state, waypointAlt: Math.max(1, Math.min(200, action.payload || 1)) }
    case 'setAddPointsMode':
      return { ...state, addPointsMode: action.payload }
    case 'toggleAddPointsMode':
      return { ...state, addPointsMode: !state.addPointsMode }
    case 'addWaypoint':
      return { ...state, waypoints: [...state.waypoints, action.payload] }
    case 'deleteWaypoint':
      return { ...state, waypoints: state.waypoints.filter((_, idx) => idx !== action.payload) }
    case 'clearWaypoints':
      return { ...state, waypoints: [] }
    case 'setMissionStatus':
      return { ...state, missionStatus: action.payload }
    case 'setVisionTrackerHealthy':
      return { ...state, visionTrackerHealthy: action.payload }
    default:
      return state
  }
}

type MissionControlContextValue = {
  state: MissionControlState
  setTelemetry: (telemetry: TelemetryResponse | null, wsStatus: MissionControlState['wsStatus']) => void
  setViewportMode: (mode: ViewportMode) => void
  setWaypointAlt: (alt: number) => void
  setAddPointsMode: (enabled: boolean) => void
  toggleAddPointsMode: () => void
  addWaypoint: (point: Waypoint) => void
  deleteWaypoint: (index: number) => void
  clearWaypoints: () => void
  setMissionStatus: (status: MissionStatus | null) => void
  setVisionTrackerHealthy: (healthy: boolean) => void
}

const MissionControlContext = createContext<MissionControlContextValue | null>(null)

export function MissionControlProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, initialState)

  const setTelemetry = useCallback(
    (telemetry: TelemetryResponse | null, wsStatus: MissionControlState['wsStatus']) => {
      dispatch({ type: 'setTelemetry', payload: { telemetry, wsStatus } })
    },
    [],
  )
  const setViewportMode = useCallback((mode: ViewportMode) => {
    dispatch({ type: 'setViewport', payload: mode })
  }, [])
  const setWaypointAlt = useCallback((alt: number) => {
    dispatch({ type: 'setWaypointAlt', payload: alt })
  }, [])
  const setAddPointsMode = useCallback((enabled: boolean) => {
    dispatch({ type: 'setAddPointsMode', payload: enabled })
  }, [])
  const toggleAddPointsMode = useCallback(() => {
    dispatch({ type: 'toggleAddPointsMode' })
  }, [])
  const addWaypoint = useCallback((point: Waypoint) => {
    dispatch({ type: 'addWaypoint', payload: point })
  }, [])
  const deleteWaypoint = useCallback((index: number) => {
    dispatch({ type: 'deleteWaypoint', payload: index })
  }, [])
  const clearWaypoints = useCallback(() => {
    dispatch({ type: 'clearWaypoints' })
  }, [])
  const setMissionStatus = useCallback((status: MissionStatus | null) => {
    dispatch({ type: 'setMissionStatus', payload: status })
  }, [])
  const setVisionTrackerHealthy = useCallback((healthy: boolean) => {
    dispatch({ type: 'setVisionTrackerHealthy', payload: healthy })
  }, [])

  const value = useMemo<MissionControlContextValue>(
    () => ({
      state,
      setTelemetry,
      setViewportMode,
      setWaypointAlt,
      setAddPointsMode,
      toggleAddPointsMode,
      addWaypoint,
      deleteWaypoint,
      clearWaypoints,
      setMissionStatus,
      setVisionTrackerHealthy,
    }),
    [
      state,
      setTelemetry,
      setViewportMode,
      setWaypointAlt,
      setAddPointsMode,
      toggleAddPointsMode,
      addWaypoint,
      deleteWaypoint,
      clearWaypoints,
      setMissionStatus,
      setVisionTrackerHealthy,
    ],
  )

  return <MissionControlContext.Provider value={value}>{children}</MissionControlContext.Provider>
}

export function useMissionControlStore() {
  const ctx = useContext(MissionControlContext)
  if (!ctx) {
    throw new Error('useMissionControlStore must be used inside MissionControlProvider')
  }
  return ctx
}
