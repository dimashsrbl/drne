import type { MissionStatus, Waypoint } from './types'
import { apiFetch } from './shared'

export type RouteRequest = {
  waypoints: Waypoint[]
  arm?: boolean
  takeoff_alt?: number | null
  land_at_end?: boolean
}

export const navApi = {
  route: (body: RouteRequest) =>
    apiFetch<MissionStatus>('/nav/route', { method: 'POST', body: JSON.stringify(body) }),
}

