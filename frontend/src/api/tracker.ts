/** Базовый URL vision-tracker. Пусто → Vite proxy /tracker → localhost (только если tracker на ПК). */
export const TRACKER_BASE = String(import.meta.env.VITE_VISION_TRACKER_URL ?? '').replace(/\/$/, '')

export function trackerUrl(path: string): string {
  const p = path.startsWith('/') ? path : `/${path}`
  return TRACKER_BASE ? `${TRACKER_BASE}${p}` : `/tracker${p}`
}
