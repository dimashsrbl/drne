export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  const text = await res.text()
  const data = text ? (JSON.parse(text) as unknown) : undefined

  if (!res.ok) {
    let detail =
      typeof data === 'object' && data && 'detail' in (data as any)
        ? String((data as any).detail)
        : `HTTP ${res.status}`
    if (res.status === 502) {
      detail =
        'HTTP 502 — нет связи с backend на Pi. Проверь IP в frontend/.env.local (VITE_DRONE_API_URL), ' +
        'что Pi в той же сети, drone-mission запущен: curl http://IP:8000/health. Перезапусти npm run dev после смены .env.'
    }
    throw new Error(detail)
  }

  return data as T
}

