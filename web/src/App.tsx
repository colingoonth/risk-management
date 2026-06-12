import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Shell } from './components/Shell'
import { SemesterProvider } from './lib/SemesterContext'
import { Dashboard } from './pages/Dashboard'
import { Events } from './pages/Events'
import { Roster } from './pages/Roster'
import { Strikes } from './pages/Strikes'
import { Swaps } from './pages/Swaps'

export default function App() {
  return (
    <SemesterProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="events" element={<Events />} />
            <Route path="swaps" element={<Swaps />} />
            <Route path="strikes" element={<Strikes />} />
            <Route path="roster" element={<Roster />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SemesterProvider>
  )
}
