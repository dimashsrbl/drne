import { apiFetch } from './shared'

export type BetaflightStepAction =
  | 'arm'
  | 'neutral'
  | 'throttle'
  | 'takeoff_alt'
  | 'hold_alt'
  | 'forward'
  | 'back'
  | 'left'
  | 'right'
  | 'yaw_left'
  | 'yaw_right'
  | 'land'
  | 'disarm'
  | 'wait'

export type BetaflightSequenceStep = {
  action: BetaflightStepAction
  seconds?: number
  throttle_us?: number | null
  stick_delta?: number | null
  target_alt_m?: number | null
  settle_s?: number | null
}

export type BetaflightSequenceRequest = {
  steps: BetaflightSequenceStep[]
  port?: string | null
  baud?: number | null
}

export type BetaflightEmergencyLandRequest = {
  port?: string | null
  baud?: number | null
  seconds?: number
  throttle_us?: number | null
}

export type BetaflightTrackStartRequest = {
  port?: string | null
  baud?: number | null
  vision_url?: string | null
  target_alt_m?: number
  wait_lock_s?: number
  auto_lock?: boolean
  throttle_us?: number | null
}

export type BetaflightVisionCheckResponse = {
  ok: boolean
  vision_url: string
  camera_status: string
  backend?: string | null
  target_locked: boolean
  lost: boolean
  cx?: number
  cy?: number
}

export type BetaflightCheckResponse = {
  ok: boolean
  detail: string
  port: string
  variant?: string | null
  version?: string | null
  status?: Record<string, number | string> | null
}

export type BetaflightSequenceStatus = {
  status: 'idle' | 'running' | 'completed' | 'stopped' | 'error'
  current_step?: number | null
  total_steps?: number | null
  current_action?: string | null
  elapsed_s: number
  error?: string | null
  port?: string | null
  current_channels?: number[] | null
  current_alt_m?: number | null
  target_alt_m?: number | null
}

export const betaflightApi = {
  check: (port?: string, baud?: number) => {
    const params = new URLSearchParams()
    if (port) params.set('port', port)
    if (baud) params.set('baud', String(baud))
    const qs = params.toString()
    return apiFetch<BetaflightCheckResponse>(`/betaflight/check${qs ? `?${qs}` : ''}`)
  },

  startSequence: (body: BetaflightSequenceRequest) =>
    apiFetch<BetaflightSequenceStatus>('/betaflight/sequence/start', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  stopSequence: () =>
    apiFetch<BetaflightSequenceStatus>('/betaflight/sequence/stop', {
      method: 'POST',
    }),

  emergencyLand: (body: BetaflightEmergencyLandRequest = {}) =>
    apiFetch<BetaflightSequenceStatus>('/betaflight/sequence/emergency-land', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  trackStart: (body: BetaflightTrackStartRequest = {}) =>
    apiFetch<BetaflightSequenceStatus>('/betaflight/track/start', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  visionCheck: (visionUrl?: string) => {
    const params = new URLSearchParams()
    if (visionUrl) params.set('vision_url', visionUrl)
    const qs = params.toString()
    return apiFetch<BetaflightVisionCheckResponse>(`/betaflight/vision/check${qs ? `?${qs}` : ''}`)
  },

  status: () => apiFetch<BetaflightSequenceStatus>('/betaflight/sequence/status'),

  heartbeat: () =>
    apiFetch<{ ok: string }>('/betaflight/sequence/heartbeat', { method: 'POST' }),
}
