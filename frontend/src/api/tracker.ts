/** Базовый URL vision-tracker. Пусто → Vite proxy /tracker → localhost (только если tracker на ПК). */
export const TRACKER_BASE = String(import.meta.env.VITE_VISION_TRACKER_URL ?? '').replace(/\/$/, '')

export function trackerUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`
  return TRACKER_BASE ? `${TRACKER_BASE}${p}` : `/tracker${p}`
}

export async function fetchDetectionEnabled(): Promise<boolean> {
  const res = await fetch(trackerUrl('/detection'))
  if (!res.ok) throw new Error(`detection status: ${res.status}`)
  const data = (await res.json()) as { enabled?: boolean }
  return data.enabled !== false
}

export async function setDetectionEnabled(enabled: boolean): Promise<boolean> {
  const res = await fetch(trackerUrl('/detection'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  })
  if (!res.ok) throw new Error(`detection toggle: ${res.status}`)
  const data = (await res.json()) as { enabled?: boolean }
  return data.enabled !== false
}
