import './App.css'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './layout/AppShell'
import { MissionControlPage } from './pages/MissionControlPage'
import { MissionBuilder } from './pages/MissionBuilder'
import { RemotePage } from './pages/RemotePage'
import { VisionPage } from './pages/VisionPage'
import { Sim3DPage } from './pages/Sim3DPage'
import { BetaflightSequencePage } from './pages/BetaflightSequencePage'

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<Navigate to="/mission-control" replace />} />
        <Route path="/mission-control" element={<MissionControlPage />} />
        <Route path="/mission" element={<MissionBuilder />} />
        <Route path="/betaflight" element={<BetaflightSequencePage />} />
        <Route path="/pult" element={<RemotePage />} />
        <Route path="/vision" element={<VisionPage />} />
        <Route path="/sim3d" element={<Sim3DPage />} />
        <Route path="*" element={<Navigate to="/mission-control" replace />} />
      </Route>
    </Routes>
  )
}

export default App
