import type { TelemetryResponse } from '../../api/types'

export function CompassGpsPanel({ telemetry }: { telemetry: TelemetryResponse | null }) {
  const heading = telemetry?.heading ?? 0
  const hasGps = telemetry?.lat != null && telemetry?.lon != null

  function gpsFixLabel(fix: number | null | undefined): string {
    if (fix == null) return 'фикс'
    if (fix >= 2) return '3D'
    if (fix === 1) return '2D'
    return 'нет'
  }

  return (
    <section className="card">
      <div className="cardTitle">Компас и GPS</div>
      <div className="missionCompassWrap">
        <div className="missionCompass">
          <div className="missionCompassRose">N</div>
          <div className="missionCompassRose missionCompassRoseS">S</div>
          <div className="missionCompassRose missionCompassRoseE">E</div>
          <div className="missionCompassRose missionCompassRoseW">W</div>
          <div className="missionCompassNeedle" style={{ transform: `translate(-50%, -100%) rotate(${heading}deg)` }} />
        </div>
        <div className="missionCompassMeta">
          <div className="missionGauge">
            <span>Курс</span>
            <b>{telemetry?.heading != null ? `${Math.round(telemetry.heading)}°` : '—'}</b>
          </div>
          <div className="missionGauge">
            <span>GPS</span>
            <b style={{ color: hasGps ? 'var(--gcs-accent-bright)' : 'var(--gcs-danger)' }}>
              {hasGps ? gpsFixLabel(telemetry?.gps_fix) : 'нет фикса'}
              {telemetry?.gps_sats != null ? ` · ${telemetry.gps_sats} sat` : ''}
            </b>
          </div>
          <div className="missionGauge">
            <span>Координаты</span>
            <b>{hasGps ? `${telemetry!.lat!.toFixed(5)}, ${telemetry!.lon!.toFixed(5)}` : '—'}</b>
          </div>
        </div>
      </div>
    </section>
  )
}
