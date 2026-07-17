import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../api/client'
import type { DroneProfile } from '../api/types'
import { TelemetryStrip } from './TelemetryStrip'

const nav = [
  { to: '/mission-control', label: 'Mission Control', mc: true as const },
  { to: '/mission', label: 'Миссии' },
  { to: '/betaflight', label: 'Betaflight' },
  { to: '/pult', label: 'Пульт' },
  { to: '/vision', label: 'Камера' },
  { to: '/sim3d', label: '3D сим' },
] as const

export function AppShell() {
  const { pathname } = useLocation()
  const [profile, setProfile] = useState<DroneProfile | null>(null)
  const missionControlActive = pathname === '/' || pathname === '/mission-control'

  useEffect(() => {
    let dead = false
    void api
      .profile()
      .then((data) => {
        if (!dead) setProfile(data)
      })
      .catch(() => {})
    return () => {
      dead = true
    }
  }, [])

  return (
    <div className="gcsRoot">
      <header className="gcsHeader">
        <div className="gcsHeader__left">
          <div className="gcsLogo">
            <span className="gcsLogo__mark" aria-hidden />
            <div>
              <div className="gcsLogo__title">Наземная станция</div>
              <div className="gcsLogo__sub">
                {profile ? (
                  <>
                    <span className="gcsLogo__profile">{profile.label}</span>
                    <span className="gcsLogo__sep">·</span>
                    <code className="gcsLogo__code">{profile.profile}</code>
                  </>
                ) : (
                  <span className="gcsLogo__muted">профиль…</span>
                )}
              </div>
            </div>
          </div>
        </div>

        <nav className="gcsNav" aria-label="Разделы">
          {nav.map((item) => {
            const { to, label, mc } = item as { to: string; label: string; mc?: true }
            const displayLabel = to === '/betaflight' && profile?.profile === 'ardupilot' ? 'Pixhawk' : label
            return (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) =>
                  `gcsNavLink${mc ? (missionControlActive ? ' gcsNavLink--active' : '') : isActive ? ' gcsNavLink--active' : ''}`
                }
              >
                {displayLabel}
              </NavLink>
            )
          })}
        </nav>

        <div className="gcsHeader__right">
          <span className="gcsApiPill" title="Прокси Vite: запросы к бэкенду">
            API <span className="gcsApiPill__path">/api</span>
          </span>
        </div>
      </header>

      <TelemetryStrip />

      <main className="gcsMain">
        <Outlet />
      </main>
    </div>
  )
}
