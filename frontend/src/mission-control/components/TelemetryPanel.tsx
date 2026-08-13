import type { DroneProfile, TelemetryResponse } from '../../api/types'

const WS_LABEL: Record<string, string> = {
  connecting: 'подключение…',
  open: 'онлайн',
  closed: 'нет связи',
}

export function TelemetryPanel({
  telemetry,
  profile,
  wsStatus,
}: {
  telemetry: TelemetryResponse | null
  profile: DroneProfile | null
  wsStatus: 'connecting' | 'open' | 'closed'
}) {
  return (
    <section className="card">
      <div className="cardTitle">Телеметрия</div>
      <div className="subtitle">
        WS: <b>{WS_LABEL[wsStatus]}</b> • статус: <b>{telemetry?.status ?? 'неизвестно'}</b>
        {telemetry?.mode ? <> • режим: <b>{telemetry.mode}</b></> : null}
        {telemetry?.armed != null ? (
          <>
            {' '}
            • <b style={{ color: telemetry.armed ? 'var(--gcs-accent-bright)' : 'var(--gcs-danger)' }}>{telemetry.armed ? 'ARMED' : 'DISARMED'}</b>
          </>
        ) : null}
      </div>

      <div className="kv missionKv" style={{ marginTop: 10 }}>
        <div className="k">широта</div>
        <div className="v">{telemetry?.lat?.toFixed(6) ?? '—'}</div>
        <div className="k">долгота</div>
        <div className="v">{telemetry?.lon?.toFixed(6) ?? '—'}</div>
        <div className="k">высота AGL</div>
        <div className="v">{telemetry?.alt != null ? `${telemetry.alt.toFixed(1)} м` : '—'}</div>
        <div className="k">скорость</div>
        <div className="v">{telemetry?.speed != null ? `${telemetry.speed.toFixed(1)} м/с` : '—'}</div>
        <div className="k">курс</div>
        <div className="v">{telemetry?.heading != null ? `${telemetry.heading.toFixed(0)}°` : '—'}</div>
        <div className="k">GPS</div>
        <div className="v">
          {telemetry?.gps_fix != null
            ? `${telemetry.gps_fix >= 3 ? '3D' : telemetry.gps_fix === 2 ? '2D' : telemetry.gps_fix === 1 ? 'нет фикса' : 'нет GPS'}${telemetry.gps_sats != null ? ` · ${telemetry.gps_sats} sat` : ''}`
            : '—'}
        </div>
        <div className="k">батарея</div>
        <div className="v">{telemetry?.battery != null ? `${telemetry.battery.toFixed(0)}%` : '—'}</div>
      </div>

      {profile ? (
        <div className="hint" style={{ marginTop: 10 }}>
          Профиль: <b>{profile.label}</b> (<code>{profile.profile}</code>)
        </div>
      ) : null}
    </section>
  )
}
