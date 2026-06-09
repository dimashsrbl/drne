import { createContext, useContext, type ReactNode } from 'react'
import { useTelemetryWs } from '../hooks/useTelemetryWs'

type TelemetryContextValue = ReturnType<typeof useTelemetryWs>

const TelemetryContext = createContext<TelemetryContextValue | null>(null)

export function TelemetryProvider({ children }: { children: ReactNode }) {
  const value = useTelemetryWs()
  return <TelemetryContext.Provider value={value}>{children}</TelemetryContext.Provider>
}

export function useTelemetry(): TelemetryContextValue {
  const ctx = useContext(TelemetryContext)
  if (!ctx) {
    throw new Error('useTelemetry must be used within TelemetryProvider')
  }
  return ctx
}
