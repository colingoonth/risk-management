import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Shell } from './components/Shell'
import { SemesterProvider } from './lib/SemesterContext'
import { Dashboard } from './pages/Dashboard'
import { Events } from './pages/Events'
import { Roster } from './pages/Roster'

export default function App() {
  return (
    <SemesterProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Shell />}>
            <Route index element={<Dashboard />} />
            <Route path="events" element={<Events />} />
            <Route path="roster" element={<Roster />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SemesterProvider>
  )
}
