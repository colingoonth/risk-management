import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Shell } from './components/Shell'
import { SemesterProvider } from './lib/SemesterContext'
import { Calendar } from './pages/Calendar'
import { Dashboard } from './pages/Dashboard'
import { Events } from './pages/Events'
import { Notes } from './pages/Notes'
import { Ops } from './pages/Ops'
import { PledgeTakeover } from './pages/PledgeTakeover'
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
            <Route path="calendar" element={<Calendar />} />
            <Route path="swaps" element={<Swaps />} />
            <Route path="strikes" element={<Strikes />} />
            <Route path="pledges" element={<PledgeTakeover />} />
            <Route path="roster" element={<Roster />} />
            <Route path="notes" element={<Notes />} />
            <Route path="ops" element={<Ops />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SemesterProvider>
  )
}
