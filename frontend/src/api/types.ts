export type CommandResponse = {
  ok: boolean
  detail?: string | null
}

export type TelemetryResponse = {
  lat: number | null
  lon: number | null
  alt: number | null
  battery: number | null
  status: string
  speed: number | null
  armed: boolean | null
  mode: string | null
  heading: number | null
  source?: string | null
  note?: string | null
}

export type SafetyGate = {
  id: string
  title: string
  level: string
}

export type DroneProfile = {
  profile: string
  label: string
  supports_missions: boolean
  supports_manual_control: boolean
  supports_direct_commands: boolean
  supports_video: boolean
  video_url?: string | null
  warnings: string[]
  safety_gates: SafetyGate[]
}

export type MissionStatus = {
  status: 'idle' | 'running' | 'completed' | 'error'
  current_step?: number | null
  total_steps?: number | null
  current_action?: string | null
  error?: string | null
}

export type Waypoint = {
  lat: number
  lon: number
  alt: number
}

