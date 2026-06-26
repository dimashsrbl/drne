import type { ReactNode } from 'react'
import { useTelemetry } from '../telemetry/TelemetryProvider'

const WS_LABEL: Record<string, string> = {
  connecting: 'LINK…',
  open: 'ONLINE',
  closed: 'OFFLINE',
}

function Cell({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <div className={`telCell${wide ? ' telCell--wide' : ''}`}>
      <span className="telCell__label">{label}</span>
      <span className="telCell__value">{children}</span>
    </div>
  )
}

export function TelemetryStrip() {
  const { telemetry, wsStatus } = useTelemetry()
  const t = telemetry

  const batt = t?.battery
  const battPct = batt != null ? Math.max(0, Math.min(100, batt)) : null

  return (
    <div className="telStrip" aria-label="Телеметрия дрона">
      <div className="telStrip__inner">
        <div className="telStrip__brand">
          <span className="telStrip__pulse" data-state={wsStatus} aria-hidden />
          <span className="telStrip__brandText">TELEMETRY</span>
        </div>

        <Cell label="LINK">{WS_LABEL[wsStatus] ?? wsStatus}</Cell>

        <Cell label="ARM">
          {t?.armed == null ? (
            '—'
          ) : (
            <span className={t.armed ? 'telAccent telAccent--armed' : 'telMuted'}>{t.armed ? 'ARMED' : 'SAFE'}</span>
          )}
        </Cell>

        <Cell label="MODE" wide>
          {t?.mode ?? '—'}
        </Cell>

        <Cell label="LAT">{t?.lat != null ? t.lat.toFixed(6) : '—'}</Cell>
        <Cell label="LON">{t?.lon != null ? t.lon.toFixed(6) : '—'}</Cell>
        <Cell label="GPS">
          {t?.gps_fix != null || t?.gps_sats != null ? (
            <span style={{ color: (t?.gps_fix ?? 0) >= 2 ? undefined : '#f87171' }}>
              {t?.gps_fix != null ? (t.gps_fix >= 2 ? '3D' : t.gps_fix === 1 ? '2D' : '—') : '—'}
              {t?.gps_sats != null ? ` · ${t.gps_sats}` : ''}
            </span>
          ) : (
            '—'
          )}
        </Cell>
        <Cell label="BARO">{t?.baro_alt_m != null ? `${t.baro_alt_m.toFixed(1)} m` : t?.alt != null ? `${t.alt.toFixed(1)} m` : '—'}</Cell>
        <Cell label="AGL">{t?.baro_baseline_m != null && t?.alt != null ? `+${t.alt.toFixed(1)} m` : '—'}</Cell>
        <Cell label="HDG">{t?.heading != null ? `${t.heading.toFixed(0)}°` : '—'}</Cell>
        <Cell label="SPD">{t?.speed != null ? `${t.speed.toFixed(1)} m/s` : '—'}</Cell>

        <div className="telCell telCell--battery">
          <span className="telCell__label">BAT</span>
          <div className="telBatt">
            <div
              className="telBatt__fill"
              style={{ width: battPct != null ? `${battPct}%` : '0%' }}
              data-low={battPct != null && battPct <= 20 ? '1' : '0'}
            />
            <span className="telBatt__text">{batt != null ? `${batt.toFixed(0)}%` : '—'}</span>
          </div>
        </div>

        <Cell label="STAT">{t?.status ?? '—'}</Cell>

        <Cell label="SRC" wide>
          {t?.source ?? '—'}
        </Cell>

        {t?.note ? (
          <Cell label="NOTE" wide>
            <span className="telNote" title={t.note}>
              {t.note}
            </span>
          </Cell>
        ) : null}
      </div>
    </div>
  )
}
