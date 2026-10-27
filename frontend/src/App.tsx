import { Navigate, Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { RequireAuth } from './components/RequireAuth';
import { RefreshProvider } from './components/RefreshContext';
import { AlertCenter } from './pages/AlertCenter';
import { DependencyMap } from './pages/DependencyMap';
import { LogExplorer } from './pages/LogExplorer';
import { Login } from './pages/Login';
import { Metrics } from './pages/Metrics';
import { Overview } from './pages/Overview';
import { RootCauseDetail, RootCauseList } from './pages/RootCause';
import { ScenarioLab } from './pages/ScenarioLab';
import { TraceDetail } from './pages/TraceDetail';
import { TraceExplorer } from './pages/TraceExplorer';

export function App() {
  return (
    <RefreshProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth />}>
          <Route element={<Layout />}>
            <Route index element={<Overview />} />
            <Route path="traces" element={<TraceExplorer />} />
            <Route path="traces/:traceId" element={<TraceDetail />} />
            <Route path="dependencies" element={<DependencyMap />} />
            <Route path="logs" element={<LogExplorer />} />
            <Route path="metrics" element={<Metrics />} />
            <Route path="root-cause" element={<RootCauseList />} />
            <Route path="root-cause/:id" element={<RootCauseDetail />} />
            <Route path="alerts" element={<AlertCenter />} />
            <Route path="scenarios" element={<ScenarioLab />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Routes>
    </RefreshProvider>
  );
}
