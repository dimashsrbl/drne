import type { CommandResponse, DroneProfile, TelemetryResponse } from './types'
import { apiFetch } from './shared'

export const api = {
  health: () => apiFetch<{ status: string }>('/health'),
  telemetry: () => apiFetch<TelemetryResponse>('/telemetry'),
  profile: () => apiFetch<DroneProfile>('/drone/profile'),

  arm: (force?: boolean) =>
    apiFetch<CommandResponse>(
      force == null ? '/drone/arm' : `/drone/arm?force=${force ? 'true' : 'false'}`,
      { method: 'POST' },
    ),
  disarm: () => apiFetch<CommandResponse>('/drone/disarm', { method: 'POST' }),
  land: () => apiFetch<CommandResponse>('/drone/land', { method: 'POST' }),
  returnHome: () => apiFetch<CommandResponse>('/drone/return-home', { method: 'POST' }),
  takeoff: (altitude: number, noGps = false) =>
    apiFetch<CommandResponse>('/drone/takeoff', {
      method: 'POST',
      body: JSON.stringify({ altitude, no_gps: noGps }),
    }),

  setHome: (lat: number, lon: number, alt: number) =>
    apiFetch<CommandResponse>('/drone/home', {
      method: 'POST',
      body: JSON.stringify({ lat, lon, alt }),
    }),

  setFlightMode: (mode: string) =>
    apiFetch<CommandResponse>('/drone/flight-mode', {
      method: 'POST',
      body: JSON.stringify({ mode }),
    }),
}

