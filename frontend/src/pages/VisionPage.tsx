import { useEffect, useRef, useState } from 'react'

interface VisionStatus {
  running: boolean
  camera_index: number
  fps: number
  error: string | null
  obstacle_detected: boolean
  obstacle_area_ratio: number
  obstacle_offset_x: number
  obstacle_offset_y: number
  obstacle_high_threat: boolean
  obstacle_primary_class: string
  obstacle_track_id: number | null  // ByteTrack ID главного препятствия
  frame_b64: string
  backend: string   // "yolo" | "yolo-int8" | "yolo-onnx" | "canny" | "none"
}

interface AvoidanceStatus {
  active: boolean
  phase: string
  target_lat: number | null
  target_lon: number | null
  target_alt: number | null
  log: string[]
}

const POLL_MS = 500
const MJPEG_URL = '/api/vision/stream'

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, opts)
  const data = await res.json()
  if (!res.ok) throw new Error(data?.detail ?? `HTTP ${res.status}`)
  return data as T
}

export function VisionPage() {
  const [visionStatus, setVisionStatus] = useState<VisionStatus | null>(null)
  const [avoidStatus, setAvoidStatus] = useState<AvoidanceStatus | null>(null)
  const [cameraIndex, setCameraIndex] = useState(0)
  const [targetLat, setTargetLat] = useState(51.1694)
  const [targetLon, setTargetLon] = useState(71.4491)
  const [targetAlt, setTargetAlt] = useState(15)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [mjpegKey, setMjpegKey] = useState(0)  // перезапуск стрима при включении камеры
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    const tick = async () => {
      try {
        const [vs, as_] = await Promise.all([
          apiFetch<VisionStatus>('/vision/status'),
          apiFetch<AvoidanceStatus>('/vision/avoidance/status'),
        ])
        setVisionStatus(vs)
        setAvoidStatus(as_)
        setErr(null)
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e))
      }
    }
    void tick()
    pollRef.current = window.setInterval(() => void tick(), POLL_MS)
    return () => { if (pollRef.current) window.clearInterval(pollRef.current) }
  }, [])

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    setErr(null)
    try { await fn() } catch (e) { setErr(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(null) }
  }

  const obstacleColor = visionStatus?.obstacle_detected ? '#f87171' : '#4ade80'

  return (
    <div className="grid" style={{ gridTemplateColumns: '1.4fr 0.6fr' }}>

      {/* Левая колонка — видео + статус */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

        {/* Видеопоток — MJPEG */}
        <section className="card" style={{ padding: 0, overflow: 'hidden', position: 'relative' }}>
          {visionStatus?.running ? (
            <img
              key={mjpegKey}
              src={MJPEG_URL}
              alt="camera stream"
              style={{ width: '100%', display: 'block', borderRadius: 12 }}
            />
          ) : (
            <div style={{
              height: 300, display: 'flex', alignItems: 'center',
              justifyContent: 'center', opacity: 0.4, fontSize: 14, flexDirection: 'column', gap: 8,
            }}>
              <span style={{ fontSize: 32 }}>📷</span>
              Камера не запущена
            </div>
          )}
          {visionStatus?.running && (
            <div style={{
              position: 'absolute', top: 8, left: 8, display: 'flex', gap: 6,
            }}>
              <div style={{
                background: 'rgba(0,0,0,0.6)', borderRadius: 6,
                padding: '2px 8px', fontSize: 11, color: '#4ade80',
              }}>
                LIVE · {visionStatus.fps.toFixed(0)} fps
              </div>
              {visionStatus.backend && (
                <div style={{
                  background: visionStatus.backend.startsWith('yolo')
                    ? visionStatus.backend.includes('int8')
                      ? 'rgba(16,185,129,0.88)'   // зелёный — INT8 (самый быстрый)
                      : 'rgba(99,102,241,0.85)'   // фиолетовый — обычный YOLO
                    : 'rgba(80,80,80,0.7)',
                  borderRadius: 6, padding: '2px 8px', fontSize: 11, color: '#fff', fontWeight: 700,
                }}>
                  {visionStatus.backend.toUpperCase()}
                </div>
              )}
            </div>
          )}
          {visionStatus?.obstacle_detected && (
            <div style={{
              position: 'absolute', top: 8, right: 8,
              background: visionStatus.obstacle_high_threat ? 'rgba(220,38,38,0.92)' : 'rgba(234,88,12,0.85)',
              borderRadius: 6, padding: '2px 10px', fontSize: 12, fontWeight: 700, color: '#fff',
            }}>
              {visionStatus.obstacle_high_threat ? '⚠ HIGH THREAT' : 'OBSTACLE'}
              {visionStatus.obstacle_primary_class ? ` · ${visionStatus.obstacle_primary_class}` : ''}
              {visionStatus.obstacle_track_id != null ? ` #${visionStatus.obstacle_track_id}` : ''}
            </div>
          )}
        </section>

        {/* Статус детектирования */}
        <section className="card">
          <div className="cardTitle">Детектирование препятствий</div>
          <div className="kv" style={{ marginTop: 8 }}>
            <div className="k">Камера</div>
            <div className="v">{visionStatus?.running ? `#${visionStatus.camera_index} ✓` : 'не запущена'}</div>
            <div className="k">FPS</div>
            <div className="v">{visionStatus?.fps.toFixed(1) ?? '—'}</div>
            <div className="k">Препятствие</div>
            <div className="v" style={{ color: obstacleColor, fontWeight: 600 }}>
              {visionStatus?.obstacle_detected ? 'ОБНАРУЖЕНО' : 'чисто'}
            </div>
            <div className="k">Площадь</div>
            <div className="v">
              {visionStatus ? `${(visionStatus.obstacle_area_ratio * 100).toFixed(1)}%` : '—'}
            </div>
            <div className="k">Смещение X</div>
            <div className="v">
              {visionStatus
                ? `${visionStatus.obstacle_offset_x > 0 ? 'правее' : 'левее'} (${visionStatus.obstacle_offset_x.toFixed(2)})`
                : '—'}
            </div>
            <div className="k">Объект</div>
            <div className="v" style={{ color: visionStatus?.obstacle_high_threat ? '#f87171' : undefined }}>
              {visionStatus?.obstacle_primary_class || '—'}
            </div>
            <div className="k">Track ID</div>
            <div className="v">
              {visionStatus?.obstacle_track_id != null
                ? <span style={{ color: '#fbbf24', fontWeight: 600 }}>#{visionStatus.obstacle_track_id}</span>
                : '—'}
            </div>
            <div className="k">Backend</div>
            <div className="v" style={{
              color: visionStatus?.backend?.includes('int8')
                ? '#34d399'
                : visionStatus?.backend?.startsWith('yolo') ? '#818cf8' : '#9ca3af',
            }}>
              {visionStatus?.backend || '—'}
            </div>
          </div>
          {visionStatus?.error && (
            <div className="alert" style={{ marginTop: 10 }}>
              <div className="alertBody">{visionStatus.error}</div>
            </div>
          )}
        </section>
      </div>

      {/* Правая колонка — управление */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

        {/* Управление камерой */}
        <section className="card">
          <div className="cardTitle">Камера</div>
          {err && (
            <div className="alert" style={{ marginBottom: 10 }}>
              <div className="alertBody">{err}</div>
            </div>
          )}
          <label className="field">
            <span>Индекс камеры</span>
            <input type="number" min={0} max={10} value={cameraIndex}
              onChange={(e) => setCameraIndex(Number(e.target.value))} />
          </label>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={!!busy}
              onClick={() => void run('cam-start', async () => {
                await apiFetch('/vision/camera/start', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ camera_index: cameraIndex }),
                })
                setMjpegKey((k) => k + 1)  // перезапустить <img> чтобы начать стрим
              })}>
              Запустить
            </button>
            <button className="btn danger" disabled={!!busy}
              onClick={() => void run('cam-stop', () =>
                apiFetch('/vision/camera/stop', { method: 'POST' })
              )}>
              Стоп
            </button>
          </div>
        </section>

        {/* Управление облётом */}
        <section className="card">
          <div className="cardTitle">Облёт препятствий</div>

          <div className="kv" style={{ marginBottom: 10 }}>
            <div className="k">Статус</div>
            <div className="v" style={{ fontWeight: 600 }}>
              {avoidStatus?.active
                ? <span style={{ color: '#facc15' }}>{avoidStatus.phase}</span>
                : <span style={{ opacity: 0.5 }}>неактивен</span>}
            </div>
          </div>

          <label className="field">
            <span>Цель: широта</span>
            <input type="number" step="0.00001" value={targetLat}
              onChange={(e) => setTargetLat(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Цель: долгота</span>
            <input type="number" step="0.00001" value={targetLon}
              onChange={(e) => setTargetLon(Number(e.target.value))} />
          </label>
          <label className="field">
            <span>Высота AGL (м)</span>
            <input type="number" min={1} max={200} value={targetAlt}
              onChange={(e) => setTargetAlt(Number(e.target.value))} />
          </label>

          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn primary" disabled={!!busy || avoidStatus?.active}
              onClick={() => void run('avoid-start', () =>
                apiFetch('/vision/avoidance/start', {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ lat: targetLat, lon: targetLon, alt: targetAlt }),
                })
              )}>
              Лететь + облёт
            </button>
            <button className="btn danger" disabled={!!busy || !avoidStatus?.active}
              onClick={() => void run('avoid-stop', () =>
                apiFetch('/vision/avoidance/stop', { method: 'POST' })
              )}>
              Стоп
            </button>
          </div>
        </section>

        {/* Лог */}
        <section className="card">
          <div className="cardTitle">Лог облёта</div>
          <div style={{
            fontFamily: 'ui-monospace, Menlo, Consolas, monospace',
            fontSize: 11,
            maxHeight: 200,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
          }}>
            {avoidStatus?.log.length
              ? [...avoidStatus.log].reverse().map((line, i) => (
                <div key={i} style={{ opacity: i === 0 ? 1 : 0.65 }}>{line}</div>
              ))
              : <div style={{ opacity: 0.4 }}>Лог пуст</div>
            }
          </div>
        </section>

      </div>
    </div>
  )
}
