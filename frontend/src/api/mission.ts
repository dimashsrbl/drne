import type { MissionStatus } from './types'
import { apiFetch } from './shared'

export type MissionAction =
  | { action: 'arm' }
  | { action: 'disarm' }
  | { action: 'takeoff'; alt: number; no_gps?: boolean }
  | { action: 'land' }
  | { action: 'goto'; lat: number; lon: number; alt: number }
  | { action: 'wait'; seconds: number }
  | { action: 'return_home' }

export type MissionRequest = { mission: MissionAction[] }

export const missionApi = {
  start: (body: MissionRequest) =>
    apiFetch<MissionStatus>('/mission', { method: 'POST', body: JSON.stringify(body) }),
  status: () => apiFetch<MissionStatus>('/mission/status'),
}

