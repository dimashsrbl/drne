import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import './index.css'
import App from './App.tsx'
import { TelemetryProvider } from './telemetry/TelemetryProvider.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <TelemetryProvider>
        <App />
      </TelemetryProvider>
    </BrowserRouter>
  </StrictMode>,
)
